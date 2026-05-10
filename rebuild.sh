#!/usr/bin/env bash
# rebuild.sh — Linux equivalent of rebuild.ps1
# Usage:
#   ./rebuild.sh              # full build → produces .deb and .AppImage installers
#   ./rebuild.sh --dev        # launch dev session (tauri dev, no installer)
#   ./rebuild.sh --clean-rust # also wipe the Rust target dir (full recompile)
#
# Prerequisites (one-time setup):
#   sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev \
#                    librsvg2-dev patchelf curl build-essential pkg-config
#   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
#   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
#   sudo apt install -y nodejs
#   Place stockfish-linux-x86-64-avx2 in the stockfish/ directory (chmod +x it).

set -euo pipefail

DEV=false
CLEAN_RUST=false
for arg in "$@"; do
    case $arg in
        --dev)        DEV=true ;;
        --clean-rust) CLEAN_RUST=true ;;
    esac
done

if [ "$DEV" = true ]; then
    echo "=== Chess AI Dev Build ==="
else
    echo "=== Chess AI Full Rebuild ==="
fi

# --- KILL RUNNING PROCESSES ---
echo "Stopping running processes..."
pkill -f run_server 2>/dev/null || true
pkill -f ChessAI    2>/dev/null || true
sleep 1

# --- WIPE APP DATA ---
echo "Wiping app data..."
APP_DATA="$HOME/chess-ai"
rm -f "$APP_DATA/db.sqlite3"
rm -f "$APP_DATA/chess_model.pth"
rm -f "$APP_DATA/hardware_config.json"

# --- CLEAN FRONTEND ---
echo "Cleaning frontend..."
rm -rf chess-ai/dist
rm -rf chess-ai/node_modules
rm -f  chess-ai/package-lock.json

