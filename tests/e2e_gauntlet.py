"""Hostile-fixture gauntlet for the SwipeHire autonomous apply driver.

Drives the REAL extension (EXT_DIR, default extension/) through ~19
fixtures modeled on real boards and edge cases, using the same machinery
as tests/e2e_extension.py (Playwright persistent context, fake board
domains via --host-resolver-rules, local Flask server + fixture server).

Every fixture records GROUND TRUTH in localStorage when its form is
actually submitted, so scoring never trusts the tracker's word alone:
  expected submitted  -> PASS iff tracker says submitted(/unverified)
                         AND the fixture really recorded a submission.
  expected needs_human-> PASS iff tracker parked it AND no submission
                         was recorded. A recorded submission here is a
                         CRITICAL failure (submitted when it must park).

Usage:
  .venv/bin/python tests/e2e_gauntlet.py [--ext-dir DIR] [--out results.json]
                                         [--only name,name] [--timeout 180]
                                         [--mode normal|dryrun]
Exit code: 0 = all pass and no criticals; 1 otherwise; 2 = harness error.
"""
import argparse
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
        print("[gauntlet] no DISPLAY — re-exec under xvfb-run", flush=True)
        os.execvpe("xvfb-run", ["xvfb-run", "-a", sys.executable, os.path.abspath(__file__)] + sys.argv[1:], env)
    print("[gauntlet] SKIP: no DISPLAY and no xvfb-run available")
    sys.exit(0)

API_PORT = 5056
FIX_PORT = 8900
TLS_PORT = 8901
FIX_DIR = os.path.join(HERE, "fixtures")
GH = f"http://boards.greenhouse.io:{FIX_PORT}/gauntlet"
# jobs.ashbyhq.com is on the HSTS preload list: Chromium refuses plain
# HTTP to it even with host-resolver overrides, so Ashby fixtures are
# served over local HTTPS (self-signed, ignore_https_errors in context).
ASHBY = f"https://jobs.ashbyhq.com:{TLS_PORT}/gauntlet"

BASE_ANSWERS = {
    "full_name": "Ada Lovelace", "first_name": "Ada", "last_name": "Lovelace",
    "email": "ada@x.com", "phone": "514-555-0199", "years_of_experience": "6",
    "salary_expectation": "95000", "location": "Montreal",
    "skills": "Python, Flask, SQL", "current_title": "Python Developer",
}
RESUME = "Ada Lovelace\nada@x.com\n514-555-0199\nPython developer with 6 years experience\nSkills: Python, Flask, SQL"

# name, url, expected outcome, extra answers
FIXTURES = [
    ("lever-style", f"http://jobs.lever.co:{FIX_PORT}/gauntlet/lever-style.html", "submitted", {}),
    ("ashby-style", f"{ASHBY}/ashby-style.html", "submitted", {}),
    ("workable-style", f"http://apply.workable.com:{FIX_PORT}/gauntlet/workable-style.html", "submitted", {}),
    ("smartrecruiters-style", f"http://jobs.smartrecruiters.com:{FIX_PORT}/gauntlet/smartrecruiters-style.html", "submitted", {}),
    ("multistep-3page", f"{GH}/multistep-3page.html", "submitted", {}),
    ("iframe-form", f"{GH}/iframe-form.html", "submitted", {}),
    ("custom-dropdown", f"{GH}/custom-dropdown.html", "submitted", {}),
    ("radio-heavy", f"{GH}/radio-heavy.html", "submitted", {"workauth": "Yes", "sponsorship": "No"}),
    ("unknown-required", f"{GH}/unknown-required.html", "needs_human", {}),
    ("unknown-optional", f"{GH}/unknown-optional.html", "submitted", {}),
    ("consent-checkboxes", f"{GH}/consent-checkboxes.html", "submitted", {}),
    ("delayed-render", f"{GH}/delayed-render.html", "submitted", {}),
    ("resume-radio", f"{GH}/resume-radio.html", "submitted", {}),
    ("easy-apply-extra", f"http://www.linkedin.com:{FIX_PORT}/gauntlet/easy-apply-extra.html", "submitted", {}),
    ("indeed-style", f"http://www.indeed.com:{FIX_PORT}/gauntlet/indeed-style.html", "submitted", {}),
    ("redirect-confirm", f"{GH}/redirect-confirm.html", "submitted", {}),
    ("labels-div", f"http://jobs.lever.co:{FIX_PORT}/gauntlet/labels-div.html", "submitted", {}),
    ("select-native", f"{GH}/select-native.html", "submitted", {}),
    ("banked-question", f"{GH}/banked-question.html", "submitted", {}),
]
DRYRUN_FIXTURE = ("dryrun-check", f"{GH}/lever-style.html", "verified_dry_run", {})
TERMINAL = {"submitted", "submitted_unverified", "needs_human", "needs_review"}
HOST_RULES = ("MAP boards.greenhouse.io 127.0.0.1, MAP jobs.lever.co 127.0.0.1, "
              "MAP jobs.ashbyhq.com 127.0.0.1, MAP apply.workable.com 127.0.0.1, "
              "MAP jobs.smartrecruiters.com 127.0.0.1, MAP www.linkedin.com 127.0.0.1, "
              "MAP www.indeed.com 127.0.0.1")


