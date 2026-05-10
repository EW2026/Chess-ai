"""
evaluation.py
=============
All board evaluation logic lives here. "Evaluation" means: given a board position,
who is winning and by how much?

The answer is a single float in "pawn units":
  +1.0 = White is about one pawn ahead
  -1.0 = Black is about one pawn ahead
   0.0 = roughly equal

We combine three sources of information:
  1. Material + PST (piece-square tables): which pieces exist and where they stand
  2. Explicit chess terms: known heuristics like pawn structure and king safety
  3. Neural network: a small AI trained on real game outcomes to fill in the gaps

The NN contribution starts at 15% when untrained and grows up to 50% max as it
learns. The PST always has at least 50% say — it's the reliable safety net.
"""

import chess


# =========================
# PIECE VALUES (centipawns)
# =========================
# 100 centipawns = 1 pawn. We work in centipawns for precision, divide by 100 at the end.
PIECE_VALUES_CP = {
    chess.PAWN:   100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK:   500,
    chess.QUEEN:  900,
    chess.KING:   20000,
}

# Total material at the start of a game (both sides, excluding kings).
# Kings are never captured, so we don't count them in the "material remaining" calculation.
_STARTING_MATERIAL = (
    8 * PIECE_VALUES_CP[chess.PAWN]   * 2 +  # 8 pawns per side × 2 sides
    2 * PIECE_VALUES_CP[chess.KNIGHT] * 2 +  # 2 knights per side × 2 sides
    2 * PIECE_VALUES_CP[chess.BISHOP] * 2 +
    2 * PIECE_VALUES_CP[chess.ROOK]   * 2 +
    1 * PIECE_VALUES_CP[chess.QUEEN]  * 2
)  # = 7800 centipawns total


# =========================
# GAME PHASE DETECTION  (item 12)
# =========================
def get_game_phase(board):
    """How far into the game are we? Returns 1.0 = opening, 0.0 = endgame.

    We measure this by counting how much material is still on the board.
    As pieces get traded off, the value drops toward 0.0 (endgame).
    This lets evaluation weights smoothly blend between game phases instead
    of snapping between "opening mode" and "endgame mode" at a hard boundary.
    """
    remaining = sum(
        PIECE_VALUES_CP[p.piece_type]
        for p in board.piece_map().values()
        if p.piece_type != chess.KING   # kings never leave; exclude them
    )
    # Clamp to [0, 1]: promotions can briefly push past the starting total
    return min(1.0, remaining / _STARTING_MATERIAL)


# =========================
# PIECE-SQUARE TABLES (PST)
# =========================
# Each table has 64 values — one per board square (a1=index 0, h8=index 63)
# from White's perspective. For Black pieces, we mirror the square vertically
# (flip the rank) before looking up, so the same table works for both colors.
#
# Why PSTs? They encode positional wisdom without any search:
#   - Pawns are rewarded for advancing toward promotion
#   - Knights are rewarded for the center (where they control the most squares)
#   - Kings are rewarded for hiding behind pawns in the middlegame

