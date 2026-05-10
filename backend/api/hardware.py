import os
import json
import struct
import torch
from pathlib import Path
import psutil
import ctypes


def get_app_dir():
    """
    User-writable directory for all runtime data (model weights, DB, hardware config).
    Uses LOCALAPPDATA on Windows so the app works whether installed in Program Files
    (which is read-only without elevation) or run from any other location.
    Typical path: C:\\Users\\<name>\\AppData\\Local\\chess-ai\\
    """
    base = os.environ.get('LOCALAPPDATA', str(Path.home()))
    path = Path(base) / 'chess-ai'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_ram_gb():
    """Detect total physical RAM in gigabytes.
    Tries psutil first (cross-platform), falls back to a direct Windows API call
    via ctypes if psutil is unavailable, then defaults to 4 GB as a safe estimate."""
    try:
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass

    try:
        # MEMORYSTATUSEX is a Windows kernel struct for querying memory info
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength",                 ctypes.c_ulong),
                ("dwMemoryLoad",             ctypes.c_ulong),
                ("ullTotalPhys",             ctypes.c_ulonglong),
                ("ullAvailPhys",             ctypes.c_ulonglong),
                ("ullTotalPageFile",         ctypes.c_ulonglong),
                ("ullAvailPageFile",         ctypes.c_ulonglong),
                ("ullTotalVirtual",          ctypes.c_ulonglong),
                ("ullAvailVirtual",          ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys / (1024 ** 3)
    except Exception:
        pass

    return 4.0  # safe conservative fallback


def _get_cuda_gpu_info():
    """NVIDIA only. Returns (name, vram_gb) from CUDA device properties."""
    try:
        props = torch.cuda.get_device_properties(0)
        return props.name, round(props.total_memory / (1024 ** 3), 1)
    except Exception:
        return "", 0.0


def _get_directml_vram():
    """Query dedicated GPU VRAM via WMI for non-NVIDIA cards.
    WMI's AdapterRAM field is a 32-bit integer that caps at ~4 GB for larger cards.
    We accept this limitation and return 4.0 as the minimum when detection fails,
    which is conservative enough to correctly enable the large network on any
    modern discrete AMD/Intel GPU."""
    try:
        import subprocess, json
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-WmiObject Win32_VideoController"
             " | Where-Object {$_.Name -notlike '*Microsoft*' -and $_.Name -notlike '*Basic*'}"
             " | Sort-Object AdapterRAM -Descending"
             " | Select-Object -First 1 -ExpandProperty AdapterRAM"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode == 0:
            ram = int(result.stdout.strip() or 0)
            if ram > 0:
                return round(ram / (1024 ** 3), 1)
    except Exception:
        pass
    return 4.0  # safe default: any modern discrete GPU has ≥4 GB


def _get_directml_info():
    """Detect a DirectML-capable GPU (AMD, Intel, or any DX12 device).
    Returns (name, vram_gb) or None if torch_directml is not installed or
    no DX12 device is present.

    torch_directml must be installed separately (pip install torch-directml).
    It is only installed by rebuild.ps1 when an AMD GPU is detected — NVIDIA
    machines use CUDA instead, and CPU-only machines skip it entirely."""
    try:
        # Lazy: torch_directml is an optional dependency (pip install torch-directml).
        # It is only installed by rebuild.ps1 when an AMD/Intel GPU is detected —
        # NVIDIA machines use CUDA and CPU-only machines skip it entirely.
        # A top-level import would crash on any machine where it isn't installed.
        import torch_directml
        if torch_directml.device_count() == 0:
            return None
        name    = torch_directml.device_name(0)
        vram_gb = _get_directml_vram()
        return name, vram_gb
    except ImportError:
        return None
    except Exception:
        return None


def _scale_training_iterations(has_gpu, vram_gb):
    """Return how many training iterations to run per game based on available hardware.

    WHY THE CEILING IS LOW (max 75):
    A typical game produces ~50 board positions. Running 1000 iterations on 50
    positions means the network sees each position 1000 times in one sitting —
    it memorises the game rather than learning general chess principles.
    The goal is for the AI to improve gradually across many games, picking up
    a little knowledge each time, not to become an expert after 5 games.

    WHY GPU TIERS DON'T SCALE PAST 75:
    The extra VRAM on a high-end GPU doesn't mean the AI should learn faster —
    it just means it *can* run more iterations without slowing down. Since we
    deliberately want slow, steady growth (one player, one PC), we cap all GPU
    users at the same ceiling. The hardware advantage is already felt through
    the larger network architecture (768→256→128→1 vs 64→128→64→1) and faster
    search depth, not through faster learning per game.

    Tiers:
      CPU           → 20
      GPU < 4GB     → 40
      GPU 4-6GB     → 50
      GPU 6-8GB     → 60
      GPU >= 8GB    → 75  (hard ceiling regardless of VRAM)
    """
    if not has_gpu:
        return 20

    if   vram_gb >= 8: return 75
    elif vram_gb >= 6: return 60
    elif vram_gb >= 4: return 50
    else:              return 40


def _is_laptop():
    """Return True when a battery is present — the most reliable cross-version way
    to tell we're on a laptop without reading SMBIOS tables or WMI."""
    try:
        return psutil.sensors_battery() is not None
    except Exception:
        return False


def _get_core_groups():
    """Detect P-core / E-core / LP E-core groups via GetLogicalProcessorInformationEx.

    Windows assigns each physical core an EfficiencyClass value:
      highest value  = P-cores  (Performance — fastest, boost capable)
      second value   = E-cores  (Efficiency — medium speed)
      third value    = LP E-cores (Low-Power E-cores — slowest, Meteor Lake+)
    Non-hybrid CPUs have only one EfficiencyClass across all cores.

    Returns a dict with:
      p_core_groups   — list of [logical_cpu_ids] per P-core physical
      e_core_groups   — list of [logical_cpu_ids] per E-core physical
      lpe_core_groups — list of [logical_cpu_ids] per LP E-core physical
      is_hybrid       — True when multiple EfficiencyClass values found
    """
    _empty = {"p_core_groups": [], "e_core_groups": [], "lpe_core_groups": [], "is_hybrid": False}

    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return _empty   # not Windows

    # RelationProcessorCore (0): one record per physical core, includes EfficiencyClass
    RelationProcessorCore = 0
    buf_size = ctypes.c_ulong(0)

    # First call returns ERROR_INSUFFICIENT_BUFFER but fills buf_size with the needed size
    kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, None, ctypes.byref(buf_size))
    if buf_size.value == 0:
        return _empty

    buf = (ctypes.c_byte * buf_size.value)()
    if not kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, buf, ctypes.byref(buf_size)):
        return _empty

    # Each record is SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX.  Relevant byte offsets:
    #   +0   DWORD Relationship
    #   +4   DWORD Size             (variable — step by this to reach next record)
    #   +8   BYTE  Flags            (PROCESSOR_RELATIONSHIP starts here)
    #   +9   BYTE  EfficiencyClass
    #   +10  BYTE  Reserved[20]
    #   +30  WORD  GroupCount
    #   +32  GROUP_AFFINITY Group[] (16 bytes each on 64-bit Windows)
    #          +0  KAFFINITY Mask   (8 bytes — one bit per logical processor)
    #          +8  WORD Group       (processor group index, almost always 0)
    by_class = {}  # EfficiencyClass → list of [logical_cpu_ids]
    offset   = 0
    total    = buf_size.value

    while offset < total:
        if offset + 8 > total:
            break

        relationship = struct.unpack_from('<I', buf, offset)[0]
        size         = struct.unpack_from('<I', buf, offset + 4)[0]

        if size == 0 or offset + size > total:
            break

        if relationship == RelationProcessorCore and offset + 32 <= total:
            eff_class   = struct.unpack_from('B',  buf, offset + 9)[0]
            group_count = struct.unpack_from('<H', buf, offset + 30)[0]

            cpus = []
            for g in range(group_count):
                ga = offset + 32 + g * 16   # each GROUP_AFFINITY is 16 bytes
                if ga + 10 > total:
                    break
                mask  = struct.unpack_from('<Q', buf, ga)[0]       # 8-byte affinity mask
                group = struct.unpack_from('<H', buf, ga + 8)[0]   # processor group index
                base  = group * 64  # each group has up to 64 logical processors
                for bit in range(64):
                    if mask & (1 << bit):
                        cpus.append(base + bit)

            if cpus:
                by_class.setdefault(eff_class, []).append(cpus)

        offset += size

    if not by_class:
        return _empty

    sorted_classes  = sorted(by_class.keys(), reverse=True)   # highest = P-cores
    is_hybrid       = len(sorted_classes) > 1

    p_core_groups   = by_class[sorted_classes[0]] if len(sorted_classes) >= 1 else []
    e_core_groups   = by_class[sorted_classes[1]] if len(sorted_classes) >= 2 else []
    lpe_core_groups = by_class[sorted_classes[2]] if len(sorted_classes) >= 3 else []

    return {
        "p_core_groups":   p_core_groups,
        "e_core_groups":   e_core_groups,
        "lpe_core_groups": lpe_core_groups,
        "is_hybrid":       is_hybrid,
    }


