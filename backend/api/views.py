from django.shortcuts import render
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.authentication import TokenAuthentication
from django.views.decorators.csrf import csrf_exempt
from .minimax import get_ai_move, store_position, analyze_player_move, train_model, reset_tt, stop_ponder, log_issue
from .models import Player, LearningTracker, OpeningMemory, IssueLog
from .difficulty import adapt_player
import chess
import chess.polyglot
import threading
from . import killer_moves, thermal_monitor


# Public health check — used by the frontend to confirm the backend is ready before showing the game
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok"})


def _start_pool_warmup():
    """Spawn a background thread that runs a depth-3 search on the start position.
    This forces the ProcessPoolExecutor to actually spawn its worker processes so
    they're ready before the player's first move. Called by both new_game and warmup_pool."""
    def _run():
        try:
            # Lazy: _pool is a mutable module-level variable that starts as None and is set
            # by _get_pool() on first use; importing here captures the live value at thread-run
            # time rather than the stale None that exists at module-load time.
            from .minimax import get_ranked_moves, _pool as pool
            get_ranked_moves(chess.Board(), 3)
            thermal_monitor.set_pool(pool)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


# Clears the transposition table and starts the worker pool on the first ever game.
# Called when the user clicks "Play vs AI" to prepare the pool before the first move.
@api_view(["POST"])
@permission_classes([AllowAny])
def new_game(_request):
    # Lazy: _pool starts as None at module load and is set by _get_pool() on first use;
    # must re-import at call time to read the live value rather than the stale None.
    from .minimax import _pool
    stop_ponder()
    reset_tt()              # discard cached positions from the previous game
    killer_moves.reset()    # clear killer move hints from the previous game
    thermal_monitor.reset() # reset freq baseline and worker count for the new game
    if _pool is None:
        _start_pool_warmup()
    return Response({"status": "ok"})


# Fires pool startup in the background and returns immediately so the UI isn't blocked.
# Called when the user selects "Play vs AI" so the workers are ready before move 1.
@api_view(["POST"])
@permission_classes([AllowAny])
def warmup_pool(request):
    _start_pool_warmup()
    return Response({"status": "warming"})


# Returns a Django auth token for the local user.
# The backend only listens on 127.0.0.1 (localhost) so only the Tauri app can reach it —
# the token is a second layer to prevent any browser extension or local script from
# calling protected endpoints (train, ai-move) without going through the frontend.
@api_view(["GET"])
@permission_classes([AllowAny])
def local_token(request):
    # Lazy: Django ORM models require django.setup() to have been called before access.
    # A top-level import would run before setup() completes and crash on startup.
    from django.contrib.auth.models import User
    from rest_framework.authtoken.models import Token
    user, _ = User.objects.get_or_create(username='local')
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"token": f"Token {token.key}"})


# Token-protected: only the frontend (which has the local token) can trigger training.
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@csrf_exempt
def train(request):
    game_log         = request.data.get("game_log", [])
    won              = request.data.get("won", False)
    player_name      = request.data.get("player", "Player1")
    ai_color         = request.data.get("ai_color", "b")
    difficulty = request.data.get("difficulty", "")

    player, _ = Player.objects.get_or_create(name=player_name, defaults={"elo": 1200})
    # Use the server-side aggression value — the frontend doesn't track this
    player_aggression = player.aggression
    # Walk every position in the game and record the move + outcome in PositionMemory.
    # Positions from the first 6 full moves are also stored in OpeningMemory so the
    # AI can build a player-specific opening repertoire over time.
    # Track parse successes and failures so we can log when training ran on a
    # partial game log. Without this, silently skipped positions look identical
    # to a full training run — the AI appears to learn nothing and there's no
    # indication of what went wrong.
    positions_ok = 0
    positions_failed = 0
    for entry in game_log:
        fen = entry.get("fen")
        move_uci = entry.get("move", {}).get("uci")
        if fen and move_uci:
            try:
                board = chess.Board(fen)
                move = chess.Move.from_uci(move_uci)
                store_position(board, move, won)

                if board.fullmove_number <= 6:
                    opening, created = OpeningMemory.objects.get_or_create(
                        player=player,
                        hash=str(chess.polyglot.zobrist_hash(board)),
                        move=move_uci,
                        defaults={"fen": fen, "move_number": board.fullmove_number},
                    )
                    if not created:
                        opening.times_used += 1
                    if won:
                        opening.wins += 1
                    opening.save()
                positions_ok += 1
            except Exception as e:
                positions_failed += 1
                log_issue("training_position_error", fen=fen, move=str(move_uci), detail=str(e))

    if positions_failed > 0:
        log_issue("training_degraded",
                  detail=f"{positions_ok}/{positions_ok + positions_failed} positions trained successfully")

    # Run the neural network training pass on all positions from this game
    train_model(game_log, won, ai_color=ai_color,
                opponent_elo=player.elo,
                difficulty=difficulty,
                player_elo_snapshot=player.elo,
                player_aggression=player_aggression)

    # Per-game aggression update for medium/hard/expert (easy mode does it per-move in ai_move).
    # won=True means AI won → player struggled → decrease aggression.
    # won=False means AI lost → player played well → increase aggression.
    if difficulty != "easy":
        adapt_player(player, -1 if won else 1)

    # Update player ELO: won=True means the AI won, so the player lost (result=0.0)
    last_tracker = LearningTracker.objects.order_by('-timestamp').first()
    ai_elo = last_tracker.ai_elo if last_tracker else 1200
    player_result = 0.0 if won else 1.0
    player.update_elo(ai_elo, player_result)

    return Response({"status": "trained", "player_elo": player.elo, "ai_elo": ai_elo})


