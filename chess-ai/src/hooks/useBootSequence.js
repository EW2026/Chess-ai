import { useState, useEffect } from "react";
import { Chess } from "chess.js";
import axios from "axios";

// Manages the startup sequence: polls the backend until it's ready, fetches the
// auth token, warms up the AI, and loads the player's ELO. Returns loading state
// so the caller can show a progress screen while boot is in progress.
export default function useBootSequence(API_BASE, authToken, setPlayerElo, setAiElo) {
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(0);
  const [bootMessage, setBootMessage] = useState("INITIALIZING");
  const [fade, setFade] = useState(false);  // triggers CSS fade-out before hiding the loader

  useEffect(() => {
    let isMounted = true;

    async function waitForBackend() {
      let attempts = 0;
      const MAX_ATTEMPTS = 20;   // 20 attempts × 500ms = up to 10 seconds before giving up

      while (attempts < MAX_ATTEMPTS) {
        try {
          console.log("Checking backend...");

          const health = await axios.get(`${API_BASE}/api/health/`, {
            timeout: 1000
          });

          if (health.status === 200) {
            console.log("Backend ready");

            // Fetch the local auth token — required for the protected /ai-move/ and /train/ endpoints.
            // Retry up to 3 times: the backend may still be finishing its auth table setup
            // when the health check first returns 200. A silent failure here means all AI
            // moves fail with no error shown to the player.
            let tokenFetched = false;
            for (let attempt = 0; attempt < 3 && !tokenFetched; attempt++) {
              try {
                const tokenRes = await axios.get(`${API_BASE}/api/local-token/`, { timeout: 2000 });
                authToken.current = tokenRes.data.token;
                tokenFetched = true;
              } catch (e) {
                console.warn(`Token fetch attempt ${attempt + 1}/3 failed:`, e.message);
                if (attempt < 2) await new Promise(r => setTimeout(r, 500));
              }
            }
            if (!tokenFetched) {
              console.error("Auth token unavailable after 3 attempts — AI moves will not work");
            }

            // Send a dummy AI move request at startup to force the Django process to import
            // torch and load the model weights into memory, so the first real move isn't slow
            try {
              await axios.post(
                `${API_BASE}/api/ai-move/`,
                { fen: new Chess().fen(), difficulty: "easy", player: "warmup" },
                { headers: { Authorization: authToken.current }, timeout: 2000 }
              );
            } catch (e) {
              console.warn("Warmup failed:", e.message);
            }

            try {
              const statsRes = await axios.get(`${API_BASE}/api/player-stats/?player=Player1`);
              if (isMounted) {
                setPlayerElo(statsRes.data.elo);
                setAiElo(statsRes.data.ai_elo ?? 1200);
              }
            } catch (e) {
              console.warn("Player stats fetch failed:", e.message);
            }

            if (isMounted) {
              setBootMessage("READY");
              setProgress(100);

              // Brief pause so the user sees "READY" before the loading screen fades out
              setTimeout(() => {
                setFade(true);
                setTimeout(() => setLoading(false), 400);
              }, 300);
            }

            return;
          }
        } catch (e) {
          console.log("Backend not ready:", e.message);
        }

        attempts++;
        setProgress((p) => Math.min(p + 5, 95));  // cap at 95 — 100 only when truly ready
        setBootMessage("STARTING ENGINE");

        await new Promise((r) => setTimeout(r, 500));
      }

      console.error("Backend failed to start");

      if (isMounted) {
        setBootMessage("OFFLINE");
        setLoading(false);
      }
    }

    waitForBackend();

    // Cleanup: if the component unmounts while still booting, suppress any pending state updates
    return () => {
      isMounted = false;
    };
  }, []);

  return { loading, fade, progress, bootMessage };
}
