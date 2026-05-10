import wp from "../assets/pieces/wp.svg";
import wr from "../assets/pieces/wr.svg";
import wn from "../assets/pieces/wn.svg";
import wb from "../assets/pieces/wb.svg";
import wq from "../assets/pieces/wq.svg";
import wk from "../assets/pieces/wk.svg";
import bp from "../assets/pieces/bp.svg";
import br from "../assets/pieces/br.svg";
import bn from "../assets/pieces/bn.svg";
import bb from "../assets/pieces/bb.svg";
import bq from "../assets/pieces/bq.svg";
import bk from "../assets/pieces/bk.svg";
import { Chess } from "chess.js";

const PIECES = { wp, wr, wn, wb, wq, wk, bp, br, bn, bb, bq, bk };
const FILES   = ["a","b","c","d","e","f","g","h"];
const SQ_PX   = 36;   // pixels per square → 288 × 288 total

// Convert square name ("e4") to pixel centre coordinates
function sqCenter(sq) {
  const file = FILES.indexOf(sq[0]);
  const rank = parseInt(sq[1]) - 1;
  return {
    x: file * SQ_PX + SQ_PX / 2,
    y: (7 - rank) * SQ_PX + SQ_PX / 2,
  };
}

// SVG arrow with arrowhead from fromSq to toSq
function Arrow({ fromSq, toSq }) {
  const f = sqCenter(fromSq);
  const t = sqCenter(toSq);

  const dx = t.x - f.x;
  const dy = t.y - f.y;
  const len = Math.sqrt(dx * dx + dy * dy);
  if (len === 0) return null;

  const ux = dx / len;
  const uy = dy / len;

  // Start slightly away from the from-square centre, end short of the to-square
  // centre so the arrowhead lands inside the to-square without covering the piece.
  const startX = f.x + ux * SQ_PX * 0.22;
  const startY = f.y + uy * SQ_PX * 0.22;
  const endX   = t.x - ux * SQ_PX * 0.28;
  const endY   = t.y - uy * SQ_PX * 0.28;

  const boardSize = SQ_PX * 8;
  return (
    <svg
      style={{ position: "absolute", top: 0, left: 0, width: boardSize, height: boardSize, pointerEvents: "none" }}
      viewBox={`0 0 ${boardSize} ${boardSize}`}
    >
      <defs>
        <marker id="mb-arrow" markerWidth="5" markerHeight="5" refX="4.5" refY="2.5" orient="auto">
          <path d="M0,0 L5,2.5 L0,5 Z" fill="rgba(255,180,0,0.95)" />
        </marker>
      </defs>
      <line
        x1={startX} y1={startY}
        x2={endX}   y2={endY}
        stroke="rgba(255,180,0,0.85)"
        strokeWidth={SQ_PX * 0.18}
        strokeLinecap="round"
        markerEnd="url(#mb-arrow)"
      />
    </svg>
  );
}

// Read-only mini chess board. Always rendered from White's perspective.
// fromSq is highlighted amber (origin), toSq is highlighted green (destination),
// and an arrow connects them.
export default function MiniBoard({ fen, fromSq, toSq }) {
  const chess = new Chess(fen);
  const boardSize = SQ_PX * 8;

  const squares = [];
  for (let rank = 8; rank >= 1; rank--) {
    for (let fileIdx = 0; fileIdx < 8; fileIdx++) {
      const sq    = FILES[fileIdx] + rank;
      const piece = chess.get(sq);
      const isDark = (fileIdx + rank) % 2 === 0;
      squares.push({ sq, piece, isDark });
    }
  }

  return (
    <div className="mini-board" style={{ width: boardSize, height: boardSize }}>
      {squares.map(({ sq, piece, isDark }) => {
        const isFrom = sq === fromSq;
        const isTo   = sq === toSq;
        const cls = [
          "mini-sq",
          isDark ? "mini-dark" : "mini-light",
          isFrom ? "mini-from" : isTo ? "mini-to" : "",
        ].filter(Boolean).join(" ");

        return (
          <div key={sq} className={cls}>
            {piece && (
              <img
                src={PIECES[piece.color + piece.type]}
                className="mini-piece"
                alt=""
                draggable={false}
              />
            )}
          </div>
        );
      })}
      <Arrow fromSq={fromSq} toSq={toSq} />
    </div>
  );
}
