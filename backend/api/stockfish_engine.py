import chess
import subprocess
import threading
import random
import sys
import os
import time
import queue as _queue

from .neural_net import hash_board_str

# =========================
# OPENING BOOK
# =========================
# Stockfish movetime budget per difficulty (milliseconds).
# None = no Stockfish (easy mode uses random moves instead).
_OPENING_MOVETIME = {
    "easy":   None,
    "medium": 50,
    "hard":   150,
    "expert": 300,
}

# =========================
# STOCKFISH PATH
# =========================
def _get_stockfish_path():
    if sys.platform == 'win32':
        exe_name = "stockfish-windows-x86-64-avx2.exe"
    else:
        exe_name = "stockfish-linux-x86-64-avx2"

    if getattr(sys, 'frozen', False):
        # PyInstaller 6+ one-dir mode: bundled binaries land in _internal/,
        # which sys._MEIPASS points to. sys.executable is the loader stub in
        # the parent directory and does NOT have the bundled files next to it.
        return os.path.join(sys._MEIPASS, exe_name)
    # Dev mode: Stockfish lives in <project_root>/stockfish/
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, '..', '..', 'stockfish', exe_name))

STOCKFISH_PATH = _get_stockfish_path()

# =========================
# STOCKFISH UCI INTERFACE
# =========================
def get_stockfish_move(board, movetime=None):
    """Launch Stockfish as a subprocess and communicate via UCI protocol.
    UCI (Universal Chess Interface) is a standard text-based protocol used by
    all major chess engines. We send the board position, ask for the best move,
    read the output, then kill the process.

    A background reader thread feeds stdout into a queue so readline() never
    blocks the caller indefinitely — if Stockfish crashes or emits unexpected
    output the queue.get() times out and we return None instead of hanging."""
    process = subprocess.Popen(
        STOCKFISH_PATH,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        universal_newlines=True
    )

    def send(cmd):
        process.stdin.write(cmd + "\n")
        process.stdin.flush()

    send("uci")
    send(f"position fen {board.fen()}")
    send(f"go movetime {movetime}" if movetime else "go depth 15")

    # movetime is in milliseconds; convert to seconds and add a 2-second buffer
    # for UCI handshake overhead. "go depth 15" with no time limit gets 17 seconds.
    timeout = (movetime / 1000.0 + 2.0) if movetime else 17.0

    line_queue = _queue.Queue()

    def _reader():
        try:
            for line in process.stdout:
                line_queue.put(line)
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()

    best_move = None
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            line = line_queue.get(timeout=remaining).strip()
            if line.startswith("bestmove"):
                parts = line.split()
                if len(parts) >= 2 and parts[1] != "(none)":
                    try:
                        best_move = chess.Move.from_uci(parts[1])
                    except Exception:
                        pass
                break
        except _queue.Empty:
            break

    try:
        process.terminate()
        process.wait(timeout=1)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass

    return best_move

def get_stockfish_top_moves(board, n=5, movetime=1000):
    """Return top-N moves for the current position via Stockfish MultiPV.

    Returns a list of (centipawns, chess.Move) ordered best-first from the
    side-to-move's perspective. Returns [] if Stockfish is unavailable.
    Mate scores are mapped to ±10000 centipawns.
    """
    if not os.path.exists(STOCKFISH_PATH):
        return []

    process = subprocess.Popen(
        STOCKFISH_PATH,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        universal_newlines=True
    )

    def send(cmd):
        process.stdin.write(cmd + "\n")
        process.stdin.flush()

    send("uci")
    send(f"setoption name MultiPV value {n}")
    send(f"position fen {board.fen()}")
    send(f"go movetime {movetime}")

    timeout = movetime / 1000.0 + 3.0
    line_queue = _queue.Queue()

    def _reader():
        try:
            for line in process.stdout:
                line_queue.put(line)
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()

    # {multipv_idx: (score_cp, first_move_uci, pv_uci_list)} — last update wins (deepest depth)
    results = {}
    deadline = time.time() + timeout

    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            line = line_queue.get(timeout=remaining).strip()
            if line.startswith("bestmove"):
                break
            if not line.startswith("info") or "multipv" not in line or "pv" not in line:
                continue
            parts = line.split()
            score_cp = None
            try:
                if "cp" in parts:
                    score_cp = int(parts[parts.index("cp") + 1])
                elif "mate" in parts:
                    mate_val = int(parts[parts.index("mate") + 1])
                    score_cp = 10000 if mate_val > 0 else -10000
            except (ValueError, IndexError):
                pass
            if score_cp is None:
                continue
            try:
                mpv_idx  = int(parts[parts.index("multipv") + 1])
                pv_start = parts.index("pv") + 1
                pv_ucis  = parts[pv_start:]          # full principal variation
                if pv_ucis:
                    results[mpv_idx] = (score_cp, pv_ucis[0], pv_ucis)
            except (ValueError, IndexError):
                pass
        except _queue.Empty:
            break

    try:
        process.terminate()
        process.wait(timeout=1)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass

    output = []
    for mpv_idx in sorted(results.keys()):
        score_cp, move_uci, pv_ucis = results[mpv_idx]
        try:
            output.append((score_cp, chess.Move.from_uci(move_uci), pv_ucis))
        except Exception:
            pass
    return output


def get_opening_move(board, player, difficulty="hard"):
    """Return candidate opening moves for the first 6 full moves.

    Priority order:
    1. Player-specific learned openings from OpeningMemory (moves the AI saw win before)
    2. Stockfish at the difficulty's movetime budget
    3. Random subset of legal moves (easy mode only)

    Returns a list of (score, move) pairs so the caller can apply aggression
    weighting, or None to fall through to a full minimax search.
    """
    if board.fullmove_number > 6 or player is None:
        return None

    # Lazy: stockfish_engine is imported by minimax.py, which worker processes also load;
    # those workers never call django.setup(), so a top-level model import would crash
    # in every spawned worker process.
    from .models import OpeningMemory
    h = hash_board_str(board)

    # Check if we've seen this exact position before and have a reliable winning move
    entries = list(OpeningMemory.objects.filter(player=player, hash=h))
    if entries:
        best = max(entries, key=lambda e: e.win_rate())
        # Only trust a learned move if it has been played at least 3 times and won >40%
        if best.times_used >= 3 and best.win_rate() > 0.4:
            try:
                move = chess.Move.from_uci(best.move)
                return [(999, move)]
            except Exception:
                pass

    movetime = _OPENING_MOVETIME.get(difficulty)

    if movetime is None:
        # Easy mode: return a random half of legal moves so the AI isn't always predictable
        moves = list(board.legal_moves)
        random.shuffle(moves)
        candidates = moves[:max(1, len(moves) // 2)]
        return [(0, m) for m in candidates]

    try:
        sf_move = get_stockfish_move(board, movetime=movetime)
        if sf_move:
            return [(999, sf_move)]
    except Exception:
        pass

    return None
