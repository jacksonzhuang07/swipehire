// SwipeHire content script — AUTONOMOUS apply drivers (v6).
//
// When the background queue processor assigns this tab a job (or the user
// opens one manually from the popup), the driver for the page runs the
// FULL flow itself: fill from the application payload's stored answers,
// attach the tailored resume, and click submit. It reports back
// submitted | needs_human | failed.
//
// Facts-only rule: fields are filled ONLY from the payload's answers. A
// required field/question with no matching stored fact parks the job as
// needs_human with the question text — the driver never guesses.
//
// Safety stops (park as needs_human, NEVER defeat): CAPTCHA/reCAPTCHA,
// login wall / expired session, security checkpoint, MFA prompt, and
// account-warning text (which also pauses all autonomous applying).

(function () {
  "use strict";
  if (window.__swipehireDriverRan) return;
  window.__swipehireDriverRan = true;

  var CONFIRM_RE = /(application (has been )?(submitted|received|sent)|thank you for applying|successfully applied|you('ve| have) applied|your application (was|has been) (submitted|sent|received)|we received your application|application sent)/i;

  var MATCHERS = [
    { re: /linkedin/i, keys: ["linkedin_url", "linkedin"] },
    { re: /portfolio|website|web site|personal url/i, keys: ["website", "portfolio", "website_url"] },
    { re: /e-?mail/i, keys: ["email"] },
    { re: /phone|mobile|cellular|telephone/i, keys: ["phone"] },
    { re: /first[\s_-]*name|given[\s_-]*name/i, keys: ["first_name"] },
    { re: /last[\s_-]*name|family[\s_-]*name|surname/i, keys: ["last_name"] },
    { re: /full[\s_-]*name|your name|candidate name|applicant name|^name$/i, keys: ["full_name"] },
    { re: /years? of experience|experience in years|how many years|years of .*experience/i, keys: ["years_of_experience"] },
    { re: /salary|compensation|pay expectation|expected pay/i, keys: ["salary_expectation"] },
    { re: /current (job )?title|present (job )?title|current position/i, keys: ["current_title"] },
    { re: /location|city|where are you located/i, keys: ["location"] },
    { re: /skills?/i, keys: ["skills"] }
  ];

  var state = { job: null, mode: null, cfg: null, aborted: false, panelStatus: null };

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function visible(el) {
    if (!el || el.disabled) return false;
    if (el.type === "hidden") return false;
    var r = el.getClientRects();
    return r && r.length > 0;
  }
  function clean(s) { return (s || "").replace(/\s+/g, " ").replace(/[:*]+$/, "").trim(); }
  function labelOf(el) {
    var t = "";
    if (el.labels && el.labels.length) t = el.labels[0].innerText || el.labels[0].textContent || "";
    if (!t) t = el.getAttribute("aria-label") || "";
    if (!t) { var l = el.closest("label"); if (l) t = l.innerText || l.textContent || ""; }
    if (!t) {
      var fs = el.closest("fieldset");
      if (fs) { var lg = fs.querySelector("legend"); if (lg) t = lg.innerText || ""; }
    }
    if (!t) t = el.placeholder || "";
    if (!t) t = (el.name || el.id || "").replace(/[_-]+/g, " ");
    return clean(t).slice(0, 140);
  }
  function haystack(el) {
    return [labelOf(el), el.name, el.id, el.placeholder, el.getAttribute("aria-label"), el.autocomplete]
      .filter(Boolean).join(" ");
  }
  function answerFor(el, answers) {
    var hay = haystack(el);
    for (var i = 0; i < MATCHERS.length; i++) {
      if (MATCHERS[i].re.test(hay)) {
        for (var k = 0; k < MATCHERS[i].keys.length; k++) {
          var v = answers[MATCHERS[i].keys[k]];
          if (v) return String(v);
        }
        return null; // matched a known field, but no stored fact for it
      }
    }
    return undefined; // not a field we recognise at all
  }
  function setValue(el, value) {
    var proto = el.constructor === HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el.constructor === HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value");
    if (setter && setter.set) setter.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }
  function fillSelect(el, value) {
    var want = String(value).trim().toLowerCase();
    var opts = Array.prototype.slice.call(el.options || []);
    var pick = opts.filter(function (o) { return o.value && (o.text || "").trim().toLowerCase() === want || String(o.value).trim().toLowerCase() === want; })[0]
      || opts.filter(function (o) { return o.value && (o.text || "").trim().toLowerCase().indexOf(want) === 0; })[0]
      || opts.filter(function (o) { return o.value && (o.text || "").trim().toLowerCase().indexOf(want) !== -1; })[0];
    if (pick) { setValue(el, pick.value); return true; }
    return false;
  }
  function fillFields(root, answers) {
    var filled = 0;
    var fields = root.querySelectorAll("input, textarea, select");
    fields.forEach(function (el) {
      if (!visible(el)) return;
      var type = (el.type || "").toLowerCase();
      if (["hidden", "submit", "button", "file", "checkbox", "radio", "password"].indexOf(type) !== -1) return;
      if (el.closest("#swipehire-panel")) return;
      if (el.value && String(el.value).trim()) return;
      var v = answerFor(el, answers);
      if (v === undefined || v === null) return;
      if (el.tagName === "SELECT") { if (fillSelect(el, v)) filled++; }
      else { setValue(el, v); filled++; }
    });
    return filled;
  }
  function attachResume(root, job) {
    var inputs = root.querySelectorAll("input[type='file']");
    var attached = false, found = false;
    inputs.forEach(function (el) {
      if (el.closest("#swipehire-panel")) return;
      found = true;
      if (el.files && el.files.length) { attached = true; return; }
      var text = job.tailored_resume || "";
      if (!text) return;
      var name = ((job.answers && job.answers.full_name) || "Resume").replace(/\s+/g, "_") + "_Resume.txt";
      try {
        var file = new File([text], name, { type: "text/plain" });
        var dt = new DataTransfer();
        dt.items.add(file);
        el.files = dt.files;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        if (el.files && el.files.length) attached = true;
      } catch (e) { /* some sites need a real picker; unknownRequired will park it */ }
    });
    return { found: found, attached: attached };
  }
  function checkConsents(root) {
    var n = 0;
    root.querySelectorAll("input[type='checkbox']").forEach(function (el) {
      if (!visible(el) || el.checked || el.closest("#swipehire-panel")) return;
      if (/agree|consent|certify|acknowledge|privacy polic|terms|i confirm/i.test(haystack(el))) { el.click(); n++; }
    });
    return n;
  }
  function selectResumeRadio(root) {
    var groups = {};
    root.querySelectorAll("input[type='radio']").forEach(function (el) {
      if (el.name) (groups[el.name] = groups[el.name] || []).push(el);
    });
    Object.keys(groups).forEach(function (name) {
      var g = groups[name];
      var anyChecked = g.some(function (el) { return el.checked; });
      if (anyChecked) return;
      var gl = labelOf(g[0]);
      if (/resume|cv/i.test(gl) || g.some(function (el) { return /resume|\.pdf|\.txt/i.test(labelOf(el)); })) g[0].click();
    });
  }
  function unansweredRequired(root) {
    var missing = [];
    var radioGroups = {};
    root.querySelectorAll("input, textarea, select").forEach(function (el) {
      if (!visible(el) || el.closest("#swipehire-panel")) return;
      var type = (el.type || "").toLowerCase();
      if (["hidden", "submit", "button"].indexOf(type) !== -1) return;
      var required = el.required || el.getAttribute("aria-required") === "true";
      if (!required) return;
      if (type === "radio") {
        var key = el.name || labelOf(el);
        radioGroups[key] = radioGroups[key] || [];
        radioGroups[key].push(el);
        return;
      }
      if (type === "checkbox") { if (!el.checked) missing.push(labelOf(el) || "required checkbox"); return; }
      if (type === "file") { if (!el.files || !el.files.length) missing.push(labelOf(el) || "resume upload"); return; }
      if (!String(el.value || "").trim()) missing.push(labelOf(el) || (el.name || "required field"));
    });
    Object.keys(radioGroups).forEach(function (key) {
      var g = radioGroups[key];
      if (!g.some(function (el) { return el.checked; })) missing.push(labelOf(g[0]) || key);
    });
    return missing.filter(function (v, i, a) { return v && a.indexOf(v) === i; });
  }
  function pageText() { return (document.body && document.body.innerText || ""); }
  function safety() {
    var text = pageText().slice(0, 9000);
    if (/account (has been )?(restricted|limited|suspended)|warning on your account|you('re| are) (temporarily )?restricted from/i.test(text)) {
      return { reason: "Account-warning signal on this site — autonomous applying has been paused everywhere. Check your account on the site before resuming.", accountWarning: true };
    }
    if (document.querySelector("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha, [data-sitekey]") ||
        /verify you are (not )?a robot|i'm not a robot|complete the captcha|prove you are human/i.test(text)) {
      return { reason: "A CAPTCHA is on this page. The extension never solves CAPTCHAs — complete it yourself, then re-queue this job." };
    }
    var pw = Array.prototype.slice.call(document.querySelectorAll("input[type='password']")).some(visible);
    var url = location.href.toLowerCase();
    if ((pw && /(login|signin|sign-in|authwall|checkpoint|session)/.test(url)) ||
        /sign in to (continue|apply)|log in to (continue|apply)|your session has expired/i.test(text)) {
      return { reason: "Login wall / session expired on this site — sign in there in your browser, then re-queue this job." };
    }
    if (/(checkpoint|security-challenge|challenge)/.test(url) || /security check|verify your identity|unusual (activity|sign-in)/i.test(text)) {
      return { reason: "A security checkpoint is showing on this site. Clear it in your browser, then re-queue this job." };
    }
    var otp = document.querySelector("input[autocomplete='one-time-code'], input[name*='otp' i], input[id*='otp' i]");
    if (otp && visible(otp) && /verification code|two-factor|authenticator/i.test(text)) {
      return { reason: "A multi-factor / verification-code prompt is showing. Complete it yourself, then re-queue this job." };
    }
    return null;
  }
  function findButton(root, patterns, exclude) {
    var all = root.querySelectorAll("button, input[type='submit'], input[type='button']");
    var found = null;
    all.forEach(function (el) {
      if (found || !visible(el) || el.closest("#swipehire-panel")) return;
      var t = clean(el.innerText || el.value || el.textContent || "");
      for (var i = 0; i < patterns.length; i++) {
        if (patterns[i].test(t) && !(exclude && exclude.test(t))) { found = el; return; }
      }
    });
    return found;
  }
  function confirmationEvidence() {
    var text = pageText();
    var m = text.match(new RegExp(".{0,70}" + CONFIRM_RE.source + ".{0,70}", "i"));
    if (m) return clean(m[0]).slice(0, 220);
    if (/(thank|confirm|success|applied)/i.test(location.href)) return "Landed on " + location.href;
    return null;
  }
  async function waitConfirmation(ms) {
    var end = Date.now() + ms;
    while (Date.now() < end) {
      var ev = confirmationEvidence();
      if (ev) return ev;
      var s = safety();
      if (s) return { safety: s };
      await sleep(650);
    }
    return null;
  }

  // ---------- panel ----------
  function showPanel(job) {
    if (document.getElementById("swipehire-panel")) return;
    var panel = document.createElement("div");
    panel.id = "swipehire-panel";
    panel.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#fff;color:#111;" +
      "border-radius:14px;box-shadow:0 10px 34px rgba(0,0,0,.28);padding:12px 14px;width:290px;" +
      "font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:13px;";
    var title = document.createElement("div");
    title.innerHTML = "<b>💘 SwipeHire — autonomous apply</b>";
    var info = document.createElement("div");
    info.style.cssText = "color:#444;margin:5px 0 9px;";
    info.textContent = job.job_title + " @ " + job.company + " — the extension is applying for you…";
    var stop = document.createElement("button");
    stop.textContent = "Stop & park this one";
    stop.style.cssText = "background:#eef0f2;color:#111;border:0;border-radius:999px;padding:7px 12px;font-weight:700;cursor:pointer;";
    stop.addEventListener("click", function () { state.aborted = true; info.textContent = "Stopping — parking this job as Needs you…"; });
    panel.appendChild(title); panel.appendChild(info); panel.appendChild(stop);
    document.body.appendChild(panel);
    state.panelStatus = function (t) { info.textContent = t; };
    state.panelDone = function () { try { stop.remove(); } catch (e) {} };
  }
  function status(t) { if (state.panelStatus) state.panelStatus(t); }

  // ---------- reporting ----------
  async function progress(note) {
    if (state.mode === "auto") {
      try { await chrome.runtime.sendMessage({ type: "sh-progress", note: note }); } catch (e) {}
    } else if (state.cfg) {
      try {
        await fetch(state.cfg.apiBase.replace(/\/$/, "") + "/api/ext/report", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Authorization": "Bearer " + state.cfg.token },
          body: JSON.stringify({ application_id: state.job.application_id, event: "filling", note: note })
        });
      } catch (e) {}
    }
  }
  async function finish(result) {
    status(result.outcome === "submitted" ? "Submitted ✓ — synced to SwipeHire."
      : result.outcome === "needs_human" ? "Parked as Needs you: " + (result.reason || "")
      : "Could not complete: " + (result.reason || ""));
    if (state.panelDone) state.panelDone();
    if (state.mode === "auto") {
      try { await chrome.runtime.sendMessage({ type: "sh-result", result: result }); } catch (e) {}
    } else if (state.cfg) {
      var ev = result.outcome === "submitted" ? "submitted" : result.outcome === "needs_human" ? "needs_human" : "failed";
      try {
        await fetch(state.cfg.apiBase.replace(/\/$/, "") + "/api/ext/report", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Authorization": "Bearer " + state.cfg.token },
          body: JSON.stringify({ application_id: state.job.application_id, event: ev, note: result.evidence || result.reason || "" })
        });
      } catch (e) {}
    }
    return result;
  }
  function needHuman(reason, extra) {
    return finish(Object.assign({ outcome: "needs_human", reason: reason }, extra || {}));
  }

  // ---------- generic / multi-step form flow (ATS + Indeed-style) ----------
  async function formFlow(job) {
    var answers = job.answers || {};
    for (var step = 0; step < 6; step++) {
      if (state.aborted) return needHuman("You stopped the extension on this page.");
      var s = safety(); if (s) return needHuman(s.reason, s.accountWarning ? { accountWarning: true } : {});
      var root = document.body;
      var filled = fillFields(root, answers);
      var att = attachResume(root, job);
      checkConsents(root); selectResumeRadio(root);
      if (step === 0) await progress("Autonomous driver filling the form (filled " + filled + " field(s)" + (att.attached ? ", resume attached" : "") + ").");
      if (att.found && !att.attached) {
        return needHuman("This form requires a resume file, but the application package has no tailored resume text to attach.");
      }
      var missing = unansweredRequired(root);
      if (missing.length) {
        return needHuman("Required question(s) with no stored answer — the driver never guesses: " + missing.slice(0, 3).join(" · "));
      }
      var ev0 = confirmationEvidence(); if (ev0) return finish({ outcome: "submitted", evidence: ev0 });
      var submitBtn = findButton(root, [/submit/i, /send application/i, /apply now/i, /complete application/i]);
      var contBtn = submitBtn ? null : findButton(root, [/^continue$/i, /^next$/i, /continue/i]);
      var btn = submitBtn || contBtn;
      if (!btn) return needHuman("No submit or continue button the driver recognises on this page — the site's flow may have changed.");
      var isSubmit = !!submitBtn;
      status(isSubmit ? "Submitting…" : "Continuing to the next step…");
      btn.click();
      if (isSubmit) {
        var res = await waitConfirmation(10000);
        if (res && res.safety) return needHuman(res.safety.reason, res.safety.accountWarning ? { accountWarning: true } : {});
        if (res) return finish({ outcome: "submitted", evidence: typeof res === "string" ? res : "Confirmation detected." });
        var still = unansweredRequired(document.body);
        if (still.length) return needHuman("The form bounced with required fields still empty: " + still.slice(0, 3).join(" · "));
        return finish({ outcome: "failed", reason: "The driver clicked submit but no confirmation appeared on the page." });
      }
      await sleep(1400);
      var ev1 = confirmationEvidence(); if (ev1) return finish({ outcome: "submitted", evidence: ev1 });
    }
    return needHuman("The apply flow did not reach a submit step after several screens.");
  }

  // ---------- LinkedIn Easy Apply ----------
  async function easyApply(job) {
    var answers = job.answers || {};
    var entry = findButton(document.body, [/easy apply/i]);
    if (!entry) return formFlow(job); // already inside the flow / different layout
    entry.click();
    await sleep(1400);
    for (var step = 0; step < 8; step++) {
      if (state.aborted) return needHuman("You stopped the extension on this page.");
      var s = safety(); if (s) return needHuman(s.reason, s.accountWarning ? { accountWarning: true } : {});
      var modal = document.querySelector(".jobs-easy-apply-content, [data-test-modal], div[role='dialog']") || document.body;
      fillFields(modal, answers);
      var att = attachResume(modal, job);
      checkConsents(modal); selectResumeRadio(modal);
      if (step === 0) await progress("Autonomous driver working through LinkedIn Easy Apply.");
      if (att.found && !att.attached) return needHuman("Easy Apply asks for a resume file, but the application package has no tailored resume text to attach.");
      var ev0 = confirmationEvidence(); if (ev0) return finish({ outcome: "submitted", evidence: ev0 });
      var missing = unansweredRequired(modal);
      if (missing.length) return needHuman("Easy Apply asked something your stored facts can't answer: " + missing.slice(0, 3).join(" · "));
      var submitBtn = findButton(modal, [/submit application/i]);
      if (submitBtn) {
        status("Submitting your Easy Apply application…");
        submitBtn.click();
        var res = await waitConfirmation(10000);
        if (res && res.safety) return needHuman(res.safety.reason, res.safety.accountWarning ? { accountWarning: true } : {});
        if (res) {
          var done = findButton(document.body, [/^done$/i]); if (done) done.click();
          return finish({ outcome: "submitted", evidence: typeof res === "string" ? res : "Easy Apply confirmation detected." });
        }
        return finish({ outcome: "failed", reason: "Clicked 'Submit application' in Easy Apply but no confirmation appeared." });
      }
      var nextBtn = findButton(modal, [/^review/i, /^continue/i, /^next/i]);
      if (!nextBtn) return needHuman("An Easy Apply step the driver doesn't recognise (no Next / Review / Submit button).");
      nextBtn.click();
      await sleep(1200);
    }
    return needHuman("Easy Apply took more steps than the driver will walk unattended.");
  }

  // ---------- main ----------
  async function run() {
    var resp = null;
    try { resp = await chrome.runtime.sendMessage({ type: "sh-getjob" }); } catch (e) {}
    if (!resp || !resp.job) {
      // Manual fallback: v1-style storage job (popup click-to-open).
      try {
        var cfg = await chrome.storage.local.get(["apiBase", "token", "currentJob"]);
        if (!cfg.currentJob || !cfg.currentJob.application_id) return;
        if (cfg.currentJob.job_url && location.hostname && !cfg.currentJob.job_url.includes(location.hostname)) return;
        state.mode = "manual";
        state.job = cfg.currentJob;
        state.cfg = { apiBase: cfg.apiBase || "https://swipehire-jade.vercel.app", token: cfg.token || "" };
      } catch (e) { return; }
    } else {
      state.mode = resp.mode || "manual";
      state.job = resp.job;
      if (state.mode === "manual") {
        try {
          var c2 = await chrome.storage.local.get(["apiBase", "token"]);
          state.cfg = { apiBase: c2.apiBase || "https://swipehire-jade.vercel.app", token: c2.token || "" };
        } catch (e) {}
      }
    }
    var job = state.job;
    if (!job) return;
    showPanel(job);
    await sleep(700);
    var s0 = safety();
    if (s0) return needHuman(s0.reason, s0.accountWarning ? { accountWarning: true } : {});
    var host = location.hostname.toLowerCase();
    if (host.indexOf("workday") !== -1 || host.indexOf("myworkdayjobs") !== -1) {
      var hasPw = Array.prototype.slice.call(document.querySelectorAll("input[type='password']")).some(visible);
      var hasForm = document.querySelectorAll("input, select, textarea").length > 2;
      if (hasPw || !hasForm) {
        return needHuman("Workday account portals are not yet automated — this employer's portal needs an account sign-in first. (If you are already signed in there and see a plain form, re-queue and the generic driver will try it.)");
      }
      return formFlow(job);
    }
    if (host.indexOf("linkedin.") !== -1) return easyApply(job);
    return formFlow(job);
  }

  if (document.readyState === "complete" || document.readyState === "interactive") run();
  else document.addEventListener("DOMContentLoaded", run);
})();
