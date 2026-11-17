function checkHealth() {
  const badge = document.getElementById("statusBadge");
  const input = document.getElementById("serverUrlInput");
  chrome.storage.local.get({ serverUrl: "http://127.0.0.1:5000" }, (items) => {
    if (input && items && items.serverUrl) input.value = items.serverUrl;
  });
  chrome.runtime.sendMessage({ type: "CHECK_HEALTH" }, (resp) => {
    if (resp && resp.success) {
      badge.classList.add("connected");
      badge.title = "Connected to Lc0 (RTX 3070)";
    } else {
      badge.classList.remove("connected");
      badge.title = "Bridge server is not reachable";
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  checkHealth();
  const input = document.getElementById("serverUrlInput");
  if (input) {
    input.addEventListener("change", () => {
      let val = input.value.trim();
      if (!val) val = "http://127.0.0.1:5000";
      if (!val.startsWith("http://") && !val.startsWith("https://")) {
        val = "http://" + val;
      }
      chrome.storage.local.set({ serverUrl: val }, () => {
        checkHealth();
      });
    });
  }
});
