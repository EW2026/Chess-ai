"""
Integration test: verifies the backend exits within a few seconds when its
--parent-pid process dies (the ctypes WaitForSingleObject watchdog in run_server.py).

Tests the Python-level watchdog without needing the Tauri/Rust layer.

Run from the project root:
    python backend/test_shutdown.py

Requirements: requests and psutil must be installed in the active venv.
The backend will bind to PORT 18765 to avoid colliding with a running dev server.
Startup can take up to 30 seconds on a cold machine (migrations, model load).
"""

import os
import subprocess
import sys
import time

import psutil
import requests

BACKEND_DIR      = os.path.dirname(os.path.abspath(__file__))
RUN_SERVER       = os.path.join(BACKEND_DIR, 'run_server.py')
PORT             = 18765
HEALTH_URL       = f'http://127.0.0.1:{PORT}/api/health/'
STARTUP_TIMEOUT  = 30   # seconds to wait for /api/health/ to respond
SHUTDOWN_TIMEOUT = 5    # seconds the watchdog should react within after parent dies


def wait_for_health(timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(HEALTH_URL, timeout=1).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def all_backend_pids(root_pid):
    """Return the root PID plus every descendant PID (the full process tree)."""
    try:
        proc = psutil.Process(root_pid)
        return {root_pid} | {c.pid for c in proc.children(recursive=True)}
    except psutil.NoSuchProcess:
        return set()


def run():
    print('── backend shutdown test ──────────────────────────────────────────')

    # ── 1. Spawn a dummy "Tauri" parent ───────────────────────────────────────
    print('Spawning fake parent process...')
    fake_parent = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(120)'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f'  fake parent PID: {fake_parent.pid}')

    # ── 2. Start backend watching that parent ─────────────────────────────────
    env = {
        **os.environ,
        'DJANGO_SETTINGS_MODULE': 'chess_project.settings',
        'PORT': str(PORT),
    }
    print(f'Starting backend (port {PORT})...')
    backend = subprocess.Popen(
        [sys.executable, RUN_SERVER, '--parent-pid', str(fake_parent.pid)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    backend_pid = backend.pid
    print(f'  backend PID: {backend_pid}')

    # ── 3. Wait for the backend to become healthy ─────────────────────────────
    print(f'Waiting for {HEALTH_URL} (up to {STARTUP_TIMEOUT}s)...')
    if not wait_for_health(STARTUP_TIMEOUT):
        fake_parent.kill()
        backend.kill()
        print('FAIL — backend never became healthy within the timeout')
        return False
    print('  backend is healthy')

    # Snapshot the full process tree before killing the parent
    tree_before = all_backend_pids(backend_pid)
    print(f'  backend process tree: {sorted(tree_before)}')

    # ── 4. Kill the fake parent ───────────────────────────────────────────────
    print('Killing fake parent...')
    fake_parent.kill()
    fake_parent.wait()
    print(f'  parent {fake_parent.pid} is gone')

    # ── 5. Wait for the backend to exit ──────────────────────────────────────
    print(f'Waiting up to {SHUTDOWN_TIMEOUT}s for backend to exit...')
    deadline = time.time() + SHUTDOWN_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.5)
        if backend.poll() is not None:
            elapsed = SHUTDOWN_TIMEOUT - max(0.0, deadline - time.time())
            print(f'  root process exited (code {backend.returncode}) after ~{elapsed:.1f}s')
            break
    else:
        backend.kill()
        print(f'FAIL — backend root process still running {SHUTDOWN_TIMEOUT}s after parent died')
        return False

    # ── 6. Confirm no worker children are lingering ───────────────────────────
    lingering = {pid for pid in tree_before if psutil.pid_exists(pid)}
    if lingering:
        for pid in lingering:
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass
        print(f'FAIL — {len(lingering)} worker process(es) still alive after root exited: {lingering}')
        return False

    print('  no lingering worker processes')
    print('PASS')
    return True


if __name__ == '__main__':
    sys.exit(0 if run() else 1)