_PST_PAWN = [
     0,  0,  0,  0,  0,  0,  0,  0,   # rank 1 — pawns start on rank 2, can't be here
     5, 10, 10,-20,-20, 10, 10,  5,   # rank 2 — starting rank
     5, -5,-10,  0,  0,-10, -5,  5,   # rank 3
     0,  0,  0, 20, 20,  0,  0,  0,   # rank 4 — central push bonus
     5,  5, 10, 25, 25, 10,  5,  5,   # rank 5 — advanced pawn threatens promotion
    10, 10, 20, 30, 30, 20, 10, 10,   # rank 6
    50, 50, 50, 50, 50, 50, 50, 50,   # rank 7 — one step from queening!
     0,  0,  0,  0,  0,  0,  0,  0,   # rank 8 — unreachable for pawns
]
_PST_KNIGHT = [
    -50,-40,-30,-30,-30,-30,-40,-50,   # corners are terrible; knight controls few squares
    -40,-20,  0,  5,  5,  0,-20,-40,
    -30,  5, 10, 15, 15, 10,  5,-30,
    -30,  0, 15, 20, 20, 15,  0,-30,   # center squares give knights the most reach
    -30,  5, 15, 20, 20, 15,  5,-30,
    -30,  0, 10, 15, 15, 10,  0,-30,
    -40,-20,  0,  0,  0,  0,-20,-40,
    -50,-40,-30,-30,-30,-30,-40,-50,
]
_PST_BISHOP = [
    -20,-10,-10,-10,-10,-10,-10,-20,
    -10,  5,  0,  0,  0,  0,  5,-10,
    -10, 10, 10, 10, 10, 10, 10,-10,
    -10,  0, 10, 10, 10, 10,  0,-10,
    -10,  5,  5, 10, 10,  5,  5,-10,
    -10,  0,  5, 10, 10,  5,  0,-10,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -20,-10,-10,-10,-10,-10,-10,-20,
]
_PST_ROOK = [
     0,  0,  0,  5,  5,  0,  0,  0,
    -5,  0,  0,  0,  0,  0,  0, -5,
    -5,  0,  0,  0,  0,  0,  0, -5,
    -5,  0,  0,  0,  0,  0,  0, -5,
    -5,  0,  0,  0,  0,  0,  0, -5,
    -5,  0,  0,  0,  0,  0,  0, -5,
     5, 10, 10, 10, 10, 10, 10,  5,   # 7th rank rook threatens the back rank strongly
     0,  0,  0,  0,  0,  0,  0,  0,
]
_PST_QUEEN = [
    -20,-10,-10, -5, -5,-10,-10,-20,
    -10,  0,  5,  0,  0,  0,  0,-10,
    -10,  5,  5,  5,  5,  5,  0,-10,
      0,  0,  5,  5,  5,  5,  0, -5,
     -5,  0,  5,  5,  5,  5,  0, -5,
    -10,  0,  5,  5,  5,  5,  0,-10,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -20,-10,-10, -5, -5,-10,-10,-20,
]

# The king has two tables because its ideal location changes dramatically:
# In the middlegame, the king should hide behind pawns after castling.
# In the endgame, the king should march to the center to support passed pawns.
_PST_KING_MIDDLEGAME = [
     20, 30, 10,  0,  0, 10, 30, 20,   # rank 1: castled positions (g1 / c1) are safest
     20, 20,  0,  0,  0,  0, 20, 20,
    -10,-20,-20,-20,-20,-20,-20,-10,
    -20,-30,-30,-40,-40,-30,-30,-20,
    -30,-40,-40,-50,-50,-40,-40,-30,   # center = very dangerous in middlegame
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
]
_PST_KING_ENDGAME = [
    -50,-30,-30,-30,-30,-30,-30,-50,
    -30,-30,  0,  0,  0,  0,-30,-30,
    -30,-10, 20, 30, 30, 20,-10,-30,
    -30,-10, 30, 40, 40, 30,-10,-30,   # center = great in the endgame
    -30,-10, 30, 40, 40, 30,-10,-30,
    -30,-10, 20, 30, 30, 20,-10,-30,
    -30,-20,-10,  0,  0,-10,-20,-30,
    -50,-40,-30,-20,-20,-30,-40,-50,
]

PST_TABLES = {
    chess.PAWN:   _PST_PAWN,
    chess.KNIGHT: _PST_KNIGHT,
    chess.BISHOP: _PST_BISHOP,
    chess.ROOK:   _PST_ROOK,
    chess.QUEEN:  _PST_QUEEN,
    # King table not included here — it's blended per-phase in evaluate_pst()
}


# =========================
# EXPLICIT CHESS EVALUATION TERMS  (item 11)
# =========================
# PSTs capture general positional preference but miss specific structural patterns.
# These functions add bonuses/penalties for well-known chess concepts.

