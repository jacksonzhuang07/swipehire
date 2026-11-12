# BUILD_NOTES — SwipeHire

## How to run
```bash
cd ~/workspace/swipehire
.venv/bin/python run.py        # http://localhost:5050  (venv already created; or pip install -r requirements.txt)
.venv/bin/python -m pytest tests/ -q
```
Deploy: import the GitHub repo into Vercel (Hobby/free) — `vercel.json` → `api/index.py` serves the Flask app; runtime deps in `api/requirements.txt`.

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
