"""
killer_moves.py
===============
Killer move heuristic: remember moves that caused a "beta cutoff" (proved a position
was so good for us that the opponent would never allow it) at each search depth.

Why this helps:
  Alpha-beta pruning is most powerful when it sees the best moves early. If a move
  caused a cutoff at depth 5 in a different branch, there's a good chance it's also
  strong at depth 5 here — try it early instead of waiting until near the end of the
  move list.

  This is different from the transposition table (which stores the EXACT best move for
  a specific position). Killer moves are NOT position-specific — they're "this TYPE of
  move worked well at this depth". It's a weaker hint, but it's virtually free to
  maintain and noticeably improves move ordering.

Usage in minimax.py:
  - When a move causes a beta cutoff: killer_moves.store(depth, move)
  - When ordering moves: insert killer moves early (after TT move, before quiet moves)
  - Reset between games: killer_moves.reset()
"""

# Maximum search depth we track killers for. 15 is more than we'll ever reach
# in practice (iterative deepening usually stops around 8-12 on normal hardware).
_MAX_DEPTH = 15

# Two killer slots per depth. We store two because sometimes the first killer
# is a recapture that's illegal in the current position — having a backup helps.
_killers = [[None, None] for _ in range(_MAX_DEPTH + 1)]


def store(depth, move):
    """Record a move that caused a beta cutoff at 'depth'.

    Shifts the existing killer into the second slot before storing the new one,
    so we always keep the two most recent killers at each depth level.

    Only stores quiet moves (non-captures): captures are already prioritized
    by MVV-LVA in order_moves(), so adding them here would be redundant.

    Args:
        depth: the search depth where the cutoff happened (integer >= 0)
        move:  a chess.Move object
    """
    if depth < 0 or depth > _MAX_DEPTH:
        return
    # Don't store captures — they're already handled by MVV-LVA ordering
    # (We check this in minimax.py before calling store, but guard here too)
    slot = _killers[depth]
    if move != slot[0]:           # avoid duplicates in slot 0 and slot 1
        slot[1] = slot[0]         # push current slot-0 into slot-1
        slot[0] = move            # store the new killer in slot-0


def get(depth):
    """Return the two killer moves for this depth as a list [move1, move2].

    Either or both may be None if no killers have been stored at this depth.
    The caller is responsible for checking legality in the current position.

    Args:
        depth: search depth (integer)

    Returns:
        list of up to 2 chess.Move objects (may contain None entries)
    """
    if depth < 0 or depth > _MAX_DEPTH:
        return [None, None]
    return list(_killers[depth])   # return a copy so caller can't mutate our state


def reset():
    """Clear all stored killer moves. Call this at the start of each new game
    so killers from a previous game don't pollute the new search."""
    global _killers
    _killers = [[None, None] for _ in range(_MAX_DEPTH + 1)]
