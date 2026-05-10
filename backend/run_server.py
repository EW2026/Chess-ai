import multiprocessing
import os
import sys
import io
import threading
import ctypes
import subprocess
import time

import django
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from waitress import serve


def _warmup_pool():
    """Start the process pool in the background so the first minimax move isn't slow.
    Spawning worker processes takes 1-3 seconds because each one imports Python,
    torch, and the chess engine. We do this at startup so that cost is paid before
    the player makes their first move.

    The delay before starting the warmup is scaled to the machine's hardware:
    faster machines start Waitress and initialize Django quicker, so they need
    less of a buffer before we add the extra load of spawning worker processes."""
    def _run():
        # Lazy: api.hardware and api.minimax are only needed by this background warmup
        # thread. Keeping them here avoids adding heavyweight api imports to run_server's
        # own top-level import chain (which is otherwise stdlib + waitress + django only).
        from api.hardware import HW

        # Base delay by RAM tier — more RAM means faster SQLite and model loading
        if HW.get("has_gpu"):
            delay = 0.5    # GPU systems are typically high-spec across the board
        elif HW.get("ram_gb", 4) >= 16:
            delay = 1.0
        elif HW.get("ram_gb", 4) >= 8:
            delay = 2.0
        elif HW.get("ram_gb", 4) >= 4:
            delay = 3.0
        else:
            delay = 4.0

        # Each extra worker beyond 2 needs time to spawn and import torch,
        # so add half a second per additional worker, capped at 4 seconds total
        delay = min(4.0, delay + max(0, HW.get("workers", 1) - 2) * 0.5)

        time.sleep(delay)
        try:
            # Lazy: warmup-only — same reason as api.hardware above.
            import chess
            from api.minimax import get_ranked_moves
            get_ranked_moves(chess.Board(), 3)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


if __name__ == '__main__':
    # freeze_support() MUST be the very first thing inside __main__ (Python docs requirement).
    # When PyInstaller bundles this into run_server.exe, worker processes re-run the same exe.
    # freeze_support() detects this, executes the worker's task, and calls sys.exit() — the
    # rest of the startup code below never runs in a worker process.
    multiprocessing.freeze_support()

    # Reconfigure stdout/stderr FIRST — before any prints.
    # When Tauri spawns this exe with Stdio::piped(), fd 1/2 are valid pipe handles
    # but Python's default TextIOWrapper may not be wired to them yet (PyInstaller
    # can leave sys.stdout in an indeterminate state without a real console).
    # Reconfiguring here ensures every subsequent print goes into the pipe.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    else:
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Parse --parent-pid <PID> passed by the Tauri launcher.
    # A daemon watchdog thread exits this process the moment the parent is gone,
    # covering every exit scenario: normal close, crash, or force-kill.
    _parent_pid = None
    for _i, _a in enumerate(sys.argv):
        if _a == '--parent-pid' and _i + 1 < len(sys.argv):
            try:
                _parent_pid = int(sys.argv[_i + 1])
            except ValueError:
                pass

    if _parent_pid is not None:
        def _watch_parent(pid):
            # Open a synchronization handle on the Tauri process.
            # WaitForSingleObject blocks (no polling) until the process exits.
            # ctypes is a Python built-in — always present in any PyInstaller bundle.
            try:
                handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                if not handle:
                    raise OSError('OpenProcess returned null')
                ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                # Fallback: poll os.kill every second (pure stdlib, no external deps)
                while True:
                    time.sleep(1)
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break

            print("[backend] Parent process gone — terminating backend", flush=True)

            # Kill the ENTIRE process tree so worker children also die.
            # os._exit(0) alone only kills this process; workers would become orphans.
            try:
                subprocess.Popen(
                    ['taskkill', '/F', '/T', '/PID', str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
            except Exception:
                pass
            os._exit(0)

        t = threading.Thread(target=_watch_parent, args=(_parent_pid,), daemon=True)
        t.start()
        print(f"[backend] Watchdog armed — blocking on parent PID {_parent_pid}", flush=True)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chess_project.settings")

    django.setup()

    # Run migrations automatically on every startup (idempotent — safe to run repeatedly).
    # This ensures the database schema is always up to date without a manual step.
    call_command('migrate', '--run-syncdb', verbosity=0)

    # Lazy: Django ORM models require django.setup() (called above) before they can be
    # accessed. Moving these to the top of the file would fail because DJANGO_SETTINGS_MODULE
    # isn't configured and setup() hasn't run when the module is first imported.
    from django.contrib.auth.models import User
    from rest_framework.authtoken.models import Token

    # Create the local auth user and token if they don't exist yet.
    # get_or_create is intentional: never replace the token so a frontend that already
    # fetched it doesn't get invalidated on the next server restart.
    local_user, _ = User.objects.get_or_create(username='local')
    Token.objects.get_or_create(user=local_user)

    application = get_wsgi_application()

    port = int(os.environ.get("PORT", 8000))
    print(f"[backend] Server starting on http://127.0.0.1:{port} (watching parent PID {_parent_pid})", flush=True)

    # Start warming the process pool while Waitress begins accepting connections
    _warmup_pool()

    # Waitress is a production-grade WSGI server. We bind only to 127.0.0.1 (localhost)
    # so the backend is completely inaccessible from the network — only the Tauri app can reach it.
    serve(application, host="127.0.0.1", port=port)
