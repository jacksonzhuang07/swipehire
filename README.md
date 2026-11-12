# SwipeHire 💘

Tinder-style job matching with real accounts. **Candidates** sign up, upload a resume plus supporting documents (references, certifications, project write-ups, transcripts), and SwipeHire compiles everything into one **Master Profile** — every fact tagged with the document it came from. Jobs from six live job-board sources are ranked on a personal **For You feed** (match score + your preferences + what your swipes taught it), and **swipe right = SwipeHire applies for you** with a **truthfully tailored resume**: built only from your Master Profile facts, with keyword gaps listed — never invented. **Employers** sign up too, post jobs, and swipe on ranked candidates; a mutual right-swipe is a **Match**.

## Demo accounts (seeded, password `demo` — demo only, change/remove for real use)
| Account | Role | Notes |
|---|---|---|
| `demo@swipehire.app` / `demo` | Candidate | Instant try-out sample candidate |
| `employer@swipehire.app` / `demo` | Employer | Demo Employer Inc. |
| Jackson's own email (from his resume) / `demo` | Candidate | Seeded from his real parsed resume + verified LinkedIn headshot — demo seed |

## Run locally
```bash
cd swipehire
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py          # http://localhost:5050
.venv/bin/python -m pytest tests/ -q
```
Unauthenticated visitors get the login/signup screen. Sessions are Flask cookie sessions; set `SWIPEHIRE_SECRET` in production.

## How it works
- **Accounts** (`app/__init__.py` auth routes): email + password (werkzeug hashing), candidate or employer. Everything — profile, documents, photo, swipes, applications, jobs, matches, messages — is scoped to the logged-in user.
- **Passwordless email-code sign-in (v4)**: third auth option, “Email me a sign-in code”. `POST /api/auth/code/request` generates a 6-digit code — only its hash is stored, 10-minute expiry, single-use, max 5 attempts, max 3 requests per email per 15 minutes, generic responses so account existence isn't leaked. `POST /api/auth/code/verify` logs you in, **creating the account on first use** (role selectable, default candidate). Delivery is pluggable (`app/mailer.py`): Resend API if `RESEND_API_KEY` is set, SMTP if `SMTP_HOST` is set — otherwise **demo mode: no email is sent and the code is shown on the sign-in screen in a clearly-labelled demo box**. That is a deliberate demo tradeoff, not a hidden one. (Sign in with Google is not built; it needs a Google Cloud OAuth project + client secret. The code flow gives the same one-click feel with zero provisioning.)
- **Profile picture**: PNG/JPEG upload (magic-byte checked, 3 MB cap) stored under `data/uploads/`, shown on the profile page, employer candidate cards and matches. No photo → initials avatar. Employers get a logo upload the same way.
- **Document vault + Master Profile** (`app/documents.py`): documents typed `resume / cover_letter / reference_letter / project_writeup / transcript / certification / other` (file .txt/.md/.pdf or pasted text) are parsed for skills, experience entries, education, certifications, references and links. Facts merge into the Master Profile **with per-fact provenance** (source document). The **Compiled Master Resume** rebuilds automatically on every add/remove — viewable in-app, downloadable as .txt, and as print-friendly HTML.
- **Job pool** (`app/job_sources.py`): live adapters for **Remotive** (6 categories), **Arbeitnow** (3 pages), **Jobicy**, **RemoteOK**, **Himalayas** (v4) and **We Work Remotely** (v4, RSS via stdlib XML), deduped across sources, plus the curated 14-job seeded fallback for offline. A fresh scan on 2026-11-12 produced a **727-job pool** (arbeitnow 284, weworkremotely 167, himalayas 100, remoteok 98, jobicy 50, remotive 14, seeded 14). Also probed and **dead** (2026-11-12): The Muse (API 404 / Cloudflare — fetcher kept behind `SWIPEHIRE_ENABLE_MUSE=1`) and Working Nomads (public endpoints 404). Seeded fallback jobs carry a −15 feed penalty so real listings outrank them. A **daily Vercel Cron** (`vercel.json`, 06:00 UTC) hits `GET /api/cron/rescan` to refresh the pool automatically — guarded by `CRON_SECRET` when set.
- **For You ranking** (`app/feed.py`): rank = match score (`app/scoring.py`) + preference fit (target titles, locations, remote-only, min salary) + learned signals from *your own* swipe history (right-swiped skills/titles boost, left-swiped patterns penalised) + freshness. Diversity rule: max 2 per company / near-identical title in the first 10. About every 7th card is a labelled **Stretch pick**. Every card shows **“Why this job”** (2–3 concrete reasons). Employers get the mirror ranking with “Why this candidate”.
- **Truthful tailoring** (`app/tailoring.py`): on swipe-right, the apply pipeline (`app/apply_engine.py`: dedupe, score gate, answers only from stored facts, unknown questions → “needs review”) also generates a per-job tailored resume from the Master Profile — headline from the candidate's *own* best-matching title, skills reordered with job-matching skills first, experience bullets reordered by relevance — plus a keyword coverage report (covered / missing). Missing keywords are stored as **gaps** on the application, never filled in. View/download the tailored resume per application (“Tailored for this job” badge).
- **Matches**: employer right-swipe + candidate applied/right-swiped the same job → Match, scoped per user, with an in-app message stub stored in the DB (no external sending).

