"""Adversarial / break-it tests (QA pass, Nov 12 2026).

Authorization (IDOR), role confusion, malformed/wrong-typed input,
auth abuse, upload abuse, injection/XSS payloads, extension-API auth.

Several tests in the "bad input" section pin the fixes from the QA bug
batch: before it, non-object JSON bodies, wrong-typed fields and a
string salary_max on one employer's job posting returned 500 — the
salary one poisoned OTHER users' discovery feeds (cross-user DoS).
See docs/QA_USER_STORIES.md, "Bugs found & fixed during QA".
"""
import io, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64
HUGE_ID = 99999999999999999999999999  # > SQLite int64 max

@pytest.fixture()
def app_(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db"), testing=True)

@pytest.fixture()
def client(app_):
    return app_.test_client()

def signup(client, email, role="candidate", name="User", password="pw1234", company="Acme"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password, "role": role, "name": name, "company": company})
    assert r.status_code == 201, r.data
    return r.json["user"]

def login_demo(client, email="demo@swipehire.app"):
    r = client.post("/api/auth/login", json={"email": email, "password": "demo"})
    assert r.status_code == 200, r.data
    return r.json["user"]

def _insert_job(app_, url, title="Adv Job"):
    import zlib
    from app.db import get_conn
    from app.job_sources import insert_jobs
    conn = get_conn(app_.config["DB_PATH"])
    insert_jobs(conn, [{"external_id": f"adv-{zlib.crc32((url + title).encode())}", "title": title, "company": "NetCo",
                        "location": "Remote", "remote": 1, "salary_min": None, "salary_max": None,
                        "salary_text": "", "description": "Python Flask SQL role", "url": url,
                        "source": "testsrc", "tags": ["Python"], "posted_at": ""}])
    row = conn.execute("SELECT * FROM jobs WHERE url=? ORDER BY id DESC", (url,)).fetchone()
    d = dict(row)
    conn.close()
    return d

