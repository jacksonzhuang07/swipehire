# SwipeHire Apply Assistant — Chrome extension (v1, assisted apply)

The extension is an **add-on for login-required jobs only** (LinkedIn,
Indeed, and Workday-style portals). Jobs on ATS/company application forms
(Greenhouse, Lever, Ashby, company sites…) never involve it — no account is
needed for those.

## What v1 honestly does

**Assisted apply, in YOUR browser, under YOUR logins.** SwipeHire never
sees or stores your job-board passwords — there is no password field
anywhere in this flow.

1. In SwipeHire, right-swipe a 🔒 login-required job. Without the extension
   it is saved as **Prepared — Needs extension**; with the extension
   connected it is **Queued for extension**.
2. Pair once: SwipeHire → **Connections → Tier 1** → generate a pairing
   token → paste it into this extension's popup (with the SwipeHire
   address). Only a hash of the token is stored server-side; revoke it any
   time from the same page.
3. Open the popup → **Load my queue** → click a job. It opens in a new tab
   in your normal, already-logged-in browser session.
4. The content script autofills the fields it can detect (name, email,
   phone) from your SwipeHire application payload and shows a small
   SwipeHire panel. **You review and click the site's own final submit
   button** — v1 never submits on your behalf.
5. Tap **I submitted it ✓** (or **Couldn't apply**) in the panel and the
   result syncs back to SwipeHire. An application is marked *Submitted*
   only from that explicit report, or when the extension detects the
   site's application-confirmation page. Nothing auto-claims a submission.

## Install (load unpacked — until a Web Store listing exists)

1. Download the zip from SwipeHire → Connections → **Download extension
   (.zip)** and unzip it (or clone this repo and use this `extension/`
   folder directly).
2. Chrome → `chrome://extensions` → enable **Developer mode**.
3. **Load unpacked** → select the `extension/` folder.
4. Click the SwipeHire icon, paste your pairing token, **Save pairing**.

## API (token-authenticated, no session cookies)

- `GET /api/ext/queue` — this user's `queued_extension` +
  `extension_required` applications: job URL, tailored answers, resume.
- `POST /api/ext/report` `{application_id, event, note}` with
  `event ∈ opened | filled | submitted | failed`.

Both send the pairing token as `Authorization: Bearer <token>`.

## Designed next step: autonomous mode

The queue/claim/report architecture is intentionally the exact shape an
auto-drive engine needs: a background queue processor that works the
queue one application at a time — claim next → open → autofill → submit →
report — reusing the same endpoints and events, with `submitted` still
recorded only from a real submission signal. Jackson has confirmed
autonomous mode as the next step after launch; it is **not** in v1 — v1
stays assisted, with the human making the final submit click.
