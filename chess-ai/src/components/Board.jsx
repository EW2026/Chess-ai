import { useState, useRef, useEffect } from "react";

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

// Piece image lookup: key = color + type (e.g. "wp", "bn")
const pieces = {
  wp, wr, wn, wb, wq, wk,
  bp, br, bn, bb, bq, bk,
};

// Board component owns all drag-and-drop state and input handling.
// Props:
//   game         — Chess instance (read-only; App owns game state)
//   playerColor  — 'w' | 'b'; flips board orientation and restricts which pieces can be dragged
//   isThinking   — true while waiting for the AI response; shows overlay and thinking ring
//   lastMove     — { from, to } for the golden last-move highlight, or null
//   difficulty   — current difficulty string; easy mode shows legal-move dots
//   moveHint     — { from, to, san } for the better-move hint highlight, or null
//   canInteract  — false while it's the AI's turn (prevents dragging in AI mode)
//   onMove       — callback(from, to) called when the player completes a drag
export default function Board({ game, playerColor, mode, isThinking, lastMove, difficulty, moveHint, canInteract, onMove }) {
  const [dragging, setDragging] = useState(null);  // { from: square, piece: imgSrc } while dragging
  const [dragPos, setDragPos] = useState({ x: 0, y: 0 });
  const [legalMoves, setLegalMoves] = useState([]);  // destination squares to highlight (easy mode)
  const boardRef = useRef(null);

  function handleMouseDown(e, square) {
    const piece = game.get(square);

    if (!piece) return;
    // In PVP both players share the board, so restrict by whose turn it is.
    // In AI mode restrict to the human player's fixed color.
    const allowedColor = mode === 'pvp' ? game.turn() : playerColor;
    if (piece.color !== allowedColor) return;
    if (!canInteract) return;                 // can't move while AI is thinking

    setDragging({
      from: square,
      piece: pieces[piece.color + piece.type],
    });

    setDragPos({ x: e.clientX, y: e.clientY });

    // Easy mode only: highlight legal destination squares as a hint for the player
    if (difficulty === "easy") {
      const moves = game.moves({ square, verbose: true });
      setLegalMoves(moves.map((m) => m.to));
    }
  }

  function handleRightClick(e) {
    e.preventDefault();
    setDragging(null);
    setLegalMoves([]);
  }

  // mousemove and mouseup are attached to the window rather than the board element.
  // This allows the player to drag a piece outside the board bounds and release it —
  // without window-level listeners, the mouseup event would be lost if the cursor
  // leaves the board div, leaving the piece "stuck" to the cursor.
  // The effect re-registers whenever dragging changes so the handlers always have
  // the current dragging state in their closure.
  useEffect(() => {
    function onMouseMove(e) {
      if (!dragging) return;
      setDragPos({ x: e.clientX, y: e.clientY });
    }

    function onMouseUp(e) {
      if (!dragging) return;

      const rect = boardRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // Convert pixel position to board column/row (0–7)
      const screenCol = Math.floor((x / rect.width) * 8);
      const screenRow = Math.floor((y / rect.height) * 8);

      if (screenCol >= 0 && screenCol < 8 && screenRow >= 0 && screenRow < 8) {
        // When playing as Black the board is flipped, so screen column 0 is file H (index 7).
        // Mirror both axes to convert screen coordinates to actual board coordinates.
        const actualC = playerColor === 'b' ? 7 - screenCol : screenCol;
        const actualR = playerColor === 'b' ? 7 - screenRow : screenRow;
        const to = "abcdefgh"[actualC] + (8 - actualR);
        onMove(dragging.from, to);
      }

      setDragging(null);
      setLegalMoves([]);
    }

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);

    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [dragging, playerColor]);

  const board = game.board();  // 8×8 array of { type, color } | null from chess.js

  if (!board || board.length !== 8) {
    console.error("Invalid board:", board);
    return <div>Board failed to load</div>;
  }

  // When playing as Black, the board is flipped: files go H→A and ranks go 1→8
  const files = playerColor === 'b'
    ? ['H','G','F','E','D','C','B','A']
    : ['A','B','C','D','E','F','G','H'];
  const ranks = playerColor === 'b'
    ? [1,2,3,4,5,6,7,8]
    : [8,7,6,5,4,3,2,1];

  return (
    <div className="board-outer">
      {/* top file labels */}
      <div className="coords-files-row">
        {files.map(f => <span key={f} className="coords-label">{f}</span>)}
      </div>

      {/* middle row: left rank labels | board | right rank labels */}
      <div className="coords-board-row">
        <div className="coords-ranks-col">
          {ranks.map(r => <span key={r} className="coords-label">{r}</span>)}
        </div>

        <div className="board-wrap">
          <div ref={boardRef} className={`board${isThinking ? " thinking" : ""}`} onContextMenu={handleRightClick}>
            {board.map((row, r) =>
              row.map((_, c) => {
                // r and c are screen-space indices (0 = top-left).
                // actualR and actualC are board-space indices (0 = rank 8 for white).
                // When flipped (playing as Black), mirror both axes.
                const actualR = playerColor === 'b' ? 7 - r : r;
                const actualC = playerColor === 'b' ? 7 - c : c;
                const file = "abcdefgh"[actualC];
                const rank = 8 - actualR;
                const sq = file + rank;  // e.g. "e4"

                const cell = board[actualR][actualC];
                let piece = null;

                if (cell && cell.type && cell.color) {
                  const key = cell.color + cell.type;
                  if (!pieces[key]) console.warn("Missing piece asset:", key);
                  piece = pieces[key];
                }

                // Square color: dark when (row + col) is odd in screen space
                const isDark = (r + c) % 2 === 1;
                const highlight = legalMoves.includes(sq);
                const last = lastMove && (lastMove.from === sq || lastMove.to === sq);
                const isHint = moveHint && (moveHint.from === sq || moveHint.to === sq);

                return (
                  <div
                    key={sq}
                    className={`square ${isDark ? "dark" : "light"} ${last ? "last" : ""} ${isHint ? "hint" : ""} ${highlight ? "highlight" : ""}`}
                  >
                    {piece && (
                      <img
                        src={piece}
                        className="piece"
                        onMouseDown={(e) => handleMouseDown(e, sq)}
                        draggable={false}   // disable browser's native drag so our custom drag takes over
                        onError={() => console.error("Image failed:", piece)}
                      />
                    )}
                  </div>
                );
              })
            )}

            {/* Dragging ghost: renders the piece image centered on the cursor */}
            {dragging && (
              <img
                src={dragging.piece}
                className="dragging-piece"
                style={{ left: dragPos.x, top: dragPos.y }}
              />
            )}
          </div>

          {/* Semi-transparent overlay shown while waiting for the AI response */}
          {isThinking && (
            <div className="thinking-overlay">
              <span className="thinking-overlay-text">Thinking...</span>
            </div>
          )}
        </div>

        <div className="coords-ranks-col">
          {ranks.map(r => <span key={`r-right-${r}`} className="coords-label">{r}</span>)}
        </div>
      </div>

      {/* bottom file labels */}
      <div className="coords-files-row">
        {files.map(f => <span key={`f-bot-${f}`} className="coords-label">{f}</span>)}
      </div>
    </div>
  );
}
