/**
 * AlphaZero Bot — Anti-Ban Humanizer Layer
 *
 * Removes the statistical fingerprints that chess sites use to flag bots:
 *   1. Metronomic timing        -> log-normal think budget + "recognition" instants + rare deep thinks
 *   2. 100% engine-match play   -> softmax sampling among near-best MultiPV candidates
 *   3. Teleport clicks          -> Bezier mouse travel with overshoot + settle
 *   4. Fixed polling cadence    -> randomized observation interval
 *   5. Perfect clock discipline -> clock-aware acceleration / time-trouble behaviour
 *
 * Every game gets a fresh random "persona" (think pace, risk appetite, mouse speed,
 * click-vs-drag style, walk-away habits) so two games never share one signature.
 *
 * Exposes window.AZHumanizer. Loaded before content.js in the isolated world.
 */

(function () {
  "use strict";

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const pick = (list) => list[Math.floor(Math.random() * list.length)];

  /** Standard normal via Box-Muller. */
  function gaussian() {
    let u1 = Math.random();
    let u2 = Math.random();
    if (u1 < 1e-9) u1 = 1e-9;
    return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  }

  /** Log-normal sample: humans think multiplicatively, not additively. */
  function logNormal(median, sigma) {
    return median * Math.exp(clamp(gaussian(), -2.5, 2.5) * sigma);
  }

  /**
   * Candidate-selection tolerance in centipawns, by declared engine level.
   * Lower levels tolerate far more inaccuracy, so the bot stops looking like a tablebase.
   */
  const TOLERANCE_BY_LEVEL = { 1: 140, 2: 95, 3: 65, 4: 40, 5: 24, 6: 14, 7: 8, 8: 5 };

  /**
   * Base risk of deviating from the engine's top choice, by level.
   * Even level 8 must deviate occasionally or it becomes trivially detectable.
   */
  const DEVIATION_BY_LEVEL = { 1: 0.62, 2: 0.55, 3: 0.46, 4: 0.36, 5: 0.26, 6: 0.17, 7: 0.1, 8: 0.07 };

  /** Creates a fresh per-game persona. Two games never share a signature. */
  function newSession(level) {
    const lvl = clamp(Number(level) || 8, 1, 8);
    return {
      createdAt: Date.now(),
      level: lvl,
      // --- timing character ---
      baseThinkScale: 0.78 + Math.random() * 0.62, // personal pace multiplier
      thinkSigma: 0.38 + Math.random() * 0.28, // how erratic this person is
      instantProb: 0.04 + Math.random() * 0.13, // genuine "saw it instantly" moves
      deepThinkProb: 0.015 + Math.random() * 0.045, // occasional long calculation
      // --- choice character ---
      deviation: (DEVIATION_BY_LEVEL[lvl] !== undefined ? DEVIATION_BY_LEVEL[lvl] : 0.07) *
        (0.72 + Math.random() * 0.56),
      tolerance: (TOLERANCE_BY_LEVEL[lvl] !== undefined ? TOLERANCE_BY_LEVEL[lvl] : 5) *
        (0.75 + Math.random() * 0.5),
      // --- input character ---
      dragStyle: Math.random() < 0.42, // drag the piece instead of click-click
      mouseSpeed: 0.65 + Math.random() * 0.9,
      overshootChance: 0.15 + Math.random() * 0.4,
      pressHoldMs: 45 + Math.random() * 75,
      // --- habit character ---
      pauseProbPerMove: 0.015 + Math.random() * 0.05, // stands up, checks phone
      premoveProb: 0.2 + Math.random() * 0.5,
      firstMoveDelayMs: 900 + Math.random() * 2600,
      // --- tilt state (mutated during play) ---
      tilt: 0,
      blunders: 0,
    };
  }

  /**
   * How long a human with this persona would take on this move.
   * @param {object} persona
   * @param {object} ctx { baseTimeMs, clockLeftMs, isForced, isRecapture, pieceCount, inCheck }
   */
  function thinkTime(persona, ctx) {
    const base = Math.max(120, Number(ctx.baseTimeMs) || 500);

    // Recognition: an obvious recapture / forced reply needs almost no thought.
    if (ctx.isForced || Math.random() < persona.instantProb * (ctx.isRecapture ? 2.2 : 1)) {
      return clamp(90 + Math.random() * 150, 70, 320);
    }

    let median = base * persona.baseThinkScale;
    if (ctx.isRecapture) median *= 0.55;
    if (ctx.inCheck) median *= 0.7;
    if (ctx.pieceCount && ctx.pieceCount > 20) median *= 1.12; // opening: more choice, more thought
    if (persona.tilt > 0) median *= 1 - clamp(persona.tilt, 0, 0.35);

    let ms = logNormal(median, persona.thinkSigma);

    // Occasionally people actually calculate hard — but a recapture rarely triggers that.
    const deepChance = ctx.isRecapture ? persona.deepThinkProb * 0.35 : persona.deepThinkProb;
    if (Math.random() < deepChance) {
      ms *= ctx.isRecapture ? 1.5 + Math.random() : 2.2 + Math.random() * 2.3;
    }

    // Clock safety: never burn more than a third of what is left, never less than 80ms.
    const left = Number(ctx.clockLeftMs);
    if (Number.isFinite(left) && left > 0) {
      const cap = Math.max(80, left - 120);
      ms = Math.min(ms, cap * (left < 8000 ? 0.28 : 0.45));
      if (left < 2500) ms = Math.min(ms, 220 + Math.random() * 260); // flag panic
    }
    // A recapture is a reflex, not a calculation: hard-cap it regardless of the clock.
    if (ctx.isRecapture) ms = Math.min(ms, 750 + Math.random() * 550);
    return Math.round(clamp(ms, 80, 22000));
  }

  /**
   * Multiplier applied to the engine's own latency, so the pause stays proportional even when
   * a deep search already consumed seconds — otherwise every high-level move lands at the same
   * interval and the bot is detectable again.
   */
  function stretch(persona) {
    return Math.min(2.4, Math.max(0.95, logNormal(1.26, persona.thinkSigma * 0.65)));
  }

  /**
   * Picks a move the way a human would: usually the best, sometimes a close alternative.
   * @param {Array<{move:string, score_cp:number|null, mate:number|null}>} candidates best-first
   * @param {object} persona
   * @returns {{move:string, rank:number, score_cp:number|null, mate:number|null}}
   */
  function choose(candidates, persona) {
    const list = (candidates || []).filter((c) => c && c.move);
    if (list.length === 0) return null;
    if (list.length === 1) return { ...list[0], rank: 0 };

    // The arbiter pins its verdict at index 0, but another candidate may actually score higher.
    // Tolerance is therefore measured from the true best score in the pool, not from index 0,
    // so a move that is *better* than the pinned one can never be unfairly dominant or excluded.
    let topScore = -Infinity;
    const scores = list.map((c) => {
      const s = scoreValue(c);
      if (s > topScore) topScore = s;
      return s;
    });

    const deviate = Math.random() < persona.deviation + persona.tilt * 0.15;
    if (!deviate) return { ...list[0], rank: 0 };

    // Only moves within the persona's tolerance are plausible to a human of that strength.
    const tolerance = persona.tolerance * (1 + persona.tilt);
    const pool = [];
    for (let i = 0; i < list.length; i++) {
      const loss = topScore - scores[i];
      if (loss <= tolerance) pool.push({ c: list[i], loss, rank: i });
    }
    if (pool.length === 0) return { ...list[0], rank: 0 };
    if (pool.length === 1) return { ...pool[0].c, rank: pool[0].rank };

    // Softmax over the loss window: near-best moves are strongly favoured, but not certain.
    const temp = Math.max(6, tolerance / 2.2);
    let total = 0;
    const weights = pool.map((entry) => {
      const w = Math.exp(-entry.loss / temp);
      total += w;
      return w;
    });
    let roll = Math.random() * total;
    for (let i = 0; i < pool.length; i++) {
      roll -= weights[i];
      if (roll <= 0) return { ...pool[i].c, rank: pool[i].rank };
    }
    return { ...pool[pool.length - 1].c, rank: pool[pool.length - 1].rank };
  }

  /** Normalizes mate distances into a comparable centipawn-like scale. */
  function scoreValue(candidate) {
    if (typeof candidate.mate === "number") {
      return candidate.mate > 0 ? 100000 - candidate.mate * 100 : -100000 - candidate.mate * 100;
    }
    return typeof candidate.score_cp === "number" ? candidate.score_cp : 0;
  }

  /** Should the player get up and walk away for a while? */
  function shouldPause(persona) {
    return Math.random() < persona.pauseProbPerMove;
  }

  /** Randomized observation cadence so the poll clock is not perfect. */
  function pollInterval() {
    return Math.round(680 + Math.random() * 720);
  }

  /**
   * Builds a curved, slightly overshooting mouse trajectory between two points.
   * @returns {Array<{x:number, y:number}>} waypoints including the start, excluding the end
   */
  function mousePath(from, to, persona, sqSize) {
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const dist = Math.hypot(dx, dy) || 1;
    const spread = Math.max(3, Math.min(26, (sqSize || 60) * 0.18));

    // Perpendicular bow gives the arc that real wrists produce.
    const bow = (Math.random() - 0.5) * spread * 2.2;
    const nx = -dy / dist;
    const ny = dx / dist;
    const midX = (from.x + to.x) / 2 + nx * bow;
    const midY = (from.y + to.y) / 2 + ny * bow;

    const steps = Math.round(clamp(dist / (14 / persona.mouseSpeed), 6, 34));
    const points = [];
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      const mt = 1 - t;
      // Quadratic Bezier
      const x = mt * mt * from.x + 2 * mt * t * midX + t * t * to.x;
      const y = mt * mt * from.y + 2 * mt * t * midY + t * t * to.y;
      // Ease-out arc-length feel plus tremor
      points.push({ x: x + gaussian() * 0.8, y: y + gaussian() * 0.8, t });
    }

    // Overshoot past the target, then come back — the classic human correction.
    if (Math.random() < persona.overshootChance) {
      const over = 2 + Math.random() * 9;
      const ux = dx / dist;
      const uy = dy / dist;
      points.push({ x: to.x + ux * over + gaussian() * 1.5, y: to.y + uy * over + gaussian() * 1.5, t: 1 });
      points.push({ x: to.x - ux * over * 0.25, y: to.y - uy * over * 0.25, t: 1 });
    }
    return points;
  }

  /** Per-step delay for mouse travel, in ms. */
  function stepDelay(persona, t) {
    const eased = 0.35 + Math.sin(Math.PI * t) * 0.9; // slow start, fast middle, settle
    return Math.round(clamp((9 + Math.random() * 9) / (persona.mouseSpeed * eased), 2, 26));
  }

  /** Records that the bot just took a hit, raising risk appetite. */
  function registerOutcome(persona, wasBlunder) {
    if (!persona) return;
    if (wasBlunder) {
      persona.blunders += 1;
      persona.tilt = clamp(persona.tilt + 0.12, 0, 0.8);
    } else {
      persona.tilt = clamp(persona.tilt - 0.03, 0, 0.8);
    }
  }

  window.AZHumanizer = {
    newSession,
    thinkTime,
    stretch,
    choose,
    shouldPause,
    pollInterval,
    mousePath,
    stepDelay,
    registerOutcome,
    scoreValue,
  };
})();
