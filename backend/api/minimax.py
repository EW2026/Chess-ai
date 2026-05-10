import chess
import random
import multiprocessing
import threading
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

from .hardware import HW
from .neural_net import hash_board, PIECE_VALUES
from .evaluation import evaluate
from . import killer_moves as _km
from .difficulty import TIME_MAP, BEST_MOVE_RATES, MAX_DEPTH, select_move_by_aggression, analyze_player_move
from .stockfish_engine import get_stockfish_move, get_opening_move
from .learning import log_issue, get_best_db_move, store_position, cache_computed_move, train_model

__all__ = [
    "get_ai_move", "reset_tt", "stop_ponder",
    "store_position", "train_model", "log_issue", "analyze_player_move",
    "get_ranked_moves",
]

# =========================
# TRANSPOSITION TABLE
# =========================
# The transposition table (TT) caches the result of previously evaluated positions.
# Key: Zobrist hash of the board. Value: (score, best_move, search_depth).
# When we reach a position we've already searched at an equal or greater depth,
# we can reuse the cached result instead of searching again — often saving enormous time.
TT = OrderedDict()
MAX_TT_SIZE  = HW["tt_size"]
_TT_EVICT_AT = int(MAX_TT_SIZE * 0.9)

# =========================
# PER-GAME STAT ACCUMULATORS
# =========================
# Populated during each game; read by learning.py at training time; reset by reset_tt().
_game_depths        = []   # completed search depth per AI move
_game_move_times_ms = []   # wall-clock ms per AI move
_game_db_hits       = 0    # moves served from PositionMemory (skip search)
_game_opening_moves = 0    # moves served from opening book
_game_tt_hits       = 0    # root position found in TT before searching
_game_tt_lookups    = 0    # total root-position TT checks
_game_best_moves    = 0    # times AI played its true best-ranked move
_game_total_moves   = 0    # total AI moves this game
_game_workers       = []   # active worker count per move (for avg/min)


def reset_tt():
    """Clear the transposition table and per-game stat accumulators for a new game."""
    global TT
    global _game_depths, _game_move_times_ms, _game_db_hits, _game_opening_moves
    global _game_tt_hits, _game_tt_lookups, _game_best_moves, _game_total_moves, _game_workers
    TT = OrderedDict()
    _game_depths        = []
    _game_move_times_ms = []
    _game_db_hits       = 0
    _game_opening_moves = 0
    _game_tt_hits       = 0
    _game_tt_lookups    = 0
    _game_best_moves    = 0
    _game_total_moves   = 0
    _game_workers       = []

def _tt_store(key, value):
    """Write to the TT with LRU eviction when it reaches 90% capacity."""
    global TT
    if len(TT) >= _TT_EVICT_AT:
        evict_count = len(TT) - int(MAX_TT_SIZE * 0.8)
        for _ in range(max(1, evict_count)):
            TT.popitem(last=False)
    TT[key] = value

# =========================
# MOVE ORDERING
# =========================
def order_moves(board, tt_move, killer1=None, killer2=None):
    """Sort moves so the best candidates are searched first.

    Alpha-beta pruning is most effective when it sees strong moves early — a move
    that causes a cutoff early prunes the most branches. Priority order:
      1000 — Transposition table move (best from a previous search of this exact position)
       100+ — Captures ranked by MVV-LVA (Most Valuable Victim - Least Valuable Attacker)
               e.g. pawn takes queen (score 149) beats queen takes pawn (score 105)
        50  — Moves that give check (often strong tactical moves)
        30  — Killer move slot 1 (caused a cutoff at this depth in a different branch)
        25  — Killer move slot 2
         0  — Quiet moves (last — least likely to cause cutoffs)
    """
    def priority(move):
        if move == tt_move:
            return 1000
        if board.is_capture(move):
            victim   = board.piece_at(move.to_square)
            attacker = board.piece_at(move.from_square)
            vv = PIECE_VALUES[victim.piece_type]   if victim   else PIECE_VALUES[chess.PAWN]
            av = PIECE_VALUES[attacker.piece_type] if attacker else 0
            return 100 + vv * 10 - av
        if board.gives_check(move):
            return 50
        if move == killer1:
            return 30
        if move == killer2:
            return 25
        return 0

    return sorted(board.legal_moves, key=priority, reverse=True)


