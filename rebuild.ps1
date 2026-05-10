param(
    [switch]$CleanRust,  # Pass -CleanRust only when main.rs or Cargo.toml changed
    [switch]$Dev         # Run cargo tauri dev instead of building an installer
)

if ($Dev) {
    Write-Host "=== Chess AI Dev Build ===" -ForegroundColor Cyan
    Write-Host "  Backend EXE will be loaded from backend\dist\run_server\ directly."
    Write-Host "  Close the app window to end the session."
} else {
    Write-Host "=== Chess AI Full Rebuild ===" -ForegroundColor Cyan
}

# --- KILL RUNNING PROCESSES ---
Write-Host "Stopping running processes..."
Get-Process run_server -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process ChessAI   -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

# --- WIPE APP DATA (database, trained model, hardware cache) ---
Write-Host "Wiping app data..."
$appData = "$env:LOCALAPPDATA\chess-ai"
Remove-Item -Force "$appData\db.sqlite3"           -ErrorAction SilentlyContinue
Remove-Item -Force "$appData\chess_model.pth"      -ErrorAction SilentlyContinue
Remove-Item -Force "$appData\hardware_config.json" -ErrorAction SilentlyContinue

# --- CLEAN FRONTEND ---
Write-Host "Cleaning frontend..."
Remove-Item -Recurse -Force chess-ai\dist              -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force chess-ai\node_modules      -ErrorAction SilentlyContinue
Remove-Item -Force         chess-ai\package-lock.json  -ErrorAction SilentlyContinue

# --- CLEAN BACKEND BUILDS ---
Write-Host "Cleaning backend builds..."
Remove-Item -Recurse -Force backend\build     -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force backend\dist      -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force backend\static\*  -ErrorAction SilentlyContinue

# --- CLEAN PYTHON CACHE ---
Get-ChildItem -Path "." -Recurse -Include "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# --- CLEAN TAURI BUILD ---
Write-Host "Cleaning Tauri build..."
if ($CleanRust) {
    Write-Host "Wiping Rust target (full recompile)..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force chess-ai\src-tauri\target -ErrorAction SilentlyContinue
}
# resources/run_server/ is repopulated from backend dist after the backend build (production only)

# --- VIRTUAL ENVIRONMENT ---
Write-Host "Checking virtual environment..."
$root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$venvPython      = "$root\chessai\Scripts\python.exe"
$venvPyInstaller = "$root\chessai\Scripts\pyinstaller.exe"

