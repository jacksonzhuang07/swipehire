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
//   - at least 90 seconds between submissions, plus 0-45s random jitter
//   - rolling burst cap: 5 submissions per 30 minutes
//   - daily caps per board: LinkedIn 30, Indeed 20, ATS 40
// Safety: a driver result flagged as an account warning triggers a hard
// 48-HOUR halt of all autonomous applying (resumable only after it
// expires, or by explicit user override in the popup). CAPTCHAs, login
// walls, checkpoints and unanswerable required questions are parked as
// needs_human by the drivers — never defeated, never retried blindly.
// DRY-RUN MODE (popup toggle): drivers fill + verify but never submit,
// reporting verified_dry_run with a per-field report; dry runs never
// touch the pacing counters (nothing was submitted).

"use strict";

var CAPS = { linkedin: 30, indeed: 20, ats: 40 };
var BURST_MAX = 5, BURST_WINDOW_MS = 30 * 60 * 1000;
var MIN_GAP_MS = 90 * 1000;
var JITTER_MAX_MS = 45 * 1000;
var HALT_MS = 48 * 3600 * 1000;
var CLAIM_TIMEOUT_MS = 150 * 1000;

function gapMs() { return MIN_GAP_MS + Math.floor(Math.random() * (JITTER_MAX_MS / 1000)) * 1000; }

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

async function report(applicationId, event, note, extra) {
  var body = { application_id: applicationId, event: event, note: note || "" };
  if (extra) Object.assign(body, extra);
  return api("/api/ext/report", { method: "POST", body: JSON.stringify(body) });
}

async function getProc() {
  var s = await get(["proc", "autoEnabled", "autoPaused", "dryRun", "autoHaltUntil"]);
  return { proc: s.proc || null, autoEnabled: s.autoEnabled !== false, autoPaused: !!s.autoPaused,
           dryRun: !!s.dryRun, autoHaltUntil: s.autoHaltUntil || 0 };
}

async function haltActive() {
  var s = await get(["autoHaltUntil"]);
  return !!(s.autoHaltUntil && Date.now() < s.autoHaltUntil);
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
  if (st.autoHaltUntil && Date.now() < st.autoHaltUntil) return; // 48h account-warning halt
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
  // Pre-flight liveness: a dead posting parks cheaply, without driving.
  // Only a definitive 404/410 parks — network flakiness proceeds as usual.
  try {
    var resp = await fetch(item.job_url, { method: "GET", credentials: "include" });
    if (resp.status === 404 || resp.status === 410) {
      await report(item.application_id, "needs_human",
        "Pre-flight: this posting appears to be gone (HTTP " + resp.status + ") — parked without driving the form.");
      await log("Pre-flight parked application " + item.application_id + ": posting HTTP " + resp.status + ".");
      return;
    }
  } catch (e) { /* unreachable from here is not proof of death — drive it */ }
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
  if (result.outcome === "submitted" || result.outcome === "submitted_unverified") {
    // A submit click really happened in both cases, so both consume
    // pacing — re-driving sooner would risk a duplicate application.
    await report(appId, result.outcome, result.evidence || "Confirmation detected by the autonomous driver.",
                 result.fieldReport ? { report: result.fieldReport } : null);
    var pace = await getPace();
    pace.subs.push(Date.now());
    pace.daily[board] = (pace.daily[board] || 0) + 1;
    var gap = gapMs();
    pace.nextAllowedAt = Date.now() + gap;
    await setPace(pace);
    await log((result.outcome === "submitted" ? "Submitted: application " : "Submitted (unverified): application ") + appId + " (" + board + ", today " + pace.daily[board] + "/" + CAPS[board] + "). Next submission allowed in ~" + Math.round(gap / 1000) + "s.");
  } else if (result.outcome === "verified_dry_run") {
    // Nothing was submitted: store the per-field report, touch NOTHING
    // in the pacing counters.
    await report(appId, "verified_dry_run", result.evidence || "Dry run verified — submit not clicked.",
                 result.fieldReport ? { report: result.fieldReport } : null);
    await log("Dry run verified: application " + appId + " — form filled and verified, submit NOT clicked.");
  } else if (result.outcome === "needs_human") {
    await report(appId, "needs_human", result.reason || "The driver needs a human on this page.",
                 result.fieldReport ? { report: result.fieldReport } : null);
    await log("Needs you: application " + appId + " — " + (result.reason || ""));
    if (result.accountWarning) {
      var haltUntil = Date.now() + HALT_MS;
      await chrome.storage.local.set({ autoPaused: true, autoHaltUntil: haltUntil });
      await log("⚠ Account-warning signal detected — autonomous applying is HALTED for 48 hours (until " + new Date(haltUntil).toLocaleString() + "). It resumes only when the halt expires or you explicitly override it in the popup.");
    }
  } else {
    await report(appId, "failed", result.reason || "The driver could not complete this application.",
                 result.fieldReport ? { report: result.fieldReport } : null);
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
        sendResponse({ mode: "auto", job: job, dryRun: st.dryRun });
      } else if ((!st.proc || !st.proc.busy) && job && job.job_url && sender.tab && job.job_url.split("#")[0] === (sender.tab.url || "").split("#")[0]) {
        sendResponse({ mode: "manual", job: job, dryRun: st.dryRun });
      } else {
        sendResponse({ mode: null, job: null });
      }
      return;
    }
    if (msg.type === "sh-answer-lookup") {
      var r = await api("/api/ext/answer-lookup", { method: "POST", body: JSON.stringify({ question: msg.question || "" }) });
      sendResponse(r.json || { found: false });
      return;
    }
    if (msg.type === "sh-submitclick") {
      var stc = await getProc();
      if (stc.proc && stc.proc.busy) {
        await report(stc.proc.appId, "submit_clicked", "Driver is clicking Submit now.");
      }
      sendResponse({ ok: true });
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
        autoEnabled: st3.autoEnabled, autoPaused: st3.autoPaused, dryRun: st3.dryRun,
        haltUntil: st3.autoHaltUntil || 0,
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
      if (typeof msg.dryRun === "boolean") patch.dryRun = msg.dryRun;
      if (msg.overrideHalt === true) {
        // Explicit user override of the 48h account-warning halt.
        patch.autoHaltUntil = 0;
        patch.autoPaused = false;
        await log("48-hour account-warning halt OVERRIDDEN by user in the popup.");
      }
      await chrome.storage.local.set(patch);
      if (typeof msg.dryRun === "boolean") await log(msg.dryRun ? "Dry-run mode ON — drivers will fill and verify but NEVER submit." : "Dry-run mode OFF — drivers submit for real.");
      else if (!msg.overrideHalt) await log(msg.paused ? "Autonomous applying paused by user." : (msg.enabled === false ? "Autonomous applying turned OFF by user." : "Autonomous applying resumed/enabled by user."));
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
