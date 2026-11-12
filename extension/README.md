# SwipeHire Autonomous Apply — Chrome extension (v6, AUTONOMOUS)

The extension is SwipeHire's **executor**. It is required for
login-required jobs (LinkedIn, Indeed, and Workday-style portals), and it
also acts as the form executor for no-login ATS application forms
(Greenhouse, Lever, Ashby, Workable, SmartRecruiters…) when those
applications are in its queue. SwipeHire never sees or stores your
job-board passwords — everything runs in **your** browser, under **your**
existing logins.

## What v6 does — autonomously

**The extension applies for you, end to end. The final submit click is
the extension's.** There is no human step in the normal path.

1. In SwipeHire, right-swipe jobs. 🔒 login-required jobs are **Queued
   for extension** once the extension is connected (before that they are
   **Prepared — Needs extension**); prepared ATS-form jobs join the queue
   too, because the extension is their form executor.
2. Pair once: SwipeHire → **Connections → Tier 1** → generate a pairing
   token → paste it into this extension's popup (with the SwipeHire
   address). Only a hash of the token is stored server-side; revoke any
   time from the same page. Saving the pairing turns **Autonomous apply
   ON** (toggle + a big Pause button live in the popup).
3. A background queue processor claims the next queued application,
   opens the job in a tab it manages, and the per-site driver runs:
   - **Generic ATS driver** — label-matched filling of text/select/
     textarea fields from your stored answers, tailored-resume file
     attachment, consent checkboxes, submit click, confirmation
     detection.
   - **LinkedIn Easy Apply driver** — opens the modal and walks
     Next / Review / Submit itself.
   - **Indeed driver** — drives the Indeed Apply flow the same way
     (best-effort selectors; Indeed's embedded smart-apply iframes are
     a known limitation — those park as Needs you).
4. Every outcome syncs back to SwipeHire: `opened → filling →
   submitted` (only on a detected confirmation — thank-you text, a
   confirmation banner, or a confirmation URL), or it parks.

## Pacing (hard-coded launch defaults, shown in the popup)

- **≥ 90 seconds** between submissions
- **Rolling burst cap: 5 submissions per 30 minutes**
- **Daily caps per board: LinkedIn 30 · Indeed 20 · ATS 40** (deliberately
  under observed platform limits), counters in extension storage with
  day rollover
- The popup shows today's counters, the last-30-min count, and a **run
  log** of the last 50 events.

## Needs you — the exception path (never defeated)

The driver **parks** a job as `needs_human` (shown as **Needs you** in
your SwipeHire tracker, with the reason) instead of guessing or forcing
its way through, when it meets:

- a **CAPTCHA / reCAPTCHA** — never solved, ever;
- a **login wall / expired session** or a **security checkpoint**;
- a **multi-factor / verification-code** prompt;
- a **required question with no stored answer** — drivers fill ONLY
  from your application payload's facts; the unanswered question's text
  is recorded so you can answer it once (it then becomes a stored fact);
- an **account-warning signal** — this additionally **pauses ALL
  autonomous applying** until you resume it from the popup.

**Workday** (`ats_account`) portals: autonomous account creation is
**not** attempted. If the portal shows a sign-in, the job parks as Needs
you; if you are already signed in and a plain application form is
showing, the generic driver will try it.

You can also stop the driver on any page with the panel's
**Stop & park this one** button, and open a queued job manually from the
popup — the driver still runs autonomously on that tab.

## Install (load unpacked — until a Web Store listing exists)

1. Download the zip from SwipeHire → Connections → **Download extension
   (.zip)** and unzip it (or clone this repo and use this `extension/`
   folder directly).
2. Chrome → `chrome://extensions` → enable **Developer mode**.
3. **Load unpacked** → select the `extension/` folder.
4. Click the SwipeHire icon, paste your pairing token, **Save pairing**
   — autonomous apply starts.

## API (token-authenticated, no session cookies)

- `GET /api/ext/queue` — everything this user has queued for the
  extension: job URL, structured answers, **tailored resume text**.
- `POST /api/ext/report` `{application_id, event, note}` with
  `event ∈ opened | filling | filled | submitted | failed |
  needs_human` (`needs_human`'s note is the reason, surfaced in the
  tracker and `needs_review`).

Both send the pairing token as `Authorization: Bearer <token>`.
