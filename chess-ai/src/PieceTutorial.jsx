import { useState, useRef, useEffect, useCallback } from "react";
import { Chess } from "chess.js";
import PIECE_MOVEMENTS from "./pieceMovements";
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

export default function PieceTutorial({ onBack }) {
  const [selectedPiece, setSelectedPiece] = useState(null);
  const [slideIndex, setSlideIndex] = useState(0);

  // explore mode: squares highlighted when the user clicks a piece on an explain slide
  const [exploreHighlights, setExploreHighlights] = useState([]);
  const [exploreSquare, setExploreSquare] = useState(null);

  // practice mode: legal destinations computed when the user clicks the active piece
  const [legalDests, setLegalDests] = useState([]);

  const [dragging, setDragging] = useState(null);
  const [dragPos, setDragPos] = useState({ x: 0, y: 0 });
  const [feedback, setFeedback] = useState(null); // null | "right" | "wrong"
  const [complete, setComplete] = useState(false);
  const [pendingAdvance, setPendingAdvance] = useState(null); // null | { nextIdx, last }
  const [displayFen, setDisplayFen] = useState(null); // FEN after correct drop; null = show slide.fen

  const boardRef = useRef(null);

  function resetSlideState() {
    setExploreHighlights([]);
    setExploreSquare(null);
    setLegalDests([]);
    setDragging(null);
    setFeedback(null);
    setDisplayFen(null);
  }

  function selectPiece(piece) {
    setSelectedPiece(piece);
    setSlideIndex(0);
    setComplete(false);
    setPendingAdvance(null);
    resetSlideState();
  }

  function goToSlide(idx) {
    setSlideIndex(idx);
    setPendingAdvance(null);
    resetSlideState();
  }

  const slide = selectedPiece ? selectedPiece.slides[slideIndex] : null;
  const isPractice = slide?.type === "practice";

  // Which highlights to render:
  //   practice  → legalDests (populated when user clicks active piece)
  //   explain   → exploreHighlights when user clicked a piece, else slide.highlights
  const displayHighlights = isPractice
    ? legalDests
    : exploreHighlights.length > 0
    ? exploreHighlights
    : slide?.highlights ?? [];

  // ── Click handler (explain slides: explore mode) ──────────────────────────
  function handlePieceClick(e, square) {
    if (isPractice) return; // practice pieces are handled by drag only
    if (!slide) return;

    const game = new Chess(slide.fen);
    const piece = game.get(square);
    if (!piece) return;

    if (exploreSquare === square) {
      // toggle off
      setExploreHighlights([]);
      setExploreSquare(null);
    } else {
      const moves = game.moves({ square, verbose: true });
      setExploreHighlights(moves.map((m) => m.to));
      setExploreSquare(square);
    }
  }

  // ── Mouse down (practice drag start) ─────────────────────────────────────
  function handleMouseDown(e, square) {
    if (!isPractice || !slide) return;
    if (displayFen) return; // piece already moved — wait for Reset
    if (square !== slide.activeSquare) return;

    const game = new Chess(slide.fen);
    const piece = game.get(square);
    if (!piece) return;

    const moves = game.moves({ square, verbose: true });
    setLegalDests(moves.map((m) => m.to));
    setDragging({ from: square, piece: PIECES[piece.color + piece.type] });
    setDragPos({ x: e.clientX, y: e.clientY });
  }

  const onMouseMove = useCallback(
    (e) => {
      if (!dragging) return;
      setDragPos({ x: e.clientX, y: e.clientY });
    },
    [dragging]
  );

  const onMouseUp = useCallback(
    (e) => {
      if (!dragging || !slide) return;

      const rect = boardRef.current?.getBoundingClientRect();
      if (rect) {
        const file = Math.floor(((e.clientX - rect.left) / rect.width) * 8);
        const rank = 7 - Math.floor(((e.clientY - rect.top) / rect.height) * 8);

        if (file >= 0 && file < 8 && rank >= 0 && rank < 8) {
          const to = "abcdefgh"[file] + (rank + 1);

          if (to !== dragging.from) {
            if (legalDests.includes(to)) {
              setFeedback("right");
              setDragging(null);
              setLegalDests([]);
              const game = new Chess(slide.fen);
              game.move({ from: dragging.from, to, promotion: "q" });
              setDisplayFen(game.fen());
              const nextIdx = slideIndex + 1;
              const isLast = nextIdx >= selectedPiece.slides.length;
              setPendingAdvance({ nextIdx, last: isLast });
              return;
            } else {
              setFeedback("wrong");
              setTimeout(() => setFeedback(null), 800);
            }
          }
        }
      }

      setDragging(null);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dragging, slide, legalDests, slideIndex, selectedPiece]
  );

  useEffect(() => {
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [onMouseMove, onMouseUp]);

  // ── Board renderer ────────────────────────────────────────────────────────
  function renderBoard() {
    if (!slide) return null;

    const board = new Chess(displayFen ?? slide.fen).board();
    const files = ["A", "B", "C", "D", "E", "F", "G", "H"];
    const ranks = [8, 7, 6, 5, 4, 3, 2, 1];

    const boardEl = (
      <div ref={boardRef} className="tut-board">
        {board.map((row, r) =>
          row.map((square, c) => {
            const sq = "abcdefgh"[c] + (8 - r);
            const isDark = (c + (7 - r)) % 2 === 0;
            const isActive = !displayFen && sq === slide.activeSquare;
            const isDest = displayHighlights.includes(sq);
            const isDragging = dragging?.from === sq;

            let pieceKey = null;
            if (square?.type && square?.color) pieceKey = square.color + square.type;

            return (
              <div
                key={sq}
                className={[
                  "tut-square",
                  isDark ? "tut-dark" : "tut-light",
                  isActive ? "ptut-active" : "",
                  isDest ? "ptut-dest" : "",
                ].join(" ")}
              >
                {pieceKey && PIECES[pieceKey] && !isDragging && (
                  <img
                    src={PIECES[pieceKey]}
                    className="tut-piece"
                    style={{
                      cursor:
                        isPractice && sq === slide.activeSquare
                          ? "grab"
                          : "pointer",
                    }}
                    onMouseDown={(e) => handleMouseDown(e, sq)}
                    onClick={(e) => handlePieceClick(e, sq)}
                    draggable={false}
                  />
                )}
              </div>
            );
          })
        )}

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
          {files.map((f) => (
            <span key={f} className="tut-coords-label">
              {f}
            </span>
          ))}
        </div>
        <div className="tut-coords-board-row">
          <div className="tut-coords-ranks-col">
            {ranks.map((r) => (
              <span key={r} className="tut-coords-label">
                {r}
              </span>
            ))}
          </div>
          {boardEl}
          <div className="tut-coords-ranks-col">
            {ranks.map((r) => (
              <span key={`r-right-${r}`} className="tut-coords-label">
                {r}
              </span>
            ))}
          </div>
        </div>
        <div className="tut-coords-files-row">
          {files.map((f) => (
            <span key={`f-bot-${f}`} className="tut-coords-label">
              {f}
            </span>
          ))}
        </div>
      </div>
    );
  }

  // ── Piece selection screen ────────────────────────────────────────────────
  if (!selectedPiece) {
    return (
      <div className="tut-select fade-in">
        <div className="tut-select-header">
          <h1>Piece Movements</h1>
          <p>Choose a piece to learn how it moves</p>
        </div>

        <div className="tut-cards">
          {PIECE_MOVEMENTS.map((piece) => (
            <button
              key={piece.key}
              className="tut-card"
              onClick={() => selectPiece(piece)}
            >
              <span className="tut-card-name">
                {piece.symbol} {piece.name}
              </span>
              <span className="tut-card-desc">{piece.description}</span>
            </button>
          ))}
        </div>

        <button className="tut-back-btn" onClick={onBack}>
          ← Back
        </button>
      </div>
    );
  }

  // ── Complete screen ───────────────────────────────────────────────────────
  if (complete) {
    return (
      <div className="tut-complete fade-in">
        <div className="tut-complete-icon">{selectedPiece.symbol}</div>
        <h1>Well Done!</h1>
        <p className="tut-complete-name">{selectedPiece.name} Complete</p>
        <p className="tut-complete-sub">
          You've learned all {selectedPiece.slides.length} slides for the{" "}
          {selectedPiece.name.toLowerCase()}.
        </p>
        <div className="tut-complete-actions">
          <button
            onClick={() => {
              setComplete(false);
              setSlideIndex(0);
              resetSlideState();
            }}
          >
            Replay
          </button>
          <button
            onClick={() => {
              setSelectedPiece(null);
              setComplete(false);
              resetSlideState();
            }}
          >
            Choose Another Piece
          </button>
          <button onClick={onBack}>Back to Tutorials</button>
        </div>
      </div>
    );
  }

  // ── Slides screen ─────────────────────────────────────────────────────────
  const totalSlides = selectedPiece.slides.length;

  let statusText;
  if (feedback === "wrong") {
    statusText = "Not quite — try again";
  } else if (feedback === "right") {
    statusText = "Correct!";
  } else if (isPractice) {
    statusText = legalDests.length > 0
      ? "Drag to any highlighted square"
      : "Click the highlighted piece to see its moves";
  } else if (exploreSquare) {
    statusText = "Click the same piece to clear, or any other piece to explore";
  } else {
    statusText = "Click any piece on the board to explore its moves";
  }

  return (
    <div className="tut-game fade-in">
      <div className="tut-header">
        <button
          className="tut-back-btn"
          onClick={() => {
            setSelectedPiece(null);
            resetSlideState();
          }}
        >
          ← Pieces
        </button>
        <h2 className="tut-opening-name">
          {selectedPiece.symbol} {selectedPiece.name}: {slide.title}
        </h2>
        <span className="tut-progress-label">
          {slideIndex + 1} / {totalSlides}
        </span>
      </div>

      <div className="tut-board-and-panel">
        {renderBoard()}

        <div className="tut-right-panel">
          <div
            className={[
              "tut-status",
              feedback === "wrong" ? "tut-status--wrong" : "",
              feedback === "right" ? "tut-status--right" : "",
            ].join(" ")}
          >
            {statusText}
          </div>

          {isPractice ? (
            <div className="ptut-instruction">{slide.instruction}</div>
          ) : (
            <div className="tut-explain">{slide.explanation}</div>
          )}

          {displayFen && (
            <button
              className="ptut-reset-btn"
              onClick={() => {
                setDisplayFen(null);
                setFeedback(null);
                setLegalDests([]);
                setPendingAdvance(null);
              }}
            >
              ↺ Reset
            </button>
          )}

          {pendingAdvance && (
            <button
              className="tut-finish-btn"
              onClick={() => {
                if (pendingAdvance.last) {
                  setComplete(true);
                } else {
                  goToSlide(pendingAdvance.nextIdx);
                }
                setPendingAdvance(null);
              }}
            >
              {pendingAdvance.last ? "Finish →" : "Continue →"}
            </button>
          )}

          {!isPractice && (
            <div className="ptut-nav">
              <button
                className="ptut-nav-btn"
                onClick={() => goToSlide(slideIndex - 1)}
                disabled={slideIndex === 0}
              >
                ← Prev
              </button>
              <div className="ptut-dots">
                {selectedPiece.slides.map((_, i) => (
                  <div
                    key={i}
                    className={`ptut-dot${i === slideIndex ? " ptut-dot--active" : ""}`}
                  />
                ))}
              </div>
              <button
                className="ptut-nav-btn"
                onClick={() => goToSlide(slideIndex + 1)}
                disabled={slideIndex >= totalSlides - 1}
              >
                Next →
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