def _pawn_file_counts(board, color):
    """Count how many pawns of 'color' are on each file (0=a to 7=h).
    Returns a dict: {file_number: count}."""
    counts = {}
    for sq in board.pieces(chess.PAWN, color):
        f = chess.square_file(sq)
        counts[f] = counts.get(f, 0) + 1
    return counts

def _pawn_files(board, color):
    """Return the set of file numbers that have at least one pawn of 'color'."""
    return {chess.square_file(sq) for sq in board.pieces(chess.PAWN, color)}


def evaluate_pawn_structure(board):
    """Centipawn score for pawn structure weaknesses and strengths. White-positive.

    Doubled pawns (-20cp per extra): two pawns on the same file block each other
    and can't defend each other diagonally.

    Isolated pawns (-15cp each): a pawn with no friendly pawns on adjacent files
    can only be defended by pieces, not by other pawns — a persistent weakness.

    Passed pawns (+20 to +80cp): a pawn with no enemy pawns blocking it or
    attacking its path to promotion is extremely dangerous. Bonus scales with
    how advanced it is (closer to the 8th rank = bigger bonus).
    """
    score = 0
    for color in (chess.WHITE, chess.BLACK):
        sign = 1 if color == chess.WHITE else -1
        enemy = not color

        my_counts    = _pawn_file_counts(board, color)
        enemy_files  = _pawn_files(board, enemy)

        for f, count in my_counts.items():
            # Doubled pawn penalty
            if count > 1:
                score += sign * (-20 * (count - 1))

            # Isolated pawn penalty: no friendly pawns on either adjacent file
            if (f - 1) not in my_counts and (f + 1) not in my_counts:
                score += sign * (-15 * count)

        # Passed pawn bonus
        for sq in board.pieces(chess.PAWN, color):
            f    = chess.square_file(sq)
            rank = chess.square_rank(sq)

            # The ranks "ahead" of this pawn (the direction it's trying to promote)
            if color == chess.WHITE:
                front_ranks = range(rank + 1, 8)
            else:
                front_ranks = range(rank - 1, -1, -1)

            passed = True
            for fr in front_ranks:
                for df in (-1, 0, 1):   # check pawn's file and one file to each side
                    ef = f + df
                    if 0 <= ef <= 7:
                        ep = board.piece_at(chess.square(ef, fr))
                        if ep and ep.piece_type == chess.PAWN and ep.color == enemy:
                            passed = False
                            break
                if not passed:
                    break

            if passed:
                # Bonus grows the closer the pawn is to promotion
                advancement = rank if color == chess.WHITE else (7 - rank)
                score += sign * (20 + advancement * 10)

    return score


def evaluate_rook_open_file(board):
    """Centipawn bonus for rooks on open or semi-open files. White-positive.

    Open file (no pawns of any color): the rook can control the entire file (+20cp).
    Semi-open file (only enemy pawns, no friendly pawns): still very useful (+10cp).
    """
    score = 0
    for color in (chess.WHITE, chess.BLACK):
        sign = 1 if color == chess.WHITE else -1
        my_files    = _pawn_files(board, color)
        enemy_files = _pawn_files(board, not color)

        for sq in board.pieces(chess.ROOK, color):
            f = chess.square_file(sq)
            if f not in my_files and f not in enemy_files:
                score += sign * 20   # fully open file
            elif f not in my_files:
                score += sign * 10   # semi-open file (only enemy pawns)

    return score


def evaluate_bishop_pair(board):
    """Centipawn bonus for having both bishops (+30cp). White-positive.

    One bishop covers light squares, the other covers dark squares. Together they
    complement each other perfectly. In open positions this is worth ~30 centipawns.
    """
    score = 0
    for color in (chess.WHITE, chess.BLACK):
        sign = 1 if color == chess.WHITE else -1
        if len(list(board.pieces(chess.BISHOP, color))) >= 2:
            score += sign * 30
    return score