# =========================
# MINIMAX WITH ALPHA-BETA PRUNING
# =========================
def minimax(board, depth, alpha, beta, maximizing, deadline=None, killer_table=None):
    """Recursive minimax search with alpha-beta pruning.

    Improvements over the basic version:
      - Killer move heuristic (item 1): try moves that caused cutoffs at this depth first.
      - Null move pruning (item 6): if we can skip our turn and the position is still
        winning for us, the opponent can't make it better — prune that branch.
      - Late move reductions (item 10): reduce search depth for moves that appear late
        in move ordering (likely bad moves). One of the best ROI search improvements.

    killer_table: a list of [killer1, killer2] per depth. None = killers disabled
                  (used in worker processes which have their own local table).
    """
    if deadline and time.time() >= deadline:
        raise TimeoutError

    key = hash_board(board)

    cached = TT.get(key)
    if cached is not None and cached[2] >= depth:
        return cached[0], cached[1]

    if board.is_game_over():
        if board.is_checkmate():
            val = -9000 if board.turn == chess.WHITE else 9000
        else:
            val = 0
        return val, None

    if depth == 0:
        val = evaluate(board)
        _tt_store(key, (val, None, 0))
        return val, None

    # ── Null move pruning (item 6) ─────────────────────────────────────────────
    # If we "skip" our turn (null move) and the resulting position is still good for
    # us (score >= beta), then the opponent certainly can't make it better with a real
    # move — so we can prune this branch entirely.
    #
    # Safe to apply only when:
    #   - Not in check (if we're in check, we MUST move — null move is illegal)
    #   - depth >= 3 (null move at shallow depth isn't worth the risk of zugzwang)
    #   - Not endgame-like (zugzwang is rare but exists; we approximate by checking
    #     that the side to move has pieces beyond king + pawns)
    #   - Not maximizing at the top of our window (avoid applying at the root)
    _R = 2   # null move reduction: reduce remaining depth by 2 after skipping a turn
    if (depth >= 3 and not board.is_check()):
        # Check that the side to move has at least one non-pawn, non-king piece
        # to reduce the risk of zugzwang (where skipping a turn is actually bad)
        side = board.turn
        has_pieces = any(
            board.pieces(pt, side)
            for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        )
        if has_pieces:
            board.push(chess.Move.null())
            try:
                null_score, _ = minimax(board, depth - 1 - _R, alpha, beta,
                                        not maximizing, deadline, killer_table)
            finally:
                board.pop()

            # If the null-move score is already >= beta (good enough to cut off),
            # we can skip searching real moves — they'll be at least this good.
            if maximizing and null_score >= beta:
                return beta, None
            if not maximizing and null_score <= alpha:
                return alpha, None

    # ── Fetch killer moves for this depth ─────────────────────────────────────
    k1 = k2 = None
    if killer_table is not None and 0 <= depth < len(killer_table):
        k1, k2 = killer_table[depth]

    tt_move   = cached[1] if cached else None
    best_move = None
    ordered   = order_moves(board, tt_move, k1, k2)

    if maximizing:
        max_eval = -9999
        for move_idx, move in enumerate(ordered):
            # ── Late move reductions (item 10) ─────────────────────────────────
            # Moves that appear late in the ordered list are likely bad. Rather than
            # searching them at full depth, reduce their search depth by 1-2 levels.
            # Only reduce quiet (non-capture, non-check) moves after the first 3.
            # Never reduce when we're in check, at low depth, or at the root (depth 1).
            reduce = 0
            if (move_idx >= 3 and depth >= 3 and not board.is_check()
                    and not board.is_capture(move) and not board.gives_check(move)):
                reduce = 1 if move_idx < 6 else 2   # reduce more for very late moves

            board.push(move)
            try:
                eval_score, _ = minimax(board, depth - 1 - reduce, alpha, beta,
                                        False, deadline, killer_table)
                # If a reduced search produced a surprisingly good result, re-search
                # at full depth to confirm it's actually good (not a search artifact)
                if reduce > 0 and eval_score > alpha:
                    eval_score, _ = minimax(board, depth - 1, alpha, beta,
                                            False, deadline, killer_table)
            finally:
                board.pop()

            if eval_score > max_eval:
                max_eval  = eval_score
                best_move = move

            alpha = max(alpha, eval_score)
            if beta <= alpha:
                # Beta cutoff — store as a killer move if it's a quiet move
                if (killer_table is not None and not board.is_capture(move)
                        and 0 <= depth < len(killer_table)):
                    _km.store(depth, move)
                    killer_table[depth] = _km.get(depth)
                break

        _tt_store(key, (max_eval, best_move, depth))
        return max_eval, best_move

    else:
        min_eval = 9999
        for move_idx, move in enumerate(ordered):
            reduce = 0
            if (move_idx >= 3 and depth >= 3 and not board.is_check()
                    and not board.is_capture(move) and not board.gives_check(move)):
                reduce = 1 if move_idx < 6 else 2

            board.push(move)
            try:
                eval_score, _ = minimax(board, depth - 1 - reduce, alpha, beta,
                                        True, deadline, killer_table)
                if reduce > 0 and eval_score < beta:
                    eval_score, _ = minimax(board, depth - 1, alpha, beta,
                                            True, deadline, killer_table)
            finally:
                board.pop()

            if eval_score < min_eval:
                min_eval  = eval_score
                best_move = move

            beta = min(beta, eval_score)
            if beta <= alpha:
                if (killer_table is not None and not board.is_capture(move)
                        and 0 <= depth < len(killer_table)):
                    _km.store(depth, move)
                    killer_table[depth] = _km.get(depth)
                break

        _tt_store(key, (min_eval, best_move, depth))
        return min_eval, best_move


