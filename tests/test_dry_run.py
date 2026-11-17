"""Dry-run mode (server side): a verified_dry_run report stores the
per-field report and must NEVER mark the application submitted."""
import json

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


def _seed_app(client, app_, url="https://boards.greenhouse.io/acme/jobs/900"):
    signup(client)
    j = _insert_job(app_, url)
    aid = client.post("/api/swipe", json={"job_id": j["id"], "direction": "right"}).json["application"]["id"]
    return aid, _pair(client)


def test_verified_dry_run_stores_report_and_never_submits(client, app_):
    aid, tok = _seed_app(client, app_)
    report = {"fields": [{"label": "Email", "status": "filled", "detail": "ada@x.com"},
                         {"label": "Cover letter", "status": "missed", "detail": "no stored answer"}],
              "would_submit": True}
    rr = client.post("/api/ext/report", headers=_auth(tok),
                     json={"application_id": aid, "event": "verified_dry_run",
                           "note": "Dry-run verified: 1 filled, 1 missed.", "report": report})
    assert rr.status_code == 200, rr.data
    assert rr.json["status"] != "submitted"
    assert rr.json["status"] in ("prepared", "queued_extension")
    stored = json.loads(rr.json["dry_run_report"])
    assert stored["fields"][0]["label"] == "Email"
    assert stored["fields"][1]["status"] == "missed"
    assert "NOT submitted" in rr.json["proof"]
    assert "Submitted — confirmed" not in rr.json["proof"]


def test_verified_dry_run_tolerates_missing_report(client, app_):
    aid, tok = _seed_app(client, app_)
    rr = client.post("/api/ext/report", headers=_auth(tok),
                     json={"application_id": aid, "event": "verified_dry_run", "note": "ok"})
    assert rr.status_code == 200, rr.data
    assert rr.json["status"] != "submitted"
    assert json.loads(rr.json["dry_run_report"]) == {}


def test_dry_run_does_not_touch_submission_state_machine(client, app_):
    aid, tok = _seed_app(client, app_)
    client.post("/api/ext/report", headers=_auth(tok),
                json={"application_id": aid, "event": "verified_dry_run", "report": {"fields": []}})
    row = client.get("/api/applications").json
    mine = [a for a in row if a["id"] == aid][0]
    assert mine["ext_state"] in ("", None)
    # and the job is still in the extension queue afterwards
    q = client.get("/api/ext/queue", headers=_auth(tok)).json["queue"]
    assert any(x["application_id"] == aid for x in q)