def evaluate_mobility(board):
    """Centipawn bonus for piece mobility — how many squares each piece can reach.
    White-positive. Kings are excluded (king mobility is a safety concern, not an asset).

    WHY MOBILITY MATTERS:
    PSTs assign a fixed positional bonus per square, but they can't tell whether a
    bishop is actually active on an open diagonal or stuck behind its own pawns.
    A blocked bishop and a sweeping bishop on a long diagonal score identically in
    the PST, but are worth very different amounts in practice. Mobility fixes this.

    Bonus per reachable square by piece type (centipawns):
      Knight 4cp — knights are most sensitive to mobility; a rim knight controls
                   only 2–3 squares while a central knight controls up to 8.
      Bishop 3cp — active diagonals are the bishop's main strength.
      Rook   2cp — rooks naturally have many squares on open files; smaller bonus
                   avoids over-rewarding rooks that are only mobile because the
                   position is wide open, not because they are well placed.
      Queen  1cp — queen is always mobile; tiny bonus to avoid dominating the score.
    """
    _MOBILITY_BONUS = {
        chess.KNIGHT: 4,
        chess.BISHOP: 3,
        chess.ROOK:   2,
        chess.QUEEN:  1,
    }
    score = 0
    for color in (chess.WHITE, chess.BLACK):
        sign = 1 if color == chess.WHITE else -1
        own  = board.occupied_co[color]   # bitmask of squares occupied by this color
        for piece_type, bonus in _MOBILITY_BONUS.items():
            for sq in board.pieces(piece_type, color):
                # Attacked squares minus squares occupied by own pieces = reachable squares.
                # & chess.BB_ALL masks to 64 bits so Python's arbitrary-precision ~own
                # is correctly interpreted as the 64-bit complement.
                atk      = int(board.attacks(sq))
                mobility = bin(atk & ~own & chess.BB_ALL).count('1')
                score   += sign * mobility * bonus
    return score


def evaluate_king_safety(board, phase):
    """Centipawn penalty for open files near the king in the middlegame. White-positive.

    An open file next to your king lets enemy rooks threaten it directly.
    This penalty fades toward zero as the game enters the endgame (phase → 0),
    because in the endgame an active king is an asset, not a liability.

    phase: 1.0 = opening/middlegame, 0.0 = endgame (from get_game_phase)
    """
    if phase < 0.3:
        return 0   # endgame: ignore king safety

    score = 0
    for color in (chess.WHITE, chess.BLACK):
        sign = 1 if color == chess.WHITE else -1
        king_sq = board.king(color)
        if king_sq is None:
            continue
        king_file = chess.square_file(king_sq)
        my_pawn_files = _pawn_files(board, color)

        # Penalize every open file touching the king's position
        for f in range(max(0, king_file - 1), min(8, king_file + 2)):
            if f not in my_pawn_files:
                score += sign * int(-15 * phase)

    return score


