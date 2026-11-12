"""v6 autonomous-extension tests: needs_human parking, filling progress,
opened-claim promotion, queue payload (tailored resume + structured
answers), and queue eligibility for prepared ATS-form jobs."""
import pytest
from app import create_app

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

def _insert_job(app_, url, title="Net Job", source="testsrc"):
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

def _queue(client, tok):
    r = client.get("/api/ext/queue", headers=_auth(tok))
    assert r.status_code == 200, r.data
    return r.json["queue"]

def test_needs_human_parks_with_reason(client, app_):
    signup(client)
    j = _insert_job(app_, "https://www.linkedin.com/jobs/view/601", title="Park Me")
    aid = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"}).json["application"]["id"]
    tok = _pair(client)
    reason = "A CAPTCHA is on this page. The extension never solves CAPTCHAs."
    rr = client.post("/api/ext/report", headers=_auth(tok),
                     json={"application_id": aid, "event": "needs_human", "note": reason})
    assert rr.status_code == 200, rr.data
    assert rr.json["status"] == "needs_human"
    assert reason in rr.json["needs_review"]
    assert reason in rr.json["proof"]
    # parked jobs leave the extension queue
    assert all(x["application_id"] != aid for x in _queue(client, tok))
    # tracker shows it too
    apps = client.get("/api/applications").json
    mine = [a for a in apps if a["id"] == aid]
    assert mine and mine[0]["status"] == "needs_human"

def test_filling_event_is_progress_only(client, app_):
    signup(client)
    j = _insert_job(app_, "https://www.linkedin.com/jobs/view/602", title="Fill Me")
    r = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"})
    aid = r.json["application"]["id"]
    tok = _pair(client)
    _queue(client, tok)  # token use -> connected -> still prepared until claimed
    rr = client.post("/api/ext/report", headers=_auth(tok),
                     json={"application_id": aid, "event": "filling", "note": "driver filling"})
    assert rr.status_code == 200 and rr.json["status"] == "prepared"
    assert "filling" in rr.json["proof"]

def test_opened_promotes_prepared_to_queued(client, app_):
    signup(client)
    j = _insert_job(app_, "https://boards.greenhouse.io/acme/jobs/603", title="Claim Me")
    aid = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"}).json["application"]["id"]
    tok = _pair(client)
    rr = client.post("/api/ext/report", headers=_auth(tok),
                     json={"application_id": aid, "event": "opened"})
    assert rr.status_code == 200 and rr.json["status"] == "queued_extension"

def test_queue_includes_ats_form_and_payload_is_complete(client, app_):
    signup(client)
    client.post("/api/documents", json={"doc_type": "resume", "title": "R",
        "text": "Ada Lovelace\nada@x.com\nPython developer with 6 years experience\nSkills: Python, Flask, SQL"})
    client.post("/api/candidate/preferences", json={"min_salary": 95000})
    j = _insert_job(app_, "https://boards.greenhouse.io/acme/jobs/604", title="ATS Role")
    r = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"})
    assert r.json["application"]["status"] == "prepared" and r.json["application"]["method"] == "external_link"
    tok = _pair(client)
    items = [x for x in _queue(client, tok) if x["application_id"] == r.json["application"]["id"]]
    assert items, "prepared ATS-form application must be in the extension queue (form-executor role)"
    item = items[0]
    # tailored resume rides along for the driver's file attachment
    assert item["tailored_resume"] and len(item["tailored_resume"]) > 40
    assert "Python" in item["tailored_resume"]
    # structured answers: split names + stored salary fact, facts only
    a = item["answers"]
    assert a["full_name"] == "Ada Lovelace" and a["first_name"] == "Ada" and a["last_name"] == "Lovelace"
    assert a["email"] == "cand@x.com"  # the account's stored email is the fact used
    assert a["salary_expectation"] == "95000"
    assert a["years_of_experience"] == "6.0" or a["years_of_experience"] == "6"

def test_needs_human_requires_own_token(client, app_):
    signup(client, "owner@x.com")
    j = _insert_job(app_, "https://www.linkedin.com/jobs/view/605", title="Owner Role")
    aid = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"}).json["application"]["id"]
    other = app_.test_client()
    signup(other, "other@x.com")
    tok_other = _pair(other)
    assert other.post("/api/ext/report", headers=_auth(tok_other),
                      json={"application_id": aid, "event": "needs_human", "note": "x"}).status_code == 404
    assert client.post("/api/ext/report", json={"application_id": aid, "event": "needs_human"}).status_code == 401
    tok = _pair(client)
    assert client.post("/api/ext/report", headers=_auth(tok),
                       json={"application_id": aid, "event": "needs_human", "note": "ok"}).status_code == 200

def test_health_version_6(client):
    assert client.get("/api/health").json["version"] == 8
