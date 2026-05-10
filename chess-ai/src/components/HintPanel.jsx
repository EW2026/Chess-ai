import { useState, useEffect } from "react";
import { Chess } from "chess.js";
import MiniBoard from "./MiniBoard";

// Build the FEN for each step of the continuation by replaying moves from prevFen.
// Returns an array parallel to `continuation`: fenAt[i] is the position BEFORE
// continuation[i] is played (so the mini board shows the piece on its origin square).
function buildFens(prevFen, continuation) {
  const fens = [];
  const chess = new Chess(prevFen);
  for (const step of continuation) {
    fens.push(chess.fen());
    try {
      chess.move({ from: step.from, to: step.to, promotion: "q" });
    } catch {
      break;
    }
  }
  return fens;
}

export default function HintPanel({ hint, onDismiss }) {
  const best       = (hint.topMoves || [])[0] || {};
  const steps      = best.continuation || [];
  const total      = steps.length;
  const [idx, setIdx] = useState(0);

  // Precompute FENs once per hint so each slide has the right board position
  const [fens, setFens] = useState([]);
  useEffect(() => {
    setIdx(0);
    if (hint.prevFen && steps.length > 0) {
      setFens(buildFens(hint.prevFen, steps));
    } else {
      setFens([]);
    }
  }, [hint]);

  if (total === 0) return null;

  const step    = steps[idx]  || {};
  const fen     = fens[idx]   || hint.prevFen || "";
  const isYou   = idx % 2 === 0;   // even = player's move, odd = opponent's
  const isFirst = idx === 0;

  return (
    <div className="hint-panel">

      {/* ── Header ──────────────────────────────────────────────────── */}
      <div className="hint-panel-header">
        <span className="hint-panel-label">Better Move</span>
        <button className="hint-panel-dismiss" onClick={onDismiss} aria-label="Skip and play AI move">
          ×
        </button>
      </div>

      {/* ── Step counter ─────────────────────────────────────────────── */}
      <div className="hint-panel-counter">
        Move {idx + 1} of {total}
      </div>

      {/* ── Whose turn label ─────────────────────────────────────────── */}
      <div className={`hint-turn-label ${isYou ? "hint-turn-you" : "hint-turn-them"}`}>
        {isYou ? "Your move" : "Opponent's likely response"}
      </div>

      {/* ── Move in SAN notation ─────────────────────────────────────── */}
      <div className="hint-panel-move">{step.san}</div>

      {/* ── Mini board showing the position + highlighted move ────────── */}
      {fen && step.from && step.to && (
        <div className="hint-mini-board-wrap">
          <MiniBoard fen={fen} fromSq={step.from} toSq={step.to} />
        </div>
      )}

      {/* ── Explanation (first slide only — why this move is better) ──── */}
      {isFirst && best.reason && (
        <p className="hint-panel-reason">{best.reason}</p>
      )}
      {isFirst && best.eval_diff != null && best.eval_diff > 0 && (
        <p className="hint-panel-eval">
          ~{best.eval_diff} {best.eval_diff === 1 ? "pawn" : "pawns"} better than your move
        </p>
      )}
      {!isFirst && (
        <p className="hint-panel-reason hint-continuation-note">
          This is the likely continuation after the suggested move.
        </p>
      )}

      {/* ── Prev / Next navigation ───────────────────────────────────── */}
      <div className="hint-panel-nav">
        <button
          className="hint-panel-nav-btn"
          onClick={() => setIdx(i => i - 1)}
          disabled={idx === 0}
        >
          ← Prev
        </button>
        <button
          className="hint-panel-nav-btn"
          onClick={() => setIdx(i => i + 1)}
          disabled={idx === total - 1}
        >
          Next →
        </button>
      </div>

      {/* ── Continue button ──────────────────────────────────────────── */}
      <button className="hint-panel-continue" onClick={onDismiss}>
        Play AI Move →
      </button>

    </div>
  );
}
