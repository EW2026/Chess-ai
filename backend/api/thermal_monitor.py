"""
thermal_monitor.py
==================
Monitors CPU boost clock and dynamically adjusts the number of active search
workers so the CPU does not thermally throttle during a game.

Why boost clock rather than temperature:
  A CPU's current clock speed already captures every throttle cause at once —
  thermal limits, power limits, battery limits, and power-plan caps all show up
  as a frequency drop. One number, no vendor-specific sensor API required.

Why a self-calibrating baseline:
  'spec max boost' is a single-core burst that never holds under load. Rather
  than comparing against the spec max, we sample the clock 5 seconds after the
  first minimax search starts (after the opening phase + settle time). That
  sample is the real, sustained operating point for this CPU/power-plan combo.
  All future readings are compared against that baseline.

State machine:
  WAITING    — no minimax search running yet (opening handled by Stockfish)
  SETTLING   — minimax just started; 5-second delay before taking baseline
  MONITORING — poll every 5 s, adjust active_workers based on drop from baseline

Drop thresholds (from baseline):
  < 15%  → full workers   (normal operating range)
  15–30% → 75% of max     (light throttle — reduce load a little)
  30–50% → 50% of max     (moderate throttle)
  > 50%  → 1 worker       (severe throttle — emergency reduction)

Scale-down: immediate on threshold crossing.
Scale-up  : only after 2 consecutive 5-second readings above the threshold
            (hysteresis prevents oscillation when the CPU briefly recovers).
"""

import time
import threading
import psutil

# ── State constants ────────────────────────────────────────────────────────────
_STATE_WAITING    = "WAITING"
_STATE_SETTLING   = "SETTLING"
_STATE_MONITORING = "MONITORING"

# ── Module-level state (all guarded by _lock) ──────────────────────────────────
_lock             = threading.Lock()
_state            = _STATE_WAITING
_baseline_mhz     = None   # freq sampled after the settle period
_active_workers   = 1      # current number of parallel workers
_max_workers      = 1      # upper bound set by hardware config
_p_core_groups    = []     # [[logical_cpu_ids], ...] per P-core physical — may be empty
_is_hybrid        = False  # True when CPU has P+E cores
_pool_ref         = None   # the live ProcessPoolExecutor (for affinity pinning)
_consecutive_good = 0      # consecutive readings where target > active (for scale-up)
_poll_thread      = None   # background monitoring thread

# Per-game stats — reset each new_game(), read by learning.py at training time
_clock_samples    = []     # MHz readings taken during MONITORING (for avg_clock_mhz)
_min_workers_seen = None   # lowest active_workers hit during the game


# ==============================================================================
# PUBLIC API
# ==============================================================================

def init(hw):
    """Read hardware config and set up initial state.  Call once at startup."""
    global _max_workers, _active_workers, _p_core_groups, _is_hybrid
    with _lock:
        _max_workers    = hw.get("pool_max_workers", hw.get("workers", 1))
        _active_workers = _max_workers
        _p_core_groups  = hw.get("p_core_groups", [])
        _is_hybrid      = hw.get("is_hybrid", False)


def set_pool(pool):
    """Register the live ProcessPoolExecutor.

    Called after pool creation or restart.  On hybrid CPUs, immediately attempts
    to pin all worker processes to P-core logical processors so they never run
    on E-cores or LP E-cores.  Workers may not be spawned yet (lazy spawn) —
    that is fine; the function is idempotent and safe to call again later.
    """
    global _pool_ref
    with _lock:
        _pool_ref = pool
    if pool is not None:
        _pin_workers_to_pcores(pool)


def reset():
    """Reset state between games.  Called by new_game() so each game starts fresh."""
    global _state, _baseline_mhz, _consecutive_good, _active_workers
    global _clock_samples, _min_workers_seen
    with _lock:
        _state            = _STATE_WAITING
        _baseline_mhz     = None
        _consecutive_good = 0
        _active_workers   = _max_workers
        _clock_samples    = []
        _min_workers_seen = None


def get_avg_clock():
    """Return average sustained CPU clock in MHz across the current game.  0 if not yet sampled."""
    with _lock:
        return round(sum(_clock_samples) / len(_clock_samples), 0) if _clock_samples else 0.0