def give_media(client):
    """v9/N1: new job posts require media — give this (fresh) employer a
    recruiter-profile picture first, as a real recruiter would."""
    r = client.post("/api/employer/logo", data={"file": (io.BytesIO(PNG_BYTES), "logo.png")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    return r

def _make_match(app_):
    """Employer (demo) posts a job; demo candidate right-swipes; employer
    right-swipes back. Returns (emp_client, cand_client, job, match_id)."""
    e = app_.test_client(); login_demo(e, "employer@swipehire.app")
    job = e.post("/api/employer/jobs", json={"title": "Match Role", "description": "Python role", "tags": ["Python"]}).json
    c = app_.test_client(); cand = login_demo(c)
    c.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    r = e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    assert "match" in r.json, r.data
    return e, c, job, r.json["match"]["id"]

# ---------------- 1. unauthenticated: every protected endpoint -> 401 ----------------

PROTECTED = [
    ("GET", "/api/auth/me"), ("GET", "/api/candidate"), ("POST", "/api/resume"),
    ("POST", "/api/profile/photo"), ("POST", "/api/employer/logo"),
    ("POST", "/api/candidate/preferences"), ("GET", "/api/connections"),
    ("POST", "/api/connections"), ("POST", "/api/ext/pairing-token"),
    ("DELETE", "/api/ext/pairing-token"), ("DELETE", "/api/connections/linkedin"),
    ("DELETE", "/api/account"), ("GET", "/api/documents"), ("POST", "/api/documents"),
    ("DELETE", "/api/documents/1"), ("GET", "/api/master-profile"),
    ("GET", "/api/compiled-resume"), ("GET", "/api/compiled-resume/download"),
    ("POST", "/api/jobs/scan"), ("GET", "/api/discovery"), ("POST", "/api/swipe"),
    ("GET", "/api/applications"), ("GET", "/api/applications/1/tailored-resume"),
    ("GET", "/api/candidates"), ("GET", "/api/employer/jobs"),
    ("POST", "/api/employer/jobs"), ("GET", "/api/employer/candidates"),
    ("POST", "/api/employer/swipe"), ("GET", "/api/matches"),
    ("GET", "/api/matches/1/messages"), ("POST", "/api/matches/1/messages"),
    ("GET", "/uploads/somefile.png"),
    ("GET", "/api/ext/queue"), ("POST", "/api/ext/report"),
]

@pytest.mark.parametrize("method,url", PROTECTED)
def test_unauthenticated_everything_401(client, method, url):
    r = getattr(client, method.lower())(url)
    assert r.status_code == 401, (method, url, r.status_code)

# ---------------- 2. role confusion -> 403, never 500 ----------------

CANDIDATE_ONLY = [
    ("GET", "/api/candidate"), ("POST", "/api/resume"), ("POST", "/api/profile/photo"),
    ("POST", "/api/candidate/preferences"), ("GET", "/api/connections"),
    ("POST", "/api/connections"), ("POST", "/api/ext/pairing-token"),
    ("GET", "/api/documents"), ("POST", "/api/documents"), ("DELETE", "/api/documents/1"),
    ("GET", "/api/master-profile"), ("GET", "/api/compiled-resume"),
    ("GET", "/api/discovery"), ("POST", "/api/swipe"), ("GET", "/api/applications"),
    ("GET", "/api/applications/1/tailored-resume"),
]
EMPLOYER_ONLY = [
    ("POST", "/api/employer/logo"), ("GET", "/api/candidates"),
    ("GET", "/api/employer/jobs"), ("POST", "/api/employer/jobs"),
    ("GET", "/api/employer/candidates"), ("POST", "/api/employer/swipe"),
]

@pytest.mark.parametrize("method,url", EMPLOYER_ONLY)
def test_candidate_cannot_use_employer_endpoints(app_, method, url):
    c = app_.test_client(); signup(c, "cand-only@x.com")
    r = getattr(c, method.lower())(url, json={})
    assert r.status_code == 403, (method, url, r.status_code)

@pytest.mark.parametrize("method,url", CANDIDATE_ONLY)
def test_employer_cannot_use_candidate_endpoints(app_, method, url):
    e = app_.test_client(); signup(e, "emp-only@x.com", role="employer")
    r = getattr(e, method.lower())(url, json={})
    assert r.status_code == 403, (method, url, r.status_code)

# ---------------- 3. IDOR ----------------

def test_employer_b_cannot_touch_employer_a_job(app_):
    a = app_.test_client(); signup(a, "emp-a@x.com", role="employer", company="ACo")
    give_media(a)  # v9: posting requires recruiter media (or job media)
    job = a.post("/api/employer/jobs", json={"title": "A Private Role", "description": "d", "tags": []}).json
    b = app_.test_client(); signup(b, "emp-b@x.com", role="employer", company="BCo")
    # B's job list does not contain A's job
    assert all(j["id"] != job["id"] for j in b.get("/api/employer/jobs").json)
    # B cannot open A's candidate feed for A's job
    assert b.get(f"/api/employer/candidates?job_id={job['id']}").status_code == 404
    # B cannot swipe on A's job (even with a real candidate id)
    cand_id = b.get("/api/candidates").json[0]["id"]
    r = b.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand_id, "direction": "right"})
    assert r.status_code == 404
    # and no swipe/match leaked into existence for B
    assert b.get("/api/matches").json == []

def test_match_messages_locked_to_parties(app_):
    e, c, job, mid = _make_match(app_)
    # a second employer (not the match's employer) is locked out
    e2 = app_.test_client(); signup(e2, "emp-intruder@x.com", role="employer", company="ICo")
    assert e2.get(f"/api/matches/{mid}/messages").status_code == 404
    assert e2.post(f"/api/matches/{mid}/messages", json={"body": "intrude"}).status_code == 404
    assert all(m["id"] != mid for m in e2.get("/api/matches").json)
    # both parties CAN message, in both directions
    assert c.post(f"/api/matches/{mid}/messages", json={"body": "hi from candidate"}).status_code == 201
    assert e.post(f"/api/matches/{mid}/messages", json={"body": "hi from employer"}).status_code == 201
    bodies = [m["body"] for m in e.get(f"/api/matches/{mid}/messages").json]
    assert bodies == ["hi from candidate", "hi from employer"]
    # nonexistent match is indistinguishable from not-yours
    assert c.get("/api/matches/424242/messages").status_code == 404