# =========================
# PARALLEL SEARCH POOL
# =========================
_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        ctx   = multiprocessing.get_context("spawn")
        _pool = ProcessPoolExecutor(
            max_workers=HW.get("pool_max_workers", HW["workers"]),
            mp_context=ctx,
        )
        # Best-effort initial affinity pin (workers may not be spawned yet;
        # set_pool is called again after warmup when workers are confirmed live)
        # Lazy: worker processes also import minimax.py; a top-level thermal_monitor
        # import would start its monitoring threads inside every worker, which is wrong.
        from . import thermal_monitor
        thermal_monitor.set_pool(_pool)
    return _pool

def _restart_pool():
    """Shut down the current pool so workers reload fresh model weights on next use."""
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False)
        _pool = None


# =========================
# PONDERING  (multi-line, item 15)
# =========================
# Pondering: while the player is thinking, the AI searches the positions it expects
# the player might produce. If the player plays one of the predicted moves, the AI
# has a head start. If not, the TT is still warmer from all that extra search.
#
# Multi-line pondering: instead of only predicting the single best player reply,
# we ponder the top 3 predicted replies in parallel threads. This makes pondering
# useful even when the player plays unexpectedly.
_ponder_stop    = threading.Event()
_ponder_threads = []


def _ponder_worker(fen, time_budget):
    """Background thread: search one expected position to warm the TT."""
    board    = chess.Board(fen)
    deadline = time.time() + time_budget
    for depth in range(1, 15):
        if _ponder_stop.is_set() or time.time() >= deadline:
            break
        try:
            get_ranked_moves(board, depth, deadline=deadline)
        except Exception:
            break


def start_ponder(fens, time_budget=30.0):
    """Start background ponder searches for a list of expected positions.

    Each FEN gets its own thread up to a max of 3 concurrent ponders.
    All threads share the same TT, so their results accumulate.
    """
    global _ponder_threads
    stop_ponder()
    _ponder_stop.clear()

    for fen in fens[:3]:   # max 3 simultaneous ponders
        t = threading.Thread(target=_ponder_worker, args=(fen, time_budget), daemon=True)
        t.start()
        _ponder_threads.append(t)


