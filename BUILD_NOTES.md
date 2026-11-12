# BUILD_NOTES — SwipeHire

## How to run
```bash
cd ~/workspace/swipehire
.venv/bin/python run.py        # http://localhost:5050  (venv already created; or pip install -r requirements.txt)
.venv/bin/python -m pytest tests/ -q
```
Deploy: import the GitHub repo into Vercel (Hobby/free) — `vercel.json` → `api/index.py` serves the Flask app; runtime deps in `api/requirements.txt`.

## v4 — passwordless email-code sign-in + bigger live pool (2026-11-12)

**A. Email-code sign-in** (added *alongside* password login + demo logins; nothing removed)
- Third auth tab “✉️ Email code”: enter email → `POST /api/auth/code/request` → 6-digit code → `POST /api/auth/code/verify` (email + code + optional role/name/company, used only when the email has no account yet; default role candidate) → account created on first verify, session logged in via the existing Flask session.
- Codes: only a werkzeug **hash** is stored (`login_codes` table), **10-minute expiry, single-use, max 5 attempts**, rate limit **3 requests per email per 15 min** (429 after). Request responses are generic — identical shape whether or not the account exists, so existence isn't leaked. Verify failures are one generic 401.
- Delivery is pluggable (`app/mailer.py`): `RESEND_API_KEY` → Resend API (From `SWIPEHIRE_MAIL_FROM` or onboarding@resend.dev); else `SMTP_HOST/PORT/USER/PASS` → smtplib; else **DEMO MODE — no email is sent**: the code is returned in the API response and shown on the auth screen in a clearly-labelled dashed-yellow demo box (“Demo mode: no email service is connected yet, so no email was sent. Your code is …”) and logged server-side. Code-created accounts get an unusable random password hash (code-login only until a password flow exists). Test hook: `SWIPEHIRE_MAIL_CAPTURE=1` captures mail into `mailer.LAST_SENT`.
- Google sign-in was **not** built (needs a Google Cloud OAuth project + client-secret provisioning by Jackson).

**B. More actual jobs**
- Source probing (live, 2026-11-12): **Himalayas** `himalayas.app/jobs/api` — 200 JSON, kept. **We Work Remotely** RSS (root + programming/devops/product/design category feeds, parsed with stdlib `xml.etree`) — 200, kept. **The Muse** — www API endpoints 404, `api-v2` Cloudflare 403: **dead for no-key use**; normalizer + fetcher kept behind `SWIPEHIRE_ENABLE_MUSE=1`, excluded from the default scan. **Working Nomads** — `/api/exposed/jobs/` and `/jobs.json` both 404: no working public endpoint found, no fetcher.
- **Fresh live scan on an empty DB (2026-11-12): pool_total 727** (was 462 in v2) — arbeitnow 284, weworkremotely 167, himalayas 100 (5 cursor pages × 20), remoteok 98, jobicy 50, remotive 14, seeded 14. The long-running local DB (accumulated since v2) measures 758.
- Feed: `seeded` fallback jobs take a **−15 feed-score penalty** (`components.seeded_penalty`), so real-source jobs outrank them at comparable scores; seeded cards remain source-labelled.
- Auto-refresh: `vercel.json` now declares a **daily Vercel Cron** (`0 6 * * *`) → `GET /api/cron/rescan`, which runs a full scan of the shared pool; guarded by `CRON_SECRET` (Vercel sends `Authorization: Bearer …`) when set, open in demo when unset. Verified live on :5050 (200, all sources ok, pool_total 758) and in tests (mocked scan + secret guard).

**Tests — 53 passed in 48.43s, one clean final pass** (37 v3 + 16 v4): code happy path (existing user), code signup creating candidate + employer accounts, generic response for unknown email, wrong code rejected then correct accepted, **single-use**, **5-attempt cap burns the code**, **expired code rejected**, **hash-only storage**, **rate limit** (3 ok → 429, other email unaffected), password login untouched by code flow, cron endpoint with mocked sources, cron secret guard (401/no + wrong, 200/correct), seeded-penalty ordering (exactly 15 points, real job first), Himalayas + WWR-RSS + The Muse fixture normalizers. The 2 layout tests ran and passed (not skipped).

**Visual QA (gate applied):** `visual-qa/qa.py` extended with an auth pass — screenshots `v4-auth-{1920x1080,390x844}.png`, `v4-auth-code-{1920x1080,390x844}.png` (exercises the real code-request UI; demo box measurably visible at both widths), `v4-discovery-{1920x1080,1366x768,390x844}.png`, `v4-documents-1920x1080.png`, `v4-employer-1920x1080.png`. The developer read the auth, code-entry (desktop + mobile), and discovery shots personally: three auth tabs fit without squeeze, demo box is unmistakable, code input is large/centred, no overflow anywhere (scrollWidth == innerWidth at all widths; card 520px/370px; card ∩ nav = ∅; why-ratio 0.90–0.93). Assets bumped to `?v=4`. Vercel entrypoint re-verified (`VERCEL=1`: health version 4, home 200, code request 200 in demo mode). Local server on :5050 restarted with v4 and left running.

