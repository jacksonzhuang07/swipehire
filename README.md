# SwipeHire 💘

Tinder-style job matching. Candidates upload a resume → SwipeHire parses it, scans job boards, scores every job against the profile, and presents them as swipe cards. **Swipe right and SwipeHire applies for you** (apply pipeline with dedupe, score gate, and answers built only from stored resume facts — unknown screening questions are logged as “needs review”, never invented). Swipe left to pass forever.

Employers get their own mode: post a job, swipe on candidates scored against that job, and a mutual right-swipe creates a **Match** (with an in-app message stub stored in the database — no external sending).

## Run locally
```bash
cd swipehire
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py          # http://localhost:5050
# or: .venv/bin/python -m flask --app "app:create_app" run --port 5050
.venv/bin/python -m pytest tests/ -q
```

## How it works
- **Resume parsing** (`app/resume_parser.py`): .txt/.md/.pdf (pypdf, with plain-text fallback) or pasted text → name, email, phone, skills (curated list), years of experience, title, summary.
- **Job scanning** (`app/job_sources.py`): live adapters for Remotive and Arbeitnow (free, no key, 8s timeouts), normalized + deduped into SQLite. A curated **seeded fallback pool (14 jobs, labelled `seeded` in the UI)** guarantees Discovery works offline. “Rescan boards” reports new jobs per source.
- **Match scoring** (`app/scoring.py`): 0–100 = skills (50) + title (25) + location/remote (15) + salary (10); score + top matching skills shown on every card.
- **Apply pipeline** (`app/apply_engine.py`): dedupe (never apply twice), score gate, applications tracked in *My Applications*. Jobs with a real external apply URL are recorded as `submitted / external_link`; jobs with no apply API use a **simulated executor, labelled as such in the UI** — no fake claims of external submission.
- **Employer mode**: post jobs (they appear in candidate Discovery), swipe candidates, matches + stored messages.

## Hosting (free — Vercel Hobby)
Deploy-ready for Vercel like the sibling auto-job-agent project: `vercel.json` rewrites all traffic to `api/index.py`, which serves the Flask app; deps for the Vercel Python runtime are in `api/requirements.txt` (and root `requirements.txt`).

**Honest limitation:** on Vercel, SQLite lives in `/tmp` (the only writable path) and is **ephemeral** — data resets on cold starts / redeploys. That is fine for a demo; for durable production hosting, swap SQLite for a hosted database (e.g. Turso / Supabase / Vercel Postgres) — the DB layer is isolated in `app/db.py`.

## Layout
```
app/            Flask app: db, resume_parser, scoring, job_sources, apply_engine, seed_data, templates/, static/
api/index.py    Vercel entrypoint
tests/          Offline pytest suite (API responses mocked/fixtured)
run.py          Local server (port 5050)
```
No secrets required; see `.env.example`.
