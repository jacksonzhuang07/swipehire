// SwipeHire extension popup: pairing + queue.
// The queue/claim/report API is deliberately small and stateless so a
// future autonomous queue processor can drive the same flow:
// claim (GET /api/ext/queue) -> open -> report(opened|filled|submitted|failed).
const $ = (id) => document.getElementById(id);

function setStatus(msg, err) {
  const el = $("status");
  el.textContent = msg || "";
  el.className = err ? "err" : "";
}

async function getCfg() {
  const c = await chrome.storage.local.get(["apiBase", "token"]);
  return { apiBase: (c.apiBase || "https://swipehire-jade.vercel.app").replace(/\/$/, ""), token: c.token || "" };
}

async function api(path, opts) {
  const { apiBase, token } = await getCfg();
  const r = await fetch(apiBase + path, Object.assign({}, opts || {}, {
    headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token }
  }));
  let j = null;
  try { j = await r.json(); } catch (e) { /* non-JSON */ }
  return { status: r.status, json: j };
}

async function report(applicationId, event, note) {
  return api("/api/ext/report", { method: "POST", body: JSON.stringify({ application_id: applicationId, event: event, note: note || "" }) });
}

async function loadQueue() {
  setStatus("Loading queue…");
  const r = await api("/api/ext/queue");
  const box = $("queue");
  box.innerHTML = "";
  if (r.status !== 200) {
    setStatus((r.json && r.json.error) || "Could not load the queue — check pairing.", true);
    return;
  }
  const items = (r.json && r.json.queue) || [];
  if (!items.length) {
    setStatus("Queue is empty. Right-swipe a 🔒 login-required job in SwipeHire and it lands here.");
    return;
  }
  setStatus(items.length + " job(s) waiting.");
  for (const item of items) {
    const div = document.createElement("div");
    div.className = "job";
    const b = document.createElement("b");
    b.textContent = item.job_title;
    const s = document.createElement("span");
    s.textContent = item.company + " · " + item.source + (item.status === "queued_extension" ? " · queued" : " · needs extension");
    div.appendChild(b);
    div.appendChild(s);
    div.addEventListener("click", async () => {
      await chrome.storage.local.set({ currentJob: item });
      await report(item.application_id, "opened");
      chrome.tabs.create({ url: item.job_url });
    });
    box.appendChild(div);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const c = await chrome.storage.local.get(["apiBase", "token"]);
  if (c.apiBase) $("apiBase").value = c.apiBase;
  if (c.token) $("token").value = c.token;
  $("save").addEventListener("click", async () => {
    await chrome.storage.local.set({
      apiBase: $("apiBase").value.trim() || "https://swipehire-jade.vercel.app",
      token: $("token").value.trim()
    });
    setStatus("Pairing saved.");
    loadQueue();
  });
  $("load").addEventListener("click", loadQueue);
  if (c.token) loadQueue();
});