def stop_ponder():
    """Signal all ponder threads to stop and wait for them."""
    global _ponder_threads
    _ponder_stop.set()
    for t in _ponder_threads:
        if t.is_alive():
            t.join(timeout=0.5)
    _ponder_threads = []


# =========================
# PARALLEL WORKER FUNCTION
# =========================
def _search_chunk(fen, moves_uci, depth, deadline=None):
    """Worker function executed in a separate process.
    Each worker process has its own local killer table — they don't share state
    across processes, but that's fine since killers are just an ordering hint."""
    board      = chess.Board(fen)
    maximizing = board.turn == chess.WHITE
    # Local killer table for this worker (not shared with main process)
    local_killers = [[None, None] for _ in range(16)]
    results    = []

    for uci in moves_uci:
        if deadline and time.time() >= deadline:
            break
        move = chess.Move.from_uci(uci)
        board.push(move)
        try:
            score, _ = minimax(board, depth - 1, -9999, 9999, not maximizing,
                                deadline, local_killers)
            results.append((score, uci))
        except TimeoutError:
            board.pop()
            break
        board.pop()

    return results


# =========================
# RANKED MOVE SEARCH
# =========================
def get_ranked_moves(board, depth, deadline=None):
    """Evaluate all legal moves at the given depth and return them sorted best-first."""
    # Lazy: same reason as _get_pool() — worker processes import this module too;
    # thermal_monitor must only initialize in the main process.
    from . import thermal_monitor
    maximizing = board.turn == chess.WHITE
    moves      = list(board.legal_moves)

    killer_table = [_km.get(d) for d in range(16)]

    # Use the thermally-adjusted worker count — may be lower than pool_max_workers
    # if the CPU is throttling under sustained load.
    n_workers = thermal_monitor.get_active_workers()

    if n_workers <= 1 or len(moves) <= n_workers:
        scored = []
        for move in moves:
            if deadline and time.time() >= deadline:
                break
            board.push(move)
            timed_out = False
            try:
                score, _ = minimax(board, depth - 1, -9999, 9999, not maximizing,
                                   deadline, killer_table)
                scored.append((score, move))
            except TimeoutError:
                timed_out = True
            finally:
                board.pop()
            if timed_out:
                break
        scored.sort(key=lambda x: x[0], reverse=maximizing)
        return scored

    fen    = board.fen()
    chunks = [[m.uci() for m in moves[i::n_workers]] for i in range(n_workers)]
    futures = [_get_pool().submit(_search_chunk, fen, chunk, depth, deadline)
               for chunk in chunks if chunk]

    scored          = []
    collect_timeout = max(0.05, deadline - time.time()) if deadline else 60
    try:
        for f in as_completed(futures, timeout=collect_timeout):
            try:
                for score, uci in f.result():
                    scored.append((score, chess.Move.from_uci(uci)))
            except Exception:
                pass
    except FuturesTimeoutError:
        for f in futures:
            f.cancel()

    if not scored:
        _restart_pool()
        emergency_deadline = time.time() + 0.5
        for move in moves:
            board.push(move)
            try:
                score, _ = minimax(board, depth - 1, -9999, 9999, not maximizing,
                                   emergency_deadline, killer_table)
                scored.append((score, move))
            except TimeoutError:
                board.pop()
                break
            board.pop()

    scored.sort(key=lambda x: x[0], reverse=maximizing)
    return scored


