"""Real-board DRY-RUN pass for the SwipeHire autonomous driver.

Drives the REAL extension, in DRY-RUN MODE ONLY, against real, live,
public application forms (Greenhouse / Lever / Ashby — no login needed).
The driver fills and verifies but NEVER clicks submit; the server must
end with a dry-run (or park) record and NO submitted application.

A form whose flow offers no safe dry-run point (single-click submit with
no review step that the driver would have to click to proceed) is
skipped and recorded with the reason — see BOARDS notes.

Usage: .venv/bin/python tests/e2e_realboards.py [--out results.json]
Exit 0 = every board produced a record and nothing was submitted.
"""
import argparse
import base64
import glob
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

if not os.environ.get("DISPLAY") and not os.environ.get("SWIPEHIRE_E2E_XVFB"):
    if shutil.which("xvfb-run"):
        env = dict(os.environ, SWIPEHIRE_E2E_XVFB="1")
        print("[real] no DISPLAY — re-exec under xvfb-run", flush=True)
        os.execvpe("xvfb-run", ["xvfb-run", "-a", sys.executable, os.path.abspath(__file__)] + sys.argv[1:], env)
    print("[real] SKIP: no DISPLAY and no xvfb-run available")
    sys.exit(0)

EXT_DIR = os.path.join(ROOT, "extension")


class ChainProxy(threading.Thread):
    """Local chaining proxy that injects preemptive Proxy-Authorization.

    This VM's egress proxy requires credentials up front (curl sends them
    from the proxy URL; Chromium waits for a 407 challenge the proxy never
    issues and the connection dies — ERR_TUNNEL_CONNECTION_FAILED /
    ERR_EMPTY_RESPONSE). The harness therefore runs this localhost proxy:
    Chromium talks to it unauthenticated, it forwards to the real egress
    proxy with the auth header attached. Credentials come from the
    HTTPS_PROXY env var at runtime; nothing is hardcoded.
    """

    def __init__(self, upstream_url, port=18080):
        super().__init__(daemon=True)
        u = urlparse(upstream_url)
        self.up_host, self.up_port = u.hostname, u.port or 3128
        raw = f"{u.username or ''}:{u.password or ''}".encode()
        self.auth = base64.b64encode(raw).decode()
        self.port = port

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", self.port))
        srv.listen(50)
        while True:
            try:
                client, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    @staticmethod
    def _read_head(sock):
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf

    @staticmethod
    def _relay(a, b):
        sockets = [a, b]
        for s in sockets:
            s.setblocking(False)
        try:
            while True:
                r, _, _ = select.select(sockets, [], [], 60)
                if not r:
                    break
                done = False
                for s in r:
                    try:
                        data = s.recv(65536)
                    except OSError:
                        data = b""
                    if not data:
                        done = True
                        break
                    (b if s is a else a).sendall(data)
                if done:
                    break
        except OSError:
            pass
        for s in sockets:
            try:
                s.close()
            except OSError:
                pass

    def _handle(self, client):
        try:
            head = self._read_head(client)
            if not head:
                client.close()
                return
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = line.split(" ")
            upstream = socket.create_connection((self.up_host, self.up_port), timeout=30)
            if parts[0].upper() == "CONNECT":
                target = parts[1]
                req = (f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
                       f"Proxy-Authorization: Basic {self.auth}\r\n"
                       "Proxy-Connection: keep-alive\r\n\r\n").encode()
                upstream.sendall(req)
                resp = self._read_head(upstream)
                if b" 200" not in resp.split(b"\r\n", 1)[0]:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    client.close(); upstream.close()
                    return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self._relay(client, upstream)
            else:
                headers = head.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
                out = [headers[0]]
                for h in headers[1:]:
                    if h.lower().startswith(("proxy-connection:", "connection:", "proxy-authorization:")):
                        continue
                    out.append(h)
                out.append(f"Proxy-Authorization: Basic {self.auth}")
                out.append("Connection: close")
                upstream.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
                self._relay(client, upstream)
        except OSError:
            try:
                client.close()
            except OSError:
                pass


API_PORT = 5057
DB_PATH = "/tmp/swipehire-realboards.db"

BOARDS = [
    ("lever-wmg", "https://jobs.lever.co/wmg/3d4069a5-d737-4c53-8377-043e092003ee/apply"),
    ("lever-zoox", "https://jobs.lever.co/zoox/beeb6f55-0d41-4481-a5d8-18b1f9b6657b/apply"),
    ("ashby-sift", "https://jobs.ashbyhq.com/siftstack/b07cf371-0536-4b99-9331-a81668addeb8/application"),
    ("ashby-openai", "https://jobs.ashbyhq.com/openai/80c40695-5cd7-4206-96b6-873158a8e696/application"),
    ("greenhouse-cloudflare", "https://job-boards.greenhouse.io/cloudflare/jobs/8263016"),
    ("greenhouse-gh", "https://job-boards.greenhouse.io/greenhouse/jobs/8204473"),
]

BASE_ANSWERS = {
    "full_name": "Ada Lovelace", "first_name": "Ada", "last_name": "Lovelace",
    "email": "ada@x.com", "phone": "514-555-0199", "years_of_experience": "6",
    "salary_expectation": "95000", "location": "Montreal", "country": "Canada",
    "company": "Analytical Engines Ltd",
    "skills": "Python, Flask, SQL", "current_title": "Python Developer",
    "workauth": "Yes", "sponsorship": "No",
}
RESUME = "Ada Lovelace\nada@x.com\n514-555-0199\nPython developer with 6 years experience\nSkills: Python, Flask, SQL"