def test_uploads_require_login_but_are_shared_with_signed_in_users(app_):
    # By design: profile photos/logos are visible to signed-in counterparties
    # (employers must see candidate photos), filenames are unguessable UUIDs.
    # The guarantee enforced here: anonymous -> 401.
    a = app_.test_client(); signup(a, "photo-a@x.com")
    r = a.post("/api/profile/photo", data={"file": (io.BytesIO(PNG_BYTES), "me.png")}, content_type="multipart/form-data")
    url = r.json["url"]
    anon = app_.test_client()
    assert anon.get(url).status_code == 401
    assert anon.get("/uploads/../app.db").status_code in (401, 404)  # no traversal, no leak
    other = app_.test_client(); signup(other, "photo-b@x.com", role="employer")
    assert other.get(url).status_code == 200  # signed-in counterparty can view

def test_candidate_deletion_cascades_to_counterparty(app_):
    e, c, job, mid = _make_match(app_)
    c.post(f"/api/matches/{mid}/messages", json={"body": "before delete"})
    assert c.delete("/api/account").status_code == 200
    # employer side: match and its messages are gone, messages route 404s
    assert e.get("/api/matches").json == []
    assert e.get(f"/api/matches/{mid}/messages").status_code == 404
    # the candidate no longer appears in the employer's feed for the job
    feed = e.get(f"/api/employer/candidates?job_id={job['id']}").json
    assert all(x["email"] != "demo@swipehire.app" for x in feed["candidates"])

def test_employer_deletion_removes_jobs_and_matches(app_):
    e, c, job, mid = _make_match(app_)
    assert any(j["id"] == job["id"] for j in c.get("/api/discovery").json) is False  # already swiped
    assert e.delete("/api/account").status_code == 200
    assert c.get("/api/matches").json == []
    assert c.get(f"/api/matches/{mid}/messages").status_code == 404
    # cascade: applications/swipes scoped to the employer's jobs are deleted too
    assert c.get("/api/applications").json == []

# ---------------- 4. bad input: never 500 ----------------

NON_DICT_BODIES = [
    ("POST", "/api/auth/signup"), ("POST", "/api/auth/login"),
    ("POST", "/api/auth/code/request"), ("POST", "/api/auth/code/verify"),
]

@pytest.mark.parametrize("method,url", NON_DICT_BODIES)
def test_non_dict_json_body_public_endpoints(client, method, url):
    for payload in (["x"], "just-a-string", 42):
        r = getattr(client, method.lower())(url, json=payload)
        assert r.status_code in (400, 401), (url, payload, r.status_code)

def test_non_dict_json_body_authed_endpoints(app_):
    c = app_.test_client(); signup(c, "nd@x.com")
    for url in ("/api/swipe", "/api/candidate/preferences", "/api/connections", "/api/documents"):
        r = c.post(url, json=["x"])
        assert r.status_code in (400, 404), (url, r.status_code)
    e = app_.test_client(); signup(e, "nde@x.com", role="employer")
    assert e.post("/api/employer/jobs", json=["x"]).status_code == 400
    assert e.post("/api/employer/swipe", json=["x"]).status_code == 400

def test_signup_wrong_types_and_validation(client):
    assert client.post("/api/auth/signup", json={"email": 123, "password": "pw1234", "role": "candidate"}).status_code == 400
    assert client.post("/api/auth/signup", json={"email": "t@x.com", "password": 12345, "role": "candidate"}).status_code == 400
    assert client.post("/api/auth/signup", json={"email": "t@x.com", "password": "pw", "role": "candidate"}).status_code == 400  # too short
    assert client.post("/api/auth/signup", json={"email": "not-an-email", "password": "pw1234", "role": "candidate"}).status_code == 400
    assert client.post("/api/auth/signup", json={"email": "t@x.com", "password": "pw1234", "role": "admin"}).status_code == 400
    assert client.post("/api/auth/signup", json={"password": "pw1234"}).status_code == 400  # missing email

def test_login_wrong_types(client):
    signup(client, "lt@x.com")
    assert client.post("/api/auth/login", json={"email": 5, "password": "pw1234"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "lt@x.com", "password": 5}).status_code == 401
    assert client.post("/api/auth/login", json={}).status_code == 401

def test_preferences_wrong_types_do_not_corrupt(client):
    u = signup(client, "prefs@x.com")
    assert client.post("/api/candidate/preferences", json={"min_salary": "abc"}).status_code == 400
    assert client.post("/api/candidate/preferences", json={"target_titles": "Backend Developer"}).status_code == 400
    assert client.post("/api/candidate/preferences", json={"locations": {"city": "Montreal"}}).status_code == 400
    # valid update still works and discovery is healthy afterwards
    r = client.post("/api/candidate/preferences", json={"min_salary": "90000", "target_titles": ["Backend Developer"]})
    assert r.status_code == 200 and r.json["min_salary"] == 90000
    assert client.get("/api/discovery").status_code == 200

