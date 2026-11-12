"""Phase 2 monetization tests (docs/MONETIZATION.md).

The paywall machine WITHOUT live payments: catalog from config, honest
503 checkout seam, waitlist interest, 402 identity-reveal gates (counts
stay free), free autonomous-queue allowance (prepared never gated),
sponsored ordering + labelling, boost ordering, and free-core regression.
Pro is granted ONLY by direct DB writes here — exactly what the Stripe
webhook will do later; no endpoint grants it.
"""
import io, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.db import get_conn
from app.job_sources import insert_jobs

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64
MP = "multipart/form-data"

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

def give_media(client):
    r = client.post("/api/employer/logo", data={"file": (io.BytesIO(PNG_BYTES), "logo.png")}, content_type=MP)
    assert r.status_code == 200, r.data

def make_employer(app_, email="emp@bill.com", company="Bill Co", job_title="Bill Role"):
    e = app_.test_client()
    u = signup(e, email, role="employer", company=company)
    give_media(e)
    job = e.post("/api/employer/jobs", json={"title": job_title, "description": "Python role", "tags": ["Python"]}).json
    return e, u, job

def set_user_plan(app_, email, plan="pro"):
    conn = get_conn(app_.config["DB_PATH"])
    conn.execute("UPDATE users SET plan=?, plan_started_at=datetime('now') WHERE email=?", (plan, email))
    conn.commit(); conn.close()

def set_employer_plan(app_, employer_id, plan="pro"):
    conn = get_conn(app_.config["DB_PATH"])
    conn.execute("UPDATE employers SET plan=?, plan_started_at=datetime('now') WHERE id=?", (plan, employer_id))
    conn.commit(); conn.close()

def set_skills(app_, candidate_id, skills):
    import json as _json
    conn = get_conn(app_.config["DB_PATH"])
    conn.execute("UPDATE candidates SET skills=? WHERE id=?", (_json.dumps(skills), candidate_id))
    conn.commit(); conn.close()

# ---------------- catalog + seam ----------------

def test_plans_catalog_from_config(client):
    r = client.get("/api/billing/plans")
    assert r.status_code == 200
    plans = {p["key"]: p for p in r.json["plans"]}
    assert plans["candidate_pro"]["price"] == 24
    assert plans["recruiter_pro"]["price"] == 149
    assert r.json["sponsored_alacarte"] == {"price": 200, "days": 30}
    assert r.json["free_queue_allowance"] == 5
    assert r.json["payments_configured"] is False

def test_checkout_seam_503_and_no_pro_granted(client, app_):
    u = signup(client, "co@bill.com")
    r = client.post("/api/billing/checkout", json={"plan": "candidate_pro"})
    assert r.status_code == 503 and r.json["error"] == "payments_not_configured"
    # the seam never marks anyone Pro
    assert client.get("/api/auth/me").json["candidate"]["plan"] == "free"
    assert client.post("/api/billing/checkout", json={"plan": "gold_plz"}).status_code == 400
    assert app_.test_client().post("/api/billing/checkout", json={"plan": "candidate_pro"}).status_code == 401

def test_interest_waitlist_recorded(client, app_):
    signup(client, "w@bill.com")
    r = client.post("/api/billing/interest", json={"plan": "recruiter_pro"})
    assert r.status_code == 201 and r.json["recorded"] is True
    assert client.post("/api/billing/interest", json={"plan": "recruiter_pro"}).status_code == 201  # dup is fine
    assert client.post("/api/billing/interest", json={"plan": "nope"}).status_code == 400
    conn = get_conn(app_.config["DB_PATH"])
    rows = conn.execute("SELECT email, role, plan FROM billing_interest").fetchall()
    conn.close()
    assert len(rows) == 1 and rows[0]["email"] == "w@bill.com" and rows[0]["plan"] == "recruiter_pro"

# ---------------- identity reveal gates ----------------

def test_candidate_liker_identities_gated(app_):
    e, eu, job = make_employer(app_)
    c = app_.test_client(); cand = signup(c, "rv1@bill.com")
    e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    # count is FREE
    assert c.get("/api/me/likes").json["likes"] == 1
    r = c.get("/api/me/likers")
    assert r.status_code == 402 and r.json == {"error": "pro_required", "plan": "candidate_pro", "price": 24}
    set_user_plan(app_, "rv1@bill.com")
    r = c.get("/api/me/likers")
    assert r.status_code == 200
    assert r.json["likers"][0]["company"] == "Bill Co" and r.json["likers"][0]["job_title"] == "Bill Role"

