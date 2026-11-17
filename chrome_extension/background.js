let SERVER_URL = "http://127.0.0.1:5000";

function updateServerUrl() {
  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
    chrome.storage.local.get({ serverUrl: "http://127.0.0.1:5000" }, (items) => {
      if (items && items.serverUrl) SERVER_URL = items.serverUrl;
    });
  }
}
updateServerUrl();
if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.onChanged) {
  chrome.storage.onChanged.addListener((changes) => {
    if (changes && changes.serverUrl) SERVER_URL = changes.serverUrl.newValue;
  });
}
const attachedTabs = new Set();
const ALLOWED_EVENTS = new Set(["mouseMoved", "mousePressed", "mouseReleased"]);

/**
 * Turns a raw input descriptor into a validated CDP event list.
 * Accepts either a full humanized `events` array or the legacy 4-point click payload.
 * @returns {Array<{type:string,x:number,y:number,delayMs:number}>|null}
 */
function buildEvents(payload) {
  const { fromX, fromY, toX, toY, promoX, promoY } = payload || {};

  if (Array.isArray(payload && payload.events) && payload.events.length > 0) {
    const events = [];
    for (const raw of payload.events) {
      if (!raw || !ALLOWED_EVENTS.has(raw.type)) return null;
      if (!Number.isFinite(raw.x) || !Number.isFinite(raw.y)) return null;
      const delay = Number.isFinite(raw.delayMs) ? Math.max(0, Math.min(400, raw.delayMs)) : 0;
      events.push({ type: raw.type, x: raw.x, y: raw.y, delayMs: delay });
    }
    return events;
  }

  const hasPromo = promoX !== undefined || promoY !== undefined;
  if (![fromX, fromY, toX, toY].every(Number.isFinite)) return null;
  if (hasPromo && ![promoX, promoY].every(Number.isFinite)) return null;

  const events = [
    { type: "mousePressed", x: fromX, y: fromY, delayMs: 0 },
    { type: "mouseReleased", x: fromX, y: fromY, delayMs: 95 },
    { type: "mousePressed", x: toX, y: toY, delayMs: 0 },
    { type: "mouseReleased", x: toX, y: toY, delayMs: 0 },
  ];
  if (hasPromo) {
    events.push({ type: "mousePressed", x: promoX, y: promoY, delayMs: 0 });
    events.push({ type: "mouseReleased", x: promoX, y: promoY, delayMs: 0 });
  }
  return events;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "CHECK_HEALTH") {
    fetch(`${SERVER_URL}/health`)
      .then(res => res.json())
      .then(data => sendResponse({ success: true, data }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (message.type === "GET_MOVE") {
    fetch(`${SERVER_URL}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload)
    })
      .then(res => {
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        return res.json();
      })
      .then(data => sendResponse({ success: true, data }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (message.type === "DISPATCH_TRUSTED_CLICK" || message.type === "DISPATCH_TRUSTED_INPUT") {
    const tabId = sender.tab ? sender.tab.id : null;
    const events = buildEvents(message.payload);
    if (tabId == null || !events) {
      sendResponse({ success: false, dispatched: false, error: "Invalid tab or coordinates" });
      return false;
    }
    const target = { tabId };
    // Track button state so mid-drag moves carry buttons=1, which is what a real drag emits.
    let held = false;
    const sendEvent = (index = 0) => {
      const event = events[index];
      if (event.type === "mousePressed") held = true;
      try {
        chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
          type: event.type,
          x: Math.round(event.x),
          y: Math.round(event.y),
          button: event.type === "mouseMoved" ? "none" : "left",
          buttons: held ? 1 : 0,
          clickCount: event.type === "mouseMoved" ? 0 : 1
        }, () => {
          const error = chrome.runtime.lastError;
          if (error) {
            sendResponse({ success: false, dispatched: "unknown", error: error.message });
            return;
          }
          if (event.type === "mouseReleased") held = false;
          if (index === events.length - 1) {
            sendResponse({ success: true });
            return;
          }
          const next = () => sendEvent(index + 1);
          if (event.delayMs > 0) setTimeout(next, event.delayMs);
          else next();
        });
      } catch (error) {
        sendResponse({ success: false, dispatched: "unknown", error: error.message });
      }
    };
    if (attachedTabs.has(tabId)) sendEvent();
    else {
      try {
        chrome.debugger.attach(target, "1.3", () => {
          const error = chrome.runtime.lastError;
          if (error) {
            sendResponse({ success: false, dispatched: false, error: error.message });
            return;
          }
          attachedTabs.add(tabId);
          sendEvent();
        });
      } catch (error) {
        sendResponse({ success: false, dispatched: false, error: error.message });
      }
    }
    return true;
  }
});

chrome.debugger.onDetach.addListener((source) => {
  attachedTabs.delete(source.tabId);
});
