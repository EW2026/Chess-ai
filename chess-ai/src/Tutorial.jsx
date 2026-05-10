import { useState, useRef, useEffect, useCallback } from "react";
import { Chess } from "chess.js";
import OPENINGS from "./openings";
import SPECIAL_MOVES from "./specialMoves";
import PieceTutorial from "./PieceTutorial";
import "./Tutorial.css";

import wp from "./assets/pieces/wp.svg";
import wr from "./assets/pieces/wr.svg";
import wn from "./assets/pieces/wn.svg";
import wb from "./assets/pieces/wb.svg";
import wq from "./assets/pieces/wq.svg";
import wk from "./assets/pieces/wk.svg";
import bp from "./assets/pieces/bp.svg";
import br from "./assets/pieces/br.svg";
import bn from "./assets/pieces/bn.svg";
import bb from "./assets/pieces/bb.svg";
import bq from "./assets/pieces/bq.svg";
import bk from "./assets/pieces/bk.svg";

const PIECES = { wp, wr, wn, wb, wq, wk, bp, br, bn, bb, bq, bk };

export default function Tutorial({ onBack }) {
  // phase controls which screen is visible:
  //   "select"    → opening/special-move card grid
  //   "colorPick" → choose White or Black for the selected opening
  //   "play"      → interactive board following the lesson's move sequence
  //   "complete"  → congratulations screen after all moves are done
  const [pieceMode, setPieceMode] = useState(false);
  const [phase, setPhase] = useState("select");
  const [opening, setOpening] = useState(null);          // the resolved opening being played
  const [pendingOpening, setPendingOpening] = useState(null);  // selected before color is chosen
  const [game, setGame] = useState(null);
  const [moveIndex, setMoveIndex] = useState(0);         // index into opening.moves[]
  const [explanation, setExplanation] = useState("");     // text shown in the explanation panel
  const [feedback, setFeedback] = useState(null);        // null | "wrong" | "right"
  const [tutComplete, setTutComplete] = useState(false); // true when last move done; shows Finish button
  const [dragging, setDragging] = useState(null);
  const [dragPos, setDragPos] = useState({ x: 0, y: 0 });
  const [lastMove, setLastMove] = useState(null);        // { from, to } for golden highlight
  const [hintSquares, setHintSquares] = useState([]);    // squares highlighted in green as hints
  const [opponentThinking, setOpponentThinking] = useState(false);

  const boardRef = useRef(null);
  const opponentTimerRef = useRef(null);   // holds the setTimeout ID so we can cancel it

  // flipped = true when the player is Black so the board renders from Black's perspective
  const playerColor = opening?.playerColor ?? "b";
  const flipped = playerColor === "b";

  // ── Opening initialization ────────────────────────────────────────────────────
  function startOpening(op, colorOverride) {
    const color = colorOverride ?? op.defaultColor ?? op.playerColor ?? "w";

    // Some openings (castling, en passant, promotion) need a different starting FEN
    // for each color because the positions are fundamentally different per side.
    // If startFen is an object, pick the entry matching the chosen color.
    const startFen = op.startFen && typeof op.startFen === "object"
      ? op.startFen[color]
      : op.startFen;

    // Same for moves: if moves is an object keyed by color, pick the right array.
    // Standard openings have a single shared move list (Array.isArray returns true).
    const moves = Array.isArray(op.moves) ? op.moves : op.moves[color];

    const resolved = { ...op, playerColor: color, startFen, moves };
    setOpening(resolved);
    setGame(startFen ? new Chess(startFen) : new Chess());
    setMoveIndex(0);
    setLastMove(null);
    setExplanation("");
    setFeedback(null);
    setHintSquares([]);
    setTutComplete(false);
    setPhase("play");
  }

  // After the play phase mounts, auto-play any opponent moves that come before the
  // player's first move. For example, in a Black-side opening, White plays 1.e4
  // automatically before the player is expected to respond.
  useEffect(() => {
    if (phase !== "play" || !opening || !game) return;
    scheduleNextOpponentMove(game, moveIndex);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, opening]);

  // ── Opponent auto-play ────────────────────────────────────────────────────────
  // This function is called whenever the current move belongs to the opponent.
  // It plays the move after a short delay (simulating thinking time), then either:
  //   - Marks the lesson complete if there are no more moves
  //   - Shows a hint for the player's next move
  //   - Calls itself recursively if the next move is also the opponent's
  //     (consecutive opponent moves happen in some opening lines)
  function scheduleNextOpponentMove(currentGame, currentIndex) {
    // Guard against a malformed opening where moves wasn't resolved to an array —
    // startOpening() resolves per-color move objects, but if the data shape is wrong
    // opening.moves could still be an object, which would crash on index access.
    if (!opening || !Array.isArray(opening.moves)) return;
    const move = opening.moves[currentIndex];
    if (!move) return;

    if (move.side !== playerColor) {
      setOpponentThinking(true);
      clearTimeout(opponentTimerRef.current);
      opponentTimerRef.current = setTimeout(() => {
        const copy = new Chess(currentGame.fen());
        const result = copy.move({ from: move.from, to: move.to, promotion: "q" });
        if (!result) return;
        setGame(copy);
        setLastMove({ from: move.from, to: move.to });
        setExplanation(move.explanation);
        setOpponentThinking(false);

        const nextIndex = currentIndex + 1;
        setMoveIndex(nextIndex);

        if (nextIndex >= opening.moves.length) {
          setTutComplete(true);
        } else {
          const nextMove = opening.moves[nextIndex];
          if (nextMove && nextMove.side === playerColor) {
            // Highlight the from-square of the player's next expected move as a hint
            setHintSquares([nextMove.from]);
          } else {
            // Another opponent move follows — schedule it recursively
            scheduleNextOpponentMove(copy, nextIndex);
          }
        }
      }, 900);
    } else {
      // Player's turn: just highlight the expected from-square
      setHintSquares([move.from]);
    }
  }

  // ── Player move validation ────────────────────────────────────────────────────
  // The tutorial only accepts the exact move defined in opening.moves[moveIndex].
  // Any other legal move is rejected with a brief "wrong" flash so the player
  // learns the specific sequence rather than improvising.
  function handlePlayerMove(from, to) {
    if (!opening || opponentThinking) return;
    const expected = opening.moves[moveIndex];
    if (!expected || expected.side !== playerColor) return;

    if (from !== expected.from || to !== expected.to) {
      setFeedback("wrong");
      setTimeout(() => setFeedback(null), 800);
      return;
    }

    const copy = new Chess(game.fen());
    const result = copy.move({ from, to, promotion: "q" });
    if (!result) return;

    setGame(copy);
    setLastMove({ from, to });
    setExplanation(expected.explanation);
    setFeedback("right");
    setHintSquares([]);
    setTimeout(() => setFeedback(null), 600);

    const nextIndex = moveIndex + 1;
    setMoveIndex(nextIndex);

    if (nextIndex >= opening.moves.length) {
      setTutComplete(true);
    } else {
      scheduleNextOpponentMove(copy, nextIndex);
    }
  }

  // ── Drag handlers ─────────────────────────────────────────────────────────────
  // Same drag-and-drop pattern as App.jsx: window-level listeners so releasing
  // outside the board doesn't leave the piece stuck to the cursor.

  function handleMouseDown(e, square) {
    if (!game || opponentThinking) return;
    const piece = game.get(square);
    if (!piece) return;
    if (piece.color !== playerColor) return;  // can't drag opponent's pieces

    setDragging({ from: square, piece: PIECES[piece.color + piece.type] });
    setDragPos({ x: e.clientX, y: e.clientY });
  }

  function handleRightClick(e) {
    e.preventDefault();
    setDragging(null);
  }

  // useCallback memoizes the handlers so the useEffect dependency array is stable —
  // without this the effect would re-register listeners on every render.
  const onMouseMove = useCallback((e) => {
    if (!dragging) return;
    setDragPos({ x: e.clientX, y: e.clientY });
  }, [dragging]);

  const onMouseUp = useCallback((e) => {
    if (!dragging) return;

    const rect = boardRef.current?.getBoundingClientRect();
    if (rect) {
      let file, rank;
      if (flipped) {
        // When the board is flipped (playing as Black), mirror both axes to convert
        // screen pixel coordinates back to actual board square coordinates
        file = 7 - Math.floor(((e.clientX - rect.left) / rect.width) * 8);
        rank = Math.floor(((e.clientY - rect.top) / rect.height) * 8);
      } else {
        file = Math.floor(((e.clientX - rect.left) / rect.width) * 8);
        rank = 7 - Math.floor(((e.clientY - rect.top) / rect.height) * 8);
      }

      if (file >= 0 && file < 8 && rank >= 0 && rank < 8) {
        const to = "abcdefgh"[file] + (rank + 1);
        handlePlayerMove(dragging.from, to);
      }
    }

    setDragging(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging, game, opening, moveIndex, opponentThinking, flipped]);

  useEffect(() => {
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [onMouseMove, onMouseUp]);

  // Clear any pending opponent move timer when the component unmounts
  useEffect(() => () => clearTimeout(opponentTimerRef.current), []);

  // ── Board renderer ────────────────────────────────────────────────────────────
  function renderBoard() {
    const board = game.board();
    const rows = flipped ? [...board].reverse() : board;

    // File and rank labels flip when playing as Black (same logic as App.jsx)
    const files = flipped
      ? ['H','G','F','E','D','C','B','A']
      : ['A','B','C','D','E','F','G','H'];
    const ranks = flipped
      ? [1,2,3,4,5,6,7,8]
      : [8,7,6,5,4,3,2,1];

    const boardEl = (
      <div
        ref={boardRef}
        className="tut-board"
        onContextMenu={handleRightClick}
      >
        {rows.map((row, r) => {
          const displayRow = flipped ? [...row].reverse() : row;
          return displayRow.map((square, c) => {
            // fileIdx and rankIdx are the actual board coordinates (0-based),
            // accounting for the board flip when playing as Black
            const fileIdx = flipped ? 7 - c : c;
            const rankIdx = flipped ? r : 7 - r;
            const sq = "abcdefgh"[fileIdx] + (rankIdx + 1);

            // Square color: a1 is dark (fileIdx + rankIdx is even)
            const isDark = (fileIdx + rankIdx) % 2 === 0;
            const isLast = lastMove && (lastMove.from === sq || lastMove.to === sq);
            const isHint = hintSquares.includes(sq);

            let pieceKey = null;
            if (square?.type && square?.color) pieceKey = square.color + square.type;

            return (
              <div
                key={sq}
                className={[
                  "tut-square",
                  isDark ? "tut-dark" : "tut-light",
                  isLast ? "tut-last" : "",
                  isHint ? "tut-hint" : "",
                ].join(" ")}
              >
                {pieceKey && PIECES[pieceKey] && (
                  <img
                    src={PIECES[pieceKey]}
                    className="tut-piece"
                    onMouseDown={(e) => handleMouseDown(e, sq)}
                    draggable={false}
                  />
                )}
              </div>
            );
          });
        })}

        {/* Dragging ghost centered on the cursor */}
        {dragging && (
          <img
            src={dragging.piece}
            className="tut-dragging"
            style={{ left: dragPos.x, top: dragPos.y }}
          />
        )}
      </div>
    );

    return (
      <div className="tut-board-outer">
        <div className="tut-coords-files-row">
          {files.map(f => <span key={f} className="tut-coords-label">{f}</span>)}
        </div>
        <div className="tut-coords-board-row">
          <div className="tut-coords-ranks-col">
            {ranks.map(r => <span key={r} className="tut-coords-label">{r}</span>)}
          </div>
          {boardEl}
          <div className="tut-coords-ranks-col">
            {ranks.map(r => <span key={`r-right-${r}`} className="tut-coords-label">{r}</span>)}
          </div>
        </div>
        <div className="tut-coords-files-row">
          {files.map(f => <span key={`f-bot-${f}`} className="tut-coords-label">{f}</span>)}
        </div>
      </div>
    );
  }

  // ── Screen rendering ──────────────────────────────────────────────────────────

  if (pieceMode) {
    return <PieceTutorial onBack={() => setPieceMode(false)} />;
  }

  if (phase === "select") {
    return (
      <div className="tut-select fade-in">
        <div className="tut-select-header">
          <h1>Opening Tutorial</h1>
          <p>Choose a lesson to study</p>
        </div>

        <div className="tut-section-label">Piece Movements</div>
        <div className="tut-cards tut-cards--special">
          <button
            className="tut-card tut-card--special"
            onClick={() => setPieceMode(true)}
          >
            <span className="tut-card-name">♟ How Pieces Move</span>
            <span className="tut-card-desc">
              Learn each piece's movement rules — including why the knight can
              jump over pieces that block the queen, rook, and bishop.
            </span>
          </button>
        </div>

        <div className="tut-section-label">Special Moves</div>
        <div className="tut-cards tut-cards--special">
          {SPECIAL_MOVES.map((op) => (
            <button
              key={op.key}
              className="tut-card tut-card--special"
              onClick={() => { setPendingOpening(op); setPhase("colorPick"); }}
            >
              <span className="tut-card-name">{op.name}</span>
              <span className="tut-card-desc">{op.description}</span>
            </button>
          ))}
        </div>

        <div className="tut-section-label">Opening Tutorials</div>
        <div className="tut-cards">
          {OPENINGS.map((op) => (
            <button
              key={op.key}
              className="tut-card"
              onClick={() => { setPendingOpening(op); setPhase("colorPick"); }}
            >
              <span className="tut-card-name">{op.name}</span>
              <span className="tut-card-desc">{op.description}</span>
            </button>
          ))}
        </div>

        <button className="tut-back-btn" onClick={onBack}>
          ← Back
        </button>
      </div>
    );
  }

  if (phase === "colorPick" && pendingOpening) {
    // defaultColor is the "recommended" side (usually the side that plays the opening).
    // e.g. the Sicilian Defense is typically shown from Black's perspective.
    const def = pendingOpening.defaultColor ?? pendingOpening.playerColor;
    return (
      <div className="tut-select fade-in">
        <div className="tut-select-header">
          <h1>{pendingOpening.name}</h1>
          <p>{pendingOpening.description}</p>
        </div>

        <div className="tut-colorpick">
          <button
            className={`tut-color-btn tut-color-white${def === "w" ? " recommended" : ""}`}
            onClick={() => startOpening(pendingOpening, "w")}
          >
            <span className="tut-color-icon">&#9812;</span>
            <span className="tut-color-label">Play as White</span>
            {def === "w" && <span className="tut-color-tag">Recommended</span>}
          </button>

          <button
            className={`tut-color-btn tut-color-black${def === "b" ? " recommended" : ""}`}
            onClick={() => startOpening(pendingOpening, "b")}
          >
            <span className="tut-color-icon">&#9818;</span>
            <span className="tut-color-label">Play as Black</span>
            {def === "b" && <span className="tut-color-tag">Recommended</span>}
          </button>
        </div>

        <button className="tut-back-btn" onClick={() => setPhase("select")}>
          ← Back
        </button>
      </div>
    );
  }

  if (phase === "complete") {
    return (
      <div className="tut-complete fade-in">
        <div className="tut-complete-icon">♛</div>
        <h1>Opening Complete!</h1>
        <p className="tut-complete-name">{opening.name}</p>
        <p className="tut-complete-sub">
          You've learned all {opening.moves.length} moves of this opening.
        </p>
        <div className="tut-complete-actions">
          {/* Play Again re-enters colorPick so the player can switch sides */}
          <button onClick={() => { setPendingOpening(opening); setPhase("colorPick"); }}>Play Again</button>
          <button onClick={() => setPhase("select")}>Choose Another</button>
          <button onClick={onBack}>Main Menu</button>
        </div>
      </div>
    );
  }

  // ── Play phase ────────────────────────────────────────────────────────────────
  const totalMoves = opening.moves.length;
  const progress = Math.round((moveIndex / totalMoves) * 100);

  const currentExpected = opening.moves[moveIndex];
  const isPlayerTurn = currentExpected?.side === playerColor && !opponentThinking;

  return (
    <div className="tut-game fade-in">
      {/* Header: back button | opening name | move counter */}
      <div className="tut-header">
        <button className="tut-back-btn" onClick={() => setPhase("select")}>
          ← Openings
        </button>
        <h2 className="tut-opening-name">{opening.name}</h2>
        <span className="tut-progress-label">
          {moveIndex} / {totalMoves}
        </span>
      </div>

      {/* Thin progress bar showing how far through the opening sequence we are */}
      <div className="tut-progress-wrap">
        <div className="tut-progress-fill" style={{ width: `${progress}%` }} />
      </div>

      <div className="tut-board-and-panel">
        {renderBoard()}

        <div className="tut-right-panel">
          {/* Status banner: changes color and text based on feedback and turn */}
          <div
            className={[
              "tut-status",
              feedback === "wrong" ? "tut-status--wrong" : "",
              feedback === "right" ? "tut-status--right" : "",
              opponentThinking ? "tut-status--thinking" : "",
            ].join(" ")}
          >
            {feedback === "wrong"
              ? "Not quite — try again"
              : feedback === "right"
              ? "Correct!"
              : opponentThinking
              ? "Opponent is playing…"
              : isPlayerTurn
              ? `Your move (${playerColor === "w" ? "White" : "Black"})`
              : ""}
          </div>

          {/* Explanation panel: shows the explanation for the last move played,
              or a prompt when it's the player's turn and no move has been made yet */}
          <div className="tut-explain">
            {explanation
              ? explanation
              : isPlayerTurn
              ? "Find the correct move for this opening."
              : ""}
          </div>

          {tutComplete && (
            <button className="tut-finish-btn" onClick={() => setPhase("complete")}>
              Finish →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
