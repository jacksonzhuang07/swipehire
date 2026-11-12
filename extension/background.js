// SwipeHire extension — background service worker (v6 AUTONOMOUS apply).
//
// The queue processor lives here. When autonomous apply is enabled (the
// default once paired) and not paused, it claims queued applications one
// at a time from GET /api/ext/queue, opens each job in a tab it manages,
// and lets the per-site driver (content.js) fill AND submit the form.
// Every outcome is reported back via POST /api/ext/report:
//   opened -> filling -> submitted | needs_human | failed
//
// Pacing (launch defaults, shown in the popup, counters in chrome.storage
// with day rollover):
//   - at least 90 seconds between submissions
//   - rolling burst cap: 5 submissions per 30 minutes
//   - daily caps per board: LinkedIn 30, Indeed 20, ATS 40
// Safety: a driver result flagged as an account warning pauses ALL
// autonomous applying until the user resumes from the popup. CAPTCHAs,
// login walls, checkpoints and unanswerable required questions are parked
// as needs_human by the drivers — never defeated, never retried blindly.

"use strict";

var CAPS = { linkedin: 30, indeed: 20, ats: 40 };
var BURST_MAX = 5, BURST_WINDOW_MS = 30 * 60 * 1000;
var MIN_GAP_MS = 90 * 1000;
var CLAIM_TIMEOUT_MS = 150 * 1000;

function todayKey() { return new Date().toISOString().slice(0, 10); }

async function get(keys) { return chrome.storage.local.get(keys); }

async function log(msg) {
  var s = await get(["runLog"]);
  var entries = s.runLog || [];
  entries.push({ t: new Date().toISOString(), msg: String(msg) });
  while (entries.length > 50) entries.shift();
  await chrome.storage.local.set({ runLog: entries });
}

async function getPace() {
  var s = await get(["pace"]);
  var p = s.pace || {};
  if (p.day !== todayKey()) p = { day: todayKey(), subs: [], daily: { linkedin: 0, indeed: 0, ats: 0 } };
  p.subs = (p.subs || []).filter(function (t) { return Date.now() - t < 24 * 3600 * 1000; });
  p.daily = p.daily || { linkedin: 0, indeed: 0, ats: 0 };
  return p;
}

async function setPace(p) { await chrome.storage.local.set({ pace: p }); }

function boardOf(item) {
  var url = (item.job_url || "").toLowerCase();
  if (url.indexOf("linkedin.") !== -1) return "linkedin";
  if (url.indexOf("indeed.") !== -1) return "indeed";
  return "ats";
}

async function cfg() {
  var s = await get(["apiBase", "token"]);
  return { apiBase: (s.apiBase || "https://swipehire-jade.vercel.app").replace(/\/$/, ""), token: s.token || "" };
}

async function api(path, opts) {
  var c = await cfg();
  if (!c.token) return { status: 0, json: null };
  var r = await fetch(c.apiBase + path, Object.assign({}, opts || {}, {
    headers: { "Content-Type": "application/json", "Authorization": "Bearer " + c.token }
  }));
  var j = null;
  try { j = await r.json(); } catch (e) { /* non-JSON */ }
  return { status: r.status, json: j };
}

async function report(applicationId, event, note) {
  return api("/api/ext/report", { method: "POST", body: JSON.stringify({ application_id: applicationId, event: event, note: note || "" }) });
}

async function getProc() {
  var s = await get(["proc", "autoEnabled", "autoPaused"]);
  return { proc: s.proc || null, autoEnabled: s.autoEnabled !== false, autoPaused: !!s.autoPaused };
}

async function setProc(p) { await chrome.storage.local.set({ proc: p }); }

var _ticking = false; // in-memory lock: storage reads are async, so two
// near-simultaneous ticks could otherwise both pass the busy check and
// double-claim the same application.
async function tick() {
  if (_ticking) return;
  _ticking = true;
  try { await tickInner(); } finally { _ticking = false; }
}

async function tickInner() {
  var st = await getProc();
  if (!st.autoEnabled || st.autoPaused) return;
  var c = await cfg();
  if (!c.token) return;
  var proc = st.proc;
  if (proc && proc.busy) {
    // Watchdog: a claim that never reported back gets parked, not retried.
    if (Date.now() - proc.startedAt > CLAIM_TIMEOUT_MS) {
      await log("Timed out on application " + proc.appId + " — parking as Needs you.");
      await report(proc.appId, "needs_human", "The extension timed out driving this page (no result within 150s).");
      if (proc.tabId) { try { await chrome.tabs.remove(proc.tabId); } catch (e) {} }
      await setProc(null);
      await chrome.storage.local.remove(["currentJob"]);
    }
    return;
  }
  var pace = await getPace();
  if (pace.nextAllowedAt && Date.now() < pace.nextAllowedAt) return;
  var recent = pace.subs.filter(function (t) { return Date.now() - t < BURST_WINDOW_MS; });
  if (recent.length >= BURST_MAX) return;
  var r = await api("/api/ext/queue");
  if (r.status !== 200 || !r.json) return;
  var queue = r.json.queue || [];
  if (!queue.length) return;
  var item = null, board = null;
  for (var i = 0; i < queue.length; i++) {
    var b = boardOf(queue[i]);
    if ((pace.daily[b] || 0) < CAPS[b]) { item = queue[i]; board = b; break; }
  }
  if (!item) { await log("Daily cap reached for every board in the queue — waiting for tomorrow."); return; }
  await claim(item, board);
}

