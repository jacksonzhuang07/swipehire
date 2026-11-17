"""Phase 2 hardening (server side): answer bank, crash recovery /
no-double-apply state machine, submitted_unverified, board health."""
import pytest
from app import create_app
from app.answers import normalize_question


@pytest.fixture()
def app_(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db"), testing=True)


@pytest.fixture()
def client(app_):
    return app_.test_client()


def signup(client, email="cand@x.com", name="Ada Lovelace"):
    r = client.post("/api/auth/signup", json={"email": email, "password": "pw1234", "role": "candidate", "name": name, "company": ""})
    assert r.status_code == 201, r.data
    return r


def _insert_job(app_, url, title="Net Job", source="greenhouse"):
    import zlib
    from app.db import get_conn
    from app.job_sources import insert_jobs
    conn = get_conn(app_.config["DB_PATH"])
    insert_jobs(conn, [{"external_id": f"ext-{zlib.crc32((url + title).encode())}", "title": title, "company": "NetCo",
                        "location": "Remote", "remote": 1, "salary_min": None, "salary_max": None,
                        "salary_text": "", "description": "Python Flask SQL role", "url": url,
                        "source": source, "tags": ["Python", "Flask", "SQL"], "posted_at": ""}])
    row = conn.execute("SELECT * FROM jobs WHERE url=? ORDER BY id DESC", (url,)).fetchone()
    d = dict(row)
    conn.close()
    return d


def _pair(client):
    r = client.post("/api/ext/pairing-token")
    assert r.status_code == 201, r.data
    return r.json["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _report(client, tok, aid, event, **kw):
    body = {"application_id": aid, "event": event}
    body.update(kw)
    return client.post("/api/ext/report", headers=_auth(tok), json=body)


def _queue_ids(client, tok):
    r = client.get("/api/ext/queue", headers=_auth(tok))
    assert r.status_code == 200, r.data
    return [x["application_id"] for x in r.json["queue"]]


def _seed_app(client, app_, url):
    j = _insert_job(app_, url)
    return client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"}).json["application"]["id"]


# ---------------- answer bank ----------------

def test_normalize_question_synonym_variants_converge():
    a = normalize_question("Are you authorized to work in Canada?")
    b = normalize_question("Legally entitled to work here?")
    assert a == b == "workauth"
    c = normalize_question("Will you now or in the future require sponsorship?")
    d = normalize_question("Do you need sponsorship to work?")
    assert c == d == "sponsorship"
    e = normalize_question("What is your expected salary?")
    assert "salaryexpectation" in e


def test_answer_bank_roundtrip_and_variant_lookup(client, app_):
    signup(client)
    r = client.post("/api/candidate/answer-bank",
                    json={"question": "Are you authorized to work in Canada?", "answer": "Yes"})
    assert r.status_code == 201, r.data
    tok = _pair(client)
    hit = client.post("/api/ext/answer-lookup", headers=_auth(tok),
                      json={"question": "Legally entitled to work here"})
    assert hit.status_code == 200 and hit.json["found"] is True
    assert hit.json["answer"] == "Yes"
    miss = client.post("/api/ext/answer-lookup", headers=_auth(tok),
                       json={"question": "What is your favourite colour?"})
    assert miss.json["found"] is False
    rows = client.get("/api/candidate/answer-bank").json["answers"]
    assert len(rows) == 1 and rows[0]["answer"] == "Yes"


def test_answer_bank_requires_question_and_answer(client):
    signup(client)
    r = client.post("/api/candidate/answer-bank", json={"question": "", "answer": "Yes"})
    assert r.status_code == 400


# ---------------- crash recovery / no double-apply ----------------

def test_submit_clicked_is_never_requeued_and_parks_after_lease_expiry(client, app_):
    signup(client)
    tok = _pair(client)
    aid = _seed_app(client, app_, "https://boards.greenhouse.io/acme/jobs/910")
    assert aid in _queue_ids(client, tok)
    _report(client, tok, aid, "opened")
    _report(client, tok, aid, "filling")
    _report(client, tok, aid, "submit_clicked")
    # Browser "crashes" here: no outcome ever reported. The job must NOT
    # be handed out again while the lease runs…
    assert aid not in _queue_ids(client, tok)
    # …and once the lease expires it converts to needs_human, still not queued.
    from app.db import get_conn
    conn = get_conn(app_.config["DB_PATH"])
    conn.execute("UPDATE applications SET ext_claim_expires_at=datetime('now','-1 minute') WHERE id=?", (aid,))
    conn.commit(); conn.close()
    assert aid not in _queue_ids(client, tok)
    mine = [a for a in client.get("/api/applications").json if a["id"] == aid][0]
    assert mine["status"] == "needs_human"
    assert any("duplicate protection" in n for n in mine["needs_review"])


def test_active_claim_lease_excludes_from_queue(client, app_):
    signup(client)
    tok = _pair(client)
    aid = _seed_app(client, app_, "https://boards.greenhouse.io/acme/jobs/911")
    _report(client, tok, aid, "opened")
    # A second browser polling the queue must not double-claim it.
    assert aid not in _queue_ids(client, tok)


def test_submitted_unverified_state(client, app_):
    signup(client)
    tok = _pair(client)
    aid = _seed_app(client, app_, "https://boards.greenhouse.io/acme/jobs/912")
    _report(client, tok, aid, "opened")
    _report(client, tok, aid, "submit_clicked")
    rr = _report(client, tok, aid, "submitted_unverified", note="no confirmation text")
    assert rr.status_code == 200, rr.data
    assert rr.json["status"] == "submitted_unverified"
    assert rr.json["ext_state"] == "submit_clicked"
    assert aid not in _queue_ids(client, tok)


def test_confirmed_state_after_submitted(client, app_):
    signup(client)
    tok = _pair(client)
    aid = _seed_app(client, app_, "https://boards.greenhouse.io/acme/jobs/913")
    _report(client, tok, aid, "opened")
    _report(client, tok, aid, "submit_clicked")
    rr = _report(client, tok, aid, "submitted", note="Thank you for applying")
    assert rr.json["status"] == "submitted" and rr.json["ext_state"] == "confirmed"


# ---------------- board health ----------------

def test_board_auto_disables_after_repeated_failures(client, app_):
    signup(client)
    tok = _pair(client)
    # 10 decisive outcomes on greenhouse: 8 failed, 2 submitted -> 20% < 30%
    for i in range(10):
        aid = _seed_app(client, app_, f"https://boards.greenhouse.io/acme/jobs/92{i}")
        _report(client, tok, aid, "opened")
        _report(client, tok, aid, "submitted" if i < 2 else "failed", note="boom")
    health = client.get("/api/ext/board-health", headers=_auth(tok)).json
    assert "greenhouse" in health["disabled"], health
    # A fresh queued greenhouse job now parks instead of being handed out.
    aid = _seed_app(client, app_, "https://boards.greenhouse.io/acme/jobs/935")
    assert aid not in _queue_ids(client, tok)
    mine = [a for a in client.get("/api/applications").json if a["id"] == aid][0]
    assert mine["status"] == "needs_human"
    assert any("auto-disabled" in n for n in mine["needs_review"])
    # Re-enable clears it.
    r = client.post("/api/candidate/board-health/reenable", json={"board": "greenhouse"})
    assert r.status_code == 200 and "greenhouse" not in r.json["disabled"]


def test_board_stays_enabled_when_healthy(client, app_):
    signup(client)
    tok = _pair(client)
    for i in range(10):
        aid = _seed_app(client, app_, f"https://jobs.lever.co/acme/93{i}")
        _report(client, tok, aid, "opened")
        _report(client, tok, aid, "submitted", note="ok")
    health = client.get("/api/ext/board-health", headers=_auth(tok)).json
    assert "lever" not in health["disabled"]
    assert health["boards"]["lever"]["rate"] == 1.0
