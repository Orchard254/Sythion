const DEFAULT_PORT = 8000;

function configFromStorage() {
  return chrome.storage.local.get(["sythionPort", "sythionToken"]);
}

async function checkStatus() {
  const dot = document.getElementById("dot");
  const text = document.getElementById("status-text");

  let port = DEFAULT_PORT;
  let token = "";
  try {
    const stored = await configFromStorage();
    port = Number(stored.sythionPort) || DEFAULT_PORT;
    token = stored.sythionToken || "";
  } catch (e) {
    // ignore
  }

  if (!token) {
    dot.className = "dot";
    text.innerText = "No control token configured";
    return;
  }

  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/browser/status`, {
      headers: { "X-Control-Token": token },
    });
    if (res.ok) {
      const data = await res.json();
      if (data.connected) {
        dot.className = "dot connected";
        text.innerText = "Connected to Sythion";
        return;
      }
      dot.className = "dot";
      text.innerText = "Backend online; extension linking...";
      return;
    }
    if (res.status === 401 || res.status === 403) {
      dot.className = "dot";
      text.innerText = "Token rejected - update it in the popup";
      return;
    }
  } catch (e) {
    // ignore
  }

  dot.className = "dot";
  text.innerText = "Backend Offline (Start Sythion)";
}

async function loadConfig() {
  const stored = await configFromStorage();
  document.getElementById("port").value = stored.sythionPort || DEFAULT_PORT;
  document.getElementById("token").value = stored.sythionToken || "";
}

document.getElementById("save").addEventListener("click", async () => {
  const portInput = document.getElementById("port").value.trim();
  const tokenInput = document.getElementById("token").value.trim();
  const note = document.getElementById("saved-note");
  const port = Number(portInput) > 0 ? Number(portInput) : DEFAULT_PORT;
  await chrome.storage.local.set({ sythionPort: port, sythionToken: tokenInput });
  note.innerText = "Saved";
  setTimeout(() => { note.innerText = ""; }, 1500);
});

loadConfig();
checkStatus();
setInterval(checkStatus, 2000);
