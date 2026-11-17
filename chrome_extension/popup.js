function checkHealth() {
  const badge = document.getElementById("statusBadge");
  chrome.runtime.sendMessage({ type: "CHECK_HEALTH" }, (resp) => {
    if (resp && resp.success) {
      badge.classList.add("connected");
      badge.title = "Connected to Lc0 (RTX 3070)";
    } else {
      badge.classList.remove("connected");
      badge.title = "Local bridge_server.py is not running";
    }
  });
}

document.addEventListener("DOMContentLoaded", checkHealth);