def test_employer_job_liker_identities_gated(app_):
    e, eu, job = make_employer(app_, email="emp2@bill.com")
    c = app_.test_client(); signup(c, "rv2@bill.com")
    c.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    mine = [j for j in e.get("/api/employer/jobs").json if j["id"] == job["id"]][0]
    assert mine["likes_count"] == 1  # count free
    r = e.get(f"/api/employer/jobs/{job['id']}/likers")
    assert r.status_code == 402 and r.json == {"error": "pro_required", "plan": "recruiter_pro", "price": 149}
    set_employer_plan(app_, eu["employer"]["id"])
    r = e.get(f"/api/employer/jobs/{job['id']}/likers")
    assert r.status_code == 200 and r.json["likers"][0]["email"] == "rv2@bill.com"
    # another employer's job is a 404 before any gate logic
    other = app_.test_client(); signup(other, "rv3@bill.com", role="employer", company="Other")
    assert other.get(f"/api/employer/jobs/{job['id']}/likers").status_code == 404

# ---------------- autonomous queue allowance ----------------

def _login_jobs(app_, n=6):
    conn = get_conn(app_.config["DB_PATH"])
    jobs = [{"external_id": f"q-{i}", "title": f"Portal Role {i}", "company": "PortalCo",
             "location": "Remote", "remote": 1, "description": "Python Flask SQL role",
             "url": f"https://www.linkedin.com/jobs/view/9{i}", "source": "billq",
             "tags": ["Python", "Flask", "SQL"], "posted_at": "", "apply_type": "network_login",
             "requires_login": 1} for i in range(n)]
    insert_jobs(conn, jobs)
    rows = [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE source='billq' ORDER BY id").fetchall()]
    conn.close()
    return rows

def test_free_queue_allowance(app_):
    c = app_.test_client(); cand = signup(c, "q1@bill.com")
    set_skills(app_, cand["candidate"]["id"], ["Python", "Flask", "SQL"])
    tok = c.post("/api/ext/pairing-token").json["token"]
    assert c.get("/api/ext/queue", headers={"Authorization": f"Bearer {tok}"}).status_code == 200  # marks token used
    jobs = _login_jobs(app_)
    for j in jobs[:5]:
        r = c.post("/api/swipe", json={"job_id": j["id"], "direction": "right"})
        assert r.status_code == 200 and r.json["application"]["status"] == "queued_extension", r.data
    r = c.post("/api/swipe", json={"job_id": jobs[5]["id"], "direction": "right"})
    assert r.status_code == 402 and r.json["error"] == "queue_allowance_exhausted" and r.json["price"] == 24
    conn = get_conn(app_.config["DB_PATH"])
    n = conn.execute("SELECT COUNT(*) c FROM applications WHERE candidate_id=? AND status='queued_extension'", (cand["candidate"]["id"],)).fetchone()["c"]
    assert n == 5
    # 'prepared' applications are NEVER gated by the allowance
    insert_jobs(conn, [{"external_id": "q-open", "title": "Open Role", "company": "OpenCo", "location": "Remote",
                        "remote": 1, "description": "Python role", "url": "https://example.com/job",
                        "source": "billq", "tags": ["Python"], "posted_at": ""}])
    open_job = dict(conn.execute("SELECT * FROM jobs WHERE external_id='q-open'").fetchone())
    conn.close()
    r = c.post("/api/swipe", json={"job_id": open_job["id"], "direction": "right"})
    assert r.status_code == 200 and r.json["application"]["status"] == "prepared"
    # Pro lifts the cap: the refused job now queues
    set_user_plan(app_, "q1@bill.com")
    r = c.post("/api/swipe", json={"job_id": jobs[5]["id"], "direction": "right"})
    assert r.status_code == 200 and r.json["application"]["status"] == "queued_extension"

# ---------------- sponsored ----------------

def test_sponsored_gate_ordering_and_label(app_):
    e, eu, job_a = make_employer(app_, email="emp3@bill.com")
    job_b = e.post("/api/employer/jobs", json={"title": "Second Role", "tags": ["Python"]}).json
    r = e.post(f"/api/employer/jobs/{job_a['id']}/sponsor", json={"active": True})
    assert r.status_code == 402 and r.json["plan"] == "recruiter_pro"
    set_employer_plan(app_, eu["employer"]["id"])
    r = e.post(f"/api/employer/jobs/{job_a['id']}/sponsor", json={"active": True})
    assert r.status_code == 200 and r.json["sponsored"] is True and r.json["sponsored_until"]
    # one active at a time: sponsoring B moves it from A
    e.post(f"/api/employer/jobs/{job_b['id']}/sponsor", json={"active": True})
    mine = {j["id"]: j for j in e.get("/api/employer/jobs").json}
    assert mine[job_a["id"]]["sponsored"] is False and mine[job_b["id"]]["sponsored"] is True
    # discovery: sponsored sorts ahead of ALL organic, and is labelled
    c = app_.test_client(); signup(c, "sp1@bill.com")
    feed = c.get("/api/discovery").json
    sponsored = [j for j in feed if j.get("sponsored")]
    organic = [j for j in feed if not j.get("sponsored")]
    assert [j["id"] for j in sponsored] == [job_b["id"]]
    assert max(feed.index(j) for j in sponsored) < min(feed.index(j) for j in organic)
    # unsponsor removes it
    e.post(f"/api/employer/jobs/{job_b['id']}/sponsor", json={"active": False})
    feed = c.get("/api/discovery").json
    assert all(not j.get("sponsored") for j in feed)

# ---------------- boost ----------------

def test_boost_gate_and_employer_ordering(app_):
    e, eu, job = make_employer(app_, email="emp4@bill.com")
    c1 = app_.test_client(); u1 = signup(c1, "b1@bill.com", name="Strong Fit")
    set_skills(app_, u1["candidate"]["id"], ["Python", "Flask", "SQL"])
    c2 = app_.test_client(); u2 = signup(c2, "b2@bill.com", name="Weak Fit")
    set_skills(app_, u2["candidate"]["id"], ["Python"])
    feed = e.get(f"/api/employer/candidates?job_id={job['id']}").json["candidates"]
    ids = [x["id"] for x in feed]
    assert ids.index(u1["candidate"]["id"]) < ids.index(u2["candidate"]["id"])  # fit order baseline
    r = c2.post("/api/me/boost")
    assert r.status_code == 402 and r.json == {"error": "pro_required", "plan": "candidate_pro", "price": 24}
    set_user_plan(app_, "b2@bill.com")
    r = c2.post("/api/me/boost")
    assert r.status_code == 200 and r.json["boost_active"] is True
    me = c2.get("/api/auth/me").json["candidate"]
    assert me["boost_active"] is True and me["plan"] == "pro"
    feed = e.get(f"/api/employer/candidates?job_id={job['id']}").json["candidates"]
    assert feed[0]["id"] == u2["candidate"]["id"]  # boosted ahead despite weaker fit
    # boost state is candidate-only: never labelled in employer payloads
    for x in feed:
        assert "boost_until" not in x and "_boost_active" not in x and "boost_active" not in x

# ---------------- free core regression + migration ----------------

def test_free_core_still_works(app_):
    e, eu, job = make_employer(app_, email="emp5@bill.com")
    c = app_.test_client(); cand = signup(c, "fc1@bill.com")
    r = c.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    assert r.status_code == 200  # free swiping + applying (prepared) fine
    r = e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    assert "match" in r.json  # free matching fine
    mid = r.json["match"]["id"]
    assert c.post(f"/api/matches/{mid}/messages", json={"body": "hi"}).status_code in (200, 201)  # free messaging fine
    assert e.post("/api/employer/jobs", json={"title": "Another Free Post"}).status_code == 201  # free posting fine

def test_billing_migration_on_existing_db(tmp_path):
    p = str(tmp_path / "old.db")
    a1 = create_app(db_path=p, testing=True)
    a1.test_client().get("/api/billing/plans")  # boot: schema + migrations
    a2 = create_app(db_path=p, testing=True)     # re-boot must be idempotent
    c = a2.test_client()
    u = signup(c, "mig@bill.com")
    assert u["candidate"]["plan"] == "free"
    conn = get_conn(p)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert "plan" in cols and "plan_started_at" in cols
    assert conn.execute("SELECT COUNT(*) c FROM billing_interest").fetchone()["c"] == 0
    conn.close()