def test_employer_job_wrong_types_do_not_poison_feeds(app_):
    e = app_.test_client(); signup(e, "poison@x.com", role="employer", company="PCo")
    give_media(e)  # v9: so the one GOOD post below can pass the media rule; the bad ones fail on their own defects first
    assert e.post("/api/employer/jobs", json={"title": "Bad Salary", "salary_max": "xyz"}).status_code == 400
    assert e.post("/api/employer/jobs", json={"title": "Bad Salary 2", "salary_min": [1]}).status_code == 400
    assert e.post("/api/employer/jobs", json={"title": 42}).status_code == 400
    assert e.post("/api/employer/jobs", json={"title": ["x"]}).status_code == 400
    assert e.post("/api/employer/jobs", json={"title": "Bad Tags", "tags": "Python"}).status_code == 400
    assert e.post("/api/employer/jobs", json={"title": ""}).status_code == 400
    assert e.get("/api/employer/jobs").json == []  # nothing was stored
    # numeric strings are accepted and coerced
    ok = e.post("/api/employer/jobs", json={"title": "Good Job", "salary_min": "80000", "salary_max": "100000"})
    assert ok.status_code == 201 and ok.json["salary_max"] == 100000
    # a candidate with a salary floor can still load discovery (the old 500)
    c = app_.test_client(); signup(c, "victim@x.com")
    c.post("/api/candidate/preferences", json={"min_salary": 50000})
    assert c.get("/api/discovery").status_code == 200

def test_swipe_bad_ids_and_direction(client):
    signup(client, "sw@x.com")
    job = client.get("/api/discovery").json[0]
    assert client.post("/api/swipe", json={"job_id": "abc", "direction": "right"}).status_code == 404
    assert client.post("/api/swipe", json={"job_id": [job["id"]], "direction": "right"}).status_code == 404
    assert client.post("/api/swipe", json={"job_id": HUGE_ID, "direction": "right"}).status_code == 404
    assert client.post("/api/swipe", json={"job_id": -5, "direction": "right"}).status_code == 404
    assert client.post("/api/swipe", json={"job_id": job["id"], "direction": "up"}).status_code == 400
    assert client.post("/api/swipe", json={"job_id": job["id"], "direction": 1}).status_code == 400
    assert client.post("/api/swipe", json={}).status_code == 404
    # and a well-formed swipe still works after all that
    assert client.post("/api/swipe", json={"job_id": job["id"], "direction": "left"}).status_code == 200

def test_huge_and_negative_path_ids_404(client):
    signup(client, "huge@x.com")
    assert client.delete(f"/api/documents/{HUGE_ID}").status_code == 404
    assert client.get(f"/api/applications/{HUGE_ID}/tailored-resume").status_code == 404
    assert client.get(f"/api/matches/{HUGE_ID}/messages").status_code == 404
    assert client.get("/api/applications/-1/tailored-resume").status_code == 404
    assert client.get("/api/matches/-1/messages").status_code == 404

def test_employer_candidates_huge_job_id(app_):
    e = app_.test_client(); signup(e, "huge-e@x.com", role="employer")
    assert e.get(f"/api/employer/candidates?job_id={HUGE_ID}").status_code == 404
    assert e.get("/api/employer/candidates?job_id=abc").status_code == 200  # unparseable -> falls back to latest job (none)

def test_documents_wrong_types(client):
    signup(client, "docs@x.com")
    assert client.post("/api/documents", json={"doc_type": "resume", "text": 123}).status_code == 400
    assert client.post("/api/documents", json={"doc_type": "resume", "text": None}).status_code == 400
    assert client.post("/api/documents", json={"doc_type": "bogus", "text": "hello"}).status_code == 400
    assert client.post("/api/documents", json={"doc_type": "resume", "text": "   "}).status_code == 400
    assert client.post("/api/resume", json={"text": 123}).status_code == 400
    assert client.post("/api/resume", json={"text": ""}).status_code == 400

