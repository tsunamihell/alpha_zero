const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

/** Loads humanizer.js into a fresh context exposing window.AZHumanizer. */
function load() {
  const context = vm.createContext({ console: { log() {} }, Math, window: {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "humanizer.js"), "utf8"), context);
  return context.window.AZHumanizer;
}

const cand = (move, cp) => ({ move, score_cp: cp, mate: null });

test("personas differ between games and track the declared level", () => {
  const h = load();
  const a = h.newSession(8);
  const b = h.newSession(8);
  assert.notEqual(a.baseThinkScale, b.baseThinkScale);
  assert.notEqual(a.mouseSpeed, b.mouseSpeed);
  assert.ok(a.deviation > 0 && a.deviation < 0.2);
  const weak = h.newSession(1);
  assert.ok(weak.deviation > a.deviation, "low levels must deviate more often");
  assert.ok(weak.tolerance > a.tolerance, "low levels must tolerate bigger score loss");
});

test("level input is clamped and survives junk", () => {
  const h = load();
  for (const value of [0, -5, 99, "abc", undefined, null]) {
    const p = h.newSession(value);
    assert.ok(p.level >= 1 && p.level <= 8);
  }
});

test("think time is log-normal scattered, never metronomic", () => {
  const h = load();
  const p = h.newSession(5);
  const ctx = { baseTimeMs: 800, clockLeftMs: 180000, isForced: false, isRecapture: false, pieceCount: 28, inCheck: false };
  const samples = Array.from({ length: 400 }, () => h.thinkTime(p, ctx));
  const mean = samples.reduce((a, b) => a + b, 0) / samples.length;
  const sd = Math.sqrt(samples.reduce((a, b) => a + (b - mean) ** 2, 0) / samples.length);
  assert.ok(sd / mean > 0.3, `relative spread too small: ${sd / mean}`);
  assert.ok(new Set(samples).size > 300, "too many repeated identical timings");
});

test("forced moves and recaptures are near-instant", () => {
  const h = load();
  const p = h.newSession(8);
  for (let i = 0; i < 50; i++) {
    const forced = h.thinkTime(p, { baseTimeMs: 3000, isForced: true });
    const recap = h.thinkTime(p, { baseTimeMs: 3000, isRecapture: true });
    assert.ok(forced <= 320, `forced took ${forced}`);
    assert.ok(recap <= 2000, `recapture took ${recap}`);
  }
});

test("clock is respected, including flag panic", () => {
  const h = load();
  const p = h.newSession(8);
  for (let i = 0; i < 200; i++) {
    const low = h.thinkTime(p, { baseTimeMs: 5000, clockLeftMs: 2000 });
    assert.ok(low <= 700, `flag panic too slow: ${low}`);
    const mid = h.thinkTime(p, { baseTimeMs: 5000, clockLeftMs: 30000 });
    assert.ok(mid <= 14000, `30s clock overshoot: ${mid}`);
  }
  const huge = h.thinkTime(p, { baseTimeMs: 20000, clockLeftMs: 600000 });
  assert.ok(huge <= 22000);
});

test("choice is sometimes not rank 1 but stays inside the tolerance window", () => {
  const h = load();
  const p = h.newSession(4);
  const candidates = [cand("e2e4", 40), cand("d2d4", 34), cand("g1f3", 20), cand("h2h4", -60)];
  const seen = new Set();
  for (let i = 0; i < 900; i++) {
    const chosen = h.choose(candidates, p);
    assert.ok(chosen);
    // h2h4 is 100cp down, outside a level-4 tolerance window even at max jitter.
    assert.notEqual(chosen.move, "h2h4");
    seen.add(chosen.move);
  }
  assert.ok(seen.size >= 3, `too narrow a move pool: ${[...seen]}`);
  // Pin is e2e4; d2d4 scores lower, so the pinned move must stay the dominant choice.
  const picks = { e2e4: 0, d2d4: 0, g1f3: 0 };
  for (let i = 0; i < 3000; i++) picks[h.choose(candidates, p).move] += 1;
  assert.ok(picks.e2e4 > picks.d2d4, "worse move overtook the best move");
  assert.ok(picks.e2e4 > picks.g1f3);
});