async function claim(item, board) {
  await setProc({ busy: true, appId: item.application_id, board: board, startedAt: Date.now(), tabId: null, fillingReported: false });
  await chrome.storage.local.set({ currentJob: item });
  await log("Claimed: " + item.job_title + " @ " + item.company + " (" + board + ").");
  await report(item.application_id, "opened", "Autonomous extension claimed this application.");
  // Open about:blank FIRST and record the tab id, then navigate — so the
  // content script can never run before the tab is registered as managed
  // (which would drop it into the manual path and strand the claim).
  var tab = await chrome.tabs.create({ url: "about:blank", active: false });
  var st = await getProc();
  if (st.proc && st.proc.appId === item.application_id) { st.proc.tabId = tab.id; await setProc(st.proc); }
  await chrome.tabs.update(tab.id, { url: item.job_url });
}

async function finishClaim(result) {
  var st = await getProc();
  var proc = st.proc;
  if (!proc) return;
  var appId = proc.appId, board = proc.board || "ats", tabId = proc.tabId;
  if (result.outcome === "submitted") {
    await report(appId, "submitted", result.evidence || "Confirmation detected by the autonomous driver.");
    var pace = await getPace();
    pace.subs.push(Date.now());
    pace.daily[board] = (pace.daily[board] || 0) + 1;
    pace.nextAllowedAt = Date.now() + MIN_GAP_MS;
    await setPace(pace);
    await log("Submitted: application " + appId + " (" + board + ", today " + pace.daily[board] + "/" + CAPS[board] + "). Next submission allowed in 90s.");
  } else if (result.outcome === "needs_human") {
    await report(appId, "needs_human", result.reason || "The driver needs a human on this page.");
    await log("Needs you: application " + appId + " — " + (result.reason || ""));
    if (result.accountWarning) {
      await chrome.storage.local.set({ autoPaused: true });
      await log("⚠ Account-warning signal detected — autonomous applying is PAUSED for all boards until you resume it in the popup.");
    }
  } else {
    await report(appId, "failed", result.reason || "The driver could not complete this application.");
    await log("Failed: application " + appId + " — " + (result.reason || ""));
  }
  if (tabId) { try { await chrome.tabs.remove(tabId); } catch (e) {} }
  await setProc(null);
  await chrome.storage.local.remove(["currentJob"]);
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  (async function () {
    if (!msg || !msg.type) return;
    if (msg.type === "sh-getjob") {
      var st = await getProc();
      var s = await get(["currentJob"]);
      var job = s.currentJob || null;
      if (st.proc && st.proc.busy && job && sender.tab && sender.tab.id === st.proc.tabId) {
        sendResponse({ mode: "auto", job: job });
      } else if ((!st.proc || !st.proc.busy) && job && job.job_url && sender.tab && job.job_url.split("#")[0] === (sender.tab.url || "").split("#")[0]) {
        sendResponse({ mode: "manual", job: job });
      } else {
        sendResponse({ mode: null, job: null });
      }
      return;
    }
    if (msg.type === "sh-progress") {
      var st2 = await getProc();
      if (st2.proc && st2.proc.busy && !st2.proc.fillingReported) {
        st2.proc.fillingReported = true;
        await setProc(st2.proc);
        await report(st2.proc.appId, "filling", msg.note || "Autonomous driver is filling the form.");
      }
      await log(msg.note || "Driver progress.");
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "sh-result") {
      await finishClaim(msg.result || {});
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "sh-log") { await log(msg.msg); sendResponse({ ok: true }); return; }
    if (msg.type === "sh-getstate") {
      var st3 = await getProc();
      var pace = await getPace();
      var s3 = await get(["runLog"]);
      sendResponse({
        autoEnabled: st3.autoEnabled, autoPaused: st3.autoPaused,
        busy: !!(st3.proc && st3.proc.busy), currentAppId: st3.proc ? st3.proc.appId : null,
        caps: CAPS, daily: pace.daily, subsLast30Min: pace.subs.filter(function (t) { return Date.now() - t < BURST_WINDOW_MS; }).length,
        nextAllowedAt: pace.nextAllowedAt || 0, runLog: (s3.runLog || []).slice(-50)
      });
      return;
    }
    if (msg.type === "sh-setauto") {
      var patch = {};
      if (typeof msg.enabled === "boolean") patch.autoEnabled = msg.enabled;
      if (typeof msg.paused === "boolean") patch.autoPaused = msg.paused;
      await chrome.storage.local.set(patch);
      await log(msg.paused ? "Autonomous applying paused by user." : (msg.enabled === false ? "Autonomous applying turned OFF by user." : "Autonomous applying resumed/enabled by user."));
      sendResponse({ ok: true });
      tick();
      return;
    }
    if (msg.type === "sh-kick") { tick(); sendResponse({ ok: true }); return; }
  })();
  return true; // async response
});

chrome.alarms.create("swipehire-tick", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(function (a) { if (a.name === "swipehire-tick") tick(); });
chrome.runtime.onInstalled.addListener(function () { log("SwipeHire extension installed/updated (autonomous v6)."); tick(); });
chrome.runtime.onStartup.addListener(function () { tick(); });