# =========================
# PST EVALUATION
# =========================
def evaluate_pst(board):
    """Material + piece-square tables + explicit chess terms. Returns pawn units.
    Positive = White is better, negative = Black is better.

    The king table is blended between the middlegame and endgame versions
    based on the current game phase, so the king transitions naturally from
    "hide behind pawns" behavior to "march to the center" behavior.
    """
    phase = get_game_phase(board)
    score = 0

    for sq, piece in board.piece_map().items():
        cp = PIECE_VALUES_CP[piece.piece_type]

        # For Black pieces, mirror the square vertically so the same PST applies
        if piece.color == chess.WHITE:
            table_sq = sq
        else:
            table_sq = (7 - sq // 8) * 8 + (sq % 8)

        if piece.piece_type == chess.KING:
            # Blend between middlegame and endgame king tables based on phase
            mg = _PST_KING_MIDDLEGAME[table_sq]
            eg = _PST_KING_ENDGAME[table_sq]
            pst_bonus = mg * phase + eg * (1.0 - phase)
        else:
            pst_bonus = PST_TABLES[piece.piece_type][table_sq]

        if piece.color == chess.WHITE:
            score += cp + pst_bonus
        else:
            score -= cp + pst_bonus

    # Add explicit chess knowledge on top of material+PST  (item 11)
    score += evaluate_pawn_structure(board)
    score += evaluate_rook_open_file(board)
    score += evaluate_bishop_pair(board)
    score += evaluate_king_safety(board, phase)
    score += evaluate_mobility(board)

    return score / 100.0   # centipawns → pawn units


# =========================
# DYNAMIC NN BLEND RATIO  (item 7)
# =========================
# The neural network starts with random weights and knows nothing. We give it
# very little influence at first (15%), then increase trust as it trains on more
# games. We cap at 50% so the PST always provides a stable floor.
#
# WHY THE THRESHOLDS ARE LONG (25 / 150 / 500 games):
# The PST is a hand-tuned, reliable evaluation that has been correct since day one.
# The NN is noisy and player-specific — it can accidentally learn bad patterns from
# early games before it has enough data to generalise. Keeping the PST dominant for
# the first ~150 games means the AI plays principled chess even while it is still
# learning, and only leans on NN patterns once it has genuinely earned that trust.
#
# WHY 50% IS THE MAXIMUM:
# We never want the NN to fully take over, because the PST provides knowledge that
# the NN may forget or contradict (e.g. material counting, king safety). Keeping
# PST at 50% minimum is a permanent safety floor that prevents the AI from drifting
# into completely unprincipled play no matter how many games it has seen.
#
# PRACTICAL GROWTH TIMELINE (at ~2 games/day):
#   Day 1–12  (~25 games):  15% NN — almost entirely PST-driven
#   Day 12–75 (~150 games): 25% NN — NN contributes but PST leads
#   Day 75+   (~500 games): 40% NN — meaningful NN influence
#   Day 250+  (500+ games): 50% NN — fully mature, still PST-balanced

_nn_weight = 0.15  # updated after each training session via set_nn_weight()


def get_nn_weight(games_played):
    """Return the appropriate NN contribution fraction for the given number of games.

    games_played is the count of completed training sessions in LearningTracker.
    """
    if games_played < 25:
        return 0.15   # fresh: network doesn't know much yet
    elif games_played < 150:
        return 0.25   # improving: small but meaningful contribution
    elif games_played < 500:
        return 0.40   # well trained: significant contribution
    else:
        return 0.50   # maximum: PST still has 50% say (never go below this)


def set_nn_weight(weight):
    """Update the active NN blend ratio. Called by learning.py after each training session."""
    global _nn_weight
    _nn_weight = max(0.0, min(0.50, weight))   # clamp: never below 0, never above 50%


def refresh_nn_weight():
    """Query the DB for the total game count and update the NN blend accordingly.
    Safe to call at startup (catches all exceptions if DB isn't ready yet)."""
    try:
        # Lazy: Django ORM not ready at module import time — requires django.setup() first.
        # evaluation.py is imported early in the chain (by minimax.py), well before setup() runs.
        from .models import LearningTracker
        games = LearningTracker.objects.count()
        set_nn_weight(get_nn_weight(games))
    except Exception:
        pass


# =========================
# FULL HYBRID EVALUATION
# =========================
def evaluate(board):
    """Combined PST + neural network evaluation. Called at every minimax leaf node.

    The neural network runs via the numpy inference cache in neural_net.py — no
    PyTorch overhead, just raw numpy matrix multiplication (~10-30x faster than torch).
    The NN weight grows as the AI plays more games (see _nn_weight above).
    """
    # Lazy: guards against a future circular import if neural_net ever imports evaluation.
    # neural_net.py currently does not import evaluation.py, but this deferral keeps the
    # two modules loosely coupled and prevents a hard-to-debug startup cycle if that changes.
    from .neural_net import nn_inference
    pst    = evaluate_pst(board)
    nn_val = nn_inference(board)
    return pst + nn_val * _nn_weight
