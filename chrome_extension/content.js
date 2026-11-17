/**
 * AlphaZero Chess Bot Content Script (v2.1.0 - Anti-Premove & Strict Turn Verification)
 * Works seamlessly on Lichess.org and Chess.com with 7 Strength Levels (1000 to 3500+ ELO).
 */

(function () {
  console.log("%c[AlphaZero Bot v2.1.0] Loaded", "color: #81b64c; font-weight: bold; font-size: 14px;");

  const STRENGTH_NAMES = {
    1: "🟢 L1: Beginner (~1000 ELO)",
    2: "🟢 L2: Casual (~1400 ELO)",
    3: "🟡 L3: Intermediate (~1800 ELO)",
    4: "🟠 L4: Advanced (~2200 ELO)",
    5: "🔴 L5: Master (~2600 ELO)",
    6: "🟣 L6: Super-GM (~3100 ELO)",
    7: "⚡ L7: Max AlphaZero (3500+)",
    8: "👑 L8: Centaur God (Lc0+SF19 3800+)"
  };

  let currentLevel = 8;
  let autoPlayEnabled = false;
  let showHintsEnabled = true;
  let moveDelayMs = 500;
  let isThinking = false;
  let lastProcessedFen = "";
  let lastSuggestedMove = null;
  let overlayContainer = null;
  let requestVersion = 0;
  let pendingMoveTimer = null;
  let observedPosition = null;
  let executedPosition = null;
  let failedPosition = null;

  // ---- Anti-ban humanizer state -------------------------------------------
  // personaLayer comes from humanizer.js (loaded before this script).
  const personaLayer = window.AZHumanizer || null;
  let humanModeEnabled = personaLayer !== null;
  let persona = personaLayer ? personaLayer.newSession(currentLevel) : null;
  let lastMouse = { x: 400, y: 400 };
  let lastPlyCount = 0;
  let lastUrl = window.location.href;
  let searchStartedAt = 0;
  let expectedThinkMs = 0;

  /** Fresh per-game persona; also clears tilt accumulated in the previous game. */
  function resetPersona() {
    if (!personaLayer) return;
    persona = personaLayer.newSession(currentLevel);
  }

  // -------------------------------------------------------------
  // 1. Create Floating UI Widget & Global Overlay
  // -------------------------------------------------------------
  function setupUI() {
    if (document.getElementById("az-bot-panel")) return;
    if (!document.body) return;

    // Overlay in document.body
    overlayContainer = document.createElement("div");
    overlayContainer.id = "az-board-overlay";
    overlayContainer.style.cssText = "position:absolute; top:0; left:0; width:100%; height:100%; pointer-events:none; z-index:2147483640;";
    document.body.appendChild(overlayContainer);

    const panel = document.createElement("div");
    panel.id = "az-bot-panel";
    panel.innerHTML = `
      <div id="az-header">
        <span>♟️ AlphaZero Bot</span>
        <div id="az-status-container">
          <span id="az-status-text">Connecting...</span>
          <span id="az-status-dot"></span>
        </div>
      </div>
      <div id="az-body">
        <div id="az-info-row">
          <span id="az-side-badge">Side: White</span>
          <span id="az-turn-badge">Turn: White</span>
        </div>

        <!-- STRENGTH LEVEL SLIDER -->
        <div class="az-strength-box">
          <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
            <span style="font-weight:600; color:#ddd;">Engine Power:</span>
            <b id="az-strength-val" style="color:#81b64c;">👑 L8: Centaur God (Lc0+SF19 3800+)</b>
          </div>
          <input type="range" id="az-strength-slider" min="1" max="8" step="1" value="8">
          <div style="display:flex; justify-content:space-between; font-size:9px; color:#777; margin-top:2px;">
            <span>1000</span>
            <span>2000</span>
            <span>3100</span>
            <span>3800+</span>
          </div>
        </div>

        <!-- WDL PROBABILITY BAR & TELEMETRY -->
        <div id="az-wdl-container" style="display:flex; flex-direction:column; gap:3px; background:#191816; padding:6px 8px; border-radius:6px; border:1px solid #333;">
          <div style="display:flex; justify-content:space-between; font-size:10px; color:#bbb;">
            <span id="az-wdl-text">WDL: -- / -- / --</span>
            <span id="az-telemetry-text" style="color:#81b64c; font-family:monospace;">Depth: - | NPS: -</span>
          </div>
          <div id="az-wdl-bar" style="display:flex; height:6px; width:100%; border-radius:3px; overflow:hidden; background:#333;">
            <div id="az-wdl-w" style="width:33.3%; background:#81b64c; transition:width 0.25s;"></div>
            <div id="az-wdl-d" style="width:33.4%; background:#888; transition:width 0.25s;"></div>
            <div id="az-wdl-l" style="width:33.3%; background:#eb6150; transition:width 0.25s;"></div>
          </div>
          <div id="az-pv-box" style="font-size:10px; color:#999; font-family:monospace; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">PV: -</div>
        </div>

        <label class="az-row">
          <input type="checkbox" id="az-autoplay">
          <b>Auto Play Moves</b>
        </label>
        <label class="az-row" title="Random human timing, imperfect move choice, curved mouse paths">
          <input type="checkbox" id="az-human" checked>
          <span>🕵️ Anti-Ban Human Mode</span>
        </label>
        <label class="az-row">
          <input type="checkbox" id="az-show-hints" checked>
          <span>Highlight Best Move</span>
        </label>

        <div class="az-slider-row">
          <div style="display:flex; justify-content:space-between;">
            <span>Move Delay:</span>
            <b id="az-delay-val">500ms</b>
          </div>
          <input type="range" id="az-delay-slider" min="100" max="2500" step="100" value="500">
        </div>

        <div id="az-eval-box">Status: Ready</div>
        <div id="az-fen-warning" style="font-size:10px; color:#ccc;"></div>
        <div style="display:flex; gap:6px;">
          <button id="az-play-now-btn" style="flex:1;">▶ Play Move</button>
          <button id="az-force-btn" style="flex:1; background:#4f4b46;">⚡ Force Move</button>
        </div>
      </div>
    `;

    const style = document.createElement("style");
    style.innerHTML = `
      #az-bot-panel {
        position: fixed;
        bottom: 20px;
        right: 20px;
        width: 255px;
        background: #21201d;
        color: #fff;
        border-radius: 8px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.65);
        border: 1px solid #3d3b38;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 12px;
        z-index: 2147483647;
        user-select: none;
      }
      #az-header {
        background: #2b2926;
        padding: 9px 12px;
        font-weight: 600;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        border-bottom: 1px solid #383531;
        display: flex;
        justify-content: space-between;
        align-items: center;
        cursor: move;
      }
      #az-status-container { display: flex; align-items: center; gap: 6px; }
      #az-status-text { font-size: 10px; color: #aaa; }
      #az-status-dot { width: 8px; height: 8px; border-radius: 50%; background: #fa412d; }
      #az-status-dot.active { background: #81b64c; box-shadow: 0 0 6px #81b64c; }
      #az-body { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
      #az-info-row { display: flex; justify-content: space-between; font-size: 11px; background: #2b2926; padding: 4px 8px; border-radius: 4px; color: #ccc; }
      .az-strength-box {
        background: #191816;
        padding: 8px 10px;
        border-radius: 6px;
        border: 1px solid #333;
      }
      .az-strength-box input {
        width: 100%;
        accent-color: #81b64c;
        cursor: pointer;
        margin: 0;
      }
      .az-row { display: flex; align-items: center; gap: 8px; cursor: pointer; }
      .az-row input[type="checkbox"] { cursor: pointer; width: 14px; height: 14px; accent-color: #81b64c; }
      .az-slider-row { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: #aaa; }
      .az-slider-row input { accent-color: #81b64c; cursor: pointer; }
      #az-eval-box { font-size: 11px; background: #161514; padding: 6px 8px; border-radius: 4px; color: #81b64c; font-family: monospace; overflow: hidden; text-overflow: ellipsis; }
      #az-play-now-btn, #az-force-btn { background: #81b64c; color: #fff; border: none; padding: 7px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 11px; }
      #az-play-now-btn:hover { background: #94c85d; }
      #az-force-btn:hover { background: #656059; }
      .az-highlight-box {
        position: absolute;
        pointer-events: none;
        box-sizing: border-box;
        border-radius: 3px;
      }
      .az-from-box {
        background: rgba(235, 97, 80, 0.45) !important;
        border: 2px solid #eb6150 !important;
      }
      .az-to-box {
        background: rgba(129, 182, 76, 0.55) !important;
        border: 2px solid #81b64c !important;
      }
    `;

    document.head.appendChild(style);
    document.body.appendChild(panel);

    // Restore saved settings
    try {
      const savedLevel = localStorage.getItem("az_level");
      if (savedLevel && STRENGTH_NAMES[savedLevel]) {
        currentLevel = parseInt(savedLevel);
        const slider = document.getElementById("az-strength-slider");
        if (slider) slider.value = currentLevel;
        const valEl = document.getElementById("az-strength-val");
        if (valEl) valEl.innerText = STRENGTH_NAMES[currentLevel];
      }

      const savedAuto = localStorage.getItem("az_autoplay");
      if (savedAuto !== null) {
        autoPlayEnabled = (savedAuto === "1");
        const chk = document.getElementById("az-autoplay");
        if (chk) chk.checked = autoPlayEnabled;
      }

      const savedDelay = localStorage.getItem("az_delay");
      if (savedDelay) {
        moveDelayMs = parseInt(savedDelay);
        const dSlider = document.getElementById("az-delay-slider");
        if (dSlider) dSlider.value = moveDelayMs;
        const dVal = document.getElementById("az-delay-val");
        if (dVal) dVal.innerText = `${moveDelayMs}ms`;
      }

      const savedHuman = localStorage.getItem("az_human");
      if (savedHuman !== null) {
        humanModeEnabled = (savedHuman === "1") && personaLayer !== null;
        const hChk = document.getElementById("az-human");
        if (hChk) hChk.checked = humanModeEnabled;
      }
    } catch (_) {}

    // Anti-ban toggle
    document.getElementById("az-human").addEventListener("change", (e) => {
      invalidateMoveWork();
      humanModeEnabled = e.target.checked && personaLayer !== null;
      try { localStorage.setItem("az_human", humanModeEnabled ? "1" : "0"); } catch (_) {}
      resetPersona();
      updateStatus(humanModeEnabled ? "Anti-Ban human mode ON" : "Anti-Ban human mode OFF");
    });

    // Strength Slider Event
    const strengthSlider = document.getElementById("az-strength-slider");
    strengthSlider.addEventListener("input", (e) => {
      invalidateMoveWork();
      currentLevel = parseInt(e.target.value);
      try { localStorage.setItem("az_level", currentLevel); } catch (_) {}
      resetPersona();
      const name = STRENGTH_NAMES[currentLevel] || `Level ${currentLevel}`;
      document.getElementById("az-strength-val").innerText = name;
      updateStatus(`Power set to: ${name}`);
    });

    // Auto-Play & Delay Events
    document.getElementById("az-autoplay").addEventListener("change", (e) => {
      invalidateMoveWork();
      autoPlayEnabled = e.target.checked;
      try { localStorage.setItem("az_autoplay", autoPlayEnabled ? "1" : "0"); } catch (_) {}
      if (autoPlayEnabled) checkAndPlayMove();
    });

    document.getElementById("az-show-hints").addEventListener("change", (e) => {
      showHintsEnabled = e.target.checked;
      if (!showHintsEnabled) clearHighlights();
    });

    document.getElementById("az-delay-slider").addEventListener("input", (e) => {
      invalidateMoveWork();
      moveDelayMs = parseInt(e.target.value);
      try { localStorage.setItem("az_delay", moveDelayMs); } catch (_) {}
      document.getElementById("az-delay-val").innerText = `${moveDelayMs}ms`;
    });

    document.getElementById("az-play-now-btn").addEventListener("click", () => {
      if (lastSuggestedMove) {
        playSuggestion(lastSuggestedMove, true);
      } else {
        checkAndPlayMove(true);
      }
    });

    document.getElementById("az-force-btn").addEventListener("click", () => {
      checkAndPlayMove(true);
    });

    // Draggable
    let isDragging = false, startX, startY, initX, initY;
    const header = document.getElementById("az-header");
    header.addEventListener("mousedown", (e) => {
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      const rect = panel.getBoundingClientRect();
      initX = rect.left;
      initY = rect.top;
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      panel.style.left = `${initX + (e.clientX - startX)}px`;
      panel.style.top = `${initY + (e.clientY - startY)}px`;
      panel.style.right = "auto";
      panel.style.bottom = "auto";
    });
    window.addEventListener("mouseup", () => { isDragging = false; });

    checkHealth();
    setInterval(checkHealth, 4000);

    // Track the real pointer so approach paths start where the cursor actually is.
    window.addEventListener("mousemove", (e) => {
      lastMouse = { x: e.clientX, y: e.clientY };
    }, { passive: true });
  }

  function updateStatus(text) {
    const el = document.getElementById("az-eval-box");
    if (el) el.innerText = text;
  }

  function checkHealth() {
    chrome.runtime.sendMessage({ type: "CHECK_HEALTH" }, (resp) => {
      const dot = document.getElementById("az-status-dot");
      const txt = document.getElementById("az-status-text");
      if (!dot || !txt) return;

      if (resp && resp.success) {
        dot.classList.add("active");
        txt.innerText = "Centaur GPU+CPU Ready";
      } else {
        dot.classList.remove("active");
        txt.innerText = "Server Offline";
      }
    });
  }

  // -------------------------------------------------------------
  // 2. Board Parsing
  // -------------------------------------------------------------
  function isChessCom() { return window.location.hostname.includes("chess.com"); }
  function isLichess() { return window.location.hostname.includes("lichess.org"); }

  function getBoardElement() {
    if (isChessCom()) {
      return document.querySelector("wc-chess-board") || document.querySelector("chess-board") || document.querySelector(".board");
    } else if (isLichess()) {
      return document.querySelector("cg-board") || document.querySelector(".cg-wrap") || document.querySelector("cg-container");
    }
    return null;
  }

  function getPlayerColor() {
    const stateEl = document.getElementById("az-game-state");
    const officialColor = stateEl ? stateEl.getAttribute("data-player-color") : "";
    if (officialColor) return officialColor;

    const board = getBoardElement();
    if (!board) return "w";

    if (isChessCom()) {
      const isFlipped = board.classList.contains("flipped") || board.hasAttribute("flipped");
      return isFlipped ? "b" : "w";
    } else if (isLichess()) {
      const wrap = document.querySelector(".cg-wrap") || document.querySelector(".round__app") || board.closest(".cg-wrap");
      if (wrap && wrap.classList.contains("orientation-black")) return "b";
      return "w";
    }
    return "w";
  }

  function getCurrentTurn() {
    const stateEl = document.getElementById("az-game-state");
    const activeTurn = stateEl ? stateEl.getAttribute("data-active-turn") : "";
    if (activeTurn) return activeTurn;

    const myColor = getPlayerColor();

    if (isChessCom()) {
      const bottomClock = document.querySelector(".clock-bottom");
      const topClock = document.querySelector(".clock-top");
      if (bottomClock && bottomClock.classList.contains("clock-player-turn")) return myColor;
      if (topClock && topClock.classList.contains("clock-player-turn")) return myColor === "w" ? "b" : "w";
      const moves = document.querySelectorAll("wc-move-list-item, .move-node, .node");
      if (moves.length > 0) return moves.length % 2 === 0 ? "w" : "b";
    } else if (isLichess()) {
      // 1. Clocks
      const bottomClock = document.querySelector(".rclock-bottom.running");
      const topClock = document.querySelector(".rclock-top.running");
      if (bottomClock) return myColor;
      if (topClock) return myColor === "w" ? "b" : "w";

      // 2. Active player indicator
      const bottomUser = document.querySelector(".ruser-bottom.ruser-active");
      const topUser = document.querySelector(".ruser-top.ruser-active");
      if (bottomUser) return myColor;
      if (topUser) return myColor === "w" ? "b" : "w";

      // 3. Move plies count (each move is a <san>)
      const sans = document.querySelectorAll("rmoves san, .moves san, rmove san, l4x u8t, .tview2 move, .tview2 san");
      if (sans.length > 0) return sans.length % 2 === 0 ? "w" : "b";

      // 4. Last rmove children
      const rmoves = document.querySelectorAll("rmoves rmove, rmove");
      if (rmoves.length > 0) {
        const lastRmove = rmoves[rmoves.length - 1];
        const children = lastRmove.querySelectorAll("san, m2");
        if (children.length === 1) return "b";
        if (children.length >= 2) return "w";
      }
    }
    return "w";
  }

  function parseLichessBoard(board) {
    const matrix = Array(8).fill(null).map(() => Array(8).fill(""));
    const pieces = document.querySelectorAll("cg-board piece, .cg-wrap piece");
    if (!pieces || pieces.length === 0) return "";

    const roleMap = { pawn: "p", knight: "n", bishop: "b", rook: "r", queen: "q", king: "k" };
    const isFlipped = getPlayerColor() === "b";
    const boardEl = document.querySelector("cg-board") || board;
    const boardRect = boardEl.getBoundingClientRect();
    const squareSize = boardRect.width / 8;
    if (squareSize <= 0) return "";

    let count = 0;
    pieces.forEach(p => {
      if (p.classList.contains("ghost") || p.classList.contains("anim")) return;
      const isWhite = p.classList.contains("white");
      let role = "";
      for (const r in roleMap) {
        if (p.classList.contains(r)) { role = roleMap[r]; break; }
      }
      if (!role) return;
      const char = isWhite ? role.toUpperCase() : role.toLowerCase();

      const tr = p.style.transform || p.style.webkitTransform || "";
      const match = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(tr);
      let col = -1, row = -1;
      if (match) {
        col = Math.round(parseFloat(match[1]) / squareSize);
        row = Math.round(parseFloat(match[2]) / squareSize);
      } else {
        const pRect = p.getBoundingClientRect();
        col = Math.round((pRect.left - boardRect.left) / squareSize);
        row = Math.round((pRect.top - boardRect.top) / squareSize);
      }

      if (col >= 0 && col < 8 && row >= 0 && row < 8) {
        const file = isFlipped ? (7 - col) : col;
        const rank = isFlipped ? row : (7 - row);
        matrix[7 - rank][file] = char;
        count++;
      }
    });

    if (count < 2) return "";
    return matrixToFen(matrix);
  }

  function parseChessComBoard(board) {
    const matrix = Array(8).fill(null).map(() => Array(8).fill(""));
    const pieces = board.querySelectorAll(".piece");
    if (!pieces || pieces.length === 0) return "";

    let count = 0;
    pieces.forEach(p => {
      let pieceType = "";
      let sqFile = -1, sqRank = -1;
      p.classList.forEach(cls => {
        if (cls.length === 2 && (cls.startsWith("w") || cls.startsWith("b"))) pieceType = cls;
        if (cls.startsWith("square-")) {
          const match = cls.match(/square-0?([1-8])0?([1-8])/);
          if (match) {
            sqFile = parseInt(match[1]) - 1;
            sqRank = parseInt(match[2]) - 1;
          }
        }
      });
      if (pieceType && sqFile >= 0 && sqRank >= 0) {
        const char = pieceType[0] === "w" ? pieceType[1].toUpperCase() : pieceType[1].toLowerCase();
        matrix[7 - sqRank][sqFile] = char;
        count++;
      }
    });

    if (count < 2) return "";
    return matrixToFen(matrix);
  }

  function matrixToFen(matrix) {
    const fenRows = [];
    for (let r = 0; r < 8; r++) {
      let emptyCount = 0;
      let rowStr = "";
      for (let c = 0; c < 8; c++) {
        const p = matrix[r][c];
        if (!p) emptyCount++;
        else {
          if (emptyCount > 0) { rowStr += emptyCount; emptyCount = 0; }
          rowStr += p;
        }
      }
      if (emptyCount > 0) rowStr += emptyCount;
      fenRows.push(rowStr);
    }
    return fenRows.join("/");
  }

  // -------------------------------------------------------------
  // 3. Exact Coordinates & Natural Human Jitter
  // -------------------------------------------------------------
  function gaussianJitter(spread) {
    // Box-Muller bounded within [-spread, +spread]
    const u1 = Math.max(1e-6, Math.random());
    const u2 = Math.random();
    const z = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
    return Math.max(-spread, Math.min(spread, z * (spread * 0.45)));
  }

  function getSquareCoords(squareStr, withJitter = false) {
    const board = document.querySelector("cg-board") || getBoardElement();
    if (!board) return null;

    const file = squareStr.charCodeAt(0) - 97;
    const rank = parseInt(squareStr[1]) - 1;
    const isFlipped = getPlayerColor() === "b";

    const rect = board.getBoundingClientRect();
    const sqW = rect.width / 8;
    const sqH = rect.height / 8;

    let col = isFlipped ? (7 - file) : file;
    let row = isFlipped ? rank : (7 - rank);

    const jX = withJitter ? gaussianJitter(sqW * 0.22) : 0;
    const jY = withJitter ? gaussianJitter(sqH * 0.22) : 0;

    const clientX = rect.left + col * sqW + (sqW / 2) + jX;
    const clientY = rect.top + row * sqH + (sqH / 2) + jY;

    const pageX = window.scrollX + rect.left + col * sqW;
    const pageY = window.scrollY + rect.top + row * sqH;

    return { clientX, clientY, pageX, pageY, sqW, sqH };
  }

  function getPromotionTargetSquare(toSquare, promotionChar) {
    if (!promotionChar) return null;
    const promo = promotionChar.toLowerCase();
    // On both Chess.com and Lichess, the promotion chooser column starts at the promotion square
    // and extends inward toward the center of the board:
    // Offset 0 = Queen (q), 1 = Knight (n), 2 = Rook (r), 3 = Bishop (b)
    const offsetMap = { q: 0, n: 1, r: 2, b: 3 };
    const offset = offsetMap[promo] !== undefined ? offsetMap[promo] : 0;
    const file = toSquare[0];
    const rank = parseInt(toSquare[1]);
    const targetRank = rank === 8 ? (8 - offset) : (1 + offset);
    return `${file}${targetRank}`;
  }

  // -------------------------------------------------------------
  // 4. Move Execution
  // -------------------------------------------------------------
  function executeMove(fromSquare, toSquare, uciMove, onResult) {
    // Standard UCI encodes castling to c/g; a board may only accept king-to-rook.
    // Use the board's legal destinations, never infer castling rights from pieces.
    if (isLichess()) {
      const rookTargets = { e1g1: "h1", e1c1: "a1", e8g8: "h8", e8c8: "a8" };
      const rook = rookTargets[uciMove];
      if (rook) {
        try {
          const state = document.getElementById("az-game-state");
          const dests = JSON.parse(state?.getAttribute("data-move-dests") || "{}")[fromSquare];
          if (Array.isArray(dests) && !dests.includes(toSquare) && dests.includes(rook)) toSquare = rook;
        } catch (_) {}
      }
    }
    const fromC = getSquareCoords(fromSquare, true);
    const toC = getSquareCoords(toSquare, true);
    if (!fromC || !toC) {
      onResult({ success: false, dispatched: false });
      return;
    }

    const promoPiece = uciMove && uciMove.length === 5 ? uciMove[4] : null;
    const promoSq = getPromotionTargetSquare(toSquare, promoPiece);
    const promoC = promoSq ? getSquareCoords(promoSq, true) : null;

    updateStatus(`Playing: ${fromSquare} ➔ ${toSquare}${promoPiece ? "=" + promoPiece.toUpperCase() : ""}`);

    const useHuman = humanModeEnabled && personaLayer && persona;
    let payload;

    if (useHuman) {
      payload = { events: buildHumanEvents(fromC, toC, promoC, uciMove) };
      // Leave time for delivery; animation must not consume the remaining clock.
      const clock = readOwnClockMs();
      if (Number.isFinite(clock)) {
        const duration = payload.events.reduce((sum, event) => sum + event.delayMs, 0);
        const scale = duration > 0 ? Math.min(1, Math.max(0, clock - 250) / duration) : 1;
        for (const event of payload.events) event.delayMs = Math.floor(event.delayMs * scale);
      }
    } else {
      payload = {
        fromX: fromC.clientX,
        fromY: fromC.clientY,
        toX: toC.clientX,
        toY: toC.clientY
      };
      if (promoC) {
        payload.promoX = promoC.clientX;
        payload.promoY = promoC.clientY;
      }
    }

    try {
      chrome.runtime.sendMessage({
        type: useHuman ? "DISPATCH_TRUSTED_INPUT" : "DISPATCH_TRUSTED_CLICK",
        payload
      }, (response) => {
        const error = chrome.runtime.lastError;
        onResult(error ? null : response);
      });
    } catch (_) {
      onResult(null);
    }
  }

  /**
   * Builds a full CDP event script: travel to the piece, press, optional drag, release.
   * The drag style is a per-game persona choice (real players split ~40/60 between drag and click-click).
   */
  function buildHumanEvents(fromC, toC, promoC, uciMove) {
    const events = [];
    const from = { x: fromC.clientX, y: fromC.clientY };
    const to = { x: toC.clientX, y: toC.clientY };
    const sqSize = fromC.sqW;

    // Approach the piece from wherever the cursor already is — never teleport.
    const approach = personaLayer.mousePath(lastMouse, from, persona, sqSize);
    for (const p of approach) {
      events.push({ type: "mouseMoved", x: p.x, y: p.y, delayMs: personaLayer.stepDelay(persona, p.t) });
    }

    events.push({ type: "mousePressed", x: from.x, y: from.y, delayMs: Math.round(persona.pressHoldMs) });

    if (persona.dragStyle || (uciMove && uciMove.length === 5)) {
      // Drag: hold the piece and slide it with decelerating motion.
      const travel = personaLayer.mousePath(from, to, persona, sqSize);
      for (const p of travel) {
        events.push({ type: "mouseMoved", x: p.x, y: p.y, delayMs: personaLayer.stepDelay(persona, p.t) });
      }
      events.push({ type: "mouseReleased", x: to.x, y: to.y, delayMs: 30 + Math.random() * 90 });
    } else {
      // Click-click: release on the origin, travel, then click the destination.
      events.push({ type: "mouseReleased", x: from.x, y: from.y, delayMs: 0 });
      const travel = personaLayer.mousePath(from, to, persona, sqSize);
      for (const p of travel) {
        events.push({ type: "mouseMoved", x: p.x, y: p.y, delayMs: personaLayer.stepDelay(persona, p.t) });
      }
      events.push({ type: "mousePressed", x: to.x, y: to.y, delayMs: Math.round(persona.pressHoldMs * 0.8) });
      events.push({ type: "mouseReleased", x: to.x, y: to.y, delayMs: 0 });
    }

    if (promoC) {
      const promo = { x: promoC.clientX, y: promoC.clientY };
      const hop = personaLayer.mousePath(to, promo, persona, sqSize);
      for (const p of hop) {
        events.push({ type: "mouseMoved", x: p.x, y: p.y, delayMs: personaLayer.stepDelay(persona, p.t) });
      }
      events.push({ type: "mousePressed", x: promo.x, y: promo.y, delayMs: 40 });
      events.push({ type: "mouseReleased", x: promo.x, y: promo.y, delayMs: 0 });
    }

    lastMouse = promoC ? { x: promoC.clientX, y: promoC.clientY } : to;
    return events;
  }

  // -------------------------------------------------------------
  // 5. Highlights & SVG Arrows
  // -------------------------------------------------------------
  function clearHighlights() {
    if (overlayContainer) overlayContainer.innerHTML = "";
  }

  function highlightMove(fromSquare, toSquare, ponderMove) {
    clearHighlights();
    if (!showHintsEnabled || !overlayContainer) return;

    const fromC = getSquareCoords(fromSquare);
    const toC = getSquareCoords(toSquare);
    if (!fromC || !toC) return;

    const fromBox = document.createElement("div");
    fromBox.className = "az-highlight-box az-from-box";
    fromBox.style.width = `${fromC.sqW}px`;
    fromBox.style.height = `${fromC.sqH}px`;
    fromBox.style.left = `${fromC.pageX}px`;
    fromBox.style.top = `${fromC.pageY}px`;

    const toBox = document.createElement("div");
    toBox.className = "az-highlight-box az-to-box";
    toBox.style.width = `${toC.sqW}px`;
    toBox.style.height = `${toC.sqH}px`;
    toBox.style.left = `${toC.pageX}px`;
    toBox.style.top = `${toC.pageY}px`;

    overlayContainer.appendChild(fromBox);
    overlayContainer.appendChild(toBox);

    // Draw SVG vector arrow over the board
    try {
      const svg = document.createElement("div");
      const x1 = fromC.pageX + fromC.sqW / 2;
      const y1 = fromC.pageY + fromC.sqH / 2;
      const x2 = toC.pageX + toC.sqW / 2;
      const y2 = toC.pageY + toC.sqH / 2;

      let ponderLine = "";
      if (ponderMove && /^[a-h][1-8][a-h][1-8]/.test(ponderMove)) {
        const pFrom = getSquareCoords(ponderMove.slice(0, 2));
        const pTo = getSquareCoords(ponderMove.slice(2, 4));
        if (pFrom && pTo) {
          const px1 = pFrom.pageX + pFrom.sqW / 2;
          const py1 = pFrom.pageY + pFrom.sqH / 2;
          const px2 = pTo.pageX + pTo.sqW / 2;
          const py2 = pTo.pageY + pTo.sqH / 2;
          ponderLine = `<line x1="${px1}" y1="${py1}" x2="${px2}" y2="${py2}" stroke="rgba(245, 166, 35, 0.65)" stroke-width="5" stroke-dasharray="6,4" marker-end="url(#az-arrow-ponder)" />`;
        }
      }

      svg.style.cssText = "position:absolute; top:0; left:0; width:100%; height:100%; pointer-events:none;";
      svg.innerHTML = `
        <svg width="100%" height="100%" style="position:absolute; top:0; left:0; overflow:visible; pointer-events:none;">
          <defs>
            <marker id="az-arrow-best" markerWidth="4" markerHeight="4" refX="2.4" refY="2" orient="auto">
              <polygon points="0 0, 4 2, 0 4" fill="rgba(129, 182, 76, 0.92)" />
            </marker>
            <marker id="az-arrow-ponder" markerWidth="4" markerHeight="4" refX="2.4" refY="2" orient="auto">
              <polygon points="0 0, 4 2, 0 4" fill="rgba(245, 166, 35, 0.75)" />
            </marker>
          </defs>
          ${ponderLine}
          <line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="rgba(129, 182, 76, 0.88)" stroke-width="8" stroke-linecap="round" marker-end="url(#az-arrow-best)" />
        </svg>
      `;
      overlayContainer.appendChild(svg);
    } catch (_) {}
  }

  function updateTelemetryUI(data) {
    const wdlText = document.getElementById("az-wdl-text");
    const telText = document.getElementById("az-telemetry-text");
    const pvBox = document.getElementById("az-pv-box");
    const barW = document.getElementById("az-wdl-w");
    const barD = document.getElementById("az-wdl-d");
    const barL = document.getElementById("az-wdl-l");

    if (Array.isArray(data.wdl) && data.wdl.length === 3) {
      const [w, d, l] = data.wdl;
      const total = Math.max(1, w + d + l);
      const wp = ((w / total) * 100).toFixed(0);
      const dp = ((d / total) * 100).toFixed(0);
      const lp = ((l / total) * 100).toFixed(0);
      if (wdlText) wdlText.innerText = `W: ${wp}% | D: ${dp}% | L: ${lp}%`;
      if (barW) barW.style.width = `${(w / total) * 100}%`;
      if (barD) barD.style.width = `${(d / total) * 100}%`;
      if (barL) barL.style.width = `${(l / total) * 100}%`;
    }

    if (telText) {
      let scoreStr = "";
      if (typeof data.mate === "number") {
        scoreStr = `M${data.mate > 0 ? "+" : ""}${data.mate}`;
      } else if (typeof data.score_cp === "number") {
        const pawns = (data.score_cp / 100).toFixed(2);
        scoreStr = `${data.score_cp >= 0 ? "+" : ""}${pawns}`;
      }
      const depthStr = data.depth ? `d${data.depth}` : "";
      const npsStr = data.nps ? `${(data.nps / 1000).toFixed(1)}k/s` : "";
      telText.innerText = [scoreStr, depthStr, npsStr].filter(Boolean).join(" • ") || "Ready";
    }

    if (pvBox) {
      const pvList = Array.isArray(data.pv) && data.pv.length > 0 ? data.pv.join(" ") : (data.bestmove || "-");
      pvBox.innerText = `PV: ${pvList}`;
    }
  }

  // -------------------------------------------------------------
  // 6. Game Loop
  // -------------------------------------------------------------
  function invalidateMoveWork() {
    requestVersion++;
    if (pendingMoveTimer !== null) clearTimeout(pendingMoveTimer);
    pendingMoveTimer = null;
    isThinking = false;
    lastProcessedFen = "";
    lastSuggestedMove = null;
    clearHighlights();
  }

  function samePosition(a, b) {
    return !!a && !!b && a.fen === b.fen && a.turn === b.turn && a.board === b.board &&
      a.color === b.color && a.url === b.url;
  }

  function observePosition() {
    const position = readPosition();
    if (!samePosition(position, observedPosition)) {
      // A fresh game (URL change or ply counter went backwards) earns a new persona.
      if (position && (position.url !== lastUrl || position.ply < lastPlyCount)) {
        lastUrl = position.url;
        resetPersona();
      }
      if (position) lastPlyCount = position.ply;
      invalidateMoveWork();
      executedPosition = null;
      failedPosition = null;
      observedPosition = position;
    }
    return position;
  }

  function playSuggestion(suggestion, manual = false) {
    const position = observePosition();
    if (suggestion.version !== requestVersion || !samePosition(position, suggestion.position) ||
        samePosition(position, executedPosition) || (!manual && !suggestion.force && !autoPlayEnabled)) return;
    if (!suggestion.force && position.turn !== position.color) return;
    if (pendingMoveTimer !== null) clearTimeout(pendingMoveTimer);
    pendingMoveTimer = null;
    lastSuggestedMove = null;
    executedPosition = position;
    isThinking = false;
    executeMove(suggestion.from, suggestion.to, suggestion.uci, (result) => {
      if (executedPosition !== position || !samePosition(position, readPosition())) return;
      if (result && result.success === false && result.dispatched === false) {
        executedPosition = null;
        failedPosition = position;
        updateStatus("Move not dispatched. Play Move or Force Move to retry.");
      } else if (!result || result.success !== true) {
        updateStatus("Dispatch outcome unknown; retry blocked to avoid duplicate moves.");
      }
    });
  }

  function readPosition() {
    const board = getBoardElement();
    if (!board) return null;

    let fullFen = "";
    const stateEl = document.getElementById("az-game-state");
    const officialFen = stateEl ? stateEl.getAttribute("data-official-fen") : "";
    const officialColor = stateEl ? stateEl.getAttribute("data-player-color") : "";
    const officialTurn = stateEl ? stateEl.getAttribute("data-active-turn") : "";
    const rawUciMoves = stateEl ? stateEl.getAttribute("data-uci-moves") : "";
    const uciMoves = rawUciMoves ? rawUciMoves.trim().split(/\s+/).filter(Boolean) : [];

    const playerColor = officialColor || getPlayerColor();
    let currentTurn = officialTurn || getCurrentTurn();

    const completeFen = officialFen && officialFen.trim().split(/\s+/).length === 6;
    if (completeFen) {
      const parts = officialFen.trim().split(/\s+/);
      if (officialTurn === "w" || officialTurn === "b") {
        currentTurn = officialTurn;
        parts[1] = officialTurn;
      } else {
        currentTurn = parts[1];
      }
      fullFen = parts.join(" ");
    } else {
      let partialFen = officialFen ? officialFen.trim().split(/\s+/)[0] : "";
      if (!partialFen && isChessCom()) partialFen = parseChessComBoard(board);
      else if (!partialFen && isLichess()) partialFen = parseLichessBoard(board);

      if (!partialFen) return;

      fullFen = `${partialFen} ${currentTurn} - - 0 1`;
    }

    const sideBadge = document.getElementById("az-side-badge");
    const turnBadge = document.getElementById("az-turn-badge");
    if (sideBadge) sideBadge.innerText = `You: ${playerColor === "w" ? "White" : "Black"}`;
    if (turnBadge) turnBadge.innerText = `Turn: ${currentTurn === "w" ? "White" : "Black"}`;

    const limitation = document.getElementById("az-fen-warning");
    if (limitation) limitation.textContent = (completeFen || uciMoves.length > 0) ? "" :
      "Partial position: castling/en passant unavailable; move counters unknown.";
    return {
      fen: fullFen,
      moves: uciMoves,
      ply: uciMoves.length,
      clock: readOwnClockMs(),
      color: playerColor,
      turn: currentTurn,
      board,
      url: window.location.href
    };
  }

  /** Parses the player's own remaining clock, or null when the site does not expose one. */
  function readOwnClockMs() {
    let el = null;
    if (isChessCom()) {
      el = document.querySelector(".clock-bottom .clock-time-monospace, .clock-bottom");
    } else if (isLichess()) {
      // Player clocks are positioned relative to the board, not piece colour.
      el = document.querySelector(".rclock-bottom .time, .rclock-bottom");
    }
    if (!el) return null;
    const text = (el.textContent || "").replace(/\s+/g, "");
    const match = /^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d+))?$/.exec(text);
    if (!match) return null;
    const hours = Number(match[1] || 0);
    const minutes = Number(match[2]);
    const secondsPart = Number(match[3]);
    if (secondsPart >= 60 || (match[1] && minutes >= 60)) return null;
    const seconds = hours * 3600 + minutes * 60 + secondsPart + Number("0." + (match[4] || "0"));
    return Number.isFinite(seconds) ? seconds * 1000 : null;
  }

  function checkAndPlayMove(force = false) {
    const position = observePosition();
    if (!position || samePosition(position, executedPosition)) return;
    if (!force && samePosition(position, failedPosition)) return;
    if (force) invalidateMoveWork();
    if (isThinking || pendingMoveTimer !== null) return;
    const fullFen = position.fen;
    const isMyTurn = position.color === position.turn;
    if (!force && fullFen === lastProcessedFen) return;
    const version = ++requestVersion;

    const useHuman = humanModeEnabled && personaLayer && persona;

    // Plan the human think budget now, so it is measured from the instant the
    // opponent's move appeared — not from when the engine happened to answer.
    let planCtx = null;
    if (useHuman) {
      const moves = position.moves || [];
      const recapture = moves.length >= 2 && moves[moves.length - 1].slice(2, 4) === moves[moves.length - 2].slice(2, 4);
      const boardField = (fullFen || "").split(" ")[0] || "";
      const pieceCount = (boardField.match(/[a-zA-Z]/g) || []).length;
      planCtx = {
        baseTimeMs: moveDelayMs * 1.35,
        clockLeftMs: position.clock,
        isForced: false,
        isRecapture: recapture,
        pieceCount,
        inCheck: false
      };
      expectedThinkMs = force ? 60 : personaLayer.thinkTime(persona, planCtx);
      searchStartedAt = Date.now();
    } else {
      expectedThinkMs = 0;
      searchStartedAt = Date.now();
    }

    isThinking = true;
    updateStatus(isMyTurn ? `Thinking (Level ${currentLevel})...` : "Analyzing position...");

    const payload = {
      fen: fullFen,
      level: currentLevel
    };
    if (Array.isArray(position.moves) && position.moves.length > 0) {
      // Full move history lets Lc0 keep its 8-plane history and detect three-fold repetition.
      payload.fen = "";
      payload.moves = position.moves;
    }
    // Human mode asks the engine for close alternatives so the bot is not always 100% top-move.
    if (useHuman && currentLevel >= 3) payload.multipv = 4;

    chrome.runtime.sendMessage({
      type: "GET_MOVE",
      payload
    }, (resp) => {
      const currentPosition = observePosition();
      if (version !== requestVersion || !samePosition(position, currentPosition)) return;
      isThinking = false;
      if (!resp || !resp.success || !resp.data) {
        updateStatus("Engine error");
        return;
      }

      const data = resp.data;
      if (useHuman && !force && (typeof data.in_check === "boolean" || Number.isInteger(data.legal_move_count))) {
        planCtx.inCheck = data.in_check === true;
        planCtx.isForced = data.legal_move_count === 1;
        expectedThinkMs = personaLayer.thinkTime(persona, planCtx);
      }
      lastProcessedFen = fullFen;

      // Pick a move the way a human of this persona would, not always rank 1.
      let chosen = null;
      if (useHuman && Array.isArray(data.candidates) && data.candidates.length > 0) {
        chosen = personaLayer.choose(data.candidates, persona);
      }
      const bestMove = chosen ? chosen.move : data.bestmove;
      if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(bestMove || "")) return;
      const fromSq = bestMove.slice(0, 2);
      const toSq = bestMove.slice(2, 4);

      const suggestion = { from: fromSq, to: toSq, uci: bestMove, position, version, force };
      lastSuggestedMove = suggestion;

      const rankNote = chosen && chosen.rank > 0 ? ` [rank ${chosen.rank + 1}]` : "";
      updateStatus(`[L${currentLevel}] Best: ${bestMove}${rankNote}`);
      updateTelemetryUI(data);
      highlightMove(fromSq, toSq, data.ponder);

      if ((autoPlayEnabled && isMyTurn) || force) {
        if (!useHuman) {
          const jitter = Math.floor((Math.random() - 0.5) * (moveDelayMs * 0.6));
          const legacyDelay = force ? 50 : Math.max(250, moveDelayMs + jitter);
          pendingMoveTimer = setTimeout(() => {
            const freshPos = observePosition();
            if (!force && freshPos && freshPos.turn !== freshPos.color) {
              console.log("[AlphaZero Bot] Premove blocked: opponent is still thinking.");
              pendingMoveTimer = null;
              return;
            }
            playSuggestion(suggestion);
          }, legacyDelay);
          return;
        }

        // ---- Human timing path ----
        // The engine's own latency is the floor; the persona stretches it variably, so a
        // 3-second search never collapses back to a fixed cadence.
        const elapsed = Date.now() - searchStartedAt;
        const floor = Math.max(55, elapsed * personaLayer.stretch(persona));
        const target = Math.max(expectedThinkMs, floor);
        let remaining = Math.max(45, target - elapsed + (Math.random() - 0.5) * 90);

        // Occasional walk-away pause, capped by the clock when time is short.
        let pauseMs = 0;
        if (!force && personaLayer.shouldPause(persona)) {
          pauseMs = 7000 + Math.random() * 14000;
          const left = position.clock;
          if (Number.isFinite(left)) pauseMs = Math.min(pauseMs, Math.max(0, (left - elapsed) * 0.2));
          pauseMs = Math.round(pauseMs);
        }

        let waitMs = Math.round(remaining + pauseMs);
        const freshClock = currentPosition.clock;
        const clockBudgets = [];
        if (Number.isFinite(position.clock)) clockBudgets.push(position.clock - elapsed);
        if (Number.isFinite(freshClock)) clockBudgets.push(freshClock);
        if (clockBudgets.length) {
          // Reserve room for the input sequence and delivery after engine latency.
          waitMs = Math.min(waitMs, Math.max(0, Math.min(...clockBudgets) - 2500));
        }
        pendingMoveTimer = setTimeout(() => {
          // Release the slot before re-evaluating: playSuggestion re-checks everything and
          // a failed guard must not leave the poll loop permanently blocked.
          pendingMoveTimer = null;
          const freshPos = observePosition();
          // Never premove: if the opponent's move has not landed yet, retry shortly.
          if (!force && freshPos && freshPos.turn !== freshPos.color) {
            if (isThinking) return;
            pendingMoveTimer = setTimeout(() => {
              pendingMoveTimer = null;
              playSuggestion(suggestion);
            }, 180 + Math.random() * 320);
            return;
          }
          if (isThinking) return;
          playSuggestion(suggestion);
        }, waitMs);
      } else {
        lastProcessedFen = fullFen;
      }
    });
  }

  function init() {
    setupUI();
    // Randomized cadence: a fixed 800ms poll is a recognizable bot signature.
    schedulePoll(700 + Math.random() * 700);
  }

  function schedulePoll(delay) {
    setTimeout(() => {
      if (!document.getElementById("az-bot-panel")) setupUI();
      checkAndPlayMove();
      const next = humanModeEnabled && personaLayer ? personaLayer.pollInterval() : 800;
      schedulePoll(next);
    }, delay);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

})();
