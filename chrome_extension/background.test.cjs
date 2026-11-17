const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function dispatch({ attachError = false, commandError = -1, tab = true } = {}) {
  let listener;
  const commands = [], responses = [];
  const runtime = { onMessage: { addListener(fn) { listener = fn; } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "background.js"), "utf8"), {
    chrome: { runtime, debugger: {
      onDetach: { addListener() {} },
      attach(target, version, callback) {
        runtime.lastError = attachError ? { message: "attach failed" } : undefined;
        callback();
        runtime.lastError = undefined;
      },
      sendCommand(target, method, event, callback) {
        commands.push(event);
        runtime.lastError = commands.length - 1 === commandError ? { message: "command failed" } : undefined;
        callback();
        runtime.lastError = undefined;
      }
    } }, setTimeout(fn) { fn(); }
  });
  listener({ type: "DISPATCH_TRUSTED_CLICK", payload: { fromX: 1, fromY: 2, toX: 3, toY: 4 } },
    tab ? { tab: { id: 1 } } : {}, response => responses.push(response));
  return { commands, responses };
}

test("attach failure and missing tab reliably report no dispatch", () => {
  for (const options of [{ attachError: true }, { tab: false }]) {
    const result = dispatch(options);
    assert.equal(result.commands.length, 0);
    assert.equal(result.responses.length, 1);
    assert.equal(result.responses[0].success, false);
    assert.equal(result.responses[0].dispatched, false);
  }
});

test("each CDP error stops sequence and reports uncertain outcome", () => {
  for (let i = 0; i < 4; i++) {
    const result = dispatch({ commandError: i });
    assert.equal(result.commands.length, i + 1);
    assert.equal(result.responses.length, 1);
    assert.equal(result.responses[0].success, false);
    assert.equal(result.responses[0].dispatched, "unknown");
  }
});

test("successful dispatch sends exactly one click sequence without drag replay", () => {
  const result = dispatch();
  assert.equal(result.commands.length, 4);
  assert.equal(result.responses.length, 1);
  assert.equal(result.responses[0].success, true);
});