## v3 — Tinder-style UI + visual QA (2026-11-12)

**What Jackson saw:** a screenshot of the live v2 Discovery page where the job card rendered as scattered, overlapping pieces (badges stacked, text spilling past the nav, skill chips in a narrow column, the "Why this job" box squeezed to ~150px).

**Root-cause findings (verified, not guessed):**
1. **The v2 code does NOT reproduce that breakage locally.** The committed v2 was run from a pristine `git archive` snapshot on :5051 and screenshot with headless Chromium (visual-qa/before-*): the card renders as a correct centered 620px column at 1920/1366/390 — no overlap, why-ratio 0.92. So the scattered live render is not deterministic from the repo's HTML/CSS alone.
2. The live pattern — header/button rules visibly applied while the card's box rules (`.card` width/margin/padding, `.badges` flex row) behaved as if absent — is consistent with the page loading a **stale/mixed asset set**: v2 was redeployed over the same Vercel domain Jackson had already visited as v1, and `/static/style.css` + `/static/app.js` were referenced **with no cache-busting token**, so a browser could pair a cached stylesheet/script from one deployment with markup from another. **Fix shipped:** both assets are now referenced as `style.css?v=3` / `app.js?v=3` in index.html (bump on future UI changes).
3. Contributing fragility in v2 regardless: the entire stylesheet was one minified, under-specified line written without ever rendering the page — several JS-generated structures had no explicit containment (no `#deck` wrapper sizing, nav as bare inline buttons, `.why` width purely implicit). There was **no visual check anywhere in the pipeline**; the 35 API tests all passed while the page was (on the live deployment) broken. That process hole is the main thing fixed here.

