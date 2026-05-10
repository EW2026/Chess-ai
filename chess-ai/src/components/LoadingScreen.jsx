// Loading screen used for both the initial boot sequence and the "Updating AI"
// training wait screen. Pass animated=true for the indeterminate progress bar
// (used when the wait duration is unknown), or progress + fade for the boot flow.
export default function LoadingScreen({ fade = false, progress = 0, bootMessage, animated = false }) {
  return (
    <div className={`loading-screen ${fade ? "fade-out" : "fade-in"}`}>
      <div className="loading-board-bg" />
      <div className="loading-content">
        <div className="loading-piece">&#9822;</div>
        <h1 className="loading-title">Chess AI</h1>
        <div className="loading-bar-wrap">
          <div
            className={`loading-bar-fill${animated ? " loading-bar-animated" : ""}`}
            style={animated ? {} : { width: `${progress}%` }}
          />
        </div>
        <p className="loading-status">{bootMessage}</p>
      </div>
    </div>
  );
}
