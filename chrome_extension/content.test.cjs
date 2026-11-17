const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function harness() {
  const elements = new Map();
  function element() {
    return { style: {}, classList: { contains: () => false, add() {}, remove() {} },
      addEventListener(type, fn) { this[type] = fn; }, appendChild() {},
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 800 }) };
  }
  for (const id of ["az-strength-slider", "az-strength-val", "az-autoplay", "az-show-hints",
  "az-delay-slider", "az-delay-val", "az-play-now-btn", "az-force-btn", "az-header",
  "az-eval-box", "az-fen-warning", "az-side-badge", "az-turn-badge", "az-human",
  "az-wdl-text", "az-telemetry-text", "az-pv-box", "az-wdl-w", "az-wdl-d", "az-wdl-l"]) elements.set(id, element());
const attrs = { "data-official-fen": "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", "data-player-color": "w", "data-active-turn": "w", "data-uci-moves": "" };
  elements.set("az-game-state", { getAttribute: key => attrs[key] });
  const board = element();
  const requests = [], clicks = [], timers = new Map();
  let nextTimer = 0;
  let now = 10000;
  const delays = [];
  const clock = { textContent: "" };
  const selectors = [];
  const context = vm.createContext({ console: { log() {} }, Math,
    Date: { now: () => now },
    document: { readyState: "loading", addEventListener() {}, body: element(), head: element(),
      createElement: element, getElementById: id => elements.get(id),
      querySelector: selector => { selectors.push(selector); return selector.includes("clock") ? clock : board; } },
    window: { location: { hostname: "chess.com", href: "https://www.chess.com/game/1" },
      addEventListener() {}, dispatchEvent() {}, scrollX: 0, scrollY: 0 },
    CustomEvent: function (type, options) { this.detail = options.detail; },
    localStorage: { getItem: () => null, setItem() {} },
    chrome: { runtime: { sendMessage(msg, callback) {
      if (msg.type === "GET_MOVE") requests.push({ msg, callback });
      if (msg.type === "DISPATCH_TRUSTED_CLICK" || msg.type === "DISPATCH_TRUSTED_INPUT") clicks.push({ msg, callback });
    } } },
    setInterval() {}, setTimeout(fn, ms) { delays.push(ms); timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout(id) { timers.delete(id); } });
  // The humanizer must be present on window before content.js reads it.
  vm.runInContext(fs.readFileSync(path.join(__dirname, "humanizer.js"), "utf8"), context);
  const source = fs.readFileSync(path.join(__dirname, "content.js"), "utf8");
  vm.runInContext(source.replace("  function init() {", "  globalThis.api = { setupUI, checkAndPlayMove, readOwnClockMs };\n  function init() {"), context);
  context.api.setupUI();
  return { attrs, requests, clicks, timers, elements, clock, selectors, delays, context,
    advance: ms => { now += ms; }, readClock: () => context.api.readOwnClockMs(),
    humanizer: context.window.AZHumanizer, poll: () => context.api.checkAndPlayMove(),
    event(id, type, value) { elements.get(id)[type]({ target: { checked: value, value } }); },
    respond(index = requests.length - 1) { requests[index].callback({ success: true, data: { bestmove: "a1a2", from: "a1", to: "a2" } }); },
    fire() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()); } };
}

test("clock parsing preserves fractions and reads the bottom clock for black", () => {
  const h = harness();
  h.context.window.location.hostname = "lichess.org";
  h.attrs["data-player-color"] = "b";
  for (const [value, expected] of [["00:00.3", 300], [" 03 : 02 ", 182000], ["1:02:03.5", 3723500], ["00:00", 0], ["00:99", null], ["missing", null]]) {
    h.clock.textContent = value;
    assert.equal(h.readClock(), expected);
  }
  assert.equal(h.selectors.at(-1), ".rclock-bottom .time, .rclock-bottom");
});

test("Lichess castling targets follow advertised destinations for both wings and colours", () => {
  for (const [move, rook] of [["e1g1", "h1"], ["e1c1", "a1"], ["e8g8", "h8"], ["e8c8", "a8"]]) {
    for (const rookOnly of [true, false]) {
      const h = harness();
      h.context.window.location.hostname = "lichess.org";
      h.event("az-human", "change", false);
      const from = move.slice(0, 2), to = move.slice(2);
      const black = from[1] === "8";
      h.attrs["data-player-color"] = black ? "b" : "w";
      h.attrs["data-active-turn"] = black ? "b" : "w";
      h.attrs["data-move-dests"] = JSON.stringify({ [from]: rookOnly ? [rook] : [to, rook] });
      h.attrs["data-official-fen"] = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1";
      h.poll();
      h.requests[0].callback({ success: true, data: { bestmove: move } });
      h.event("az-play-now-btn", "click");
      const target = rookOnly ? rook : to;
      const p = h.clicks[0].msg.payload;
      const file = target.charCodeAt(0) - 97;
      assert.equal(Math.floor(p.toX / 100), black ? 7 - file : file);
      assert.equal(Math.floor(p.toY / 100), black ? Number(target[1]) - 1 : 8 - Number(target[1]));
    }
  }
});

