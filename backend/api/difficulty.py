import chess
import random
import os
import sys
from datetime import datetime

def _hint_log(msg):
    """Append a timestamped line to hint_debug.log in the app data directory."""
    try:
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        else:
            base = os.path.expanduser("~")
        log_path = os.path.join(base, "chess-ai", "hint_debug.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

_PIECE_NAMES = {
    chess.PAWN:   "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK:   "rook",
    chess.QUEEN:  "queen",
    chess.KING:   "king",
}

# Standard material values used in explanations (pawns of worth)
_PIECE_VALUES_EXPLAIN = {
    chess.PAWN:   1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK:   5,
    chess.QUEEN:  9,
    chess.KING:   0,
}

# =========================
# DIFFICULTY PARAMETERS
# =========================
# How often the AI picks its actual best move. The rest of the time it
# intentionally picks a suboptimal move (see select_move_by_aggression).
BEST_MOVE_RATES = {
    "easy":   0.40,
    "medium": 0.60,
    "hard":   0.80,
    "expert": 0.95,
}

# Time budgets per difficulty (seconds). The AI searches as deeply as possible
# within this window using iterative deepening.
TIME_MAP = {
    "easy":   1.0,
    "medium": 3.0,
    "hard":   6.0,
    "expert": 10.0,
}

# Hard depth ceiling per difficulty. Iterative deepening stops here even if
# time remains. This is the primary strength cap — with LMR and null move
# pruning already in the search, effective strength at each ceiling is roughly:
#   easy   depth 5  → ~800–1000 ELO
#   medium depth 7  → ~1100–1300 ELO
#   hard   depth 9  → ~1400–1600 ELO
#   expert depth 10 → ~1800–2000 ELO
#
# WHY A DEPTH CAP INSTEAD OF RELYING ON THE TIMER:
# Search depth reached within a fixed time varies by hardware — a fast CPU hits
# depth 12 in 10 s while a slow one only hits depth 8. A depth ceiling gives
# consistent, hardware-independent strength regardless of machine speed.
#
# WHY EXPERT IS CAPPED AT 10:
# LMR + null move make depth 10 significantly stronger than pre-improvement
# depth 10 — it's the sweet spot for ~2000 ELO play. Raising this value
# (to 11 or 12) is the correct way to allow the expert ceiling to grow if
# the AI ever feels too easy after hundreds of games.
MAX_DEPTH = {
    "easy":   5,
    "medium": 7,
    "hard":   9,
    "expert": 10,
}

def select_move_by_aggression(ranked_moves, best_move_rate, aggression):
    """Pick a move based on difficulty and player-specific aggression.

    best_move_rate% of the time: play the objectively best move.
    Otherwise: intentionally pick a suboptimal move whose rank depends on aggression.
      aggression = 1.0 → 2nd best move (index 1) — still strong, just slightly off
      aggression = 0.0 → up to 6th best move (index 5) — notably weaker
    This makes the AI feel more human — it makes occasional mistakes and adjusts
    its 'style' to match how aggressively the player has been playing.
    """
    if not ranked_moves:
        return None
    if random.random() < best_move_rate or len(ranked_moves) == 1:
        return ranked_moves[0][1]
    max_idx = min(5, len(ranked_moves) - 1)
    idx = round(1 + (1.0 - aggression) * (max_idx - 1))
    return ranked_moves[max(1, min(idx, max_idx))][1]

def adapt_player(player, move_quality):
    """Nudge the player's aggression score up or down based on move quality.
    Over many games this shapes how the AI responds to the player's style."""
    if move_quality > 0:
        player.aggression = min(1.0, player.aggression + 0.01)
    else:
        player.aggression = max(0.0, player.aggression - 0.01)
    player.save()

def _explain_better_move(board, player_move, best_move):
    """Generate a multi-sentence beginner-friendly explanation of why best_move
    is stronger than player_move. board is in the state BEFORE either move is played;
    we temporarily push/pop to inspect resulting positions without altering board state."""

    # ── Priority 1: best move delivers checkmate ──────────────────────────────
    board.push(best_move)
    is_mate = board.is_checkmate()
    board.pop()
    if is_mate:
        return (
            "This move delivers checkmate — the opponent's king has no legal escape, "
            "and the game ends immediately in your favor. "
            "Checkmate is the ultimate goal in chess, so never pass it up when it's available!"
        )

    # ── Priority 2: best move captures a piece ────────────────────────────────
    if board.is_capture(best_move):
        victim = board.piece_at(best_move.to_square)
        if victim:
            name  = _PIECE_NAMES.get(victim.piece_type, "piece")
            value = _PIECE_VALUES_EXPLAIN.get(victim.piece_type, 0)
            board.push(best_move)
            can_recapture = board.is_attacked_by(board.turn, best_move.to_square)
            board.pop()
            value_str = f"{value} pawn{'s' if value != 1 else ''} of material"
            if not can_recapture:
                return (
                    f"This captures the opponent's {name} completely for free — "
                    f"they cannot take back, so you gain {value_str} at no cost. "
                    f"Winning material for free is one of the strongest advantages in chess; "
                    f"always scan the board for free captures before making any other move."
                )
            else:
                mover      = board.piece_at(best_move.from_square)
                mover_name  = _PIECE_NAMES.get(mover.piece_type, "piece")  if mover else "piece"
                mover_value = _PIECE_VALUES_EXPLAIN.get(mover.piece_type, 0) if mover else 0
                if mover_value < value:
                    return (
                        f"This captures the opponent's {name} ({value_str}) with your {mover_name} "
                        f"(worth only {mover_value} pawn{'s' if mover_value != 1 else ''}). "
                        f"Even though your opponent can take back, you are trading a less valuable "
                        f"piece for a more valuable one — that is called winning material and gives "
                        f"you a lasting advantage."
                    )
                else:
                    return (
                        f"This captures the opponent's {name} ({value_str}). "
                        f"Your opponent can recapture, making it an even trade, but the resulting "
                        f"position gives you better piece placement and board control."
                    )

    # ── Priority 3: best move gives check ─────────────────────────────────────
    board.push(best_move)
    gives_check = board.is_check()
    board.pop()
    if gives_check:
        return (
            "This move puts the opponent's king in check, forcing them to spend their entire "
            "next turn dealing with the threat — they must block, move the king, or capture the "
            "attacking piece. "
            "Giving check limits your opponent's choices and lets you control the tempo of the game, "
            "often leading to winning more material or creating unstoppable threats."
        )

    # ── Priority 4: best move creates a fork ─────────────────────────────────
    board.push(best_move)
    mover_color = not board.turn  # the side that just moved
    forked = [
        board.piece_at(sq)
        for sq in chess.SQUARES
        if (board.piece_at(sq)
            and board.piece_at(sq).color == board.turn          # opponent's piece
            and best_move.to_square in board.attackers(mover_color, sq))
    ]
    board.pop()
    # Only report a fork if it threatens at least two non-pawn pieces (or king)
    valuable_forked = [p for p in forked if p.piece_type != chess.PAWN]
    if len(valuable_forked) >= 2:
        names = [_PIECE_NAMES.get(p.piece_type, "piece") for p in valuable_forked[:2]]
        return (
            f"This creates a fork — one piece simultaneously attacks the opponent's "
            f"{names[0]} and {names[1]}. "
            f"Because the opponent can only move one piece per turn, they can only save one of them, "
            f"meaning you get to capture the other for free. "
            f"Forks are one of the most powerful tactics in chess and are often game-deciding."
        )

    # ── Priority 5: player's move left a piece hanging ───────────────────────
    board.push(player_move)
    moved_piece = board.piece_at(player_move.to_square)
    if moved_piece:
        opponent = board.turn
        own      = not board.turn
        attacked = board.is_attacked_by(opponent, player_move.to_square)
        defended = board.is_attacked_by(own,      player_move.to_square)
        if attacked and not defended:
            name  = _PIECE_NAMES.get(moved_piece.piece_type, "piece")
            value = _PIECE_VALUES_EXPLAIN.get(moved_piece.piece_type, 0)
            board.pop()
            return (
                f"Your {name} moved to a square where the opponent can capture it for free — "
                f"this is called a 'hanging piece.' "
                f"A {name} is worth about {value} pawn{'s' if value != 1 else ''} of material, and "
                f"losing it for nothing gives your opponent a big lead. "
                f"Before moving any piece, always check whether it is safe on its new square — "
                f"ask yourself: can the opponent take it, and can I take back?"
            )
    board.pop()

    # ── Priority 6: general positional improvement ────────────────────────────
    return (
        "This move places your pieces on more active, better-coordinated squares "
        "that give you greater control of the board. "
        "In chess, strong piece activity gradually builds an advantage — pieces on good squares "
        "have more options and put more pressure on the opponent, often leading to winning "
        "material or a decisive attack later in the game."
    )


def _pv_to_san(board, pv_ucis, max_moves):
    """Convert up to max_moves UCI strings from a Stockfish PV into step objects.
    Each step carries the SAN string plus from/to square names so the frontend can
    render a mini board with a coloured from-square, coloured to-square, and an
    arrow overlay. Stops early if a move is illegal."""
    copy = board.copy()
    steps = []
    for uci in pv_ucis[:max_moves]:
        try:
            move = chess.Move.from_uci(uci)
            if move not in copy.legal_moves:
                break
            steps.append({
                "san":  copy.san(move),
                "from": chess.square_name(move.from_square),
                "to":   chess.square_name(move.to_square),
            })
            copy.push(move)
        except Exception:
            break
    return steps


def analyze_player_move(board, player_move):
    """Compare the player's move to top Stockfish alternatives.
    Returns mistake details with up to 5 ranked alternatives, each with a
    beginner-friendly explanation. Only called in easy mode.

    Uses Stockfish MultiPV for move quality (far stronger than minimax depth 2).
    Falls back to minimax if Stockfish binary is unavailable."""
    # Lazy: breaks a circular import chain — difficulty.py is imported by minimax.py
    # (from .difficulty import ...), and stockfish_engine is also imported by minimax.py.
    # Importing stockfish_engine here at call time avoids a module-level cycle.
    from .stockfish_engine import get_stockfish_top_moves

    player_color = board.turn
    _hint_log(f"--- analyze_player_move ---")
    _hint_log(f"  FEN:         {board.fen()}")
    _hint_log(f"  Player move: {player_move.uci()}  (color: {'white' if player_color == chess.WHITE else 'black'})")

    # --- Stockfish path ---
    sf_top = get_stockfish_top_moves(board, n=5, movetime=1000)
    if sf_top:
        best_score_cp = sf_top[0][0]
        _hint_log(f"  Stockfish top moves: {[(round(s/100,2), m.uci()) for s,m,_ in sf_top]}")

        # Evaluate the player's move from the opponent's side (they are now to move),
        # then negate to express the score from the player's perspective.
        board.push(player_move)
        sf_player = get_stockfish_top_moves(board, n=1, movetime=500)
        board.pop()

        if not sf_player:
            _hint_log("  sf_player returned empty — skipping")
            return {"mistake": False}

        player_score_cp = -sf_player[0][0]
        diff_cp = best_score_cp - player_score_cp
        _hint_log(f"  best_cp={best_score_cp}  player_cp={player_score_cp}  diff_cp={diff_cp}")

        # Only flag as a mistake if the gap exceeds 1.5 pawns (150 centipawns)
        if diff_cp <= 150:
            _hint_log(f"  diff {diff_cp}cp <= 150 threshold — no hint")
            return {"mistake": False}

        _hint_log(f"  MISTAKE flagged (diff {diff_cp}cp > 150) — building hint slides")
        top_moves = []
        for score_cp, move, pv_ucis in sf_top:
            if move not in board.legal_moves:
                continue
            move_diff_cp = score_cp - player_score_cp
            if move_diff_cp <= 0:
                continue
            # Show more continuation moves the larger the mistake
            if move_diff_cp >= 300:
                max_cont = 5
            elif move_diff_cp >= 200:
                max_cont = 4
            else:
                max_cont = 3
            try:
                reason       = _explain_better_move(board, player_move, move)
                continuation = _pv_to_san(board, pv_ucis, max_cont)
                _hint_log(f"  option {move.uci()}  diff={round(move_diff_cp/100,2)}p  cont={[s['san'] for s in continuation]}")
                top_moves.append({
                    "san":          board.san(move),
                    "uci":          move.uci(),
                    "from":         chess.square_name(move.from_square),
                    "to":           chess.square_name(move.to_square),
                    "reason":       reason,
                    "eval_diff":    round(move_diff_cp / 100.0, 1),
                    "continuation": continuation,
                })
            except Exception as e:
                _hint_log(f"  option {move.uci()} failed: {e}")

        if not top_moves:
            _hint_log("  No valid top_moves built — no hint")
            return {"mistake": False}

        _hint_log(f"  Returning {len(top_moves)} hint option(s), best continuation has {len(top_moves[0]['continuation'])} slides")
        return {
            "mistake":        True,
            "top_moves":      top_moves,
            "best_move":      top_moves[0]["uci"],
            "best_move_san":  top_moves[0]["san"],
            "reason":         top_moves[0]["reason"],
            "eval_diff":      top_moves[0]["eval_diff"],
        }

    # --- Minimax fallback (Stockfish binary unavailable) ---
    _hint_log("  Stockfish unavailable — using minimax fallback (no continuation slides)")
    # Lazy: circular import — difficulty.py is imported by minimax.py at the top level
    # (from .difficulty import ...), so difficulty cannot import minimax at the top
    # level without creating a deadlock at startup. evaluation.py is kept here for
    # symmetry with minimax since this entire fallback block is rarely reached.
    from .minimax import minimax
    from .evaluation import evaluate

    _, best_move = minimax(board, 2, -9999, 9999, player_color)
    if best_move is None:
        _hint_log("  Minimax returned no move — no hint")
        return None

    board.push(player_move)
    player_eval = evaluate(board)
    board.pop()

    board.push(best_move)
    best_eval = evaluate(board)
    board.pop()

    _hint_log(f"  Minimax: best_eval={best_eval:.2f}  player_eval={player_eval:.2f}  diff={abs(best_eval-player_eval):.2f}")
    if abs(best_eval - player_eval) <= 1.5:
        _hint_log("  diff <= 1.5 threshold — no hint")
        return {"mistake": False}

    maximizing = (player_color == chess.WHITE)
    all_scored = []
    for move in list(board.legal_moves):
        board.push(move)
        score, _ = minimax(board, 1, -9999, 9999, board.turn)
        board.pop()
        all_scored.append((score, move))
    all_scored.sort(key=lambda x: x[0], reverse=maximizing)

    top_moves = []
    for _, move in all_scored[:5]:
        board.push(move)
        move_eval = evaluate(board)
        board.pop()

        diff = (move_eval - player_eval) if player_color == chess.WHITE else (player_eval - move_eval)
        if diff <= 0:
            continue

        try:
            reason = _explain_better_move(board, player_move, move)
            top_moves.append({
                "san":          board.san(move),
                "uci":          move.uci(),
                "from":         chess.square_name(move.from_square),
                "to":           chess.square_name(move.to_square),
                "reason":       reason,
                "eval_diff":    round(diff, 1),
                "continuation": [],
            })
        except Exception:
            pass

    if not top_moves:
        return {"mistake": False}

    return {
        "mistake":        True,
        "top_moves":      top_moves,
        "best_move":      top_moves[0]["uci"],
        "best_move_san":  top_moves[0]["san"],
        "reason":         top_moves[0]["reason"],
        "eval_diff":      top_moves[0]["eval_diff"],
    }