def get_min_workers():
    """Return the lowest active worker count hit this game (worst throttle point)."""
    with _lock:
        return _min_workers_seen if _min_workers_seen is not None else _max_workers


def notify_minimax_started():
    """Signal that the first real minimax search past the opening has begun.

    Transitions WAITING → SETTLING and launches a background thread that will:
      1. Wait 5 seconds for the CPU to reach its sustained clock under load.
      2. Sample that clock as the baseline.
      3. Poll every 5 seconds and adjust active_workers as needed.

    Safe to call multiple times — only the first call past WAITING takes effect.
    """
    global _state, _poll_thread
    with _lock:
        if _state != _STATE_WAITING:
            return
        _state = _STATE_SETTLING

    t = threading.Thread(target=_settle_then_monitor, daemon=True)
    t.start()
    with _lock:
        _poll_thread = t


def get_active_workers():
    """Thread-safe read of the current active worker count."""
    with _lock:
        return _active_workers


# ==============================================================================
# INTERNAL: STATE MACHINE
# ==============================================================================

def _settle_then_monitor():
    """Background thread: settle delay → baseline → poll loop."""
    time.sleep(5.0)     # let the CPU reach its sustained clock under load
    _take_baseline()

    while True:
        time.sleep(5.0)
        with _lock:
            if _state != _STATE_MONITORING:
                break
        _check_and_adjust()


def _take_baseline():
    """Sample the current CPU frequency as the reference point for future drops."""
    global _baseline_mhz, _state
    try:
        freq = psutil.cpu_freq()
        if freq is None or freq.current <= 0:
            return
        with _lock:
            _baseline_mhz = freq.current
            _state        = _STATE_MONITORING
            _clock_samples.append(freq.current)   # count baseline as first sample
    except Exception:
        pass


def _check_and_adjust():
    """Compare current freq to baseline; scale workers up or down as needed."""
    global _active_workers, _consecutive_good, _min_workers_seen

    try:
        freq = psutil.cpu_freq()
        if freq is None or freq.current <= 0:
            return
        current_mhz = freq.current
    except Exception:
        return

    with _lock:
        if _baseline_mhz is None or _baseline_mhz <= 0:
            return
        drop  = (_baseline_mhz - current_mhz) / _baseline_mhz
        max_w = _max_workers

    # Map drop fraction to target worker count
    if   drop < 0.15: target = max_w
    elif drop < 0.30: target = max(1, round(max_w * 0.75))
    elif drop < 0.50: target = max(1, round(max_w * 0.50))
    else:             target = 1

    with _lock:
        _clock_samples.append(current_mhz)

        if target < _active_workers:
            _active_workers   = target
            _consecutive_good = 0
        elif target > _active_workers:
            _consecutive_good += 1
            if _consecutive_good >= 2:
                _active_workers   = target
                _consecutive_good = 0
        else:
            _consecutive_good = 0

        # Track the lowest worker count seen this game
        if _min_workers_seen is None or _active_workers < _min_workers_seen:
            _min_workers_seen = _active_workers


# ==============================================================================
# INTERNAL: CPU AFFINITY PINNING
# ==============================================================================

def _pin_workers_to_pcores(pool):
    """Pin all pool worker processes to P-core logical processors.

    On non-hybrid CPUs (no P/E distinction) this is a no-op — every core is
    equally fast so there is nothing to pin.

    ProcessPoolExecutor._processes is a dict {pid: Process} that is stable
    across Python versions.  It may be empty if workers haven't been spawned
    yet (first submit hasn't happened).  In that case the function is a no-op;
    call set_pool again after warmup to pick up newly spawned workers.
    """
    if not _is_hybrid or not _p_core_groups:
        return

    # Flatten all P-core logical processor IDs
    all_p_cpus = [cpu for group in _p_core_groups for cpu in group]
    if not all_p_cpus:
        return

    try:
        processes = pool._processes   # {pid: Process} — private but stable
    except AttributeError:
        return

    for pid in list(processes.keys()):
        try:
            psutil.Process(pid).cpu_affinity(all_p_cpus)
        except Exception:
            pass
