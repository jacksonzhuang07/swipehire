// SwipeHire content script — AUTONOMOUS apply drivers (v7, hardened).
//
// When the background queue processor assigns this tab a job (or the user
// opens one manually from the popup), the driver for the page runs the
// FULL flow itself: pre-flight scan, fill from the application payload's
// stored answers (+ the server answer bank), attach the tailored resume,
// and click submit. It reports back submitted | submitted_unverified |
// verified_dry_run | needs_human | failed.
//
// Facts-only rule: fields are filled ONLY from the payload's answers or
// the user's answer bank (both user-supplied). A required field/question
// with no stored fact and no banked answer parks the job as needs_human
// with the question text — the driver never guesses.
//
// DRY-RUN MODE: when enabled (popup toggle), the driver fills, attaches,
// walks multi-step flows and verifies field states, but NEVER clicks the
// final submit button; it reports verified_dry_run with a per-field
// report instead.
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

  // ----- question normalization: JS mirror of app/answers.py -----
  var SYNONYMS = [
    [/legally (authorized|authorised|entitled|eligible) to work|authorized to work|authorised to work|eligible to work|right to work|work authorization|work authorisation|permission to work/g, "workauth"],
    [/require sponsorship|need sponsorship|sponsorship (to work|now or in the future)|visa sponsorship|require a work (visa|permit)|need a work (visa|permit)/g, "sponsorship"],
    [/salary expectation|expected salary|salary requirements|desired salary|compensation expectation|expected compensation|pay expectation/g, "salaryexpectation"],
    [/years of experience|how many years of .* experience|years experience/g, "yearsexperience"],
    [/when can you start|available to start|earliest start date|start date availability/g, "startdate"],
    [/willing to relocate|open to relocation|able to relocate/g, "relocation"],
    [/driver'?s licen[cs]e|driving licen[cs]e/g, "driverslicense"],
    [/criminal record|background check|criminal background/g, "backgroundcheck"],
    [/how did you hear about (us|this (job|role|position))|how did you find (us|this (job|role|position))/g, "referralsource"]
  ];
  var STOPWORDS = { are: 1, you: 1, do: 1, does: 1, the: 1, a: 1, an: 1, in: 1, to: 1, for: 1, of: 1, or: 1, and: 1, is: 1, it: 1, if: 1, so: 1, what: 1, whats: 1, please: 1, currently: 1, will: 1, would: 1, can: 1, could: 1, have: 1, has: 1, your: 1, yours: 1, this: 1, that: 1, with: 1, without: 1, any: 1, now: 1, later: 1, require: 1, required: 1, need: 1, needs: 1, us: 1, we: 1, our: 1, be: 1, been: 1, at: 1, on: 1, by: 1, from: 1, as: 1, about: 1, into: 1, over: 1, under: 1, canada: 1, canadian: 1, here: 1, country: 1, province: 1, state: 1 };
  function swipeNormalizeQuestion(text) {
    var s = (text || "").toLowerCase();
    SYNONYMS.forEach(function (pair) { s = s.replace(pair[0], " " + pair[1] + " "); });
    s = s.replace(/[^a-z0-9 ]+/g, " ");
    var toks = {};
    s.split(/\s+/).forEach(function (t) { if (t && !STOPWORDS[t]) toks[t] = 1; });
    for (var i = 0; i < SYNONYMS.length; i++) { if (toks[SYNONYMS[i][1]]) return SYNONYMS[i][1]; }
    return Object.keys(toks).sort().join(" ");
  }

  // ----- per-ATS dedicated drivers (selector fallback chains) -----
  // Each dedicated driver adds host-specific field selectors tried BEFORE
  // the generic label matcher, plus a host-specific submit selector. The
  // generic driver remains the fallback for everything else.
  var ATS_DRIVERS = [
    { name: "greenhouse", host: /greenhouse\.io$/,
      fields: [["input#first_name", "first_name"], ["input#last_name", "last_name"],
               ["input#email", "email"], ["input#phone", "phone"]],
      submit: "#submit_app" },
    { name: "lever", host: /lever\.co$/,
      fields: [["input[name='name']", "full_name"], ["input[name='email']", "email"],
               ["input[name='phone']", "phone"]],
      submit: ".template-btn-submit" },
    { name: "ashby", host: /ashbyhq\.com$/,
      fields: [["input[name='_systemfield_name']", "full_name"],
               ["input[name='_systemfield_email']", "email"]],
      submit: "button[type='submit']" }
  ];
  function hostDriver() {
    var h = location.hostname.toLowerCase();
    for (var i = 0; i < ATS_DRIVERS.length; i++) if (ATS_DRIVERS[i].host.test(h)) return ATS_DRIVERS[i];
    return null;
  }

  var state = { job: null, mode: null, cfg: null, aborted: false, panelStatus: null,
                dryRun: false, report: [], verify: [] };

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
    if (!t) {
      // Div-label convention (Lever/Ashby-style): a label-ish element in
      // the field's container or its parent, carrying the label text.
      var boxes = [el.closest("div, li, section"), el.parentElement];
      for (var bi = 0; bi < boxes.length && !t; bi++) {
        var box = boxes[bi];
        if (!box) continue;
        var lab = box.querySelector(":scope > .label, :scope > [class*='label'], :scope > [data-label], :scope > span, :scope > div, :scope > label");
        if (lab && lab !== el && !lab.contains(el)) t = lab.innerText || lab.textContent || "";
      }
    }
    if (!t) t = el.placeholder || "";
    if (!t) t = (el.name || el.id || "").replace(/[_-]+/g, " ");
    return clean(t).slice(0, 140);
  }
  function haystack(el) {
    return [labelOf(el), el.name, el.id, el.placeholder, el.getAttribute("aria-label"), el.autocomplete]
      .filter(Boolean).join(" ");
  }
  function answerForText(hay, answers) {
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
  function answerFor(el, answers) { return answerForText(haystack(el), answers); }
  // The QUESTION for a radio group lives in the fieldset legend or a
  // label-ish container — never in an option's own label ("Yes"/"No").
  function groupLabel(g) {
    var el = g[0];
    var fs = el.closest("fieldset");
    if (fs) { var lg = fs.querySelector("legend"); if (lg) { var t = clean(lg.innerText || lg.textContent || ""); if (t) return t.slice(0, 140); } }
    var box = el.closest("div, li, section");
    if (box) {
      var lab = box.querySelector(":scope > .label, :scope > [class*='label'], :scope > p, :scope > span, :scope > div");
      if (lab && !lab.contains(el)) { var t2 = clean(lab.innerText || lab.textContent || ""); if (t2) return t2.slice(0, 140); }
    }
    return labelOf(el);
  }
  function fillRadios(root, answers) {
    var groups = {};
    root.querySelectorAll("input[type='radio']").forEach(function (el) {
      if (el.name) (groups[el.name] = groups[el.name] || []).push(el);
    });
    var n = 0;
    Object.keys(groups).forEach(function (name) {
      var g = groups[name];
      if (g.some(function (el) { return el.checked; })) return;
      var v = answerForText(groupLabel(g), answers);
      if (v === undefined || v === null) return;
      var fake = g.map(function (r) { return { text: labelOf(r), value: "", el: r }; });
      var pick = optionMatch(fake, v);
      if (pick) { pick.el.click(); n++; record(groupLabel(g), "filled", v, pick.el, "check"); }
    });
    return n;
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
  function optionMatch(opts, value) {
    var want = String(value).trim().toLowerCase();
    var texts = opts.map(function (o) { return (o.text || o.label || "").trim().toLowerCase(); });
    var vals = opts.map(function (o) { return String(o.value || "").trim().toLowerCase(); });
    var i;
    for (i = 0; i < opts.length; i++) if (texts[i] === want || vals[i] === want) return opts[i];
    for (i = 0; i < opts.length; i++) if (texts[i].indexOf(want) === 0 || vals[i].indexOf(want) === 0) return opts[i];
    // Numeric answers match on token boundaries: "6" -> "6+ years", not "16".
    if (/^\d+(\.\d+)?$/.test(want)) {
      var re = new RegExp("(^|[^0-9])" + want.replace(".", "\\.") + "([^0-9]|$)");
      for (i = 0; i < opts.length; i++) if (re.test(texts[i])) return opts[i];
    }
    for (i = 0; i < opts.length; i++) if (texts[i].indexOf(want) !== -1 || vals[i].indexOf(want) !== -1) return opts[i];
    return null;
  }
  function fillSelect(el, value) {
    var opts = Array.prototype.slice.call(el.options || []);
    var pick = optionMatch(opts.filter(function (o) { return o.value; }), value);
    if (pick) { setValue(el, pick.value); return true; }
    return false;
  }

  // ----- per-field report (dry-run + diagnostics) -----
  function record(label, status, detail, el, kind) {
    var entry = { label: String(label || "field").slice(0, 120), status: status, detail: String(detail || "").slice(0, 160) };
    state.report.push(entry);
    if (el) state.verify.push({ el: el, entry: entry, kind: kind || "value" });
    return entry;
  }
  function verifyFilled() {
    state.verify.forEach(function (v) {
      try {
        var ok;
        if (v.kind === "file") ok = !!(v.el.files && v.el.files.length);
        else if (v.kind === "check") ok = !!v.el.checked;
        else ok = !!String(v.el.value || "").trim();
        if (!ok && v.entry.status === "filled") {
          v.entry.status = "missed";
          v.entry.detail = "value did not stick after filling";
        }
      } catch (e) { /* element gone after step change — leave as filled */ }
    });
  }
  function buildReport(wouldSubmit, parkedReason) {
    var filled = state.report.filter(function (f) { return f.status === "filled"; }).length;
    var missed = state.report.filter(function (f) { return f.status === "missed"; }).length;
    return { fields: state.report.slice(0, 60), filled: filled, missed: missed,
             would_submit: !!wouldSubmit, parked_reason: parkedReason || "",
             url: location.href, dry_run: !!state.dryRun };
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
      if (v === undefined) return;
      if (v === null) { record(labelOf(el), "missed", "recognised field, no stored answer"); return; }
      if (el.tagName === "SELECT") { if (fillSelect(el, v)) { filled++; record(labelOf(el), "filled", v, el); } else record(labelOf(el), "missed", "no matching option for: " + v); }
      else { setValue(el, v); filled++; record(labelOf(el), "filled", v, el); }
    });
    return filled;
  }
  function extraFill(root, answers) {
    var drv = hostDriver();
    if (!drv) return 0;
    var n = 0;
    drv.fields.forEach(function (pair) {
      var el = root.querySelector(pair[0]);
      if (!el || !visible(el) || (el.value && String(el.value).trim())) return;
      var v = answers[pair[1]];
      if (!v) return;
      if (el.tagName === "SELECT") { if (fillSelect(el, v)) { n++; record(labelOf(el), "filled", v, el); } }
      else { setValue(el, String(v)); n++; record(labelOf(el), "filled", String(v), el); }
    });
    return n;
  }
  function attachResume(root, job) {
    var inputs = root.querySelectorAll("input[type='file']");
    var attached = false, found = false;
    inputs.forEach(function (el) {
      if (el.closest("#swipehire-panel")) return;
      found = true;
      if (el.files && el.files.length) { attached = true; record(labelOf(el) || "Resume", "filled", "file already present", el, "file"); return; }
      var text = job.tailored_resume || "";
      if (!text) { record(labelOf(el) || "Resume", "missed", "no tailored resume in package", el, "file"); return; }
      var name = ((job.answers && job.answers.full_name) || "Resume").replace(/\s+/g, "_") + "_Resume.txt";
      try {
        var file = new File([text], name, { type: "text/plain" });
        var dt = new DataTransfer();
        dt.items.add(file);
        el.files = dt.files;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        if (el.files && el.files.length) { attached = true; record(labelOf(el) || "Resume", "filled", name, el, "file"); }
      } catch (e) { /* some sites need a real picker; pre-flight will park it */ }
    });
    return { found: found, attached: attached };
  }
  function checkConsents(root) {
    var n = 0;
    root.querySelectorAll("input[type='checkbox']").forEach(function (el) {
      if (!visible(el) || el.checked || el.closest("#swipehire-panel")) return;
      if (/agree|consent|certify|acknowledge|privacy polic|terms|i confirm/i.test(haystack(el))) {
        el.click(); n++; record(labelOf(el) || "consent", "filled", "consent checked", el, "check");
      }
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
      var gl = groupLabel(g);
      if (/resume|cv/i.test(gl) || g.some(function (el) { return /resume|\.pdf|\.txt/i.test(labelOf(el)); })) {
        g[0].click(); record(gl || "resume choice", "filled", "existing-resume option selected", g[0], "check");
      }
    });
  }
  // Div-based custom dropdown widgets (not native <select>): click to
  // open, then click the option whose text matches the stored answer.
  async function fillCustomDropdowns(root, answers) {
    var widgets = root.querySelectorAll("[role='combobox'], [aria-haspopup='listbox'], .custom-select, [class*='dropdown-select']");
    var n = 0;
    for (var wi = 0; wi < widgets.length; wi++) {
      var w = widgets[wi];
      if (!visible(w) || w.closest("#swipehire-panel")) continue;
      if (w.tagName === "SELECT" || w.tagName === "INPUT") continue;
      var v = answerFor(w, answers);
      if (v === undefined || v === null) continue;
      if ((w.innerText || "").indexOf(v) !== -1) continue; // already shows the answer
      w.click();
      await sleep(350);
      var opts = root.querySelectorAll("[role='option'], [role='listbox'] li, [role='listbox'] div, .dropdown-option, [class*='option']");
      var fake = [];
      opts.forEach(function (o) { if (visible(o)) fake.push({ text: o.innerText || o.textContent || "", value: "", el: o }); });
      var pick = optionMatch(fake, v);
      if (pick) { pick.el.click(); n++; record(labelOf(w), "filled", v + " (custom dropdown)"); await sleep(200); }
      else record(labelOf(w), "missed", "custom dropdown: no option matched " + v);
    }
    return n;
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
      if (!g.some(function (el) { return el.checked; })) missing.push(groupLabel(g) || key);
    });
    return missing.filter(function (v, i, a) { return v && a.indexOf(v) === i; });
  }

  // All driveable roots: the top document plus same-origin iframes
  // (many ATS forms are embedded). Cross-origin frames are skipped —
  // the driver cannot see into them and pre-flight will park instead.
  function allRoots() {
    var roots = [document];
    document.querySelectorAll("iframe").forEach(function (f) {
      try {
        var d = f.contentDocument;
        if (d && d.body) roots.push(d);
      } catch (e) { /* cross-origin */ }
    });
    return roots;
  }
  function rootText(root) {
    try { return (root.body && root.body.innerText) || ""; } catch (e) { return ""; }
  }
  function pageText() { return allRoots().map(rootText).join("\n"); }

  function safetyIn(root) {
    var text = rootText(root).slice(0, 9000);
    var doc = root.ownerDocument || root;
    if (/account (has been )?(restricted|limited|suspended)|warning on your account|you('re| are) (temporarily )?restricted from/i.test(text)) {
      return { reason: "Account-warning signal on this site — autonomous applying has been paused everywhere. Check your account on the site before resuming.", accountWarning: true };
    }
    if (doc.querySelector("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha, [data-sitekey]") ||
        /verify you are (not )?a robot|i'm not a robot|complete the captcha|prove you are human/i.test(text)) {
      return { reason: "A CAPTCHA is on this page. The extension never solves CAPTCHAs — complete it yourself, then re-queue this job." };
    }
    var pw = Array.prototype.slice.call(doc.querySelectorAll("input[type='password']")).some(visible);
    var url = location.href.toLowerCase();
    if ((pw && /(login|signin|sign-in|authwall|checkpoint|session)/.test(url)) ||
        /sign in to (continue|apply)|log in to (continue|apply)|your session has expired/i.test(text)) {
      return { reason: "Login wall / session expired on this site — sign in there in your browser, then re-queue this job." };
    }
    if (/(checkpoint|security-challenge|challenge)/.test(url) || /security check|verify your identity|unusual (activity|sign-in)/i.test(text)) {
      return { reason: "A security checkpoint is showing on this site. Clear it in your browser, then re-queue this job." };
    }
    var otp = doc.querySelector("input[autocomplete='one-time-code'], input[name*='otp' i], input[id*='otp' i]");
    if (otp && visible(otp) && /verification code|two-factor|authenticator/i.test(text)) {
      return { reason: "A multi-factor / verification-code prompt is showing. Complete it yourself, then re-queue this job." };
    }
    return null;
  }
  function safety() {
    var roots = allRoots();
    for (var i = 0; i < roots.length; i++) { var s = safetyIn(roots[i]); if (s) return s; }
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
  function findButtonAnywhere(patterns, exclude) {
    var roots = allRoots();
    for (var i = 0; i < roots.length; i++) { var b = findButton(roots[i], patterns, exclude); if (b) return b; }
    var drv = hostDriver();
    if (drv && drv.submit) { var el = document.querySelector(drv.submit); if (el && visible(el)) return el; }
    return null;
  }
  // Hardened confirmation: a bare regex match is WEAK. Strong = confirm
  // text PLUS a structural signal: URL changed, the form is gone/hidden,
  // or the submit button that was clicked disappeared (modal step swap).
  var preSubmit = { url: "", formPresent: false, btnVisible: false };
  function anyVisibleForm() {
    var forms = document.querySelectorAll("form");
    for (var i = 0; i < forms.length; i++) if (visible(forms[i])) return true;
    return false;
  }
  function anyVisibleSubmitBtn() {
    var all = document.querySelectorAll("button, input[type='submit']");
    for (var i = 0; i < all.length; i++) {
      if (!visible(all[i])) continue;
      var t = clean(all[i].innerText || all[i].value || "");
      if (/submit|send application|apply now|complete application/i.test(t)) return true;
    }
    return false;
  }
  function snapshotPreSubmit(btn) {
    preSubmit.url = location.href;
    preSubmit.formPresent = anyVisibleForm();
    preSubmit.btnVisible = btn ? visible(btn) : anyVisibleSubmitBtn();
  }
  function confirmationSignals() {
    var text = pageText();
    var m = text.match(new RegExp(".{0,70}" + CONFIRM_RE.source + ".{0,70}", "i"));
    var urlHit = /(thank|confirm|success|applied)/i.test(location.href);
    var textHit = !!m || urlHit;
    var structural = location.href !== preSubmit.url
      || (preSubmit.formPresent && !anyVisibleForm())
      || (preSubmit.btnVisible && !anyVisibleSubmitBtn());
    return { textHit: textHit, structural: structural,
             evidence: m ? clean(m[0]).slice(0, 220) : (urlHit ? "Landed on " + location.href : "") };
  }
  async function waitConfirmation(ms) {
    var end = Date.now() + ms;
    var weak = null;
    while (Date.now() < end) {
      var sig = confirmationSignals();
      if (sig.textHit && sig.structural) return { strong: true, evidence: sig.evidence };
      if (sig.textHit && !weak) weak = sig;
      var s = safety();
      if (s) return { safety: s };
      await sleep(650);
    }
    var last = confirmationSignals();
    if (last.textHit) return { strong: false, evidence: last.evidence };
    if (weak) return { strong: false, evidence: weak.evidence };
    return null;
  }
  // Wait for a form to actually render (delayed JS rendering is common):
  // poll until a fillable field or an action button exists.
  async function waitForContent(ms) {
    var end = Date.now() + ms;
    while (Date.now() < end) {
      var roots = allRoots();
      for (var i = 0; i < roots.length; i++) {
        if (roots[i].querySelectorAll("input, textarea, select").length > 0) return true;
      }
      if (findButtonAnywhere([/submit/i, /continue/i, /next/i, /apply/i])) return true;
      await sleep(400);
    }
    return false;
  }

  // ---------- pre-flight + answer bank ----------
  // Predict, WITHOUT mutating the page, which required fields on this
  // step cannot be answered from the payload. Bank lookup then gets one
  // chance per question; anything still unanswerable parks the job
  // before a single field is touched.
  function scanUnanswerable(root, answers, job) {
    var missing = [];
    var radioGroups = {};
    root.querySelectorAll("input, textarea, select").forEach(function (el) {
      if (!visible(el) || el.closest("#swipehire-panel")) return;
      var type = (el.type || "").toLowerCase();
      if (["hidden", "submit", "button", "password"].indexOf(type) !== -1) return;
      var required = el.required || el.getAttribute("aria-required") === "true";
      if (!required) return;
      if (el.value && String(el.value).trim()) return;
      if (type === "radio") {
        var key = el.name || labelOf(el);
        (radioGroups[key] = radioGroups[key] || []).push(el);
        return;
      }
      if (type === "checkbox") {
        if (!el.checked && !/agree|consent|certify|acknowledge|privacy polic|terms|i confirm/i.test(haystack(el)))
          missing.push({ label: labelOf(el) || "required checkbox", kind: "checkbox", el: el });
        return;
      }
      if (type === "file") {
        if ((!el.files || !el.files.length) && !(job.tailored_resume || "")) 
          missing.push({ label: labelOf(el) || "resume upload", kind: "file", el: el });
        return;
      }
      var v = answerFor(el, answers);
      if (v === undefined || v === null) missing.push({ label: labelOf(el) || (el.name || "required field"), kind: el.tagName === "SELECT" ? "select" : "text", el: el });
      else if (el.tagName === "SELECT" && !optionMatch(Array.prototype.slice.call(el.options || []).filter(function (o) { return o.value; }), v))
        missing.push({ label: labelOf(el), kind: "select", el: el });
    });
    Object.keys(radioGroups).forEach(function (key) {
      var g = radioGroups[key];
      if (g.some(function (el) { return el.checked; })) return;
      var gl = groupLabel(g);
      if (/resume|cv/i.test(gl) || g.some(function (el) { return /resume|\.pdf|\.txt/i.test(labelOf(el)); })) return;
      // Answerable if the payload has a fact for the group question, or
      // a payload/bank entry under the question's normalized key.
      var v = answerForText(gl, answers);
      if (v !== undefined && v !== null) return;
      missing.push({ label: gl || key, kind: "radio", el: g[0], group: g });
    });
    return missing;
  }
  async function bankLookup(question) {
    // Payload first: an answer keyed by the question's normalized key
    // (or by the question text itself) counts as a stored fact.
    var key = swipeNormalizeQuestion(question);
    var ans = (state.job && state.job.answers) || {};
    var keys = Object.keys(ans);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i] === key || swipeNormalizeQuestion(keys[i]) === key) return String(ans[keys[i]]);
    }
    if (state.mode === "auto") {
      try {
        var r = await chrome.runtime.sendMessage({ type: "sh-answer-lookup", question: question });
        if (r && r.found) return r.answer;
      } catch (e) {}
      return null;
    }
    if (state.cfg && state.cfg.token) {
      try {
        var resp = await fetch(state.cfg.apiBase.replace(/\/$/, "") + "/api/ext/answer-lookup", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Authorization": "Bearer " + state.cfg.token },
          body: JSON.stringify({ question: question })
        });
        var j = await resp.json();
        if (j && j.found) return j.answer;
      } catch (e) {}
    }
    return null;
  }
  function fillBankAnswer(item, answer) {
    var el = item.el;
    if (!el) return false;
    if (item.kind === "radio") {
      var g = item.group || [el];
      var fake = g.map(function (r) { return { text: labelOf(r), value: "", el: r }; });
      var pick = optionMatch(fake, answer);
      if (pick) { pick.el.click(); record(item.label, "filled", String(answer) + " (answer bank)", pick.el, "check"); return true; }
      return false;
    }
    if (item.kind === "checkbox") {
      if (/^(yes|true|agree|i agree)/i.test(String(answer))) { el.click(); record(item.label, "filled", "answer bank: " + answer, el, "check"); return true; }
      return false;
    }
    if (item.kind === "select") {
      if (fillSelect(el, answer)) { record(item.label, "filled", String(answer) + " (answer bank)", el); return true; }
      return false;
    }
    if (item.kind === "text") { setValue(el, String(answer)); record(item.label, "filled", String(answer) + " (answer bank)", el); return true; }
    return false;
  }
  // Returns a park reason string, or null when the step is driveable.
  async function preflight(roots, answers, job) {
    var missing = [];
    roots.forEach(function (r) { missing = missing.concat(scanUnanswerable(r, answers, job)); });
    if (!missing.length) return null;
    var still = [];
    for (var i = 0; i < missing.length; i++) {
      var ans = await bankLookup(missing[i].label);
      if (ans !== null && ans !== undefined && fillBankAnswer(missing[i], ans)) continue;
      still.push(missing[i]);
    }
    if (!still.length) return null;
    return "Required question(s) with no stored answer and no banked answer — the driver never guesses: " +
      still.slice(0, 3).map(function (m) { return m.label; }).join(" · ");
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
    info.textContent = job.job_title + " @ " + job.company + (state.dryRun ? " — DRY RUN: verifying only, nothing will be submitted." : " — the extension is applying for you…");
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
  async function notifySubmitClick() {
    // Persisted server-side BEFORE the click's outcome is known, so a
    // browser crash here can never produce a duplicate application.
    if (state.mode === "auto") {
      try { await chrome.runtime.sendMessage({ type: "sh-submitclick" }); } catch (e) {}
    } else if (state.cfg) {
      try {
        await fetch(state.cfg.apiBase.replace(/\/$/, "") + "/api/ext/report", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Authorization": "Bearer " + state.cfg.token },
          body: JSON.stringify({ application_id: state.job.application_id, event: "submit_clicked", note: "Submit click imminent (manual mode)." })
        });
      } catch (e) {}
    }
  }
  function withDiagnostic(result) {
    if (result.outcome === "needs_human" || result.outcome === "failed") {
      var excerpt = clean(pageText()).slice(0, 240);
      var base = result.reason || "";
      result.reason = (base + (excerpt ? " | Page: " + excerpt : "")).slice(0, 490);
    }
    return result;
  }
  async function finish(result) {
    verifyFilled();
    result.fieldReport = buildReport(result.outcome === "verified_dry_run" ? true : result.wouldSubmit,
                                     result.outcome === "needs_human" ? (result.reason || "") : "");
    status(result.outcome === "submitted" ? "Submitted ✓ — synced to SwipeHire."
      : result.outcome === "submitted_unverified" ? "Submitted (unverified) — synced to SwipeHire."
      : result.outcome === "verified_dry_run" ? "Dry run verified ✓ — nothing was submitted."
      : result.outcome === "needs_human" ? "Parked as Needs you: " + (result.reason || "")
      : "Could not complete: " + (result.reason || ""));
    if (state.panelDone) state.panelDone();
    if (state.mode === "auto") {
      try { await chrome.runtime.sendMessage({ type: "sh-result", result: result }); } catch (e) {}
    } else if (state.cfg) {
      var ev = result.outcome; // server accepts the same event names
      try {
        await fetch(state.cfg.apiBase.replace(/\/$/, "") + "/api/ext/report", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Authorization": "Bearer " + state.cfg.token },
          body: JSON.stringify({ application_id: state.job.application_id, event: ev, note: result.evidence || result.reason || "",
                                 report: result.fieldReport || {} })
        });
      } catch (e) {}
    }
    return result;
  }
  function needHuman(reason, extra) {
    return finish(withDiagnostic(Object.assign({ outcome: "needs_human", reason: reason }, extra || {})));
  }
  function failWith(reason) {
    return finish(withDiagnostic({ outcome: "failed", reason: reason }));
  }
  function dryRunFinish() {
    verifyFilled();
    var rep = buildReport(true, "");
    return finish({ outcome: "verified_dry_run",
                    evidence: "Dry run: " + rep.filled + " field(s) filled and verified, " + rep.missed + " missed — submit NOT clicked.",
                    fieldReport: rep });
  }

  // ---------- generic / multi-step form flow (ATS + Indeed-style) ----------
  async function formFlow(job) {
    var answers = job.answers || {};
    for (var step = 0; step < 6; step++) {
      if (state.aborted) return needHuman("You stopped the extension on this page.");
      await waitForContent(step === 0 ? 6000 : 2500);
      var s = safety(); if (s) return needHuman(s.reason, s.accountWarning ? { accountWarning: true } : {});
      var roots = allRoots();
      // Pre-flight: park BEFORE touching the form if a required field on
      // this step has no payload answer and no banked answer.
      var park = await preflight(roots, answers, job);
      if (park) return needHuman(park);
      var filled = 0, att = { found: false, attached: false };
      for (var ri = 0; ri < roots.length; ri++) {
        filled += extraFill(roots[ri], answers);
        filled += fillFields(roots[ri], answers);
        var a2 = attachResume(roots[ri], job);
        att.found = att.found || a2.found; att.attached = att.attached || a2.attached;
        checkConsents(roots[ri]); selectResumeRadio(roots[ri]); fillRadios(roots[ri], answers);
        await fillCustomDropdowns(roots[ri], answers);
      }
      if (step === 0) await progress("Autonomous driver filling the form (filled " + filled + " field(s)" + (att.attached ? ", resume attached" : "") + ").");
      if (att.found && !att.attached) {
        return needHuman("This form requires a resume file, but the application package has no tailored resume text to attach.");
      }
      var missing = [];
      roots.forEach(function (r) { missing = missing.concat(unansweredRequired(r)); });
      missing = missing.filter(function (v, i, a) { return a.indexOf(v) === i; });
      if (missing.length) {
        return needHuman("Required question(s) still unanswered after filling — the driver never guesses: " + missing.slice(0, 3).join(" · "));
      }
      var sig0 = confirmationSignals(); if (sig0.textHit) return finish({ outcome: "submitted", evidence: sig0.evidence || "Confirmation detected." });
      var submitBtn = findButtonAnywhere([/submit/i, /send application/i, /apply now/i, /complete application/i]);
      var contBtn = submitBtn ? null : findButtonAnywhere([/^continue$/i, /^next$/i, /continue/i]);
      var btn = submitBtn || contBtn;
      if (!btn) return needHuman("No submit or continue button the driver recognises on this page — the site's flow may have changed.");
      var isSubmit = !!submitBtn;
      if (isSubmit && state.dryRun) {
        status("Dry run: form verified — submit NOT clicked.");
        return dryRunFinish();
      }
      status(isSubmit ? "Submitting…" : "Continuing to the next step…");
      snapshotPreSubmit(btn);
      if (isSubmit) await notifySubmitClick();
      btn.click();
      if (isSubmit) {
        var res = await waitConfirmation(12000);
        if (res && res.safety) return needHuman(res.safety.reason, res.safety.accountWarning ? { accountWarning: true } : {});
        if (res && res.strong) return finish({ outcome: "submitted", evidence: res.evidence || "Confirmation detected." });
        var still = [];
        allRoots().forEach(function (r) { still = still.concat(unansweredRequired(r)); });
        if (still.length) return needHuman("The form bounced with required fields still empty: " + still.slice(0, 3).join(" · "));
        if (res) return finish({ outcome: "submitted_unverified", evidence: "Submit clicked; only weak confirmation: " + (res.evidence || "none") });
        return finish({ outcome: "submitted_unverified", evidence: "Submit was clicked but no confirmation signal appeared — verify on the board before re-applying." });
      }
      await sleep(1400);
      var sig1 = confirmationSignals(); if (sig1.textHit) return finish({ outcome: "submitted", evidence: sig1.evidence || "Confirmation detected." });
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
      var park = await preflight([modal], answers, job);
      if (park) return needHuman(park);
      extraFill(modal, answers);
      fillFields(modal, answers);
      var att = attachResume(modal, job);
      checkConsents(modal); selectResumeRadio(modal); fillRadios(modal, answers);
      await fillCustomDropdowns(modal, answers);
      if (step === 0) await progress("Autonomous driver working through LinkedIn Easy Apply.");
      if (att.found && !att.attached) return needHuman("Easy Apply asks for a resume file, but the application package has no tailored resume text to attach.");
      var sig0 = confirmationSignals(); if (sig0.textHit) return finish({ outcome: "submitted", evidence: sig0.evidence || "Confirmation detected." });
      var missing = unansweredRequired(modal);
      if (missing.length) return needHuman("Easy Apply asked something your stored facts can't answer: " + missing.slice(0, 3).join(" · "));
      var submitBtn = findButton(modal, [/submit application/i]);
      if (submitBtn) {
        if (state.dryRun) { status("Dry run: Easy Apply verified — submit NOT clicked."); return dryRunFinish(); }
        status("Submitting your Easy Apply application…");
        snapshotPreSubmit(submitBtn);
        await notifySubmitClick();
        submitBtn.click();
        var res = await waitConfirmation(12000);
        if (res && res.safety) return needHuman(res.safety.reason, res.safety.accountWarning ? { accountWarning: true } : {});
        if (res && res.strong) {
          var done = findButton(document.body, [/^done$/i]); if (done) done.click();
          return finish({ outcome: "submitted", evidence: res.evidence || "Easy Apply confirmation detected." });
        }
        if (res) return finish({ outcome: "submitted_unverified", evidence: "Easy Apply submit clicked; only weak confirmation: " + (res.evidence || "none") });
        return finish({ outcome: "submitted_unverified", evidence: "Easy Apply submit was clicked but no confirmation appeared — verify on LinkedIn before re-applying." });
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
        var cfg = await chrome.storage.local.get(["apiBase", "token", "currentJob", "dryRun"]);
        if (!cfg.currentJob || !cfg.currentJob.application_id) return;
        if (cfg.currentJob.job_url && location.hostname && !cfg.currentJob.job_url.includes(location.hostname)) return;
        state.mode = "manual";
        state.job = cfg.currentJob;
        state.dryRun = !!cfg.dryRun;
        state.cfg = { apiBase: cfg.apiBase || "https://swipehire-jade.vercel.app", token: cfg.token || "" };
      } catch (e) { return; }
    } else {
      state.mode = resp.mode || "manual";
      state.job = resp.job;
      state.dryRun = !!resp.dryRun;
      if (state.mode === "manual") {
        try {
          var c2 = await chrome.storage.local.get(["apiBase", "token", "dryRun"]);
          state.dryRun = !!c2.dryRun;
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