def _detect():
    """Auto-detect hardware and return optimal settings for this machine."""
    cores       = os.cpu_count() or 1
    ram_gb      = _get_ram_gb()
    is_laptop   = _is_laptop()
    core_groups = _get_core_groups()

    # GPU detection priority: NVIDIA CUDA → DirectML (AMD/Intel) → CPU only.
    # CUDA is preferred because it has lower dispatch overhead and is better
    # supported by PyTorch. DirectML is used for AMD/Intel DX12 GPUs on Windows.
    if torch.cuda.is_available():
        has_gpu  = True
        device   = "cuda"
        gpu_name, vram_gb = _get_cuda_gpu_info()
    else:
        dml = _get_directml_info()
        if dml:
            has_gpu  = True
            device   = "dml"
            gpu_name, vram_gb = dml
        else:
            has_gpu  = False
            device   = "cpu"
            gpu_name = ""
            vram_gb  = 0.0

    # Transposition table size — scales with available RAM.
    # More RAM = larger TT = fewer cache misses = deeper effective search.
    # Each TT entry is roughly 80–100 bytes (score + move + depth).
    if   ram_gb >= 16: tt_size = 5_000_000   # ~400–500 MB
    elif ram_gb >= 8:  tt_size = 1_000_000   # ~80–100 MB
    elif ram_gb >= 4:  tt_size = 200_000     # ~16–20 MB
    else:              tt_size = 100_000     # ~8–10 MB

    # Worker count for the parallel search pool.
    # cores // 2 approximates physical cores on CPUs with hyperthreading.
    # Alpha-beta is compute-bound, so hyperthreads don't help much; using cores // 2
    # avoids the performance penalty of scheduling work on logical-only cores.
    workers = max(1, cores // 2)

    # pool_max_workers: on hybrid CPUs, spawn one worker per P-core (fastest cores only).
    # Non-hybrid CPUs use the same workers value — no benefit to pinning.
    p_cores = core_groups["p_core_groups"]
    pool_max_workers = len(p_cores) if (core_groups["is_hybrid"] and p_cores) else workers

    training_iterations = _scale_training_iterations(has_gpu, vram_gb)

    # use_large_net: GPU users with >=4GB VRAM get the larger 768→256→128→1 network
    # with 12-plane input encoding. CPU users always use the smaller 64→128→64→1 network
    # to avoid any inference slowdown during search.
    # DirectML (AMD) users are treated the same as CUDA users — if VRAM ≥4 GB,
    # use the large network. The DirectML VRAM value defaults to 4.0 GB when WMI
    # can't read it exactly, which is conservative for any modern discrete GPU.
    use_large_net = has_gpu and vram_gb >= 4.0

    config = {
        "tt_size":             tt_size,
        "workers":             workers,
        "pool_max_workers":    pool_max_workers,
        "training_iterations": training_iterations,
        "device":              device,
        "cores":               cores,
        "ram_gb":              round(ram_gb, 1),
        "has_gpu":             has_gpu,
        "gpu_name":            gpu_name,
        "gpu_vram_gb":         vram_gb,
        "use_large_net":       use_large_net,
        "is_laptop":           is_laptop,
        "is_hybrid":           core_groups["is_hybrid"],
        "p_core_groups":       core_groups["p_core_groups"],
        "e_core_groups":       core_groups["e_core_groups"],
        "lpe_core_groups":     core_groups["lpe_core_groups"],
    }

    # Store detection summary for deferred DB logging (flushed in apps.py ready()).
    # We log only on fresh detection, not on every cache load, to avoid flooding
    # the IssueLog with identical entries on every server restart.
    global _PENDING_DETECTION_LOG
    _PENDING_DETECTION_LOG = (
        f"device={device} gpu={gpu_name!r} vram={vram_gb}GB "
        f"use_large_net={use_large_net} training_iterations={training_iterations}"
    )

    return config


# Populated by _detect() when fresh hardware detection runs.
# Flushed to IssueLog by flush_detection_log(), called from apps.py ready().
_PENDING_DETECTION_LOG = None


def flush_detection_log():
    """Write the GPU detection result to IssueLog. Must be called after Django is ready
    (i.e. from AppConfig.ready()) because the ORM is not available at module import time."""
    global _PENDING_DETECTION_LOG
    if _PENDING_DETECTION_LOG is None:
        return
    try:
        # Lazy: must be called from AppConfig.ready() after django.setup() completes;
        # the ORM is not available at hardware.py module import time.
        from .models import IssueLog
        IssueLog.objects.create(
            issue_type="gpu_detection",
            detail=_PENDING_DETECTION_LOG,
        )
        _PENDING_DETECTION_LOG = None
    except Exception:
        pass


def load():
    """Load hardware config from disk if it exists, otherwise detect and save it.
    Caching avoids re-running detection (which calls psutil and torch.cuda) on every
    startup. Delete hardware_config.json to force re-detection (e.g. after a hardware upgrade)."""
    config_path = get_app_dir() / 'hardware_config.json'

    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                cfg = json.load(f)
            # Back-fill new keys that didn't exist in older config files.
            # This lets users upgrade without deleting their config.
            if "gpu_name"         not in cfg: cfg["gpu_name"]         = ""
            if "gpu_vram_gb"      not in cfg: cfg["gpu_vram_gb"]      = 0.0
            if "use_large_net"    not in cfg: cfg["use_large_net"]    = False
            if "is_laptop"        not in cfg: cfg["is_laptop"]        = _is_laptop()
            if "is_hybrid"        not in cfg: cfg["is_hybrid"]        = False
            if "p_core_groups"    not in cfg: cfg["p_core_groups"]    = []
            if "e_core_groups"    not in cfg: cfg["e_core_groups"]    = []
            if "lpe_core_groups"  not in cfg: cfg["lpe_core_groups"]  = []
            if "pool_max_workers" not in cfg:
                p = cfg.get("p_core_groups", [])
                cfg["pool_max_workers"] = len(p) if (cfg["is_hybrid"] and p) else cfg.get("workers", 1)
            if "device" not in cfg:
                cfg["device"] = "cuda" if cfg.get("has_gpu") else "cpu"
            return cfg
        except Exception:
            pass  # corrupt file — fall through to re-detect

    config = _detect()

    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass  # non-fatal — config just won't persist across restarts

    return config


# Runs once at import time. All other modules read from this dict.
HW = load()
