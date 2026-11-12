// SwipeHire extension popup (v6): pairing + AUTONOMOUS apply controls.
// Pair once; the background queue processor (background.js) then claims
// queued applications one at a time and applies — filling AND submitting —
// in this browser. Here you can pause/resume, see today's counters
// against the per-board caps, read the run log, or open a queued job
// manually (the driver still runs autonomously on that tab).
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
    setStatus("Queue is empty. Right-swipe jobs in SwipeHire and they land here for the extension.");
    return;
  }
  setStatus(items.length + " job(s) in the extension queue.");
  for (const item of items) {
    const div = document.createElement("div");
    div.className = "job";
    const b = document.createElement("b");
    b.textContent = item.job_title;
    const s = document.createElement("span");
    s.textContent = item.company + " · " + item.source + " · " + item.status.replace(/_/g, " ") + " — click to open now (driver applies)";
    div.appendChild(b);
    div.appendChild(s);
    div.addEventListener("click", async () => {
      await chrome.storage.local.set({ currentJob: item });
      await report(item.application_id, "opened", "Opened manually from the extension popup; the autonomous driver takes it from here.");
      chrome.tabs.create({ url: item.job_url });
    });
    box.appendChild(div);
  }
}

async function refreshState() {
  let st = null;
  try { st = await chrome.runtime.sendMessage({ type: "sh-getstate" }); } catch (e) { /* SW asleep */ }
  if (!st) return;
  $("autoToggle").checked = !!st.autoEnabled && !st.autoPaused;
  $("autoState").textContent = st.autoPaused ? "— PAUSED" : st.autoEnabled ? (st.busy ? "— running (application " + st.currentAppId + ")" : "— ON") : "— OFF";
  $("pauseBtn").textContent = st.autoPaused ? "▶ Resume" : "⏸ Pause";
  const d = st.daily || {};
  $("counters").innerHTML =
    "Today: LinkedIn <b>" + (d.linkedin || 0) + "/" + st.caps.linkedin + "</b> · Indeed <b>" + (d.indeed || 0) + "/" + st.caps.indeed + "</b> · ATS <b>" + (d.ats || 0) + "/" + st.caps.ats + "</b>" +
    "<br>Last 30 min: <b>" + st.subsLast30Min + "/5</b> submissions" +
    (st.nextAllowedAt && st.nextAllowedAt > Date.now() ? "<br>Next submission allowed at " + new Date(st.nextAllowedAt).toLocaleTimeString() : "");
  const ul = $("runlog");
  ul.innerHTML = "";
  (st.runLog || []).slice().reverse().forEach((e) => {
    const li = document.createElement("li");
    li.textContent = new Date(e.t).toLocaleTimeString() + " — " + e.msg;
    ul.appendChild(li);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  const c = await chrome.storage.local.get(["apiBase", "token"]);
  if (c.apiBase) $("apiBase").value = c.apiBase;
  if (c.token) $("token").value = c.token;
  $("save").addEventListener("click", async () => {
    await chrome.storage.local.set({
      apiBase: $("apiBase").value.trim() || "https://swipehire-jade.vercel.app",
      token: $("token").value.trim(),
      autoEnabled: true, autoPaused: false
    });
    setStatus("Pairing saved. Autonomous apply is ON — the queue processor will start claiming jobs.");
    try { await chrome.runtime.sendMessage({ type: "sh-setauto", enabled: true, paused: false }); } catch (e) {}
    try { await chrome.runtime.sendMessage({ type: "sh-kick" }); } catch (e) {}
    loadQueue(); refreshState();
  });
  $("load").addEventListener("click", loadQueue);
  $("autoToggle").addEventListener("change", async () => {
    const on = $("autoToggle").checked;
    try { await chrome.runtime.sendMessage({ type: "sh-setauto", enabled: on, paused: false }); } catch (e) {}
    refreshState();
  });
  $("pauseBtn").addEventListener("click", async () => {
    let st = null;
    try { st = await chrome.runtime.sendMessage({ type: "sh-getstate" }); } catch (e) {}
    const resume = st && st.autoPaused;
    try { await chrome.runtime.sendMessage({ type: "sh-setauto", paused: !resume, enabled: true }); } catch (e) {}
    refreshState();
  });
  $("kick").addEventListener("click", async () => {
    try { await chrome.runtime.sendMessage({ type: "sh-kick" }); } catch (e) {}
    setStatus("Queue processor kicked.");
    setTimeout(refreshState, 600);
  });
  if (c.token) { loadQueue(); }
  refreshState();
});
