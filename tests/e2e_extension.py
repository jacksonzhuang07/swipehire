"""End-to-end proof for the SwipeHire v6 AUTONOMOUS extension.

Loads the REAL extension (extension/) into headful Chromium (Playwright
persistent context, under xvfb), pairs it against a real local Flask
server with a real pairing token, seeds queued applications pointing at
local fixture pages, enables autonomous mode via the real popup, and
asserts — with ZERO human clicks after that:

  (a) Greenhouse-style ATS form  -> filled from payload facts, tailored
      resume attached, SUBMITTED by the driver, tracker = submitted.
  (b) LinkedIn-Easy-Apply-style multi-step modal -> walked and
      SUBMITTED by the driver, tracker = submitted.
  (c) CAPTCHA + unknown-question fixture -> parked needs_human,
      NEVER submitted.

Fixture hosts are fake board domains (boards.greenhouse.io,
www.linkedin.com) resolved to 127.0.0.1 via Chromium's
--host-resolver-rules, so classification, queue eligibility and the
content-script matches all exercise the real production paths.

Run:  .venv/bin/python tests/e2e_extension.py     (re-execs under xvfb-run)
Exit code 0 = all three fixtures behaved as required.
"""
import glob
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

if not os.environ.get("DISPLAY") and not os.environ.get("SWIPEHIRE_E2E_XVFB"):
    if shutil.which("xvfb-run"):
        env = dict(os.environ, SWIPEHIRE_E2E_XVFB="1")
        print("[e2e] no DISPLAY — re-exec under xvfb-run", flush=True)
        os.execvpe("xvfb-run", ["xvfb-run", "-a", sys.executable, os.path.abspath(__file__)], env)
    print("[e2e] SKIP: no DISPLAY and no xvfb-run available")
    sys.exit(0)

API_PORT = 5055
FIX_PORT = 8899
FIX_DIR = os.path.join(HERE, "fixtures")
DB_PATH = "/tmp/swipehire-e2e.db"
RESULTS = {}

