/**
 * Runs in the webpage's MAIN world execution context.
 * Has direct access to window and Chess.com/Lichess internal JavaScript controllers.
 * Exposes 100% accurate LIVE game FEN, Player Color, and Turn.
 */

(function () {
  console.log("%c[AlphaZero Bot] Main World Hook v2.0 active!", "color: #81b64c; font-weight: bold;");

  let stateEl = document.getElementById("az-game-state");
  if (!stateEl) {
    stateEl = document.createElement("div");
    stateEl.id = "az-game-state";
    stateEl.style.display = "none";
    document.documentElement.appendChild(stateEl);
  }

  function syncOfficialGameState() {
    let fen = "";
    let playerColor = "";
    let activeTurn = "";
    let uciMoves = [];
    let moveDests = {};

    // 1. Lichess official state
    try {
      if (window.lichess) {
        const round = window.lichess.round;
        const analysis = window.lichess.analysis;
        const cg = round?.chessground || analysis?.chessground || window.lichess.chessground;

        // Ground-truth Turn & Orientation from Chessground state
        if (cg && cg.state) {
          if (cg.state.movable?.dests instanceof Map) {
            moveDests = Object.fromEntries(cg.state.movable.dests);
          }
          if (cg.state.turnColor) {
            activeTurn = (cg.state.turnColor === "black" ? "b" : "w");
          }
          if (cg.state.orientation) {
            playerColor = (cg.state.orientation === "black" ? "b" : "w");
          }
        }

        if (round && round.data) {
          const d = round.data;
          if (!playerColor && d.player?.color) {
            playerColor = (d.player.color === "black" ? "b" : "w");
          }

          if (!activeTurn && d.game?.player) {
            activeTurn = (d.game.player === "black" ? "b" : "w");
          }

          // Get the LIVE step (most recent move in game) and full UCI move history
          const steps = d.steps || [];
          if (steps.length > 0) {
            const lastStep = steps[steps.length - 1];
            fen = lastStep.fen || "";
            if (!activeTurn && typeof lastStep.ply === "number") {
              activeTurn = (lastStep.ply % 2 === 0) ? "w" : "b";
            }
            const extracted = [];
            let validHistory = true;
            for (let i = 1; i < steps.length; i++) {
              const u = steps[i]?.uci;
              if (typeof u === "string" && /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(u)) {
                extracted.push(u);
              } else {
                validHistory = false;
                break;
              }
            }
            if (validHistory && extracted.length > 0 && (!steps[0]?.fen || steps[0].fen.startsWith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"))) {
              uciMoves = extracted;
            }
          } else if (d.game?.treeParts && d.game.treeParts.length > 0) {
            fen = d.game.treeParts[d.game.treeParts.length - 1].fen || "";
          }

          if (!activeTurn) {
            const turns = typeof d.game?.turns === "number" ? d.game.turns : (steps.length > 0 ? steps.length - 1 : 0);
            activeTurn = (turns % 2 === 0) ? "w" : "b";
          }


        } else if (analysis) {
          if (analysis.tree && typeof analysis.tree.last === "function") {
            const lastNode = analysis.tree.last();
            fen = lastNode ? lastNode.fen : "";
            if (lastNode && typeof lastNode.ply === "number") {
              activeTurn = (lastNode.ply % 2 === 0) ? "w" : "b";
            }
          } else if (analysis.data?.steps && analysis.data.steps.length > 0) {
            fen = analysis.data.steps[analysis.data.steps.length - 1].fen || "";
          }
        }
      }
    } catch (_) {}

    // 2. Chess.com official state
    try {
      const board = document.querySelector("wc-chess-board") || document.querySelector("chess-board");
      if (board && board.game) {
        if (typeof board.game.getFEN === "function") {
          fen = board.game.getFEN();
          if (fen && fen.split(" ").length >= 2) {
            activeTurn = fen.split(" ")[1];
          }
        }
        if (typeof board.game.getTurn === "function") {
          const t = board.game.getTurn();
          activeTurn = (t === 2 || t === "b" || t === "black") ? "b" : "w";
        }
        if (typeof board.game.getPlayingAs === "function") {
          const p = board.game.getPlayingAs();
          playerColor = (p === 2 || p === "b" || p === "black") ? "b" : "w";
        } else if (board.game.getOptions) {
          playerColor = board.game.getOptions().orientation === "black" ? "b" : "w";
        }
        if (typeof board.game.getMoves === "function") {
          const rawMoves = board.game.getMoves();
          if (Array.isArray(rawMoves) && rawMoves.length > 0) {
            const extracted = [];
            let valid = true;
            for (const m of rawMoves) {
              if (typeof m === "string" && /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(m)) {
                extracted.push(m);
              } else if (m && typeof m.from === "string" && typeof m.to === "string") {
                const prom = m.promotion ? String(m.promotion).toLowerCase() : "";
                const candidate = `${m.from}${m.to}${prom}`;
                if (/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(candidate)) extracted.push(candidate);
                else { valid = false; break; }
              } else {
                valid = false;
                break;
              }
            }
            if (valid && extracted.length > 0) {
              uciMoves = extracted;
            }
          }
        }
      }
    } catch (_) {}

    if (stateEl) {
      stateEl.setAttribute("data-official-fen", fen || "");
      stateEl.setAttribute("data-player-color", playerColor || "");
      stateEl.setAttribute("data-active-turn", activeTurn || "");
      stateEl.setAttribute("data-uci-moves", uciMoves.length > 0 ? uciMoves.join(" ") : "");
      stateEl.setAttribute("data-move-dests", JSON.stringify(moveDests));
    }
  }

  setInterval(syncOfficialGameState, 200);

  // Optional socket helper if available
  window.addEventListener("AZ_EXECUTE_DIRECT_MOVE", (event) => {
    const detail = event.detail;
    if (!detail) return;
    const { uci } = detail;

    try {
      if (window.lichess && window.lichess.socket && typeof window.lichess.socket.send === "function") {
        window.lichess.socket.send("move", { u: uci });
      }
    } catch (_) {}
  });
})();