# --- CLEAN BACKEND BUILDS ---
echo "Cleaning backend builds..."
rm -rf backend/build
rm -rf backend/dist
rm -rf backend/static/*

# --- CLEAN PYTHON CACHE ---
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# --- CLEAN TAURI BUILD ---
if [ "$CLEAN_RUST" = true ]; then
    echo "Wiping Rust target (full recompile)..."
    rm -rf chess-ai/src-tauri/target
fi

# --- VIRTUAL ENVIRONMENT ---
echo "Checking virtual environment..."
VENV_PYTHON="./chessai/bin/python"
VENV_PYINSTALLER="./chessai/bin/pyinstaller"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Creating virtual environment..."
    python3 -m venv chessai
    sleep 1
fi

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv creation failed." >&2
    exit 1
fi

# --- INSTALL PYTHON DEPENDENCIES ---
echo "Installing Python dependencies..."
$VENV_PYTHON -m pip install --upgrade pip --quiet
$VENV_PYTHON -m pip install -r backend/requirements.txt --quiet

# Detect GPU and install the correct PyTorch build.
# NVIDIA: CUDA 12.4 wheel (torch.cuda.is_available() = True)
# AMD:    ROCm 6.1 wheel  (HIP masquerades as CUDA — same code path as NVIDIA)
# None:   CPU wheel
CURRENT_TORCH=$($VENV_PYTHON -c "import torch; print(torch.__version__)" 2>/dev/null || echo "")
IS_CUDA_BUILD=$(echo "$CURRENT_TORCH" | grep -c "+cu"   || true)
IS_ROCM_BUILD=$(echo "$CURRENT_TORCH" | grep -c "+rocm" || true)
IS_CPU_BUILD=$( echo "$CURRENT_TORCH" | grep -c "+cpu"  || true)

NVIDIA_GPU=false
AMD_GPU=false
GPU_NAME=""

if command -v nvidia-smi &>/dev/null && nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null 2>&1; then
    NVIDIA_GPU=true
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
elif [ -d "/opt/rocm" ] || command -v rocm-smi &>/dev/null; then
    AMD_GPU=true
    GPU_NAME=$(rocm-smi --showproductname 2>/dev/null | grep -oP '(?<=Card series:\s{0,20})\S.*' | head -1 || echo "AMD GPU")
fi

if [ "$NVIDIA_GPU" = true ]; then
    if [ "$IS_CUDA_BUILD" -eq 0 ]; then
        echo "NVIDIA GPU detected ($GPU_NAME) - switching to CUDA 12.4 PyTorch (was: $CURRENT_TORCH)..."
        $VENV_PYTHON -m pip install torch --index-url https://download.pytorch.org/whl/cu124 --force-reinstall
    else
        echo "CUDA PyTorch already installed ($CURRENT_TORCH) - skipping."
    fi
elif [ "$AMD_GPU" = true ]; then
    if [ "$IS_ROCM_BUILD" -eq 0 ]; then
        echo "AMD GPU detected ($GPU_NAME) - switching to ROCm PyTorch (was: $CURRENT_TORCH)..."
        $VENV_PYTHON -m pip install torch --index-url https://download.pytorch.org/whl/rocm6.2 --force-reinstall
    else
        echo "ROCm PyTorch already installed ($CURRENT_TORCH) - skipping."
    fi
else
    if [ "$IS_CPU_BUILD" -eq 0 ]; then
        echo "No discrete GPU detected - switching to CPU PyTorch (was: $CURRENT_TORCH)..."
        $VENV_PYTHON -m pip install torch --index-url https://download.pytorch.org/whl/cpu --force-reinstall
    else
        echo "CPU PyTorch already installed ($CURRENT_TORCH) - skipping."
    fi
fi

$VENV_PYTHON -m pip install pyinstaller --quiet

# --- VERIFY STOCKFISH ---
SF_PATH="stockfish/stockfish-linux-x86-64-avx2"
if [ ! -f "$SF_PATH" ]; then
    echo "ERROR: Linux Stockfish binary not found at $SF_PATH" >&2
    echo "Download it from https://stockfishchess.org/download/ and place it there." >&2
    exit 1
fi
chmod +x "$SF_PATH"

# --- BUILD FRONTEND ---
echo "Building frontend..."
cd chess-ai
npm install
npm run build
cd ..

# --- COPY FRONTEND INTO DJANGO ---
echo "Copying frontend to Django..."
mkdir -p backend/templates backend/static
cp chess-ai/dist/index.html backend/templates/index.html
cp -r chess-ai/dist/assets  backend/static/assets
cp -r chess-ai/dist/pieces  backend/static/pieces 2>/dev/null || true

# --- BUILD BACKEND EXE ---
echo "Building backend EXE..."
cd backend
$VENV_PYINSTALLER run_server.spec --clean -y
if [ $? -ne 0 ]; then
    echo "ERROR: PyInstaller build failed" >&2
    cd ..
    exit 1
fi
cd ..

# --- VERIFY EXE ---
EXE_PATH="backend/dist/run_server/run_server"
if [ ! -f "$EXE_PATH" ]; then
    echo "ERROR: Backend executable was not created at $EXE_PATH" >&2
    exit 1
fi
chmod +x "$EXE_PATH"

# --- ENSURE STOCKFISH IS IN DIST ---
SF_INTERNAL="backend/dist/run_server/_internal/stockfish-linux-x86-64-avx2"
SF_ROOT="backend/dist/run_server/stockfish-linux-x86-64-avx2"
if [ ! -f "$SF_INTERNAL" ] && [ ! -f "$SF_ROOT" ]; then
    echo "Stockfish not bundled by spec - copying manually..."
    mkdir -p "backend/dist/run_server/_internal"
    cp "$SF_PATH" "backend/dist/run_server/_internal/"
    chmod +x "backend/dist/run_server/_internal/stockfish-linux-x86-64-avx2"
fi

# --- STRIP UNUSED CUDA .so FILES ---
# Only strip cuDNN sub-library plugins. libcudnn_ops.so loads these via dlopen()
# at runtime — they are NOT in any .so NEEDED list, so removing them will not
# cause a load-time error. All other CUDA .so files (libcufft, libcusparse,
# libcurand, libcusolver, etc.) ARE directly imported by libtorch_cuda.so and
# must stay in the bundle or torch fails to import entirely.
TORCH_LIB="backend/dist/run_server/_internal/torch/lib"
if [ -d "$TORCH_LIB" ]; then
    echo "Stripping unused CUDA libraries..."
    REMOVED=0
    for pattern in \
        "libcudnn_engines_precompiled*" \
        "libcudnn_adv*" \
        "libcudnn_heuristic*" \
        "libcudnn_engines_runtime_compiled*" \
        "libcudnn_cnn*"; do
        for f in "$TORCH_LIB"/$pattern; do
            [ -f "$f" ] && rm -f "$f" && REMOVED=$((REMOVED + 1))
        done
    done
    echo "Stripped $REMOVED libraries from torch/lib."
fi

# --- VERIFY STATIC ASSETS ---
if [ -d "backend/dist/run_server/static/assets" ]; then
    echo "Static assets OK (static/assets)"
elif [ -d "backend/dist/run_server/_internal/static/assets" ]; then
    echo "Static assets OK (_internal/static/assets)"
else
    echo "ERROR: Static assets missing from EXE build!" >&2
    exit 1
fi

if [ "$DEV" = true ]; then
    echo ""
    echo "=== Launching dev session ==="
    cd chess-ai
    npm run tauri dev
    cd ..
    echo "Dev session ended."
else
    # --- PREPARE TAURI RESOURCES ---
    echo "Preparing Tauri resources..."
    mkdir -p chess-ai/src-tauri/resources
    rm -rf chess-ai/src-tauri/resources/run_server
    cp -r backend/dist/run_server chess-ai/src-tauri/resources/run_server

    # --- BUILD TAURI APP ---
    echo "Building Tauri app..."
    cd chess-ai
    # Override targets to Linux formats (ignores the msi target in tauri.conf.json)
    npm run tauri build -- --bundles deb,appimage
    if [ $? -ne 0 ]; then
        echo "ERROR: Tauri build failed" >&2
        cd ..
        exit 1
    fi
    cd ..

    DEB=$(find "chess-ai/src-tauri/target/release/bundle/deb"      -name "*.deb"      2>/dev/null | head -1)
    APPIMAGE=$(find "chess-ai/src-tauri/target/release/bundle/appimage" -name "*.AppImage" 2>/dev/null | head -1)

    if [ -z "$DEB" ] && [ -z "$APPIMAGE" ]; then
        echo "ERROR: Build appeared to succeed but no installer files were produced!" >&2
        exit 1
    fi

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [ -n "$DEB"      ] && cp "$DEB"      "$SCRIPT_DIR/" && echo "DEB copied to:     $SCRIPT_DIR/$(basename $DEB)"
    [ -n "$APPIMAGE" ] && cp "$APPIMAGE" "$SCRIPT_DIR/" && echo "AppImage copied to: $SCRIPT_DIR/$(basename $APPIMAGE)"

    echo ""
    echo "=== BUILD COMPLETE ==="
    [ -n "$DEB"      ] && echo "DEB:      $DEB"
    [ -n "$APPIMAGE" ] && echo "AppImage: $APPIMAGE"
fi