# Returns the player's name and ELO. Defaults to 1200 (standard starting ELO) if not found.
@api_view(["GET"])
@permission_classes([AllowAny])
def player_stats(request):
    name = request.query_params.get("player", "Player1")
    try:
        player = Player.objects.get(name=name)
        player_elo = player.elo
    except Player.DoesNotExist:
        player_elo = 1200

    last_tracker = LearningTracker.objects.order_by('-timestamp').first()
    ai_elo = last_tracker.ai_elo if last_tracker else 1200

    return Response({"name": name, "elo": player_elo, "ai_elo": ai_elo})


# Returns recent IssueLog entries for debugging. Filterable by type, capped at 500.
@api_view(["GET"])
@permission_classes([AllowAny])
def issue_log(request):
    limit      = min(int(request.query_params.get("limit", 50)), 500)
    issue_type = request.query_params.get("type", None)
    qs = IssueLog.objects.order_by('-timestamp')
    if issue_type:
        qs = qs.filter(issue_type=issue_type)
    rows = qs[:limit]
    return Response([
        {
            "id":         r.id,
            "timestamp":  r.timestamp.isoformat(),
            "issue_type": r.issue_type,
            "move":       r.move,
            "fen":        r.fen,
            "detail":     r.detail,
            "difficulty": r.difficulty,
        }
        for r in rows
    ])


def frontend(request):
    return render(request, "index.html")

# Token-protected: the frontend sends the full move history so the backend can
# reconstruct the exact board state independently, rather than trusting the FEN
# sent by the client. This prevents subtle desync bugs (e.g. en passant state mismatch).
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@csrf_exempt
def ai_move(request):
    try:
        data = request.data

        fen = data.get("fen")
        difficulty = data.get("difficulty", "easy")
        player_name = data.get("player", "Player1")

        player, _ = Player.objects.get_or_create(name=player_name, defaults={"elo": 1200})

        moves_uci = data.get("moves", [])
        if moves_uci:
            # Replay the entire move history from the starting position.
            # This gives us the correct castling rights, en passant state, and half-move clock —
            # things that can't be reliably encoded in the FEN if the client has a bug.
            board = chess.Board()
            for uci in moves_uci:
                try:
                    board.push(chess.Move.from_uci(uci))
                except Exception as e:
                    detail = f"Failed at {uci} (move #{board.fullmove_number}): {e}"
                    print(f"MOVE REPLAY ERROR: {detail}")
                    log_issue("move_replay_error", move=uci, fen=fen,
                              detail=detail, difficulty=difficulty)
                    board = chess.Board(fen)   # replay failed — fall back to client FEN
                    break
            # Validate that the replayed board matches the client's FEN (first 4 fields:
            # piece placement, active color, castling rights, en passant target).
            # Ignoring halfmove clock and fullmove number to avoid spurious mismatches.
            board_core = ' '.join(board.fen().split()[:4])
            fen_core   = ' '.join(chess.Board(fen).fen().split()[:4])
            if board_core != fen_core:
                detail = f"moves→{board_core}  fen→{fen_core}"
                print(f"BOARD MISMATCH — falling back to client FEN\n  {detail}")
                log_issue("board_mismatch", fen=fen, detail=detail, difficulty=difficulty)
                board = chess.Board(fen)
        else:
            board = chess.Board(fen)

        move = get_ai_move(board, difficulty, player)

        # Final safety check: block any illegal move before it reaches the client
        if move is None or move not in board.legal_moves:
            if move is not None:
                print(f"ILLEGAL MOVE BLOCKED IN VIEW: {move.uci()} on {board.fen()}")
                log_issue("illegal_move_view", move=move.uci(), fen=board.fen(), difficulty=difficulty)
            else:
                log_issue("null_move_returned", fen=board.fen(), difficulty=difficulty)
            return Response({"move": None})

        # In easy mode, compare the player's previous move against the AI's best move
        # and optionally return feedback (used for tutorial-style hints)
        analysis = None
        prev_fen = data.get("prev_fen")
        player_move_data = data.get("player_move")
        if difficulty == "easy" and prev_fen and player_move_data:
            try:
                prev_board = chess.Board(prev_fen)
                pm = chess.Move.from_uci(player_move_data["from"] + player_move_data["to"])
                analysis = analyze_player_move(prev_board, pm)
                if analysis:
                    # Nudge aggression based on move quality: good move → up, mistake → down.
                    # Per-move signal is fine-grained, so we only do this in easy mode where
                    # analyze_player_move runs. Other difficulties use the per-game signal in train().
                    adapt_player(player, -1 if analysis.get("mistake") else 1)
            except Exception:
                pass

        return Response({
            "move": {
                "uci": move.uci(),
                "from": chess.square_name(move.from_square),
                "to": chess.square_name(move.to_square),
                "san": board.san(move),       # Standard Algebraic Notation e.g. "Nf3"
                "is_capture": board.is_capture(move),
                "is_check": board.gives_check(move),
            },
            "analysis": analysis,
        })

    except Exception as e:
        print("AI ERROR:", e)
        return Response({"error": str(e)}, status=500)