def test_connections_wrong_types(client):
    signup(client, "conn@x.com")
    assert client.post("/api/connections", json={"board": 123, "identifier": "x"}).status_code == 400
    assert client.post("/api/connections", json={"board": "linkedin", "identifier": 42}).status_code == 400
    assert client.post("/api/connections", json={"board": "linkedin"}).status_code == 400
    assert client.get("/api/connections").json["connections"] == []

# ---------------- 5. auth abuse ----------------

def test_code_for_one_email_cannot_verify_another(app_, client, monkeypatch):
    from app import mailer
    mailer.LAST_SENT.clear()
    monkeypatch.setenv("SWIPEHIRE_MAIL_CAPTURE", "1")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    import re
    client.post("/api/auth/code/request", json={"email": "owner@x.com"})
    code = re.search(r"\b(\d{6})\b", mailer.LAST_SENT[-1]["text"]).group(1)
    # the same code presented for a different email must not sign anyone in
    r = client.post("/api/auth/code/verify", json={"email": "attacker@x.com", "code": code})
    assert r.status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    # and the rightful flow still works
    r = client.post("/api/auth/code/verify", json={"email": "owner@x.com", "code": code})
    assert r.status_code in (200, 201)

def test_code_verify_wrong_types(client):
    assert client.post("/api/auth/code/verify", json={"email": "demo@swipehire.app", "code": None}).status_code == 401
    assert client.post("/api/auth/code/verify", json={"email": None, "code": "123456"}).status_code == 401
    assert client.post("/api/auth/code/request", json={"email": "no-at-sign"}).status_code == 400
    assert client.post("/api/auth/code/request", json={"email": 42}).status_code == 400

# ---------------- 6. upload abuse ----------------

def test_photo_upload_abuse(client):
    signup(client, "up@x.com")
    mp = "multipart/form-data"
    assert client.post("/api/profile/photo", data={"file": (io.BytesIO(b"GIF89a" + b"\x00" * 32), "x.gif")}, content_type=mp).status_code == 400
    assert client.post("/api/profile/photo", data={"file": (io.BytesIO(b"plain text"), "x.png")}, content_type=mp).status_code == 400
    assert client.post("/api/profile/photo", data={"file": (io.BytesIO(b""), "x.png")}, content_type=mp).status_code == 400
    assert client.post("/api/profile/photo", data={}, content_type=mp).status_code == 400
    # real PNG header but over the 3MB app limit (under the 8MB request cap)
    big = PNG_BYTES + b"\x00" * (3 * 1024 * 1024)
    assert client.post("/api/profile/photo", data={"file": (io.BytesIO(big), "big.png")}, content_type=mp).status_code == 413

def test_document_upload_abuse(client):
    signup(client, "dup@x.com")
    mp = "multipart/form-data"
    assert client.post("/api/documents", data={"doc_type": "resume", "file": (io.BytesIO(b"MZ\x90\x00"), "evil.exe")}, content_type=mp).status_code == 400
    assert client.post("/api/documents", data={"doc_type": "resume", "file": (io.BytesIO(b""), "empty.txt")}, content_type=mp).status_code == 400
    # a text file renamed .pdf must not crash the parser (graceful fallback)
    fake_pdf = b"Pat Fake\nPython developer with 6 years of experience.\nSKILLS\nPython, SQL, Flask"
    r = client.post("/api/documents", data={"doc_type": "resume", "file": (io.BytesIO(fake_pdf), "fake.pdf")}, content_type=mp)
    assert r.status_code == 201
    # over the 8MB request cap -> 413 from Flask, never 500
    huge = b"a" * (9 * 1024 * 1024)
    assert client.post("/api/documents", data={"doc_type": "resume", "file": (io.BytesIO(huge), "huge.txt")}, content_type=mp).status_code == 413

# ---------------- 7. injection / XSS payloads are data, not code ----------------

SQLI = "' OR '1'='1' --"
XSS = "<script>alert('xss')</script>"
XSS_IMG = "<img src=x onerror=alert(1)>"

def test_sqli_no_auth_bypass(client):
    assert client.post("/api/auth/login", json={"email": SQLI, "password": "x"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "demo@swipehire.app", "password": SQLI}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "demo@swipehire.app' --", "password": "demo"}).status_code == 401
    # DB is intact afterwards: the real demo login still works
    assert client.post("/api/auth/login", json={"email": "demo@swipehire.app", "password": "demo"}).status_code == 200