## Hosting (free — Vercel Hobby)
Deploy-ready: `vercel.json` rewrites all traffic to `api/index.py`; deps in `api/requirements.txt`.

**Honest limitations:** without `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` set, the database is a local SQLite file — on Vercel that lives in `/tmp` and is **ephemeral** (accounts/data reset on cold starts). Set the Turso env vars (see `.env.example`) for a persistent hosted database; the Turso code path is implemented and offline-tested, and is activated purely by those env vars. **Uploads (v7):** when `BLOB_READ_WRITE_TOKEN` is set, uploads (profile photos, employer logos, and a mirror of each document's text) go to a **Vercel Blob** store and persist; without it they live on the local filesystem (ephemeral on Vercel, fine for dev/tests). Honest access note: Blob objects are **public-if-known** — the URL is unguessable (owner id + random suffix), but unlike the session-gated local `/uploads` route there is no per-request access check on the blob URL itself. **Apply statuses are honest (v5):** the web app prepares application packages and labels them **Prepared** / **Needs extension** / **Queued for extension** / **Needs review** — an application is marked **Submitted** only when the browser extension reports a real submission from your own browser. Nothing in the web app itself transmits an application. Email-code sign-in without `RESEND_API_KEY`/SMTP configured runs in **demo mode (code shown on-screen, no email sent)** — connect one of them before real users rely on email delivery.

## Apply tiers & the browser extension (v6 — autonomous)

Jobs are classified at ingestion from their URL: `ats_form` (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Breezy, Recruitee, company sites — **no account needed**), `network_login` (LinkedIn, Indeed — 🔒 login required), `ats_account` (Workday-style portals — 🔒 login required), `external` (anything else, treated as no-login preparation). 🔒 jobs show a **Login required** badge on the card.

- **Tier 0 — no-account jobs:** never involve the extension.
- **Tier 1 — browser extension (add-on for 🔒 jobs only):** right-swiping a 🔒 job without the extension saves the application as *Prepared — Needs extension* and shows a “This one needs your login” prompt. With the extension connected it is *Queued for extension*. The extension (`extension/` in this repo, or Download from **Connections**) applies **autonomously in your own logged-in browser**: a background queue processor claims queued applications one at a time, opens each job, runs a per-site driver that fills the form from your application package (facts only — never invented), attaches your tailored resume, and **makes the final submit click itself**, pacing itself like a human (≥90s between submissions, burst cap 5/30 min, daily caps LinkedIn 30 / Indeed 20 / ATS 40). Outcomes sync back via `POST /api/ext/report`; *Submitted* is recorded only when the driver detects the site's confirmation. **Safety stops park a job as *Needs you* instead of fighting the site:** CAPTCHA, login wall, security checkpoint, MFA prompt, an account warning (which also pauses everything), or a required question with no stored answer — the question text lands in your tracker so you can answer it by enriching your profile. Workday account portals park the same way in v6. Pairing uses a token generated on the Connections page — only its hash is stored server-side, and it can be revoked there. Swipe right on no-account ATS jobs and they join the same autonomous queue too.
- **Tier 2 — link an account (fallback):** the Connections page records only the board + the username/email you type (status `pending_session`). **SwipeHire never asks for or stores job-board passwords — there is no password field.**

Launch extras (v5): `/privacy` and `/terms` pages; **Delete my account** in Profile permanently deletes all of your scoped data; `SWIPEHIRE_ENV=production` switches the footer to production wording and warns loudly at startup if `SWIPEHIRE_SECRET` is unset.

## Layout
```
app/            Flask app: auth, db, documents (vault/master), feed (For You), tailoring,
                resume_parser, scoring, job_sources, apply_engine, seed_data, templates/, static/
api/index.py    Vercel entrypoint
tests/          Offline pytest suite (API responses mocked/fixtured; the one scan-endpoint test hits live boards and tolerates their failure)
run.py          Local server (port 5050)
```