test("low clock bounds pauses and input delays after engine latency", () => {
  const h = harness();
  h.clock.textContent = "00:05";
  h.humanizer.shouldPause = () => true;
  h.humanizer.thinkTime = () => 22000;
  h.humanizer.stretch = () => 2.4;
  h.event("az-autoplay", "change", true);
  h.advance(4800);
  h.clock.textContent = "00:00.2";
  h.respond();
  assert.equal(h.delays.at(-1), 0);
  h.fire();
  assert.equal(h.clicks.length, 1);
  assert.equal(h.clicks[0].msg.payload.events.reduce((sum, event) => sum + event.delayMs, 0), 0);
});

test("forced reply and check state come from validated server position", () => {
  const h = harness();
  const contexts = [];
  h.humanizer.thinkTime = (persona, ctx) => { contexts.push({ ...ctx }); return 100; };
  h.poll();
  h.requests[0].callback({ success: true, data: { bestmove: "a1a2", in_check: true, legal_move_count: 1 } });
  assert.equal(contexts.at(-1).inCheck, true);
  assert.equal(contexts.at(-1).isForced, true);
});

test("explicit non-dispatch permits deliberate retry but never automatic retry", () => {
  const h = harness();
  h.event("az-autoplay", "change", true);
  h.respond();
  h.fire();
  h.clicks[0].callback({ success: false, dispatched: false });
  h.event("az-strength-slider", "input", 2);
  for (let i = 0; i < 5; i++) h.poll();
  assert.equal(h.requests.length, 1);
  h.event("az-play-now-btn", "click");
  h.respond();
  h.fire();
  assert.equal(h.clicks.length, 2);
  h.clicks[0].callback({ success: false, dispatched: false });
  h.clicks[1].callback({ success: true });
  h.event("az-force-btn", "click");
  h.poll();
  assert.equal(h.requests.length, 2);
});

 test("uncertain dispatch and success retain duplicate protection", () => {
  for (const result of [undefined, { success: false }, { success: false, dispatched: "unknown" }, { success: true }]) {
    const h = harness();
    h.poll();
    h.respond();
    h.event("az-play-now-btn", "click");
    h.clicks[0].callback(result);
    h.event("az-force-btn", "click");
    h.event("az-play-now-btn", "click");
    h.poll();
    assert.equal(h.clicks.length, 1);
    assert.equal(h.requests.length, 1);
  }
});

test("partial FEN never invents rights and warns; complete rights survive", () => {
  const h = harness();
  h.poll();
  assert.match(h.requests[0].msg.payload.fen, / w KQ - 0 1$/);
  h.attrs["data-official-fen"] = "4k3/8/8/8/8/8/8/R3K2R";
  h.poll();
  assert.match(h.requests[1].msg.payload.fen, / w - - 0 1$/);
  assert.match(h.elements.get("az-fen-warning").textContent, /castling\/en passant unavailable/);
});

test("polling during long delay and repeated manual clicks execute once", () => {
  const h = harness();
  h.event("az-delay-slider", "input", 2500);
  h.event("az-autoplay", "change", true);
  h.respond();
  for (let i = 0; i < 5; i++) h.poll();
  assert.equal(h.requests.length, 1);
  h.event("az-play-now-btn", "click");
  h.event("az-play-now-btn", "click");
  h.fire();
  h.poll();
  assert.equal(h.clicks.length, 1);
  assert.equal(h.requests.length, 1);
});

test("late response cannot replace a newer position suggestion", () => {
  const h = harness();
  h.poll();
  h.attrs["data-official-fen"] = "4k3/8/8/8/8/7P/8/R3K2R w KQ - 0 2";
  h.poll();
  h.respond(1);
  h.respond(0);
  h.event("az-play-now-btn", "click");
  assert.equal(h.clicks.length, 1);
});

test("position changes invalidate timers and stored manual moves without polling", () => {
  for (const auto of [true, false]) {
    const h = harness();
    h.event("az-autoplay", "change", auto);
    h.poll();
    h.respond();
    h.attrs["data-active-turn"] = "b";
    if (auto) h.fire();
    else h.event("az-play-now-btn", "click");
    assert.equal(h.clicks.length, 0);
  }
});