def get_ranked_moves_iterative(board, time_budget, max_depth=14):
    """Iterative deepening search with aspiration windows (item 14).

    Aspiration windows: instead of searching with the full [-9999, +9999] window,
    we use a narrow window [prev_score - 50, prev_score + 50] around the score from
    the previous depth. If the result falls outside the window (a "fail"), we widen
    the window and re-search. This cuts wasted nodes when the expected score is close
    to the previous depth's result — which it usually is.

    Why iterative deepening:
      1. We don't know how deep we can go within the time limit.
      2. If depth N times out, we return the result from depth N-1 immediately.
      3. Shallow searches warm the TT, making deeper searches faster.

    max_depth: hard ceiling from MAX_DEPTH in difficulty.py. The search stops here
    even if time remains — this is the primary per-difficulty strength cap.
    Ponder workers intentionally do NOT pass max_depth so they can search deeper
    to warm the TT for the next real move.
    """
    deadline        = time.time() + time_budget
    best_ranked     = None
    prev_score      = None   # score from the completed depth, used to set aspiration window
    completed_depth = 0

    for depth in range(1, max_depth + 1):
        if depth > 1 and time.time() >= deadline - 0.05:
            break

        # ── Aspiration window (item 14) ────────────────────────────────────────
        # Start with a narrow window around the previous score. Widen on failure.
        if prev_score is not None and depth >= 3:
            window = 50   # centipawns
            alpha  = prev_score - window
            beta   = prev_score + window
        else:
            alpha = -9999   # full window for depth 1 and 2
            beta  =  9999

        while True:
            ranked = _search_with_window(board, depth, alpha, beta, deadline)
            if not ranked:
                break   # timeout or no moves

            score = ranked[0][0]

            if score <= alpha:
                # Failed low: result was worse than expected — widen the alpha side
                alpha = max(-9999, alpha - 100)
                continue
            if score >= beta:
                # Failed high: result was better than expected — widen the beta side
                beta = min(9999, beta + 100)
                continue

            # Search succeeded within the window
            best_ranked     = ranked
            prev_score      = score
            completed_depth = depth
            break

        if time.time() >= deadline:
            break

    return best_ranked or [], completed_depth


def _search_with_window(board, depth, alpha, beta, deadline):
    """Search at a fixed depth with specific alpha/beta bounds."""
    maximizing = board.turn == chess.WHITE
    moves      = list(board.legal_moves)
    if not moves:
        return []

    killer_table = [_km.get(d) for d in range(16)]

    scored = []
    for move in moves:
        if deadline and time.time() >= deadline:
            break
        board.push(move)
        timed_out = False
        try:
            score, _ = minimax(board, depth - 1, alpha, beta, not maximizing,
                                deadline, killer_table)
            scored.append((score, move))
        except TimeoutError:
            timed_out = True
        finally:
            board.pop()
        if timed_out:
            break

    scored.sort(key=lambda x: x[0], reverse=maximizing)
    return scored