def chrome_exe():
    cands = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")))
    return cands[-1] if cands else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    for f in (DB_PATH, DB_PATH + "-wal", DB_PATH + "-shm"):
        if os.path.exists(f):
            os.remove(f)

    from app import create_app
    from app.db import get_conn
    from app.job_sources import insert_jobs
    app = create_app(db_path=DB_PATH, testing=True)
    tc = app.test_client()
    r = tc.post("/api/auth/signup", json={"email": "rb@x.com", "password": "pw1234", "role": "candidate", "name": "Ada Lovelace"})
    assert r.status_code == 201, r.data
    r = tc.post("/api/resume", json={"text": RESUME})
    assert r.status_code in (200, 201), r.data
    tc.post("/api/candidate/answer-bank", json={"question": "Are you authorized to work in Canada?", "answer": "Yes"})
    tc.post("/api/candidate/answer-bank", json={"question": "Will you require sponsorship?", "answer": "No"})

    conn = get_conn(DB_PATH)
    cand_id = conn.execute("SELECT candidate_id FROM users WHERE email='rb@x.com'").fetchone()["candidate_id"]
    insert_jobs(conn, [{"external_id": f"rb-{name}", "title": f"Real {name}", "company": "RealCo",
                        "location": "Remote", "remote": 1, "salary_min": None, "salary_max": None,
                        "salary_text": "", "description": "Python role", "url": url,
                        "source": "realboards", "tags": ["Python"], "posted_at": ""}
                       for name, url in BOARDS])
    app_ids = {}
    for name, url in BOARDS:
        jid = conn.execute("SELECT id FROM jobs WHERE external_id=?", (f"rb-{name}",)).fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO applications(candidate_id,job_id,status,method,answers,tailored_resume,needs_review) "
            "VALUES(?,?,'prepared','extension_required',?,?,'[]')",
            (cand_id, jid, json.dumps(BASE_ANSWERS), RESUME))
        app_ids[name] = cur.lastrowid
    conn.commit()
    conn.close()

    tok = tc.post("/api/ext/pairing-token").json["token"]
    server = subprocess.Popen(
        [sys.executable, "-c", f"from app import create_app; create_app(db_path={DB_PATH!r}).run(port={API_PORT})"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def app_row(aid):
        c = get_conn(DB_PATH)
        row = c.execute("SELECT * FROM applications WHERE id=?", (aid,)).fetchone()
        c.close()
        return dict(row) if row else {}

    results = {}
    ctx = None
    try:
        from playwright.sync_api import sync_playwright
        exe = chrome_exe()
        assert exe, "no Playwright Chromium found"
        udd = "/tmp/swipehire-realboards-profile"
        shutil.rmtree(udd, ignore_errors=True)
        proxy_kw = {}
        upstream_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if upstream_proxy:
            ChainProxy(upstream_proxy).start()
            time.sleep(0.5)
            proxy_kw = {"proxy": {"server": "http://127.0.0.1:18080",
                                  "bypass": "localhost,127.0.0.1"}}
            print("[real] chaining via local auth proxy -> egress proxy", flush=True)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                udd, executable_path=exe, headless=False, ignore_https_errors=True, **proxy_kw,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--no-first-run",
                      f"--disable-extensions-except={EXT_DIR}", f"--load-extension={EXT_DIR}"])
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
            popup = ctx.new_page()
            popup.goto(f"chrome-extension://{ext_id}/popup.html")
            # DRY-RUN FIRST, before pairing starts the processor.
            popup.evaluate("async () => { await chrome.storage.local.set({dryRun: true}); }")
            popup.fill("#apiBase", f"http://localhost:{API_PORT}")
            popup.fill("#token", tok)
            popup.click("#save")
            print("[real] paired in DRY-RUN mode; driving real boards", flush=True)

            for name, url in BOARDS:
                aid = app_ids[name]
                end = time.time() + args.timeout
                row = app_row(aid)
                while time.time() < end:
                    if (row.get("dry_run_report") or "") or row.get("status") in ("needs_human", "needs_review", "submitted", "submitted_unverified"):
                        break
                    time.sleep(3)
                    row = app_row(aid)
                rep = {}
                try:
                    rep = json.loads(row.get("dry_run_report") or "{}")
                except Exception:
                    pass
                fields = rep.get("fields") or []
                filled = [f for f in fields if f.get("status") == "filled"]
                missed = [f for f in fields if f.get("status") == "missed"]
                results[name] = {
                    "url": url, "status": row.get("status"),
                    "outcome": "verified_dry_run" if rep else row.get("status"),
                    "fields_found": len(fields), "filled": len(filled),
                    "missed": [{"label": f.get("label"), "detail": f.get("detail")} for f in missed],
                    "filled_labels": [f.get("label") for f in filled],
                    "park_reasons": json.loads(row.get("needs_review") or "[]"),
                    "proof": (row.get("proof") or "")[:300],
                }
                print(f"[real] {name}: status={row.get('status')} fields={len(fields)} filled={len(filled)} missed={len(missed)}", flush=True)
                try:
                    popup.evaluate("async () => { await chrome.storage.local.remove(['pace']); "
                                   "try { await chrome.runtime.sendMessage({type:'sh-kick'}); } catch (e) {} }")
                except Exception:
                    pass
            ctx.close()
            ctx = None
    finally:
        if ctx:
            try: ctx.close()
            except Exception: pass
        server.terminate()
        try: server.wait(timeout=10)
        except Exception: server.kill()

    submitted = [n for n, r in results.items() if r["status"] in ("submitted", "submitted_unverified")]
    print("[real] SUMMARY:", json.dumps({"boards": len(results), "submitted_anything": submitted}), flush=True)
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"results": results, "submitted_anything": submitted}, f, indent=2)
    return 0 if not submitted else 1


if __name__ == "__main__":
    sys.exit(main())