def note(name, ok, detail):
    RESULTS[name] = (ok, detail)
    print(f"[e2e] {'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)

def chrome_exe():
    cands = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")))
    return cands[-1] if cands else None

def main():
    for f in (DB_PATH, DB_PATH + "-wal", DB_PATH + "-shm"):
        if os.path.exists(f):
            os.remove(f)

    # ---------- seed via the real app (test client) ----------
    from app import create_app
    from app.db import get_conn
    from app.job_sources import insert_jobs
    app = create_app(db_path=DB_PATH, testing=True)
    tc = app.test_client()
    r = tc.post("/api/auth/signup", json={"email": "e2e@x.com", "password": "pw1234", "role": "candidate", "name": "Ada Lovelace"})
    assert r.status_code == 201, r.data
    # Primary-resume upload: populates the candidate row itself (name,
    # email, PHONE, title, skills, years) AND files the document — the
    # application answers are built from those stored facts.
    r = tc.post("/api/resume", json={"text":
        "Ada Lovelace\nada@x.com\n514-555-0199\nPython developer with 6 years experience\nSkills: Python, Flask, SQL"})
    assert r.status_code in (200, 201), r.data
    tc.post("/api/candidate/preferences", json={"min_salary": 95000})

    jobs = [
        ("http://boards.greenhouse.io:%d/ats-form.html" % FIX_PORT, "Fixture ATS Role", "greenhouse"),
        ("http://www.linkedin.com:%d/easy-apply.html" % FIX_PORT, "Fixture Easy Apply Role", "linkedin"),
        ("http://boards.greenhouse.io:%d/captcha.html" % FIX_PORT, "Fixture Captcha Role", "greenhouse"),
    ]
    conn = get_conn(DB_PATH)
    insert_jobs(conn, [{"external_id": f"e2e-{i}", "title": t, "company": "FixtureCo", "location": "Remote",
                        "remote": 1, "salary_min": None, "salary_max": None, "salary_text": "",
                        "description": "Python Flask SQL role", "url": u, "source": s,
                        "tags": ["Python", "Flask", "SQL"], "posted_at": ""} for i, (u, t, s) in enumerate(jobs)])
    rows = {r["url"]: dict(r) for r in conn.execute("SELECT * FROM jobs WHERE source IN ('greenhouse','linkedin') AND external_id LIKE 'e2e-%'").fetchall()}
    conn.close()
    app_ids = {}
    for url, title, _src in jobs:
        j = rows[url]
        rr = tc.post("/api/swipe", json={"job_id": j["id"], "direction": "right"})
        assert rr.status_code == 200, rr.data
        app_ids[title] = rr.json["application"]["id"]
    print("[e2e] seeded applications:", app_ids, flush=True)

    tok = tc.post("/api/ext/pairing-token").json["token"]

    # ---------- real HTTP server for the extension to talk to ----------
    server = subprocess.Popen(
        [sys.executable, "-c",
         f"from app import create_app; create_app(db_path={DB_PATH!r}).run(port={API_PORT})"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fixsrv = http.server.ThreadingHTTPServer(("127.0.0.1", FIX_PORT),
                                             lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=FIX_DIR, **k))
    threading.Thread(target=fixsrv.serve_forever, daemon=True).start()

    # Readiness gate: the API server must actually serve THIS run's seeded
    # queue — otherwise a stale server from a crashed earlier attempt could
    # silently be the thing answering on the port.
    import urllib.request
    queue_seen, deadline = None, time.time() + 25
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://localhost:{API_PORT}/api/ext/queue",
                                         headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                queue_seen = json.load(resp)
            break
        except Exception:
            time.sleep(0.5)
    assert queue_seen and len(queue_seen.get("queue", [])) == 3, \
        f"API server did not serve the seeded queue: {queue_seen}"
    print(f"[e2e] API server live; queue serves {len(queue_seen['queue'])} seeded items", flush=True)

    def app_row(aid):
        c = get_conn(DB_PATH)
        row = c.execute("SELECT * FROM applications WHERE id=?", (aid,)).fetchone()
        c.close()
        return dict(row) if row else {}

    POPUP = {"page": None}

    def ext_state():
        try:
            return json.dumps(POPUP["page"].evaluate(
                "() => chrome.runtime.sendMessage({type:'sh-getstate'})"), default=str)[:1500]
        except Exception as e:
            return f"<state unavailable: {e}>"

    def wait_status(aid, wants, timeout_s, label):
        end = time.time() + timeout_s
        last = None
        while time.time() < end:
            row = app_row(aid)
            last = row.get("status")
            if last in wants:
                return row
            time.sleep(3)
        row = app_row(aid)
        raise AssertionError(f"{label}: status stayed {last!r}, wanted {wants}; proof={row.get('proof')!r} needs={row.get('needs_review')!r}; ext={ext_state()}")

    ctx = None
    try:
        import urllib.request
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://localhost:{API_PORT}/api/health", timeout=2) as resp:
                    if json.loads(resp.read())["version"] == 8:
                        break
            except Exception:
                time.sleep(0.5)

        # ---------- load the real extension ----------
        from playwright.sync_api import sync_playwright
        exe = chrome_exe()
        assert exe, "no Playwright Chromium found"
        udd = "/tmp/swipehire-e2e-profile"
        shutil.rmtree(udd, ignore_errors=True)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                udd, executable_path=exe, headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-proxy-server",
                      f"--disable-extensions-except={ROOT}/extension",
                      f"--load-extension={ROOT}/extension",
                      "--host-resolver-rules=MAP boards.greenhouse.io 127.0.0.1, MAP www.linkedin.com 127.0.0.1"])
            sw = None
            if ctx.service_workers:
                sw = ctx.service_workers[0]
            else:
                try:
                    sw = ctx.wait_for_event("serviceworker", timeout=20000)
                except Exception:
                    pass
            assert sw, "extension service worker never started"
            ext_id = sw.url.split("/")[2]
            print(f"[e2e] extension loaded, id={ext_id}", flush=True)

            popup = ctx.new_page()
            POPUP["page"] = popup
            popup.goto(f"chrome-extension://{ext_id}/popup.html")
            popup.fill("#apiBase", f"http://localhost:{API_PORT}")
            popup.fill("#token", tok)
            popup.click("#save")  # pairing saved -> autonomous ON -> processor kicks
            print("[e2e] paired via popup; autonomous processor running (zero further clicks)", flush=True)

            # (a) ATS form — first in queue; pacing only gates AFTER a submission
            row = wait_status(app_ids["Fixture ATS Role"], {"submitted"}, 150, "ats")
            note("ats_form_submitted", row["status"] == "submitted",
                 f"status={row['status']} proof={row['proof'][:160]!r}")

            # (b) Easy Apply — claimed after the 90s post-submission pacing gap
            row = wait_status(app_ids["Fixture Easy Apply Role"], {"submitted"}, 300, "easy_apply")
            note("easy_apply_submitted", row["status"] == "submitted",
                 f"status={row['status']} proof={row['proof'][:160]!r}")

            # (c) CAPTCHA fixture — must park needs_human, never submit
            row = wait_status(app_ids["Fixture Captcha Role"], {"needs_human"}, 300, "captcha")
            needs = json.loads(row["needs_review"] or "[]")
            ok = row["status"] == "needs_human" and any("CAPTCHA" in n for n in needs)
            note("captcha_parked_needs_human", ok,
                 f"status={row['status']} needs_review={needs}")
            ctx.close()
            ctx = None
    finally:
        if ctx:
            try: ctx.close()
            except Exception: pass
        fixsrv.shutdown()
        server.terminate()
        try: server.wait(timeout=10)
        except Exception: server.kill()

    failed = [k for k, (ok, _d) in RESULTS.items() if not ok]
    print("[e2e] SUMMARY:", json.dumps({k: v[0] for k, v in RESULTS.items()}), flush=True)
    if len(RESULTS) < 3 or failed:
        print("[e2e] E2E FAILED:", failed or "incomplete", flush=True)
        return 1
    print("[e2e] E2E ALL PASS", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