# =========================
# TOP-LEVEL AI MOVE SELECTOR
# =========================
def get_ai_move(board, difficulty, player):
    """Top-level move selection pipeline:
    1. Stop any background ponder search (player has moved).
    2. Grandmaster: delegate entirely to Stockfish.
    3. Opening book: use learned or Stockfish moves for the first 6 full moves.
    4. DB lookup: reuse a previously computed/learned move if available.
    5. Iterative deepening with aspiration windows: full minimax search.
    6. Cache the best move found so future games in the same position are faster.
    7. Apply aggression-based selection to introduce intentional imperfection.
    """
    stop_ponder()

    if difficulty == "grandmaster":
        return get_stockfish_move(board)

    global _game_db_hits, _game_opening_moves, _game_tt_hits, _game_tt_lookups
    global _game_best_moves, _game_total_moves

    time_budget    = TIME_MAP.get(difficulty, 1.5)
    best_move_rate = BEST_MOVE_RATES.get(difficulty, 0.60)
    max_depth      = MAX_DEPTH.get(difficulty, 10)
    wall_deadline  = time.time() + time_budget
    move_start     = time.time()

    # 1. Opening book
    opening_ranked = get_opening_move(board, player, difficulty)
    if opening_ranked:
        move = select_move_by_aggression(opening_ranked, best_move_rate, player.aggression)
        _game_opening_moves += 1
        _game_total_moves   += 1
        if opening_ranked and move == opening_ranked[0][1]:
            _game_best_moves += 1
        _game_depths.append(0)
        _game_move_times_ms.append((time.time() - move_start) * 1000)
        _ponder_after(board, move, time_budget)
        return move

    # Notify the thermal monitor that real minimax work is starting.
    # Lazy: same reason as _get_pool() — workers import this module; thermal_monitor
    # must only run in the main process.
    from . import thermal_monitor
    thermal_monitor.notify_minimax_started()

    # 2. Check TT warmth at root before searching (proxy for cache health)
    _game_tt_lookups += 1
    if TT.get(hash_board(board)):
        _game_tt_hits += 1

    # 3. DB lookup
    db_move = get_best_db_move(board)
    if db_move:
        if difficulty in ("hard", "expert") or random.random() > player.risk:
            _game_db_hits    += 1
            _game_total_moves += 1
            _game_best_moves  += 1
            _game_depths.append(0)
            _game_move_times_ms.append((time.time() - move_start) * 1000)
            _ponder_after(board, db_move, time_budget)
            return db_move

    # 4. Iterative deepening search
    remaining = max(0.1, wall_deadline - time.time())
    ranked, completed_depth = get_ranked_moves_iterative(board, remaining, max_depth)
    if not ranked:
        log_issue("null_move_returned", fen=board.fen(),
                  detail="get_ranked_moves_iterative returned empty — using first legal move",
                  difficulty=difficulty)
        move = next(iter(board.legal_moves), None)
        if move:
            _game_total_moves   += 1
            _game_depths.append(0)
            _game_move_times_ms.append((time.time() - move_start) * 1000)
            _game_workers.append(thermal_monitor.get_active_workers())
            _ponder_after(board, move, time_budget)
        return move

    # 5. Cache best found move
    cache_computed_move(board, ranked[0][1])

    # 6. Pick best or intentionally suboptimal move
    move = select_move_by_aggression(ranked, best_move_rate, player.aggression)
    if move not in board.legal_moves:
        print(f"ILLEGAL MOVE FROM SEARCH: {move} on {board.fen()}")
        log_issue("illegal_move_search", move=move.uci() if move else "",
                  fen=board.fen(), difficulty=difficulty)
        move = next(iter(board.legal_moves), None)

    # Record stats for this move
    _game_total_moves   += 1
    _game_depths.append(completed_depth)
    _game_move_times_ms.append((time.time() - move_start) * 1000)
    _game_workers.append(thermal_monitor.get_active_workers())
    if ranked and move == ranked[0][1]:
        _game_best_moves += 1

    _ponder_after(board, move, time_budget)
    return move


def _ponder_after(board, ai_move, time_budget):
    """After picking a move, start pondering the top predicted player replies.

    We look up the TT for the position after the AI move to predict what the player
    is most likely to play. We ponder the top 3 predicted replies (multi-line, item 15),
    giving the AI up to 4x the normal time budget to think ahead.
    """
    try:
        b = board.copy()
        b.push(ai_move)
        if b.is_game_over():
            return

        # Collect candidate player replies using the TT for guidance
        ponder_fens = []
        key = hash_board(b)
        cached = TT.get(key)
        top_reply = cached[1] if cached else None

        # Always ponder the TT-predicted best reply first
        if top_reply and top_reply in b.legal_moves:
            bc = b.copy()
            bc.push(top_reply)
            if not bc.is_game_over():
                ponder_fens.append(bc.fen())

        # Also ponder the 2 highest-scoring legal moves (by quick static eval)
        # so we're not helpless if the player plays something unexpected
        if len(ponder_fens) < 3:
            try:
                quick_ranked = get_ranked_moves(b, depth=1)
                for _, candidate in quick_ranked[:3]:
                    if candidate == top_reply:
                        continue
                    bc = b.copy()
                    bc.push(candidate)
                    if not bc.is_game_over():
                        ponder_fens.append(bc.fen())
                    if len(ponder_fens) >= 3:
                        break
            except Exception:
                pass

        if ponder_fens:
            start_ponder(ponder_fens, time_budget=time_budget * 4)

    except Exception:
        pass