test("a candidate scoring above the pinned move can still win", () => {
  const h = load();
  // Arbiter pinned a slightly worse move first (real case after SF/Lc0 merging).
  const candidates = [cand("b8c6", -31), cand("d7d5", -8), cand("g8f6", -37)];

  // Level 8 keeps a tight window: only the near-equal alternative is live.
  const sharp = { b8c6: 0, d7d5: 0, g8f6: 0 };
  const p8 = h.newSession(8);
  for (let i = 0; i < 2000; i++) sharp[h.choose(candidates, p8).move] += 1;
  assert.ok(sharp.d7d5 > 0, "the objectively better move was never picked");
  assert.ok(sharp.d7d5 > sharp.g8f6);

  // A weaker persona tolerates the whole spread.
  const loose = { b8c6: 0, d7d5: 0, g8f6: 0 };
  const p2 = h.newSession(2);
  for (let i = 0; i < 2000; i++) loose[h.choose(candidates, p2).move] += 1;
  assert.ok(loose.g8f6 > 0);
  assert.ok(loose.b8c6 > 0 && loose.d7d5 > 0 && loose.g8f6 > 0, "weak persona must use the full pool");
});

test("mate scores outrank centipawns and side sign is correct", () => {
  const h = load();
  assert.ok(h.scoreValue({ mate: 3 }) > h.scoreValue({ score_cp: 900 }));
  assert.ok(h.scoreValue({ mate: -3 }) < h.scoreValue({ score_cp: -900 }));
  const p = h.newSession(8);
  // Index 0 is pinned by the arbiter, so it is returned when no deviation is drawn.
  const candidates = [{ move: "a1a8", score_cp: 800, mate: null }, { move: "b1b8", score_cp: null, mate: 2 }];
  for (let i = 0; i < 200; i++) {
    const chosen = h.choose(candidates, p);
    if (chosen.rank === 0) assert.equal(chosen.move, "a1a8");
  }
});

test("tilt raises risk appetite and blunder count", () => {
  const h = load();
  const p = h.newSession(8);
  const before = p.tilt;
  h.registerOutcome(p, true);
  assert.ok(p.tilt > before);
  assert.equal(p.blunders, 1);
  for (let i = 0; i < 20; i++) h.registerOutcome(p, false);
  assert.equal(p.tilt, 0);
});

test("mouse path is curved, starts adjacent to origin and lands near target", () => {
  const h = load();
  const p = h.newSession(8);
  const from = { x: 100, y: 100 };
  const to = { x: 400, y: 260 };
  const points = h.mousePath(from, to, p, 60);
  assert.ok(points.length >= 6);
  const first = points[0];
  assert.ok(Math.hypot(first.x - from.x, first.y - from.y) < 60);
  const last = points[points.length - 1];
  assert.ok(Math.hypot(last.x - to.x, last.y - to.y) < 14);
  // A straight line would keep every point on the from->to axis; a bow must not.
  const offsets = points.map((pt) => {
    const t = (pt.x - from.x) / (to.x - from.x);
    return Math.abs((from.y + t * (to.y - from.y)) - pt.y);
  });
  assert.ok(Math.max(...offsets) > 1, "path is a straight line");
});

test("step delay is variable and bounded", () => {
  const h = load();
  const p = h.newSession(8);
  const delays = new Set();
  for (let i = 0; i <= 20; i++) {
    const d = h.stepDelay(p, i / 20);
    assert.ok(d >= 2 && d <= 26);
    delays.add(d);
  }
  assert.ok(delays.size > 3);
});

test("poll cadence is randomized around the base interval", () => {
  const h = load();
  const values = new Set(Array.from({ length: 50 }, () => h.pollInterval()));
  assert.ok(values.size > 20);
  for (const v of values) assert.ok(v >= 680 && v <= 1400);
});

test("engine-latency stretch is bounded and never collapses to a constant", () => {
  const h = load();
  const p = h.newSession(8);
  const values = new Set();
  for (let i = 0; i < 300; i++) {
    const s = h.stretch(p);
    assert.ok(s >= 0.95 && s <= 2.4);
    values.add(s.toFixed(3));
  }
  assert.ok(values.size > 150, `stretch too quantized: ${values.size}`);
});

test("empty candidate list yields null instead of throwing", () => {
  const h = load();
  assert.equal(h.choose([], h.newSession(8)), null);
  assert.equal(h.choose(null, h.newSession(8)), null);
});