for (const [id, type, value] of [["az-human", "change", false], ["az-autoplay", "change", false], ["az-strength-slider", "input", 3], ["az-delay-slider", "input", 1200]]) {
  test(`${id} cancels queued moves and rejects in-flight responses`, () => {
    for (const pending of [true, false]) {
      const h = harness();
      h.event("az-autoplay", "change", true);
      if (pending) h.respond();
      h.event(id, type, value);
      if (!pending) h.respond();
      h.fire();
      assert.equal(h.clicks.length, 0);
      assert.equal(h.timers.size, 0);
    }
  });
}

test("main-world hook preserves partial positions and clears unavailable state", () => {
  const attrs = {};
  let sync;
  const window = { lichess: { round: { data: { steps: [{ fen: "4k3/8/8/8/8/8/8/R3K2R", ply: 0 }] } } }, addEventListener() {} };
  window.lichess.round.chessground = { state: { movable: { dests: new Map([["e1", ["h1", "a1"]]]) } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "main_world_hook.js"), "utf8"), {
    console: { log() {} }, window, Map,
    document: { getElementById: () => ({ setAttribute(key, value) { attrs[key] = value; } }), querySelector: () => null },
    setInterval(fn) { sync = fn; }
  });
  sync();
  assert.equal(attrs["data-official-fen"], "4k3/8/8/8/8/8/8/R3K2R");
  assert.deepEqual(JSON.parse(attrs["data-move-dests"]), { e1: ["h1", "a1"] });
  window.lichess = null;
  sync();
  assert.equal(attrs["data-official-fen"], "");
  assert.equal(attrs["data-active-turn"], "");
  assert.equal(attrs["data-move-dests"], "{}");
});

test("force supersedes in-flight requests and still binds to position", () => {
  const h = harness();
  h.poll();
  h.event("az-force-btn", "click");
  h.respond(0);
  assert.equal(h.timers.size, 0);
  h.respond(1);
  h.attrs["data-player-color"] = "b";
  h.fire();
  assert.equal(h.clicks.length, 0);
});

test("full UCI move history is sent and pawn promotion dispatches 3-step click coordinates", () => {
  const h = harness();
  h.event("az-human", "change", false); // legacy click path carries explicit promo coordinates
  h.attrs["data-official-fen"] = "4k3/P7/8/8/8/8/8/4K3 w - - 0 1";
  h.attrs["data-uci-moves"] = "e2e4 e7e5 g1f3";
  h.poll();
  assert.deepEqual([...h.requests[0].msg.payload.moves], ["e2e4", "e7e5", "g1f3"]);
  assert.equal(h.requests[0].msg.payload.fen, "");
  h.requests[0].callback({
    success: true,
    data: { bestmove: "a7a8n", from: "a7", to: "a8", promotion: "n", wdl: [800, 150, 50], depth: 26, nps: 95000, pv: ["a7a8n", "e8d8"] }
  });
  h.event("az-play-now-btn", "click");
  assert.equal(h.clicks.length, 1);
  assert.equal(typeof h.clicks[0].msg.payload.promoX, "number");
  assert.equal(typeof h.clicks[0].msg.payload.promoY, "number");
});

test("human mode asks for alternatives and replays a curated input script", () => {
  const h = harness();
  h.poll();
  // Level 8 => multipv requested so the bot can deviate from rank 1.
  assert.equal(h.requests[0].msg.payload.multipv, 4);
  h.requests[0].callback({
    success: true,
    data: {
      bestmove: "a1a2",
      from: "a1",
      to: "a2",
      candidates: [{ move: "a1a2", score_cp: 20, mate: null }, { move: "h1h2", score_cp: 19, mate: null }]
    }
  });
  h.event("az-play-now-btn", "click");
  assert.equal(h.clicks.length, 1);
  assert.ok(h.clicks[0].msg.type, "DISPATCH_TRUSTED_INPUT expected");
  const events = h.clicks[0].msg.payload.events;
  assert.ok(Array.isArray(events) && events.length > 4);
  // Travel happens before the piece is grabbed, and the script ends with a release.
  assert.equal(events[0].type, "mouseMoved");
  assert.equal(events[events.length - 1].type, "mouseReleased");
  assert.ok(events.some(e => e.type === "mousePressed"));
  for (const e of events) {
    assert.ok(Number.isFinite(e.x) && Number.isFinite(e.y));
    assert.ok(e.delayMs >= 0 && e.delayMs <= 400);
  }
});