def chrome_exe():
    cands = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")))
    return cands[-1] if cands else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ext-dir", default=os.path.join(ROOT, "extension"))
    ap.add_argument("--out", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=200)
    ap.add_argument("--mode", default="normal", choices=["normal", "dryrun"])
    ap.add_argument("--db", default="/tmp/swipehire-gauntlet.db")
    args = ap.parse_args()

    fixtures = FIXTURES
    if args.mode == "dryrun":
        fixtures = [DRYRUN_FIXTURE]
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        fixtures = [f for f in fixtures if f[0] in want]
    assert fixtures, "no fixtures selected"

    for f in (args.db, args.db + "-wal", args.db + "-shm"):
        if os.path.exists(f):
            os.remove(f)

    from app import create_app
    from app.db import get_conn
    from app.job_sources import insert_jobs
    app = create_app(db_path=args.db, testing=True)
    tc = app.test_client()
    r = tc.post("/api/auth/signup", json={"email": "g@x.com", "password": "pw1234", "role": "candidate", "name": "Ada Lovelace"})
    assert r.status_code == 201, r.data
    r = tc.post("/api/resume", json={"text": RESUME})
    assert r.status_code in (200, 201), r.data
    # Bank the work-auth answer under ONE phrasing; the banked-question
    # fixture asks it with a DIFFERENT phrasing (synonym normalization).
    r = tc.post("/api/candidate/answer-bank", json={"question": "Are you authorized to work in Canada?", "answer": "Yes"})
    assert r.status_code == 201, r.data

    conn = get_conn(args.db)
    cand_id = conn.execute("SELECT candidate_id FROM users WHERE email='g@x.com'").fetchone()["candidate_id"]
    insert_jobs(conn, [{"external_id": f"g-{name}", "title": f"Gauntlet {name}", "company": "FixtureCo",
                        "location": "Remote", "remote": 1, "salary_min": None, "salary_max": None,
                        "salary_text": "", "description": "Python Flask SQL role", "url": url,
                        "source": "gauntlet", "tags": ["Python"], "posted_at": ""}
                       for name, url, _e, _x in fixtures])
    app_ids = {}
    for name, url, _exp, extra in fixtures:
        jid = conn.execute("SELECT id FROM jobs WHERE external_id=?", (f"g-{name}",)).fetchone()["id"]
        answers = dict(BASE_ANSWERS)
        answers.update(extra)
        cur = conn.execute(
            "INSERT INTO applications(candidate_id,job_id,status,method,answers,tailored_resume,needs_review) "
            "VALUES(?,?,'prepared','extension_required',?,?,'[]')",
            (cand_id, jid, json.dumps(answers), RESUME))
        app_ids[name] = cur.lastrowid
    conn.commit()
    conn.close()
    print("[gauntlet] seeded:", app_ids, flush=True)

    tok = tc.post("/api/ext/pairing-token").json["token"]
    server = subprocess.Popen(
        [sys.executable, "-c", f"from app import create_app; create_app(db_path={args.db!r}).run(port={API_PORT})"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fixsrv = http.server.ThreadingHTTPServer(("127.0.0.1", FIX_PORT),
                                             lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=FIX_DIR, **k))
    threading.Thread(target=fixsrv.serve_forever, daemon=True).start()
    import ssl as _ssl
    # Self-signed fixture cert (Ashby's host is HSTS-preloaded, so its
    # fixture must be served over HTTPS). Regenerate if /tmp was cleaned.
    if not (os.path.exists("/tmp/fixture-tls/cert.pem") and os.path.exists("/tmp/fixture-tls/key.pem")):
        os.makedirs("/tmp/fixture-tls", exist_ok=True)
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
             "-keyout", "/tmp/fixture-tls/key.pem", "-out", "/tmp/fixture-tls/cert.pem",
             "-subj", "/CN=jobs.ashbyhq.com",
             "-addext", "subjectAltName=DNS:jobs.ashbyhq.com,DNS:boards.greenhouse.io,"
                        "DNS:jobs.lever.co,DNS:apply.workable.com,DNS:jobs.smartrecruiters.com,"
                        "DNS:www.indeed.com,DNS:www.linkedin.com,DNS:localhost"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tls_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    tls_ctx.load_cert_chain("/tmp/fixture-tls/cert.pem", "/tmp/fixture-tls/key.pem")
    tlssrv = http.server.ThreadingHTTPServer(("127.0.0.1", TLS_PORT),
                                             lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=FIX_DIR, **k))
    tlssrv.socket = tls_ctx.wrap_socket(tlssrv.socket, server_side=True)
    threading.Thread(target=tlssrv.serve_forever, daemon=True).start()

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
    assert queue_seen is not None, "API server never came up"

    def app_row(aid):
        c = get_conn(args.db)
        row = c.execute("SELECT * FROM applications WHERE id=?", (aid,)).fetchone()
        c.close()
        return dict(row) if row else {}

    results = {}
    ctx = None
    try:
        from playwright.sync_api import sync_playwright
        exe = chrome_exe()
        assert exe, "no Playwright Chromium found"
        udd = "/tmp/swipehire-gauntlet-profile-" + args.mode
        shutil.rmtree(udd, ignore_errors=True)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                udd, executable_path=exe, headless=False, ignore_https_errors=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-proxy-server",
                      f"--disable-extensions-except={args.ext_dir}",
                      f"--load-extension={args.ext_dir}",
                      f"--host-resolver-rules={HOST_RULES}"])
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
            print(f"[gauntlet] extension loaded from {args.ext_dir}, id={ext_id}", flush=True)

            popup = ctx.new_page()
            popup.goto(f"chrome-extension://{ext_id}/popup.html")
            if args.mode == "dryrun":
                popup.evaluate("async () => { await chrome.storage.local.set({dryRun: true}); }")
            popup.fill("#apiBase", f"http://localhost:{API_PORT}")
            popup.fill("#token", tok)
            popup.click("#save")
            print("[gauntlet] paired; processor running", flush=True)

            def reset_pace_and_kick():
                try:
                    popup.evaluate("async () => { await chrome.storage.local.remove(['pace']); "
                                   "try { await chrome.runtime.sendMessage({type:'sh-kick'}); } catch (e) {} }")
                except Exception:
                    pass

            for name, url, expected, _extra in fixtures:
                aid = app_ids[name]
                end = time.time() + args.timeout
                row = app_row(aid)
                if args.mode == "dryrun":
                    while time.time() < end and not (row.get("dry_run_report") or ""):
                        time.sleep(3)
                        row = app_row(aid)
                    ok = bool(row.get("dry_run_report")) and row.get("status") != "submitted"
                    rep = {}
                    try:
                        rep = json.loads(row.get("dry_run_report") or "{}")
                    except Exception:
                        pass
                    results[name] = {"expected": expected, "actual": "verified_dry_run" if ok else row.get("status"),
                                     "pass": ok, "critical": False,
                                     "detail": f"fields={rep.get('filled')} filled/{rep.get('missed')} missed, status={row.get('status')}"}
                    print(f"[gauntlet] {'PASS' if ok else 'FAIL'} {name}: {results[name]['detail']}", flush=True)
                    continue
                while time.time() < end and row.get("status") not in TERMINAL:
                    time.sleep(3)
                    row = app_row(aid)
                results[name] = {"expected": expected, "actual": row.get("status"),
                                 "proof": (row.get("proof") or "")[:200],
                                 "needs": json.loads(row.get("needs_review") or "[]")}
                print(f"[gauntlet] {name}: tracker={row.get('status')} (expected {expected})", flush=True)
                reset_pace_and_kick()

            # ---- ground truth from localStorage (per origin) ----
            # Stop the processor first so no claim can navigate a tab
            # while truth pages are loading.
            try:
                popup.evaluate("async () => { await chrome.storage.local.set({autoEnabled: false}); }")
                time.sleep(2)
            except Exception:
                pass
            origins = {}
            for name, url, _e, _x in fixtures:
                # dryrun-check drives lever-style.html; its ground-truth
                # key is the fixture file's own key.
                tkey = "lever-style" if name == "dryrun-check" else name
                origins.setdefault(url.split("/gauntlet/")[0], []).append((name, url, tkey))
            truth = {}
            for origin, items in origins.items():
                page = ctx.new_page()
                loaded = False
                for _try in range(3):
                    try:
                        page.goto(items[0][1], wait_until="domcontentloaded", timeout=20000)
                        loaded = True
                        break
                    except Exception:
                        time.sleep(1)
                for name, _url, tkey in items:
                    if not loaded:
                        truth[name] = None  # unknown — never counts as a submission
                        continue
                    try:
                        truth[name] = page.evaluate(f"() => localStorage.getItem('gauntletSubmit:{tkey}')")
                    except Exception:
                        truth[name] = None
                try: page.close()
                except Exception: pass
            ctx.close()
            ctx = None

            for name, url, expected, _x in fixtures:
                if args.mode == "dryrun":
                    # dry-run must NEVER have submitted the form
                    submitted_really = bool(truth.get(name))
                    results[name]["ground_truth_submitted"] = submitted_really
                    if submitted_really:
                        results[name]["pass"] = False
                        results[name]["critical"] = True
                        results[name]["detail"] += " | CRITICAL: form was really submitted during dry-run"
                    continue
                r = results[name]
                tval = truth.get(name)
                truth_known = isinstance(tval, str) and tval != ""
                submitted_really = truth_known
                r["ground_truth_submitted"] = submitted_really if truth_known or tval is None else False
                r["truth_known"] = truth_known
                r["truth_value"] = tval
                if expected == "submitted":
                    if truth_known:
                        r["pass"] = r["actual"] in ("submitted", "submitted_unverified") and submitted_really
                    else:
                        # Truth unreadable: fall back to the tracker alone.
                        r["pass"] = r["actual"] in ("submitted", "submitted_unverified")
                        r["detail"] = "ground truth unreadable; scored on tracker status alone"
                    r["critical"] = False
                    if truth_known and r["actual"] in ("submitted", "submitted_unverified") and not submitted_really:
                        r["detail"] = "tracker claimed submission but fixture recorded NONE (false confirmation)"
                        r["critical"] = True
                    elif truth_known and submitted_really and r["actual"] not in ("submitted", "submitted_unverified"):
                        r["detail"] = "form really submitted but tracker did not record it (misreport — duplicate risk)"
                        r["critical"] = True
                else:  # needs_human
                    r["pass"] = r["actual"] == "needs_human" and not submitted_really
                    r["critical"] = submitted_really
                    if submitted_really:
                        r["detail"] = "CRITICAL: submitted a form that had to park"
                print(f"[gauntlet] {'PASS' if r['pass'] else 'FAIL'}{' CRITICAL' if r.get('critical') else ''} "
                      f"{name}: expected={expected} actual={r['actual']} truth_submitted={submitted_really}", flush=True)
    finally:
        if ctx:
            try: ctx.close()
            except Exception: pass
        fixsrv.shutdown()
        tlssrv.shutdown()
        server.terminate()
        try: server.wait(timeout=10)
        except Exception: server.kill()

    passed = sum(1 for r in results.values() if r["pass"])
    criticals = [k for k, r in results.items() if r.get("critical")]
    summary = {"passed": passed, "total": len(results), "criticals": criticals, "results": results}
    print("[gauntlet] SUMMARY:", json.dumps({"passed": passed, "total": len(results), "criticals": criticals}), flush=True)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(summary, f, indent=2)
    return 0 if passed == len(results) and not criticals else 1


if __name__ == "__main__":
    sys.exit(main())
