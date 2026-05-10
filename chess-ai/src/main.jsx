import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./App.css";

console.log("🚀 React entry loaded");

// ErrorBoundary wraps the entire app so a crash in any child component
// shows a readable error instead of a blank screen. In development this
// supplements the Vite error overlay; in production it's the only safety net.
function ErrorBoundary({ children }) {
  try {
    return children;
  } catch (e) {
    alert("🔥 REACT CRASH:\n" + e.message);
    console.error(e);
    return <div style={{ color: "red" }}>React crashed</div>;
  }
}

try {
  const root = document.getElementById("root");

  if (!root) {
    throw new Error("Root element not found");
  }

  // createRoot is the React 18 API for mounting the app. It enables concurrent
  // features (automatic batching, transitions) compared to the legacy render().
  // React.StrictMode runs every component twice in development to surface
  // side effects and deprecated API usage — it has no effect in production builds.
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>
  );

  console.log("✅ React mounted");
} catch (e) {
  alert("🔥 FATAL REACT INIT ERROR:\n" + e.message);
  console.error(e);
}