if (!(Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    $pythonCmd = "python"
    try { & $pythonCmd --version | Out-Null } catch { $pythonCmd = "py" }
    & $pythonCmd -m venv chessai
    Start-Sleep -Seconds 2
}

if (!(Test-Path $venvPython)) {
    Write-Error "Venv creation failed."
    exit 1
}

# --- INSTALL PYTHON DEPENDENCIES ---
Write-Host "Installing Python dependencies..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r backend\requirements.txt --quiet

# Install PyTorch from the official index. Use the CUDA 12.4 wheel when an
# NVIDIA GPU is present so torch.cuda.is_available() returns True and the
# hardware detector can see the GPU and its VRAM. Fall back to the CPU wheel
# on machines with no NVIDIA card (AMD, Intel-only, or no discrete GPU).
# Note: the CUDA wheel is ~2 GB vs ~200 MB for CPU — first build will be slower.
# Detect GPU and install the correct PyTorch build — CUDA for NVIDIA, CPU otherwise.
# We check the currently installed build suffix (+cpu vs +cu*) and only force-reinstall
# when the wrong type is present. This avoids re-downloading 2 GB on every rebuild
# while still correcting a mismatched install (e.g. CPU wheel on a CUDA machine).
$nvidiaGpu    = Get-WmiObject Win32_VideoController |
    Where-Object { $_.Name -like "*NVIDIA*" } | Select-Object -First 1
$amdGpu       = Get-WmiObject Win32_VideoController |
    Where-Object { $_.Name -like "*AMD*" -or $_.Name -like "*Radeon*" } | Select-Object -First 1
$currentTorch = & $venvPython -c "import torch; print(torch.__version__)" 2>$null
$isCpuBuild   = $currentTorch -like "*+cpu*"
$isCudaBuild  = $currentTorch -like "*+cu*"

if ($nvidiaGpu) {
    # NVIDIA: use CUDA 12.4 wheel so torch.cuda.is_available() returns True
    if (-not $isCudaBuild) {
        Write-Host "NVIDIA GPU detected ($($nvidiaGpu.Name)) - switching to CUDA 12.4 PyTorch (was: $currentTorch)..."
        & $venvPython -m pip install torch --index-url https://download.pytorch.org/whl/cu124 --force-reinstall
        if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch CUDA install failed"; exit 1 }
    } else {
        Write-Host "CUDA PyTorch already installed ($currentTorch) - skipping."
    }
} elseif ($amdGpu) {
    # AMD: use CPU torch as the base + torch-directml on top.
    # PyTorch has no Windows CUDA support for AMD (ROCm is Linux-only).
    # DirectML provides GPU training via DirectX 12, which works on any AMD DX12 card.
    if (-not $isCpuBuild) {
        Write-Host "AMD GPU detected ($($amdGpu.Name)) - switching to CPU PyTorch + DirectML (was: $currentTorch)..."
        & $venvPython -m pip install torch --index-url https://download.pytorch.org/whl/cpu --force-reinstall
        if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch CPU install failed"; exit 1 }
    } else {
        Write-Host "CPU PyTorch already installed for AMD ($currentTorch) - skipping base install."
    }
    $isDmlInstalled = (& $venvPython -c "import torch_directml; print('ok')" 2>$null) -eq "ok"
    if (-not $isDmlInstalled) {
        Write-Host "Installing torch-directml for AMD GPU support..."
        & $venvPython -m pip install torch-directml
        if ($LASTEXITCODE -ne 0) { Write-Error "torch-directml install failed"; exit 1 }
    } else {
        Write-Host "torch-directml already installed - skipping."
    }
} else {
    # No discrete GPU detected - CPU only
    if (-not $isCpuBuild) {
        Write-Host "No discrete GPU detected - switching to CPU PyTorch (was: $currentTorch)..."
        & $venvPython -m pip install torch --index-url https://download.pytorch.org/whl/cpu --force-reinstall
        if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch CPU install failed"; exit 1 }
    } else {
        Write-Host "CPU PyTorch already installed ($currentTorch) - skipping."
    }
}

& $venvPython -m pip install pyinstaller --quiet

# --- BUILD FRONTEND ---
Write-Host "Building frontend..."
Set-Location chess-ai
npm install
if ($LASTEXITCODE -ne 0) { Write-Error "npm install failed"; Set-Location ..; exit 1 }
npm run build
if ($LASTEXITCODE -ne 0) { Write-Error "npm build failed"; Set-Location ..; exit 1 }
Set-Location ..

# --- COPY FRONTEND INTO DJANGO ---
Write-Host "Copying frontend to Django..."
New-Item -ItemType Directory -Force -Path backend\templates | Out-Null
New-Item -ItemType Directory -Force -Path backend\static    | Out-Null
Copy-Item chess-ai\dist\index.html backend\templates\index.html -Force
Copy-Item chess-ai\dist\assets     backend\static\assets -Recurse -Force
Copy-Item chess-ai\dist\pieces     backend\static\pieces -Recurse -Force -ErrorAction SilentlyContinue

# --- BUILD BACKEND EXE (spec file bundles Stockfish + static + templates) ---
Write-Host "Building backend EXE..."
Set-Location backend
& $venvPyInstaller run_server.spec --clean -y
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller build failed"; Set-Location ..; exit 1 }
Set-Location ..

# --- VERIFY EXE ---
$exePath = "backend\dist\run_server\run_server.exe"
if (!(Test-Path $exePath)) {
    Write-Error "Backend EXE was not created!"
    exit 1
}

# --- ENSURE STOCKFISH IS IN DIST (spec should do this; fallback if it didn't) ---
# PyInstaller 6+ one-dir mode places binaries inside _internal\ (sys._MEIPASS).
# Check both locations: root (older PyInstaller) and _internal\ (newer PyInstaller).
$sfRoot     = "backend\dist\run_server\stockfish-windows-x86-64-avx2.exe"
$sfInternal = "backend\dist\run_server\_internal\stockfish-windows-x86-64-avx2.exe"
$sfSrc      = "stockfish\stockfish-windows-x86-64-avx2.exe"
if (-not (Test-Path $sfRoot) -and -not (Test-Path $sfInternal)) {
    if (Test-Path $sfSrc) {
        Write-Host "Stockfish not bundled by spec - copying manually..." -ForegroundColor Yellow
        # Copy to _internal\ so sys._MEIPASS can find it at runtime
        New-Item -ItemType Directory -Force -Path "backend\dist\run_server\_internal" | Out-Null
        Copy-Item $sfSrc "backend\dist\run_server\_internal\" -Force
    } else {
        Write-Error "Stockfish not found at $sfSrc - engine moves will be disabled!"
        exit 1
    }
}

# --- STRIP UNUSED CUDA DLLs ---
# PyInstaller bundles every DLL torch ships, including libraries the chess AI
# never calls (FFT, sparse matrices, RNN ops, multi-GPU solvers, etc.).
# Removing them cuts ~1.75 GB from the bundle, keeping the installer under the
# 2 GB limit that both MSI and NSIS enforce on their final output files.
# What we KEEP: torch_cuda, torch_cpu, cublas64, cublasLt64, cudnn_ops64
# (sufficient for forward/backward passes on a small FC network with Adam).
Write-Host "Stripping unused CUDA DLLs..."
$torchLib = "backend\dist\run_server\_internal\torch\lib"
$dllsToRemove = @(
    # Only strip cuDNN sub-library plugins. cudnn_ops64_9.dll loads these via
    # LoadLibrary at runtime — they are NOT in any DLL import table, so removing
    # them will not cause a load-time OSError. All other CUDA DLLs (cufft, cusparse,
    # curand, cusolver, shm, etc.) ARE directly imported by torch.dll / torch_cuda.dll
    # and must stay in the bundle or torch fails to load entirely.
    "cudnn_engines_precompiled64_9.dll",      # 562 MB
    "cudnn_adv64_9.dll",                      # 230 MB
    "cudnn_heuristic64_9.dll",                #  82 MB
    "cudnn_engines_runtime_compiled64_9.dll", #   8 MB
    "cudnn_cnn64_9.dll"                       #   4 MB
)
$removed = 0
foreach ($dll in $dllsToRemove) {
    $path = "$torchLib\$dll"
    if (Test-Path $path) {
        Remove-Item $path -Force
        $removed++
    }
}
Write-Host "Stripped $removed DLLs from torch\lib." -ForegroundColor Green

# --- VERIFY STATIC ASSETS ---
$staticRoot     = "backend\dist\run_server\static\assets"
$staticInternal = "backend\dist\run_server\_internal\static\assets"
if (Test-Path $staticRoot) {
    Write-Host "Static assets OK (static/assets)" -ForegroundColor Green
} elseif (Test-Path $staticInternal) {
    Write-Host "Static assets OK (_internal/static/assets)" -ForegroundColor Green
} else {
    Write-Error "Static assets missing from EXE build!"
    exit 1
}

if ($Dev) {
    # Dev mode: the Rust binary reads the backend EXE directly from backend\dist\run_server\
    # (see the #[cfg(debug_assertions)] path in main.rs) so no resource copy is needed.
    Write-Host ""
    Write-Host "=== Launching dev session ===" -ForegroundColor Cyan
    Set-Location chess-ai
    npm run tauri dev
    Set-Location ..
    Write-Host "Dev session ended." -ForegroundColor Green
} else {
    # Production: copy the entire run_server\ directory into Tauri resources so the
    # installer bundles all PyInstaller DLLs alongside the EXE.
    Write-Host "Preparing Tauri resources..."
    New-Item -ItemType Directory -Force -Path chess-ai\src-tauri\resources | Out-Null
    Remove-Item -Recurse -Force chess-ai\src-tauri\resources\run_server -ErrorAction SilentlyContinue
    Copy-Item "backend\dist\run_server" chess-ai\src-tauri\resources\run_server -Recurse -Force

    Write-Host "Building Tauri app..."
    Set-Location chess-ai
    npm run tauri build
    if ($LASTEXITCODE -ne 0) { Write-Error "Tauri build failed"; Set-Location ..; exit 1 }
    Set-Location ..

    $msi = Get-ChildItem "chess-ai\src-tauri\target\release\bundle\msi\*.msi" -ErrorAction SilentlyContinue | Select-Object -First 1

    if (-not $msi) {
        Write-Error "Build appeared to succeed but no installer file was produced!"
        exit 1
    }

    # Copy installer to the project root so it's easy to find
    Copy-Item $msi.FullName "$root\$($msi.Name)" -Force
    Write-Host "MSI copied to: $root\$($msi.Name)" -ForegroundColor Green

    Write-Host ""
    Write-Host "=== BUILD COMPLETE ===" -ForegroundColor Green
    Write-Host "Installer: $root\$($msi.Name)" -ForegroundColor Cyan
}
