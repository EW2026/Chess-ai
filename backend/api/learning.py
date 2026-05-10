"""
learning.py
===========
Neural network training and database persistence for the chess AI.

This module is called at the end of each game to:
  1. Store every board position the AI encountered, along with the game outcome.
  2. Train the neural network on those positions (plus samples from past games).
  3. Update the AI's ELO rating.
  4. Refresh the numpy inference cache so the search uses the new weights immediately.
  5. Restart the worker pool so parallel search workers load the new model file.
"""

import chess
import threading
import torch
import torch.nn as nn
import torch.optim as optim

from .hardware import HW
from .neural_net import (
    hash_board_str, board_to_tensor,
    device, model, MODEL_PATH, _refresh_eval_weights,
    get_nn_architecture, get_nn_param_count,
)
from .replay_buffer import get_buffer

from .evaluation import set_nn_weight, get_nn_weight, evaluate_pst

# =========================
# BOARD MIRRORING FOR AUGMENTATION  (item 8)
# =========================
# Chess is horizontally symmetric: mirroring the board left-right produces a
# valid position with the same strategic value. Training on both the original
# and mirrored version doubles our dataset for free.
#
# To mirror square index i (where i = rank*8 + file):
#   mirrored_file = 7 - file = 7 - (i % 8)
#   same rank     = i // 8
#   mirrored_sq   = rank * 8 + mirrored_file = (i // 8) * 8 + (7 - i % 8)
_MIRROR_64 = [(i // 8) * 8 + (7 - i % 8) for i in range(64)]

# For the 12-plane (768-element) encoding used by the large GPU network,
# each plane is a 64-element block. Mirroring flips each block independently.
_MIRROR_768 = [plane * 64 + _MIRROR_64[sq] for plane in range(12) for sq in range(64)]


def _mirror_tensor(t):
    """Mirror a board tensor left-right. Works for both 64- and 768-element encodings."""
    if t.shape[0] == 768:
        return t[_MIRROR_768]
    return t[_MIRROR_64]


# Persistent Adam optimizer: kept alive between training calls so momentum state
# accumulates across games rather than resetting each time.
_optimizer = None

# =========================
# ISSUE LOGGING
# =========================
def log_issue(issue_type, move="", fen="", detail="", difficulty=""):
    """Fire-and-forget: write an IssueLog row without raising."""
    try:
        # Lazy: learning.py is imported by minimax.py, which worker processes also load.
        # Workers never call django.setup(), so model imports cannot be at the top level.
        from .models import IssueLog
        IssueLog.objects.create(
            issue_type=issue_type,
            move=str(move)[:20],
            fen=str(fen)[:200],
            detail=str(detail)[:1000],
            difficulty=str(difficulty)[:20],
        )
    except Exception:
        pass

# =========================
# DATABASE MOVE CACHE
# =========================
def get_best_db_move(board):
    """Look up the best previously learned move for this position from the database.
    Uses the Zobrist hash as a fast index. Each stored move has a win/loss count;
    we pick the one with the highest score (net wins per appearance, with time decay)."""
    # Lazy: same as log_issue — worker processes import this module without django.setup().
    from .models import PositionMemory
    h = hash_board_str(board)
    moves = PositionMemory.objects.filter(hash=h)

    if not moves.exists():
        return None

    legal = set(board.legal_moves)
    candidates = []
    for m in moves:
        try:
            move = chess.Move.from_uci(m.move)
            if move in legal:
                candidates.append((m.score(), move))
            else:
                log_issue("illegal_move_db", move=m.move, fen=board.fen(),
                          detail=f"Stored UCI {m.move!r} not legal in position")
        except Exception as e:
            log_issue("illegal_move_db", move=m.move, fen=board.fen(), detail=str(e))

    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]

def store_position(board, move, won):
    """Record the outcome of a move in PositionMemory.
    Each (board_hash, move) pair tracks how many times it led to a win vs loss."""
    # Lazy: same as log_issue — worker processes import this module without django.setup().
    from .models import PositionMemory
    h = hash_board_str(board)

    obj, _ = PositionMemory.objects.get_or_create(
        hash=h,
        move=str(move),
        defaults={"fen": board.fen()}
    )
    obj.times_seen += 1
    if won:
        obj.wins += 1
    else:
        obj.losses += 1
    obj.save()

def cache_computed_move(board, move):
    """Store a minimax-computed best move so future lookups for this position skip the search."""
    # Lazy: same as log_issue — worker processes import this module without django.setup().
    from .models import PositionMemory
    h = hash_board_str(board)
    PositionMemory.objects.get_or_create(
        hash=h,
        move=str(move),
        defaults={"fen": board.fen()}
    )

# =========================
# NEURAL NETWORK TRAINING
# =========================
def train_model(game_log, won, opponent_elo=1200, ai_color="b",
                difficulty="", player_elo_snapshot=0, player_aggression=0.5):
    """Train the neural network on positions from the just-completed game.

    Changes from the original version:
      - Batch training (item 3): all positions processed in one forward/backward pass
        instead of one-at-a-time (batch size 1). Drops GPU training from 5-15s to <1s.
      - Temporal difference weighting (item 4): positions near the end of the game
        get more credit — the blunder on move 30 mattered more than move 3 did.
      - Experience replay (item 5): mix in samples from past games so the network
        doesn't forget what it learned previously.
      - Board symmetry augmentation (item 8): every position is also trained on its
        horizontally-mirrored version, doubling the effective dataset for free.
      - Per-position score targets (item 13): after the outcome-based pass, do a
        secondary training pass using PST evaluation scores as per-position targets.
      - Dynamic NN weight update (item 7): after training, update how much the NN
        contributes to evaluation based on total games played.
    """

    # Lazy: same as log_issue — worker processes import this module without django.setup().
    from .models import LearningTracker

    global _optimizer

    if not game_log:
        return

    # ── Build tensors from game log ────────────────────────────────────────────
    positions  = []   # list of (tensor, board) pairs
    for entry in game_log:
        fen = entry.get("fen")
        if not fen:
            continue
        try:
            board  = chess.Board(fen)
            tensor = board_to_tensor(board)
            positions.append((tensor, board))
        except Exception:
            pass

    if not positions:
        return

    n = len(positions)

    # ── Temporal difference weighting (item 4) ────────────────────────────────
    # Positions late in the game are more responsible for the outcome than early ones.
    # We use exponential decay with gamma=0.95 per step from the END of the game.
    # Last position gets weight 1.0, second-to-last gets 0.95, move 1 gets 0.95^(n-1).
    gamma = 0.95
    td_weights = [gamma ** (n - 1 - i) for i in range(n)]

    # ── Outcome target ────────────────────────────────────────────────────────
    # If AI won: target = -1.0 for White AI or +1.0 for Black AI (network is White-positive)
    # The sign tells the network "the AI's positions were good" in whichever direction.
    target_val = (1.0 if won else -1.0) if ai_color == "w" else (-1.0 if won else 1.0)

    # ── Build batches (current game + mirrored augmentation) ─────────────────
    current_tensors = []
    current_targets = []
    current_weights = []
    replay_samples  = []

    for i, (tensor, board) in enumerate(positions):
        w = td_weights[i]

        # Original position
        current_tensors.append(tensor)
        current_targets.append(target_val)
        current_weights.append(w)

        # Mirrored position (item 8): same target value (horizontal flip doesn't swap colors)
        mirrored = _mirror_tensor(tensor)
        current_tensors.append(mirrored)
        current_targets.append(target_val)
        current_weights.append(w)

        # Collect samples for replay buffer storage
        replay_samples.append((tensor.cpu(), target_val, w))

    # Add current game to replay buffer
    buf = get_buffer()
    buf.add_game(replay_samples)

    # Sample from replay buffer (~25% of current-game count, min 8 samples)
    replay_count = max(8, len(current_tensors) // 4)
    past_samples = buf.sample(replay_count)

    for past_tensor, past_target, past_weight in past_samples:
        current_tensors.append(past_tensor.to(device))
        current_targets.append(past_target)
        current_weights.append(past_weight * 0.5)  # down-weight past samples slightly

    # ── Batch training pass (item 3) ──────────────────────────────────────────
    # Stack all tensors into one batch matrix — one forward pass covers all positions.
    # Previously: for each position in positions: model(tensor) — O(N) forward passes.
    # Now: model(batch) — O(1) forward pass with GPU parallelism across all N positions.
    batch   = torch.stack(current_tensors)                                           # (N, input_size)
    targets = torch.tensor(current_targets, dtype=torch.float32, device=device).unsqueeze(1)  # (N, 1)
    weights = torch.tensor(current_weights, dtype=torch.float32, device=device).unsqueeze(1)  # (N, 1)

    if _optimizer is None:
        _optimizer = optim.Adam(model.parameters(), lr=0.001)
    optimizer = _optimizer

    model.train()
    losses     = []
    grad_norms = []

    for _ in range(HW["training_iterations"]):
        predictions = model(batch)                         # (N, 1) — one pass for all positions
        # Weighted MSE: positions near the end of the game contribute more to the loss
        loss = ((predictions - targets) ** 2 * weights).mean()
        optimizer.zero_grad()
        loss.backward()

        grad_norm = sum(
            p.grad.data.norm(2).item() ** 2
            for p in model.parameters()
            if p.grad is not None
        ) ** 0.5
        grad_norms.append(grad_norm)

        optimizer.step()
        losses.append(loss.item())

    # ── Per-position score targets (item 13) ──────────────────────────────────
    # After the outcome-based pass, do a short secondary pass using PST scores
    # as position-specific targets. This gives the network a richer learning signal:
    # instead of just "this game was won/lost", it learns "this specific position
    # looked like White was +0.8 pawns ahead" — much more informative.
    # We use PST-only (no NN) to avoid circular feedback.
    pst_tensors = []
    pst_targets = []
    for tensor, board in positions:
        pst_score = evaluate_pst(board)   # PST-only evaluation in pawn units
        pst_tensors.append(tensor)
        pst_targets.append(float(pst_score))

    if pst_tensors:
        pst_batch   = torch.stack(pst_tensors)
        pst_targets_t = torch.tensor(pst_targets, dtype=torch.float32, device=device).unsqueeze(1)
        secondary_iters = max(5, HW["training_iterations"] // 10)

        for _ in range(secondary_iters):
            preds = model(pst_batch)
            loss  = nn.functional.mse_loss(preds, pst_targets_t)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            losses.append(loss.item())

    model.eval()
    torch.save(model.state_dict(), str(MODEL_PATH))
    _refresh_eval_weights()   # update numpy cache so search sees the new weights

    # ── Update dynamic NN blend ratio (item 7) ────────────────────────────────
    games_count = LearningTracker.objects.count() + 1   # +1 for the game we're about to record
    set_nn_weight(get_nn_weight(games_count))

    # ── Restart pool so workers reload the new model file ─────────────────────
    # Lazy: circular import — learning.py is imported by minimax.py at the top level
    # (from .learning import ...), so learning importing minimax at the top level would
    # create a deadlock at startup. Source: minimax.py top-level import of learning.
    from .minimax import _restart_pool, get_ranked_moves

    def _respawn():
        _restart_pool()
        try:
            get_ranked_moves(chess.Board(), 2)
            # Workers re-spawned after model update — re-register so affinity is re-applied
            # Lazy: _pool is a mutable variable that starts None; must read live value here.
            # Lazy: thermal_monitor must only run in main process (see minimax._get_pool note).
            from .minimax import _pool as pool
            from . import thermal_monitor
            thermal_monitor.set_pool(pool)
        except Exception:
            pass
    threading.Thread(target=_respawn, daemon=True).start()

    # ── Collect per-game search stats from minimax accumulators ──────────────
    # Lazy: circular import with minimax (same reason as _restart_pool above).
    # Lazy: thermal_monitor must only initialize in the main process (see minimax._get_pool note).
    # Lazy: get_nn_weight is already imported at the top of this module; re-imported
    # here under the alias _get_nn_weight for use in the LearningTracker.objects.create() call.
    from . import thermal_monitor
    from .minimax import (
        _game_depths, _game_move_times_ms, _game_db_hits, _game_opening_moves,
        _game_tt_hits, _game_tt_lookups, _game_best_moves, _game_total_moves, _game_workers,
    )
    from .evaluation import get_nn_weight as _get_nn_weight

    _depths     = list(_game_depths)
    _times      = list(_game_move_times_ms)
    _workers    = list(_game_workers)

    avg_depth        = round(sum(_depths) / len(_depths), 2)         if _depths   else 0.0
    max_depth        = max(_depths)                                   if _depths   else 0
    avg_move_time_ms = round(sum(_times) / len(_times), 1)           if _times    else 0.0
    avg_workers_val  = round(sum(_workers) / len(_workers), 2)       if _workers  else (1.0 if _game_total_moves > 0 else 0.0)
    tt_hit_rate      = round(_game_tt_hits / _game_tt_lookups, 3)    if _game_tt_lookups else 0.0
    ai_best_move_pct = round(_game_best_moves / _game_total_moves * 100, 1) if _game_total_moves else 0.0

    # Per-5-move timing segments: [{segment, moves, avg_ms, avg_depth}, ...]
    seg_size = 5
    move_time_segments = []
    for i in range(0, len(_times), seg_size):
        chunk_times  = _times[i:i + seg_size]
        chunk_depths = _depths[i:i + seg_size]
        if not chunk_times:
            break
        move_time_segments.append({
            "segment":   i // seg_size + 1,
            "moves":     f"{i + 1}-{min(i + seg_size, len(_times))}",
            "avg_ms":    round(sum(chunk_times) / len(chunk_times), 1),
            "avg_depth": round(sum(chunk_depths) / len(chunk_depths), 1) if chunk_depths else 0,
        })

    # ── ELO update ────────────────────────────────────────────────────────────
    last        = LearningTracker.objects.order_by('-timestamp').first()
    current_elo = last.ai_elo if last else 1200
    expected    = 1 / (1 + 10 ** ((opponent_elo - current_elo) / 400))
    new_elo     = int(current_elo + 32 * ((1.0 if won else 0.0) - expected))

    LearningTracker.objects.create(
        won=won,
        positions_trained=len(positions),
        final_loss=losses[-1] if losses else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        total_weight_changes=len(grad_norms),
        avg_weight_change=sum(grad_norms) / len(grad_norms) if grad_norms else 0.0,
        ai_elo=new_elo,
        # Game context
        difficulty=difficulty,
        ai_color=ai_color,
        total_moves=_game_total_moves,
        player_elo_snapshot=player_elo_snapshot,
        # Search depth
        avg_depth=avg_depth,
        max_depth=max_depth,
        # Move timing
        avg_move_time_ms=avg_move_time_ms,
        move_time_segments=move_time_segments,
        # Move source
        db_hits=_game_db_hits,
        opening_moves=_game_opening_moves,
        tt_hit_rate=tt_hit_rate,
        # Thermal / hardware
        avg_workers=avg_workers_val,
        min_workers=thermal_monitor.get_min_workers(),
        avg_clock_mhz=thermal_monitor.get_avg_clock(),
        # Aggression
        ai_best_move_pct=ai_best_move_pct,
        player_aggression=player_aggression,
        # NN quality
        nn_weight=round(_get_nn_weight(LearningTracker.objects.count() + 1), 3),
        nn_architecture=get_nn_architecture(),
        nn_param_count=get_nn_param_count(),
    )
