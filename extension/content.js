// SwipeHire content script — assisted apply on supported job pages.
//
// Honest v1 behaviour: when you open a queued job from the extension popup,
// this script autofills the application fields it can detect (name, email,
// phone) from the SwipeHire application payload stored locally, and shows a
// small floating SwipeHire panel. YOU review and click the site's own final
// submit button — the extension never submits on your behalf in v1.
// [Mark submitted] / [Couldn't apply] in the panel report the outcome back
// to SwipeHire (POST /api/ext/report); "submitted" is only ever recorded
// from that explicit report, or from a detected confirmation page.

(function () {
  "use strict";

  var FIELD_PATTERNS = [
    { key: "full_name", re: /(full[\s_-]?name|^name$|first[\s_-]?and[\s_-]?last)/i },
    { key: "email", re: /(e-?mail)/i },
    { key: "phone", re: /(phone|mobile|tel)/i }
  ];

  function fieldHaystack(el) {
    return [el.name, el.id, el.placeholder, el.getAttribute("aria-label"), el.autocomplete]
      .filter(Boolean).join(" ");
  }

  function setValue(el, value) {
    var proto = el.constructor === HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value");
    if (setter && setter.set) setter.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function autofill(answers) {
    var filled = 0;
    var inputs = document.querySelectorAll("input[type='text'], input[type='email'], input[type='tel'], input:not([type])");
    inputs.forEach(function (el) {
      if (el.value) return;
      var hay = fieldHaystack(el);
      for (var i = 0; i < FIELD_PATTERNS.length; i++) {
        var p = FIELD_PATTERNS[i];
        if (p.re.test(hay) && answers[p.key]) { setValue(el, answers[p.key]); filled++; break; }
      }
    });
    return filled;
  }

  function report(apiBase, token, applicationId, event, note) {
    return fetch(apiBase.replace(/\/$/, "") + "/api/ext/report", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ application_id: applicationId, event: event, note: note || "" })
    });
  }

  function showPanel(cfg, job, filledCount) {
    if (document.getElementById("swipehire-panel")) return;
    var panel = document.createElement("div");
    panel.id = "swipehire-panel";
    panel.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#fff;color:#111;" +
      "border-radius:14px;box-shadow:0 10px 34px rgba(0,0,0,.28);padding:12px 14px;width:270px;" +
      "font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:13px;";
    var title = document.createElement("div");
    title.innerHTML = "<b>💘 SwipeHire</b>";
    var info = document.createElement("div");
    info.style.cssText = "color:#656e7b;margin:5px 0 9px;";
    info.textContent = job.job_title + " @ " + job.company + " — autofilled " + filledCount +
      " field(s) from your SwipeHire profile. Review everything, then click this site's own submit button.";
    var ok = document.createElement("button");
    ok.textContent = "I submitted it ✓";
    ok.style.cssText = "background:linear-gradient(45deg,#fd267a,#ff7854);color:#fff;border:0;border-radius:999px;padding:7px 12px;font-weight:700;cursor:pointer;margin-right:6px;";
    var bad = document.createElement("button");
    bad.textContent = "Couldn't apply";
    bad.style.cssText = "background:#eef0f2;color:#111;border:0;border-radius:999px;padding:7px 12px;font-weight:700;cursor:pointer;";
    ok.addEventListener("click", function () {
      report(cfg.apiBase, cfg.token, job.application_id, "submitted", "Confirmed from the job page panel.")
        .then(function () { info.textContent = "Recorded as Submitted in SwipeHire. ✓"; ok.remove(); bad.remove(); });
    });
    bad.addEventListener("click", function () {
      report(cfg.apiBase, cfg.token, job.application_id, "failed", "User reported they could not apply from the job page.")
        .then(function () { info.textContent = "Marked as needing review in SwipeHire."; ok.remove(); bad.remove(); });
    });
    panel.appendChild(title);
    panel.appendChild(info);
    panel.appendChild(ok);
    panel.appendChild(bad);
    document.body.appendChild(panel);
  }

  function detectConfirmation(cfg, job) {
    var text = (document.body && document.body.innerText || "").slice(0, 6000);
    if (/(application (has been )?(submitted|received)|thank you for applying|successfully applied)/i.test(text)) {
      report(cfg.apiBase, cfg.token, job.application_id, "submitted", "Confirmation page detected by the extension.");
      return true;
    }
    return false;
  }

  try {
    chrome.storage.local.get(["apiBase", "token", "currentJob"], function (cfg) {
      var job = cfg && cfg.currentJob;
      if (!job || !job.application_id) return;
      if (job.job_url && location.href.split("#")[0] !== job.job_url.split("#")[0] &&
          location.hostname && !job.job_url.includes(location.hostname)) return;
      var answers = job.answers || {};
      var filled = autofill(answers);
      var full = { apiBase: cfg.apiBase || "https://swipehire-jade.vercel.app", token: cfg.token || "" };
      if (filled > 0) report(full.apiBase, full.token, job.application_id, "filled", "Autofilled " + filled + " field(s).");
      showPanel(full, job, filled);
      detectConfirmation(full, job);
    });
  } catch (e) { /* not running inside the extension context */ }
})();