def test_sqli_strings_stored_as_plain_data(app_):
    c = app_.test_client()
    u = signup(c, "sqli@x.com", name="Robert'); DROP TABLE users;--")
    assert u["candidate"]["name"] == "Robert'); DROP TABLE users;--"
    # users table survived: demo accounts endpoint and a fresh login both work
    assert c.get("/api/demo-accounts").status_code == 200
    c2 = app_.test_client()
    assert c2.post("/api/auth/login", json={"email": "demo@swipehire.app", "password": "demo"}).status_code == 200

def test_xss_payloads_roundtrip_as_json_strings(app_):
    c = app_.test_client()
    u = signup(c, "xss@x.com", name=XSS)
    me = c.get("/api/auth/me")
    assert me.content_type.startswith("application/json")
    assert me.json["candidate"]["name"] == XSS  # verbatim data in JSON; UI escapes via esc() in app.js
    e = app_.test_client(); signup(e, "xss-e@x.com", role="employer", company=XSS_IMG)
    give_media(e)  # v9: posting requires recruiter media (or job media)
    job = e.post("/api/employer/jobs", json={"title": XSS, "description": XSS_IMG, "tags": [XSS]}).json
    assert job["title"] == XSS
    feed = c.get("/api/discovery").json
    hit = [j for j in feed if j["id"] == job["id"]]
    assert hit and hit[0]["title"] == XSS  # candidate feed carries it as data too

def test_xss_in_message_body_roundtrips(app_):
    e, c, job, mid = _make_match(app_)
    r = c.post(f"/api/matches/{mid}/messages", json={"body": XSS})
    assert r.status_code == 201 and r.json["body"] == XSS
    msgs = e.get(f"/api/matches/{mid}/messages").json
    assert msgs[-1]["body"] == XSS

# ---------------- 8. extension API auth ----------------

def test_ext_endpoints_reject_session_cookie_and_bad_tokens(app_):
    c = app_.test_client(); signup(c, "ext@x.com")
    # a valid web session is NOT extension auth
    assert c.get("/api/ext/queue").status_code == 401
    assert c.post("/api/ext/report", json={"application_id": 1, "event": "opened"}).status_code == 401
    # wrong / malformed tokens
    assert c.get("/api/ext/queue", headers={"Authorization": "Bearer shx_wrong"}).status_code == 401
    assert c.get("/api/ext/queue", headers={"Authorization": "shx_wrong"}).status_code == 401
    assert c.get("/api/ext/queue", headers={"X-Extension-Token": "shx_wrong"}).status_code == 401
    # the real token works via either header
    tok = c.post("/api/ext/pairing-token").json["token"]
    assert c.get("/api/ext/queue", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert c.get("/api/ext/queue", headers={"X-Extension-Token": tok}).status_code == 200

def test_ext_report_bad_input_with_valid_token(app_):
    c = app_.test_client(); signup(c, "ext2@x.com")
    tok = c.post("/api/ext/pairing-token").json["token"]
    H = {"Authorization": f"Bearer {tok}"}
    j = _insert_job(app_, "https://www.linkedin.com/jobs/view/adv1", title="Adv Linked")
    aid = c.post("/api/swipe", json={"job_id": j["id"], "direction": "right"}).json["application"]["id"]
    assert c.post("/api/ext/report", headers=H, json=["x"]).status_code == 400
    assert c.post("/api/ext/report", headers=H, json={"application_id": [aid], "event": "opened"}).status_code == 404
    assert c.post("/api/ext/report", headers=H, json={"application_id": "abc", "event": "opened"}).status_code == 404
    assert c.post("/api/ext/report", headers=H, json={"application_id": HUGE_ID, "event": "opened"}).status_code == 404
    assert c.post("/api/ext/report", headers=H, json={"application_id": aid, "event": 42}).status_code == 400
    # non-string note is dropped, not fatal
    r = c.post("/api/ext/report", headers=H, json={"application_id": aid, "event": "failed", "note": 5})
    assert r.status_code == 200 and r.json["status"] == "needs_review"
    # honest-status guard still holds: a 'filled' report never claims submitted
    r = c.post("/api/ext/report", headers=H, json={"application_id": aid, "event": "filled"})
    assert r.status_code == 200 and r.json["status"] != "submitted"
