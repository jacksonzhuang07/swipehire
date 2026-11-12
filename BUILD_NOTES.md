# BUILD_NOTES — SwipeHire

## How to run
```bash
cd ~/workspace/swipehire
.venv/bin/python run.py        # http://localhost:5050  (venv already created; or pip install -r requirements.txt)
.venv/bin/python -m pytest tests/ -q
```
Deploy: import the GitHub repo into Vercel (Hobby/free) — `vercel.json` → `api/index.py` serves the Flask app; runtime deps in `api/requirements.txt`.

## Final clean run (2026-11-12, all in one pass, after fixes)
- **pytest: 14 passed in 2.40s** (`tests/test_swipehire.py`, fully offline — job-API tests use fixtures/mocked failing fetchers).
  Covers: resume parsing, empty-resume safety, match scoring, Remotive + Arbeitnow normalization, seeded pool ≥12, offline scan fallback, health/discovery sorting, resume upload flow, swipe-right → application + dedupe, swipe-left → no application + job hidden, employer-post → candidate-swipe → employer-swipe → Match + stored message, no-match without mutual swipe, scan endpoint.
- **Live server smoke (real HTTP against `run.py` on :5050) — SMOKE ALL OK:**
  1. Resume upload/parse → 201, name/email/skills/6.0 yrs extracted correctly.
  2. Discovery → 200, 14 seeded jobs, top card scored 100, sorted by score.
  3. Swipe right → application `submitted` / `simulated_executor` (honestly labelled), appears in Applications tracker (1 item).
  4. Rescan boards → **live APIs succeeded**: Remotive +14 new (14 found), Arbeitnow +30 new (30 found), seeded +0 (already present) — total 44 new.
  5. Employer posts job → 201 (`source: employer`); candidate right-swipes it → application created; employer right-swipes candidate → **Match created**; `/api/matches` lists it; message POST → 201 and stored.
- **Vercel entrypoint verified locally:** `VERCEL=1` import of `api.index:app` → health OK, home 200, discovery 14, candidates = Jackson Ray (parsed from his real resume PDF in `~/workspace/job-search/`, non-demo) + 3 clearly-labelled demo candidates. SQLite correctly falls back to `/tmp/swipehire.db` under Vercel.

## Honest limitations
- Vercel SQLite is in `/tmp` and **ephemeral** — data resets on cold starts. Demo-grade only; swap `app/db.py` to a hosted DB for durable production.
- Apply executor: jobs with a real external apply URL are recorded `submitted / external_link`; all other jobs use a **simulated executor, labelled as such in the UI and in the application proof text** — no external submission actually occurs for those.
- Screening answers are built only from stored resume facts; unknown questions (work authorization, salary expectation, relocation) are logged as `needs_review`, never invented.
- No auth/accounts — the "current candidate" is the first/uploaded profile; demo-grade session model.
- Salary parsing from Remotive is best-effort regex on its free-text salary field.
