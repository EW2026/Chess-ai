import { useState, useRef, useEffect } from "react";
import { Chess } from "chess.js";
import axios from "axios";
import "./App.css";
import Tutorial from "./Tutorial";

import useBootSequence from "./hooks/useBootSequence";
import Board from "./components/Board";
import GameOverModal from "./components/GameOverModal";
import LoadingScreen from "./components/LoadingScreen";
import HintPanel from "./components/HintPanel";

const API_BASE = "http://127.0.0.1:8000";

function App() {

  // useRef for authToken instead of useState: we need to read the latest token
  // inside async callbacks (like makeAIMove) without re-registering the callback
  // every time the token changes. A ref always gives the current value without
  // triggering re-renders.
  const authToken = useRef(null);

  // ── App / session state ──────────────────────────────────────────────────────
  const [game, setGame] = useState(new Chess());
  const [mode, setMode] = useState(null);            // "ai" | "pvp"
  const [screen, setScreen] = useState("menu");      // controls which screen is rendered
  const [difficulty, setDifficulty] = useState(null);
  const [playerColor, setPlayerColor] = useState('w');
  const [gameStatus, setGameStatus] = useState("");

  const [lastMove, setLastMove] = useState(null);    // { from, to } for the golden highlight
  const [gameLog, setGameLog] = useState([]);        // array of { fen, move, evaluation } per AI move
  const [playerElo, setPlayerElo] = useState(1200);
  const [aiElo, setAiElo] = useState(1200);
  const [isThinking, setIsThinking] = useState(false);  // true while waiting for AI response
  const [lastAIMove, setLastAIMove] = useState(null);

  // ── Game-over / training state ───────────────────────────────────────────────
  const [gameOver, setGameOver] = useState(null);    // { title, subtitle } — truthy = show popup
  const [isTraining, setIsTraining] = useState(false);
  const [trainingWait, setTrainingWait] = useState(false);
  const [moveHint, setMoveHint] = useState(null);   // { san, from, to } | null — better move suggestion
  const hintTimeoutRef = useRef(null);

  // Holds the in-flight training Promise so handleNewGameFromPopup can await it.
  // Stored as a ref so the async handler always reads the current Promise, not a
  // stale closure snapshot.
  const trainingPromiseRef = useRef(null);

  // Holds the resolve function for the hint-pause Promise inside makeAIMove.
  // When the player clicks Continue (or the timeout fires), this is called to
  // unblock makeAIMove so the AI move can be applied to the board.
  const hintResolveRef = useRef(null);

  // movesRef stores the full UCI move history for the current game as a plain array.
  // We use a ref (not state) because makeAIMove reads it inside an async callback —
  // if it were state, the closure would capture a stale snapshot from when the callback
  // was created rather than the current value.
  const movesRef = useRef([]);

  // gameGenRef is a generation counter that increments whenever a new game starts.
  // Every makeAIMove call captures the current generation at the start. If the generation
  // has changed by the time the response arrives (user started a new game while AI was
  // thinking), the response is silently discarded — this prevents a move from a previous
  // game being applied to a new one.
  const gameGenRef = useRef(0);

  // ── Boot sequence (delegated to hook) ────────────────────────────────────────
  const { loading, fade, progress, bootMessage } = useBootSequence(API_BASE, authToken, setPlayerElo, setAiElo);

  // ── Game-over detection ───────────────────────────────────────────────────────
  // Called after every move. Checks all five ways a game can end and sets the
  // popup state accordingly. startTraining is called here (not after each move)
  // so training always uses the complete game log.
  function updateGameStatus(board) {
    if (board.isCheckmate()) {
      const winner = board.turn() === "w" ? "Black" : "White";
      setGameOver({ title: "Checkmate!", subtitle: `${winner} wins` });
      // board.turn() is the side to move — in checkmate that side just lost.
      // If the player is in checkmate, the AI won (won = true).
      startTraining(board.turn() === playerColor);
    } else if (board.isInsufficientMaterial()) {
      setGameOver({ title: "Draw", subtitle: "Insufficient material" });
      startTraining(false);
    } else if (board.isThreefoldRepetition()) {
      setGameOver({ title: "Draw", subtitle: "Threefold repetition" });
      startTraining(false);
    } else if (board.isStalemate()) {
      setGameOver({ title: "Draw", subtitle: "Stalemate" });
      startTraining(false);
    } else if (board.isDraw()) {
      // isDraw() catches everything else: 50-move rule, fivefold repetition
      setGameOver({ title: "Draw", subtitle: "50-move rule" });
      startTraining(false);
    }
  }

  // ── Neural network training ───────────────────────────────────────────────────
  async function trainAI(won) {
    // Posts the full game log to the backend, which trains the neural network on
    // every position from this game and updates the player's ELO rating
    try {
      const res = await axios.post(
        `${API_BASE}/api/train/`,
        {
          game_log:  gameLog,
          won,
          player:    "Player1",
          ai_color:  playerColor === 'w' ? 'b' : 'w',
          difficulty: difficulty ?? "easy",
        },
        { headers: { Authorization: authToken.current } }
      );
      if (res.data.player_elo !== undefined) setPlayerElo(res.data.player_elo);
      if (res.data.ai_elo    !== undefined) setAiElo(res.data.ai_elo);
    } catch (err) {
      console.error("TRAIN ERROR:", err);
    }
  }

  function startTraining(won) {
    if (isTraining) return;  // don't start a second training run if one is already in progress
    setIsTraining(true);
    const p = trainAI(won);
    // Store the Promise so handleNewGameFromPopup can optionally await it
    trainingPromiseRef.current = p;
    p.finally(() => {
      setIsTraining(false);
      trainingPromiseRef.current = null;
    });
  }

  // ── Hint helpers ─────────────────────────────────────────────────────────────
  // Called when the player clicks Continue or × in the HintPanel.
  // Resolves the pause-Promise in makeAIMove so the AI move plays immediately.
  function dismissHint() {
    if (hintTimeoutRef.current) clearTimeout(hintTimeoutRef.current);
    if (hintResolveRef.current) {
      hintResolveRef.current();
      hintResolveRef.current = null;
    }
    setMoveHint(null);
  }

  // Called by HintPanel when the player flips to a different slide.
  // Updates the from/to stored in moveHint so Board highlights the right squares.
  function handleHintSlideChange(from, to) {
    setMoveHint(prev => prev ? { ...prev, from, to } : null);
  }

  // ── New game helpers ──────────────────────────────────────────────────────────
  function startNewGame() {
    // Increment the generation counter so any in-flight AI response from the old game
    // is ignored when it eventually arrives
    gameGenRef.current += 1;
    axios.post(`${API_BASE}/api/new-game/`).catch(() => {});
    const fresh = new Chess();
    movesRef.current = [];
    setGame(fresh);
    setLastMove(null);
    setGameStatus("");
    setGameLog([]);
    setGameOver(null);
    // Clear any lingering move hint and unblock makeAIMove if it's paused waiting for Continue
    if (hintTimeoutRef.current) clearTimeout(hintTimeoutRef.current);
    if (hintResolveRef.current) { hintResolveRef.current(); hintResolveRef.current = null; }
    setMoveHint(null);
    // If the player chose black, the AI (white) needs to make the first move immediately
    if (mode === "ai" && playerColor === 'b') {
      setTimeout(() => makeAIMove(fresh, fresh.fen(), null), 300);
    }
  }

  async function handleNewGameFromPopup() {
    // If training is still running, wait for it (up to 8 seconds) before starting
    // a new game. We don't want to discard the training data for the game that just ended.
    // Promise.race means we'll proceed after whichever finishes first: training or the 8s timeout.
    if (isTraining && trainingPromiseRef.current) {
      setTrainingWait(true);
      await Promise.race([
        trainingPromiseRef.current,
        new Promise(r => setTimeout(r, 8000)),
      ]);
      setTrainingWait(false);
    }
    startNewGame();
  }

  function handleMenuFromPopup() {
    gameGenRef.current += 1;
    setIsThinking(false);
    setGameOver(null);
    if (hintTimeoutRef.current) clearTimeout(hintTimeoutRef.current);
    if (hintResolveRef.current) { hintResolveRef.current(); hintResolveRef.current = null; }
    setMoveHint(null);
    setScreen("menu");
  }

  // ── Castling drag support ─────────────────────────────────────────────────────
  // chess.js requires castling moves to be from the king's square to the king's
  // destination (e.g. e1→g1), but some players intuitively drag the king to the
  // rook's square (e.g. e1→h1). This map converts those "rook drop" gestures into
  // the correct castling move so both drag styles work.
  const CASTLING_ROOK_TO_DEST = {
    e1h1: { from: "e1", to: "g1" },
    e1a1: { from: "e1", to: "c1" },
    e8h8: { from: "e8", to: "g8" },
    e8a8: { from: "e8", to: "c8" },
  };

  // ── Move application ──────────────────────────────────────────────────────────
  function makeMove(from, to) {
    const prevFen = game.fen();
    const copy = new Chess(game.fen());  // work on a copy so state only updates if the move is valid
    let move = copy.move({ from, to, promotion: "q" });  // auto-promote to queen

    // If the move failed, try the castling remap before giving up
    if (!move) {
      const piece = game.get(from);
      if (piece && piece.type === "k") {
        const castling = CASTLING_ROOK_TO_DEST[from + to];
        if (castling) {
          move = copy.move({ ...castling, promotion: "q" });
        }
      }
    }

    if (!move) return;  // illegal move — ignore silently

    // Append the UCI string (e.g. "e2e4" or "e7e8q") to the move history ref
    movesRef.current = [...movesRef.current, move.from + move.to + (move.promotion || "")];

    // In PVP mode the AI never calls makeAIMove, so gameLog never gets populated.
    // Track every move here so the backend has positions to train on after the game.
    if (mode === 'pvp') {
      setGameLog(prev => [...prev, {
        fen: prevFen,
        move: { from: move.from, to: move.to, uci: move.from + move.to + (move.promotion || ''), san: move.san },
        evaluation: 0,
      }]);
    }

    setGame(copy);
    setLastMove({ from, to });
    updateGameStatus(copy);

    // Trigger the AI's response only if: in AI mode, it's the AI's turn, and the game isn't over
    const aiColor = playerColor === 'w' ? 'b' : 'w';
    if (mode === "ai" && copy.turn() === aiColor && !copy.isGameOver()) {
      setTimeout(() => makeAIMove(copy, prevFen, { from, to }), 200);
    }
  }

  // ── AI move request ───────────────────────────────────────────────────────────
  async function makeAIMove(currentGame, prevFen, playerMove) {
    if (loading) return;

    // Capture the current generation at call time. If this changes by the time the
    // response arrives, the game has been reset and we should discard the response.
    const gen = gameGenRef.current;
    const moveHistory = movesRef.current;
    setIsThinking(true);

    try {
      let res;
      try {
        res = await axios.post(
          `${API_BASE}/api/ai-move/`,
          { fen: currentGame.fen(), moves: moveHistory, difficulty, player: "Player1", prev_fen: prevFen, player_move: playerMove },
          { headers: { Authorization: authToken.current } }
        );
      } catch (err) {
        // 401 means the auth token expired or was invalidated — re-fetch it and retry once
        if (err.response?.status === 401) {
          const tokenRes = await axios.get(`${API_BASE}/api/local-token/`);
          authToken.current = tokenRes.data.token;
          res = await axios.post(
            `${API_BASE}/api/ai-move/`,
            { fen: currentGame.fen(), moves: moveHistory, difficulty, player: "Player1", prev_fen: prevFen, player_move: playerMove },
            { headers: { Authorization: authToken.current } }
          );
        } else {
          throw err;
        }
      }

      // Stale response check: if the user started a new game while the AI was thinking,
      // discard this response entirely
      if (gen !== gameGenRef.current) return;

      const data = res.data;

      // The backend returns { move: null } when no legal move exists (game already over)
      if (!data.move || !data.move.from || !data.move.to) {
        console.warn("AI returned no move or incomplete move:", data.move);
        return;
      }

      // Easy mode only: show the hint BEFORE applying the AI move so the board
      // still reflects the player's last move while they read the feedback.
      if (difficulty === "easy" && data.analysis?.mistake && data.analysis.top_moves?.length > 0) {
        const topMoves = data.analysis.top_moves;
        setIsThinking(false);   // backend has finished — stop spinner while player reads hint
        setMoveHint({
          topMoves,
          prevFen,
          from: topMoves[0].from,
          to:   topMoves[0].to,
        });
        // Pause here until the player explicitly dismisses the hint panel
        await new Promise(resolve => {
          hintResolveRef.current = resolve;
        });
        setMoveHint(null);
        if (gen !== gameGenRef.current) return;  // player started a new game while reading
      }

      const copy = new Chess(currentGame.fen());
      const result = copy.move({ from: data.move.from, to: data.move.to, promotion: "q" });

      if (!result) {
        // chess.js rejected the move — the frontend and backend boards are out of sync
        // (castling rights, en passant state, or promotion mismatch). The game cannot
        // continue safely so surface it rather than hanging silently.
        console.error("AI move rejected by chess.js:", data.move, "FEN:", currentGame.fen());
        setGameOver({ title: "Game Error", subtitle: "Board state error. Please start a new game." });
        return;
      }

      movesRef.current = [...movesRef.current, data.move.uci];

      setGame(copy);
      setLastAIMove(data.move);
      setLastMove({ from: data.move.from, to: data.move.to });
      updateGameStatus(copy);

      // Append this position + move to the game log for training later
      setGameLog((prev) => [
        ...prev,
        {
          fen: currentGame.fen(),
          move: data.move,
          evaluation: data.evaluation || 0,
        },
      ]);
    } catch (err) {
      if (gen === gameGenRef.current) console.error("AI MOVE ERROR:", err);
    } finally {
      // Always clear the thinking indicator for this generation, even if an error occurred
      if (gen === gameGenRef.current) setIsThinking(false);
    }
  }

  // When the player chooses Black, White (the AI) moves first.
  // We trigger makeAIMove once when the game screen mounts.
  useEffect(() => {
    if (screen !== "game" || mode !== "ai" || playerColor !== 'b') return;
    setTimeout(() => makeAIMove(game, game.fen(), null), 300);
  }, [screen]);

  // ── Screen rendering ──────────────────────────────────────────────────────────

  if (loading) {
    return <LoadingScreen fade={fade} progress={progress} bootMessage={bootMessage} />;
  }

  if (screen === "tutorial") {
    return <Tutorial onBack={() => setScreen("menu")} />;
  }

  if (screen === "menu") {
    return (
      <div className="menu fade-in">
        <h1>Chess AI</h1>
        <p className="player-elo">Player ELO: {playerElo}</p>
        <p className="player-elo">AI ELO: {aiElo}</p>

        <button onClick={() => {
          // Kick off pool warmup immediately so workers are ready before the first move
          axios.post(`${API_BASE}/api/warmup-pool/`).catch(() => {});
          setScreen("color");
        }}>
          Play vs AI
        </button>

        <button
          onClick={() => {
            gameGenRef.current += 1;
            axios.post(`${API_BASE}/api/new-game/`).catch(() => {});
            movesRef.current = [];
            setGame(new Chess());
            setLastMove(null);
            setGameStatus("");
            setGameLog([]);
            setMode("pvp");
            setScreen("game");
          }}
        >
          Play vs Player
        </button>

        <button onClick={() => setScreen("tutorial")}>
          Opening Tutorial
        </button>
      </div>
    );
  }

  if (screen === "color") {
    return (
      <div className="menu fade-in">
        <h1>Choose Your Color</h1>

        <button onClick={() => { setPlayerColor('w'); setScreen("ai"); }}>
          White — You go first
        </button>

        <button onClick={() => { setPlayerColor('b'); setScreen("ai"); }}>
          Black — AI goes first
        </button>

        <button onClick={() => setScreen("menu")}>
          Back
        </button>
      </div>
    );
  }

  if (screen === "ai") {
    return (
      <div className="menu fade-in">
        <h1>Select Difficulty</h1>

        {["easy","medium","hard","expert","grandmaster"].map(d => (
          <button
            key={d}
            onClick={() => {
              gameGenRef.current += 1;
              axios.post(`${API_BASE}/api/new-game/`, { difficulty: d }).catch(() => {});
              movesRef.current = [];
              setGame(new Chess());
              setLastMove(null);
              setGameStatus("");
              setGameLog([]);
              setGameOver(null);
              if (hintTimeoutRef.current) clearTimeout(hintTimeoutRef.current);
              setMoveHint(null);
              setDifficulty(d);
              setMode("ai");
              setScreen("game");
            }}
          >
            {d}
          </button>
        ))}

        <button onClick={() => setScreen("color")}>
          Back
        </button>
      </div>
    );
  }

  // Shown when training is still running and the player clicked "New Game".
  // Disappears as soon as training finishes or the 8-second timeout elapses.
  if (trainingWait) {
    return <LoadingScreen bootMessage="UPDATING AI" animated />;
  }

  return (
    <div className="game fade-in">
      <h1>Chess AI</h1>

      <div className="controls">
        <span className="player-elo">You: {playerElo}</span>
        <span className="player-elo">AI: {aiElo}</span>

        <button onClick={startNewGame}>
          New Game
        </button>

        <button onClick={() => { gameGenRef.current += 1; setIsThinking(false); setGameOver(null); setScreen("menu"); }}>
          Menu
        </button>
      </div>

      <div className="game-board-area">
        <Board
          game={game}
          playerColor={playerColor}
          mode={mode}
          isThinking={isThinking}
          lastMove={lastMove}
          difficulty={difficulty}
          moveHint={moveHint}
          canInteract={!(mode === "ai" && game.turn() !== playerColor)}
          onMove={makeMove}
        />

        {moveHint && difficulty === "easy" && (
          <HintPanel
            hint={moveHint}
            onDismiss={dismissHint}
          />
        )}
      </div>

      {gameOver && (
        <GameOverModal
          gameOver={gameOver}
          isTraining={isTraining}
          onNewGame={handleNewGameFromPopup}
          onMenu={handleMenuFromPopup}
        />
      )}
    </div>
  );
}

export default App;
