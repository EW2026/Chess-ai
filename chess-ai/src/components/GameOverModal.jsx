// Overlay modal shown when a game ends. Displays the result and provides
// buttons to start a new game or return to the main menu.
export default function GameOverModal({ gameOver, isTraining, onNewGame, onMenu }) {
  return (
    <div className="gameover-overlay">
      <div className="gameover-modal">
        <h2 className="gameover-title">{gameOver.title}</h2>
        <p className="gameover-subtitle">{gameOver.subtitle}</p>
        {/* Show "Updating AI..." while the training request is still in flight */}
        {isTraining && (
          <p className="gameover-training">
            <span className="gameover-dot" />
            Updating AI...
          </p>
        )}
        <div className="gameover-actions">
          <button className="gameover-btn" onClick={onMenu}>
            Main Menu
          </button>
          <button className="gameover-btn primary" onClick={onNewGame}>
            New Game
          </button>
        </div>
      </div>
    </div>
  );
}
