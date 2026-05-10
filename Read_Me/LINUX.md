# Chess AI — Linux Setup Guide

Tested on Ubuntu 22.04 / 24.04. Debian and Linux Mint work with the same steps.
For Fedora/Arch, package names differ — see the notes at the bottom.

---

## 1. System Dependencies

```bash
sudo apt update
sudo apt install -y \
    libwebkit2gtk-4.1-dev \
    libgtk-3-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    patchelf \
    build-essential \
    pkg-config \
    curl \
    git
```

---

## 2. Rust

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

Verify: `rustc --version`

---

## 3. Node.js (v20 or newer)

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

Verify: `node --version` and `npm --version`

---

## 4. Python 3.11 or newer

Most modern Ubuntu/Debian releases ship with a suitable Python 3. Check with:

```bash
python3 --version
```

If it is older than 3.11:

```bash
sudo apt install -y python3.12 python3.12-venv
```

---

## 5. GPU Setup

### NVIDIA
Install the proprietary NVIDIA driver (470+ recommended) through
**Software & Updates → Additional Drivers**, or:

```bash
sudo apt install -y nvidia-driver-535
```

The build script detects your GPU via `nvidia-smi` and automatically installs
the CUDA 12.4 PyTorch wheel. No manual CUDA toolkit installation is needed —
PyTorch bundles everything it requires.

### AMD (ROCm)
ROCm is AMD's open-source compute stack and works natively on Linux.
Follow AMD's official guide for your distro:
https://rocm.docs.amd.com/en/latest/deploy/linux/index.html

Supported GPUs: RX 6000 series and newer (RDNA2+).

Once ROCm is installed, the build script detects it via `/opt/rocm` or
`rocm-smi` and installs the ROCm PyTorch wheel automatically.

### No discrete GPU
Nothing extra needed. The build script falls back to the CPU PyTorch wheel.
Training will be slower but the app works fully.

---

## 6. Stockfish Binary

Download the Linux Stockfish binary from:
https://stockfishchess.org/download/

Get the **Linux x86-64 AVX2** build. Place it in the project's `stockfish/`
directory with this exact name:

```
stockfish/stockfish-linux-x86-64-avx2
```

Make it executable:

```bash
chmod +x stockfish/stockfish-linux-x86-64-avx2
```

---

## 7. Build

```bash
chmod +x rebuild.sh
./rebuild.sh
```

The script will:
1. Create a Python virtual environment (`chessai/`)
2. Install Python dependencies
3. Detect your GPU and install the correct PyTorch build
4. Build the React frontend
5. Bundle the Django backend with PyInstaller
6. Build the Tauri desktop app
7. Produce a `.deb` and/or `.AppImage` installer in the project root

A full first build takes 5–15 minutes depending on your internet speed and
whether the Rust toolchain needs to compile Tauri from scratch.
Subsequent builds are faster because Rust and pip caches are reused.

### Dev mode (no installer)

```bash
./rebuild.sh --dev
```

Launches the app directly without building an installer. Useful for testing
changes quickly. Close the window to end the session.

### Force Rust recompile

```bash
./rebuild.sh --clean-rust
```

Only needed after changes to `main.rs` or `Cargo.toml`.

---

## 8. Install

### .deb package (recommended)

```bash
sudo dpkg -i ChessAI_*.deb
```

The app appears in your application launcher as **Chess AI**.
Uninstall with: `sudo apt remove chess-ai`

### AppImage (no install required)

```bash
chmod +x ChessAI_*.AppImage
./ChessAI_*.AppImage
```

---

## 9. App Data Location

Unlike Windows (which uses `%LOCALAPPDATA%`), on Linux the app stores its
data in your home directory:

```
~/chess-ai/
    db.sqlite3          ← game history, learned positions
    chess_model.pth     ← trained neural network weights
    hardware_config.json← detected hardware settings
```

To reset everything (force full re-detection and start fresh):

```bash
rm -rf ~/chess-ai/
```

---

## 10. Troubleshooting

**`libwebkit2gtk-4.1-dev: Package not found`**
On Ubuntu 22.04, the package may be `libwebkit2gtk-4.0-dev`. Try:
```bash
sudo apt install -y libwebkit2gtk-4.0-dev
```
Then update `chess-ai/src-tauri/Cargo.toml` if Tauri reports a WebKit version mismatch.

**`nvidia-smi` not found after driver install**
Reboot first. NVIDIA drivers require a restart before `nvidia-smi` is available.

**PyInstaller build fails with `ModuleNotFoundError`**
The virtual environment may be incomplete. Delete it and rebuild:
```bash
rm -rf chessai/
./rebuild.sh
```

**App launches but shows a blank screen**
The backend (Django server) takes 2–3 seconds to start. If the screen stays blank
beyond that, check if the backend process is running:
```bash
ps aux | grep run_server
```
If it is not running, the backend likely crashed on startup. Run `./rebuild.sh --dev`
to see the backend output directly in the terminal.

**`CUDA not available` despite NVIDIA driver being installed**
The PyTorch CUDA wheel requires glibc 2.17+. Run `ldd --version` to check.
All Ubuntu 22.04+ installations satisfy this requirement.

---

## Fedora / RHEL / Arch Notes

The system dependency package names differ:

| Ubuntu / Debian | Fedora / RHEL | Arch |
|---|---|---|
| `libwebkit2gtk-4.1-dev` | `webkit2gtk4.1-devel` | `webkit2gtk-4.1` |
| `libgtk-3-dev` | `gtk3-devel` | `gtk3` |
| `librsvg2-dev` | `librsvg2-devel` | `librsvg` |
| `patchelf` | `patchelf` | `patchelf` |
| `build-essential` | `gcc gcc-c++ make` | `base-devel` |

Everything else (Rust, Node.js, PyTorch, Tauri) is the same across distros.
