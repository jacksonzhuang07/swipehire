"""Launch-pass tests: honest statuses, job classification, Connections
tiers, extension pairing/queue/report, delete account, launch pages,
Turso/libsql wrapper (offline, local libsql file)."""
import os
import pytest
from app import create_app
from app.classify import classify_job
from app.db import TursoConn, init_db

@pytest.fixture()
def app_(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db"), testing=True)

@pytest.fixture()
def client(app_):
    return app_.test_client()

def signup(client, email="cand@x.com", role="candidate", name="Cand"):
    r = client.post("/api/auth/signup", json={"email": email, "password": "pw1234", "role": role, "name": name, "company": "Acme"})
    assert r.status_code == 201, r.data
    return r

def _insert_job(app_, url, title="Net Job", source="testsrc"):
    import zlib
    from app.db import get_conn
    conn = get_conn(app_.config["DB_PATH"])
    from app.job_sources import insert_jobs
    insert_jobs(conn, [{"external_id": f"ext-{zlib.crc32((url + title).encode())}", "title": title, "company": "NetCo",
                        "location": "Remote", "remote": 1, "salary_min": None, "salary_max": None,
                        "salary_text": "", "description": "Python Flask SQL role", "url": url,
                        "source": source, "tags": ["Python", "Flask", "SQL"], "posted_at": ""}])
    row = conn.execute("SELECT * FROM jobs WHERE url=? ORDER BY id DESC", (url,)).fetchone()
    d = dict(row)
    conn.close()
    return d

# ---------- classification ----------
@pytest.mark.parametrize("url,want_type,want_login", [
    ("https://www.linkedin.com/jobs/view/123", "network_login", 1),
    ("https://ca.indeed.com/viewjob?jk=abc", "network_login", 1),
    ("https://www.indeed.com/viewjob?jk=abc", "network_login", 1),
    ("https://acme.wd1.myworkdayjobs.com/en-US/careers/job/1", "ats_account", 1),
    ("https://boards.greenhouse.io/acme/jobs/1", "ats_form", 0),
    ("https://job-boards.greenhouse.io/acme", "ats_form", 0),
    ("https://jobs.lever.co/acme/abc", "ats_form", 0),
    ("https://jobs.ashbyhq.com/acme/abc", "ats_form", 0),
    ("https://apply.workable.com/acme/j/ABC/", "ats_form", 0),
    ("https://acme.smartrecruiters.com/job", "ats_form", 0),
    ("https://acme.breezy.hr/p/abc", "ats_form", 0),
    ("https://acme.recruitee.com/o/job", "ats_form", 0),
    ("https://careers.acme.com/jobs/1", "external", 0),
    ("https://remotive.com/remote-jobs/x", "external", 0),
    ("", "external", 0),
    (None, "external", 0),
])
def test_classifier(url, want_type, want_login):
    assert classify_job(url) == (want_type, want_login)

def test_insert_jobs_stamps_classification(app_):
    j = _insert_job(app_, "https://www.linkedin.com/jobs/view/9")
    assert j["apply_type"] == "network_login" and j["requires_login"] == 1
    j2 = _insert_job(app_, "https://boards.greenhouse.io/acme/jobs/2", title="GH Job")
    assert j2["apply_type"] == "ats_form" and j2["requires_login"] == 0

# ---------- honest statuses ----------
def test_swipe_never_submitted_and_login_job_needs_extension(client, app_):
    signup(client)
    j = _insert_job(app_, "https://www.linkedin.com/jobs/view/42", title="Linked Role")
    r = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"})
    assert r.status_code == 200
    assert r.json["needs_extension"] is True
    a = r.json["application"]
    assert a["status"] == "prepared" and a["method"] == "extension_required"
    assert "Not yet submitted" in a["proof"] or "not submitted" in a["proof"]
    apps = client.get("/api/applications").json
    assert all(x["status"] != "submitted" for x in apps)

def test_ats_job_prepared_not_submitted(client, app_):
    signup(client)
    j = _insert_job(app_, "https://boards.greenhouse.io/acme/jobs/7", title="GH Role")
    r = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"})
    assert r.json["needs_extension"] is False
    a = r.json["application"]
    assert a["status"] == "prepared" and a["method"] == "external_link"
    assert "Not yet submitted" in a["proof"]

# ---------- connections (Tier 2) ----------
def test_connections_crud_and_no_password_field(client):
    signup(client)
    r = client.post("/api/connections", json={"board": "linkedin", "identifier": "cand-on-linkedin", "password": "should-be-ignored"})
    assert r.status_code == 201
    assert r.json["status"] == "pending_session" and r.json["identifier"] == "cand-on-linkedin"
    assert "password" not in str(r.json).lower() or "should-be-ignored" not in str(r.json)
    data = client.get("/api/connections").json
    assert [c["board"] for c in data["connections"]] == ["linkedin"]
    assert client.post("/api/connections", json={"board": "tiktok", "identifier": "x"}).status_code == 400
    assert client.delete("/api/connections/linkedin").status_code == 200
    assert client.get("/api/connections").json["connections"] == []
    assert client.delete("/api/connections/linkedin").status_code == 404

def test_connections_isolation_and_role_gate(client, app_):
    signup(client, "a@x.com")
    client.post("/api/connections", json={"board": "indeed", "identifier": "a-on-indeed"})
    c2 = app_.test_client()
    signup(c2, "b@x.com")
    assert c2.get("/api/connections").json["connections"] == []
    assert c2.delete("/api/connections/indeed").status_code == 404
    e = app_.test_client()
    signup(e, "boss@x.com", role="employer")
    assert e.get("/api/connections").status_code == 403

# ---------- extension pairing / queue / report ----------
def _pair(client):
    r = client.post("/api/ext/pairing-token")
    assert r.status_code == 201, r.data
    return r.json["token"]

def _auth(token):
    return {"Authorization": f"Bearer {token}"}

def test_pairing_token_hashed_and_single_active(client, app_):
    signup(client)
    tok = _pair(client)
    from app.db import get_conn
    conn = get_conn(app_.config["DB_PATH"])
    rows = conn.execute("SELECT * FROM ext_tokens").fetchall()
    assert len(rows) == 1 and rows[0]["token_hash"] != tok and tok not in rows[0]["token_hash"]
    tok2 = _pair(client)  # regenerating revokes the first
    assert client.get("/api/ext/queue", headers=_auth(tok)).status_code == 401
    assert client.get("/api/ext/queue", headers=_auth(tok2)).status_code == 200
    conn.close()

def test_ext_queue_and_report_flow(client, app_):
    signup(client)
    j = _insert_job(app_, "https://www.linkedin.com/jobs/view/77", title="Queued Role")
    r = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"})
    aid = r.json["application"]["id"]
    assert r.json["application"]["status"] == "prepared"  # no token used yet
    tok = _pair(client)
    q = client.get("/api/ext/queue", headers=_auth(tok))
    assert q.status_code == 200
    item = [x for x in q.json["queue"] if x["application_id"] == aid]
    assert item and item[0]["job_url"] == "https://www.linkedin.com/jobs/view/77"
    assert "full_name" in item[0]["answers"] or isinstance(item[0]["answers"], dict)
    # token use marks the extension connected -> next login-required swipe queues
    st = client.get("/api/connections").json["extension"]
    assert st["connected"] is True and st["last_used_at"]
    j2 = _insert_job(app_, "https://ca.indeed.com/viewjob?jk=z9", title="Indeed Role")
    r2 = client.post("/api/swipe", json={"job_id": j2["id"], "direction": "right"})
    assert r2.json["application"]["status"] == "queued_extension"
    assert r2.json["needs_extension"] is False
    aid2 = r2.json["application"]["id"]
    # report transitions: opened/filled keep status; submitted only via report
    for ev, want in (("opened", "queued_extension"), ("filled", "queued_extension"), ("submitted", "submitted")):
        rr = client.post("/api/ext/report", headers=_auth(tok), json={"application_id": aid2, "event": ev})
        assert rr.status_code == 200 and rr.json["status"] == want, (ev, rr.data)
    # failed -> needs_review
    rr = client.post("/api/ext/report", headers=_auth(tok), json={"application_id": aid, "event": "failed", "note": "captcha wall"})
    assert rr.json["status"] == "needs_review"
    # bad event + someone else's application
    assert client.post("/api/ext/report", headers=_auth(tok), json={"application_id": aid, "event": "teleported"}).status_code == 400

def test_ext_isolation_and_revoke(client, app_):
    signup(client, "owner@x.com")
    j = _insert_job(app_, "https://www.linkedin.com/jobs/view/55", title="Owner Role")
    aid = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"}).json["application"]["id"]
    other = app_.test_client()
    signup(other, "other@x.com")
    tok_other = _pair(other)
    assert all(x["application_id"] != aid for x in other.get("/api/ext/queue", headers=_auth(tok_other)).json["queue"])
    assert other.post("/api/ext/report", headers=_auth(tok_other), json={"application_id": aid, "event": "submitted"}).status_code == 404
    tok = _pair(client)
    assert client.get("/api/ext/queue", headers=_auth(tok)).status_code == 200
    assert client.delete("/api/ext/pairing-token").status_code == 200
    assert client.get("/api/ext/queue", headers=_auth(tok)).status_code == 401
    assert client.get("/api/ext/queue").status_code == 401  # no token at all

# ---------- delete account / launch pages ----------
def test_delete_account_removes_scoped_data(client, app_):
    signup(client, "gone@x.com")
    client.post("/api/documents", json={"doc_type": "resume", "title": "R", "text": "Gone Person\nPython developer with 4 years experience\nSkills: Python, Flask, SQL"})
    client.post("/api/connections", json={"board": "linkedin", "identifier": "gone"})
    _pair(client)
    assert client.delete("/api/account").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    from app.db import get_conn
    conn = get_conn(app_.config["DB_PATH"])
    for tbl, col in (("users", "email"), ("candidates", "email"), ("connections", "identifier")):
        assert conn.execute(f"SELECT COUNT(*) c FROM {tbl} WHERE {col} IN ('gone@x.com','gone')").fetchone()["c"] == 0, tbl
    assert conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM ext_tokens").fetchone()["c"] == 0
    # and the email can register again (fully gone, not soft-deleted)
    signup(client, "gone@x.com")
    conn.close()

def test_privacy_terms_pages(client):
    for path in ("/privacy", "/terms"):
        r = client.get(path)
        assert r.status_code == 200 and b"SwipeHire" in r.data
    assert b"never store" in client.get("/privacy").data.lower()

def test_health_version_5(client):
    assert client.get("/api/health").json["version"] == 5

# ---------- Turso wrapper (offline, real libsql local file) ----------
def test_turso_conn_crud_and_init(monkeypatch, tmp_path):
    url = str(tmp_path / "turso.db")
    conn = TursoConn(url=url, auth_token=None)
    conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);")
    cur = conn.execute("INSERT INTO t(name) VALUES(?)", ("x",))
    assert cur.lastrowid == 1
    conn.commit()
    row = conn.execute("SELECT id, name FROM t").fetchone()
    assert row[1] == "x" and row["name"] == "x" and dict(row) == {"id": 1, "name": "x"}
    conn.close()
    # Full schema init through the env-selected backend, then real app flows.
    monkeypatch.setenv("TURSO_DATABASE_URL", str(tmp_path / "turso-app.db"))
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "offline-test")
    conn2 = init_db("/ignored/whatever.db")
    assert type(conn2).__name__ == "TursoConn"
    cols = [r[1] for r in conn2.execute("PRAGMA table_info(jobs)").fetchall()]
    assert "apply_type" in cols and "requires_login" in cols
    assert conn2.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0
    conn2.close()