**What changed:**
- `app/static/style.css` rewritten from scratch (~340 readable lines) as a **Tinder-style UI in our own CSS**: light shell (#f0f2f4 / white), white top bar, pill icon-tab nav (bottom tab bar on mobile), hero card with Tinder's coral→orange gradient family (#FD267A → #FF7854) rebuilt as generic CSS gradients, dark scrim with bold white title/company, grey tag chips, full-width "Why" box, 64px circular white action buttons (coral ✕ / green ♥) overlapping the card bottom, deck with the next card peeking behind, angled LIKE (green) / NOPE (red) stamps that fade in with drag distance, and a full-screen gradient "It's a Match!" overlay (both avatars, Send a message / Keep swiping). Every class generated by app.js was audited and is styled.
- `app/static/app.js`: card markup restructured (segments bar, hero, scrim, body), peek-card rendering for both decks, candidate cards use the profile photo full-bleed (gradient + initials fallback), stamps wired into `attachDrag`, match `alert()` replaced with the overlay, nav buttons carry icons + active state.
- `app/templates/index.html`: circular icon action buttons, match-overlay markup, cache-busted asset URLs.

**Visual QA (all screenshots in `visual-qa/`, gitignored):**
- Tooling: Playwright 1.65. Its own Chromium downloader repeatedly failed through this proxy (died at 60–90%); workaround that worked: resumable `curl -C -` download of the same `chrome-linux64.zip` from cdn.playwright.dev, unpacked into `~/.cache/ms-playwright/chromium-1254/chrome-linux/`, launched via `executable_path` fallback in the QA scripts.
- Before (pristine v2 on :5051): `before-discovery-{1920x1080,1366x768,390x844}.png`, `before-documents-1920x1080.png`, `before-employer-1920x1080.png` — locally correct (see root cause #1).
- After (v3 on :5050): `after-discovery-{1920x1080,1366x768,390x844}.png`, `after-documents-1920x1080.png`, `after-employer-1920x1080.png`, `after-auth-1920x1080.png`, `after-applications-1920x1080.png`, `after-profile-1920x1080.png`, `after-matches-1920x1080.png`, `after-drag-like-1920x1080.png` (mid-drag: LIKE stamp opacity 0.86, peek card visible). Every screenshot was read by the developer agent; iterations fixed: mobile "Log out" wrapping (username hidden ≤700px, nowrap), employer deck scrolled into frame.
- Measured on the after build at all widths: scrollWidth == innerWidth (no horizontal overflow), card 520px (370px mobile), why-box ratio 0.90–0.93 of card width, card ∩ nav = ∅.

**Regression tests:** `tests/test_layout.py` (Playwright; skips gracefully if Chromium absent) asserts the four geometric invariants at 1920×1080 and 390×844 against a real rendered page. **Final full run: 37 passed in 30.59s** (35 v2 tests + 2 layout), Vercel entrypoint re-verified (health v2, home 200). QA harness kept at `visual-qa/qa.py` for future runs.

## v2 — final clean run (2026-11-12, all in one pass, after fixes)
- **pytest: 35 passed in 20.38s** (`tests/test_swipehire.py`). The 14 v1 tests were kept (adapted to log in via the seeded demo accounts, since v2 APIs require auth; assertions unchanged) plus 21 v2 tests:
  auth (unauthenticated → 401 on all data APIs, signup/login/logout, duplicate-email 409, role isolation 403s, **two-user data isolation** incl. tailored-resume 404 and document-delete 404 across users, no cross-user feed hiding), demo + Jackson seed login, photo upload + serving + non-image rejection + anonymous 401, employer logo, Jackson seeded photo (PNG magic bytes), document parsing facts, **vault merge + provenance + compiled-resume regeneration on delete + .txt/HTML downloads**, merge unit test, **feed ranking** (preference boost, learned boost/penalty, diversity cap ≤2 SameCo in first 10, stretch pick at slot 7, learned-from-real-swipes end-to-end), **tailoring guardrail** (`extract_skills(tailored) ⊆ master skills`, Kubernetes/Rust reported as gaps and absent from the text), tailoring via the real swipe-right flow, Jobicy + RemoteOK normalizers, employer ranked deck with “Why this candidate”, scoped matches/messages.
- **Live HTTP smoke against the real server on :5050 (fresh v2 DB) — ALL OK (24/24):**
  1. Unauthenticated `/api/discovery` → 401; home page serves the login screen (200).
  2. `/api/demo-accounts` lists the 3 seeded demo accounts; **Jackson Ray logs in with password `demo`**; his seeded profile photo serves as a real PNG (747,101 bytes, from `~/workspace/job-search/jackson_linkedin_headshot.png`, magic bytes verified).
  3. Fresh candidate signup → add resume document → Master Profile merged **with provenance** (Python sourced to that document) → Compiled Master Resume contains the experience + reference facts.
  4. Discovery feed: ranked, every card carries `feed_score` + “why” reasons.
  5. Swipe right → application `tailored=1`, tailored resume viewable with coverage report.
  6. Photo upload (multipart PNG) → 200 and served.
  7. **Rescan boards (live APIs)**: Remotive ok, Arbeitnow ok, Jobicy ok, RemoteOK ok, seeded ok — **pool_total 462** (448 new on the fresh DB).
  8. Employer login → post job → candidate swipes it → employer deck is ranked with “why” + candidate photo → employer right-swipe → **Match created** → visible to the candidate with photo → message stored.
  9. Isolation: a brand-new user sees zero applications/documents from other users.
- **Job pool check (scratch DB, fresh scan)**: pool_total **462** — arbeitnow 286 (3 pages, deduped), remoteok 98, jobicy 50, remotive 14 (6 categories; Remotive's live feed is small right now), seeded 14. Himalayas also responded during evaluation but Jobicy + RemoteOK were sufficient for the 100+ target, so only those two were added.
- **Vercel entrypoint verified locally:** `VERCEL=1` import of `api.index:app` → health `{ok, version: 2}`, home 200, demo login 200, discovery 14 seeded jobs with “why” reasons, SQLite correctly created in `/tmp/swipehire.db`.
- Server on port 5050 was restarted with v2 (fresh DB; the v1 DB file was removed after the smoke — its data was re-seedable demo data) and left running.

## v1 — final clean run (2026-11-12)
- pytest: 14 passed. Live smoke: resume parse, discovery (14 seeded), swipe→application, live rescan (Remotive +14, Arbeitnow +30), employer post → mutual swipe → match + message. Vercel entrypoint verified. (Superseded by v2 above.)

## Honest limitations
- Vercel SQLite is in `/tmp` and **ephemeral** — data resets on cold starts; **uploads (photos/logos) are ephemeral there too**. Demo-grade only; swap `app/db.py` to a hosted DB and uploads to object storage for durable production.
- Apply executor: jobs with a real external apply URL are recorded `submitted / external_link`; all other jobs use a **simulated executor, labelled as such in the UI and in the application proof text** — no external submission actually occurs for those.
- Tailoring never invents: skills/experience/education come only from Master Profile facts; uncovered job keywords are stored as gaps on the application.
- Screening answers are built only from stored resume facts; unknown questions (work authorization, salary expectation, relocation) are logged as `needs_review`, never invented.
- Employer candidate deck is the marketplace directory (all candidates), as in v1; a user's *own* data (documents, swipes, applications, matches, messages) is strictly scoped to their account.
- Seeded demo accounts all use password `demo` (documented in README) — remove/rotate before any real deployment. Session secret defaults to a dev value unless `SWIPEHIRE_SECRET` is set.
- Salary parsing from Remotive is best-effort regex on its free-text salary field.
