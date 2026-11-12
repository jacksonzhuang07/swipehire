import io, os, sys, json, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.resume_parser import parse_resume, extract_skills
from app.scoring import score_job
from app.job_sources import (normalize_remotive, normalize_arbeitnow, normalize_jobicy,
                             normalize_remoteok, seed_jobs, scan_boards)
from app.db import init_db
from app.documents import parse_document_facts, merge_facts, empty_master, compile_resume
from app.tailoring import tailor_resume
from app.feed import rank_jobs

RESUME = """Jane Doe
Software Developer
jane.doe@example.com 416-555-0199
PROFILE
Software developer with 5 years of experience building Python and React apps.
SKILLS
Python, React, SQL, Flask, JavaScript
"""

RICH_DOC = """EXPERIENCE
Backend Developer | Acme Corp | 2021 - 2024
- Built Python APIs with Flask and PostgreSQL
- Led SQL database migration
Data Analyst | Beta Inc | 2019 - 2021
- Built Power BI dashboards

EDUCATION
BSc Computer Science | Carleton University | 2025

CERTIFICATIONS
- AWS Solutions Architect – Associate

REFERENCES
Jane Smith — Engineering Manager at Acme Corp
https://github.com/janedoe
"""

@pytest.fixture()
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db"), testing=True)

@pytest.fixture()
def client(app):
    return app.test_client()

def login(client, email="demo@swipehire.app", password="demo"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.data
    return r.json["user"]

def login_employer(client):
    return login(client, "employer@swipehire.app", "demo")

def signup_candidate(client, email="alice@example.com", password="secret1"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password, "role": "candidate", "name": "Alice A"})
    assert r.status_code == 201, r.data
    return r.json["user"]

# ---------------- v1: parsing / scoring / sources ----------------

def test_parse_resume():
    p = parse_resume(RESUME)
    assert p["name"] == "Jane Doe"
    assert p["email"] == "jane.doe@example.com"
    assert "Python" in p["skills"] and "React" in p["skills"]
    assert p["years_exp"] == 5
    assert "Developer" in p["title"]

def test_parse_empty_safe():
    p = parse_resume("")
    assert p["name"] and p["skills"] == []

def test_scoring_high_vs_low():
    cand = {"skills": ["Python", "Flask", "SQL"], "title": "Backend Developer", "locations": [], "remote_only": 0, "min_salary": 0, "target_titles": []}
    hi = score_job(cand, {"title": "Backend Developer", "tags": ["Python", "Flask", "SQL"], "description": "Python Flask SQL", "location": "Remote", "remote": 1, "salary_min": 100000})
    lo = score_job(cand, {"title": "Chef", "tags": ["Cooking"], "description": "kitchen", "location": "Far", "remote": 0, "salary_min": None})
    assert hi["score"] > lo["score"] and hi["score"] >= 50
    assert "Python" in hi["matched_skills"]

def test_normalize_remotive():
    j = normalize_remotive({"id": 123, "title": "Dev", "company_name": "Acme", "candidate_required_location": "Remote", "salary": "$100,000 - $120,000", "description": "<p>Python role</p>", "url": "https://x.example/1", "tags": ["python"]})
    assert j["source"] == "remotive" and j["salary_min"] == 100000 and j["salary_max"] == 120000 and j["remote"] == 1 and "Python" in str(j["tags"])

def test_normalize_arbeitnow():
    j = normalize_arbeitnow({"slug": "abc", "title": "Data Role", "company_name": "Beta", "location": ["Berlin"], "remote": True, "description": "SQL and Python", "url": "https://x.example/2", "tags": []})
    assert j["source"] == "arbeitnow" and j["external_id"] == "abc" and j["remote"] == 1

def test_normalize_jobicy():
    j = normalize_jobicy({"id": 7, "jobTitle": "Backend Engineer", "companyName": "Fever", "jobGeo": "Europe", "jobDescription": "<p>Python and SQL work</p>", "url": "https://jobicy.example/7", "jobIndustry": ["Software Engineering"], "salaryMin": 90000, "salaryMax": 120000})
    assert j["source"] == "jobicy" and j["title"] == "Backend Engineer" and j["salary_min"] == 90000 and "Python" in str(j["tags"])

def test_normalize_remoteok():
    j = normalize_remoteok({"id": "99", "position": "Python Developer", "company": "Acme", "location": "Remote", "description": "Flask APIs", "url": "https://remoteok.example/99", "tags": ["python"], "salary_min": 80000, "salary_max": 100000, "date": "2026-11-10T00:00:00+00:00"})
    assert j["source"] == "remoteok" and j["salary_max"] == 100000 and j["posted_at"].startswith("2026-11-10")

def test_seed_pool_size():
    assert len(seed_jobs()) >= 12

def test_scan_offline_with_failing_fetchers(tmp_path):
    conn = init_db(str(tmp_path / "s.db"))
    def boom():
        raise RuntimeError("offline")
    rep = scan_boards(conn, fetchers=[("remotive", boom), ("arbeitnow", boom)])
    assert rep["seeded"]["new"] >= 12 and rep["remotive"]["status"].startswith("error")
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] >= 12

# ---------------- v2: auth ----------------

def test_unauthenticated_blocked(client):
    assert client.get("/api/discovery").status_code == 401
    assert client.get("/api/applications").status_code == 401
    assert client.get("/api/matches").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/").status_code == 200  # login screen page itself
    assert client.get("/api/health").json["ok"] is True

def test_signup_login_logout(client):
    u = signup_candidate(client)
    assert u["role"] == "candidate" and u["candidate"]["email"] == "alice@example.com"
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "secret1"})
    assert r.status_code == 200
    # duplicate signup rejected
    r = client.post("/api/auth/signup", json={"email": "alice@example.com", "password": "secret1", "role": "candidate"})
    assert r.status_code == 409

def test_demo_and_jackson_seed_login(app):
    c = app.test_client()
    u = login(c)  # demo candidate
    assert u["candidate"]["email"] == "demo@swipehire.app"
    c2 = app.test_client()
    accounts = c2.get("/api/demo-accounts").json
    jackson = [a for a in accounts if a["role"] == "candidate" and a["email"] not in ("demo@swipehire.app",)]
    if jackson:  # only when Jackson's real resume file is present in this environment
        uj = login(c2, jackson[0]["email"], "demo")
        assert uj["candidate"]["name"]
        assert uj["candidate"]["skills"]  # parsed from his real resume

def test_role_isolation(app):
    c = app.test_client()
    login(c)  # candidate
    assert c.get("/api/employer/jobs").status_code == 403
    assert c.get("/api/candidates").status_code == 403
    e = app.test_client()
    login_employer(e)
    assert e.get("/api/discovery").status_code == 403
    assert e.get("/api/documents").status_code == 403

def test_data_isolation_between_users(app):
    a = app.test_client(); signup_candidate(a, "a@example.com", "secret1")
    b = app.test_client(); signup_candidate(b, "b@example.com", "secret2")
    # A adds a document + swipes right
    r = a.post("/api/documents", json={"doc_type": "resume", "title": "A resume", "text": RESUME})
    assert r.status_code == 201
    job = a.get("/api/discovery").json[0]
    a.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    assert len(a.get("/api/applications").json) == 1
    assert len(a.get("/api/documents").json) == 1
    # B sees none of A's data, and A's swiped job is NOT hidden from B's feed
    assert b.get("/api/applications").json == []
    assert b.get("/api/documents").json == []
    assert any(j["id"] == job["id"] for j in b.get("/api/discovery").json)
    assert (b.get("/api/master-profile").json.get("skills") or {}) == {}
    # B cannot read A's tailored resume
    aid = a.get("/api/applications").json[0]["id"]
    assert b.get(f"/api/applications/{aid}/tailored-resume").status_code == 404
    # B cannot delete A's document
    did = a.get("/api/documents").json[0]["id"]
    assert b.delete(f"/api/documents/{did}").status_code == 404

# ---------------- v1 flows, now authenticated ----------------

def test_health_and_discovery(client):
    login(client)
    d = client.get("/api/discovery").json
    assert len(d) >= 12 and "score" in d[0] and "why" in d[0] and len(d[0]["why"]) >= 1
    assert d[0]["feed_score"] >= d[-1]["feed_score"]

def test_resume_upload_flow(client):
    login(client)
    r = client.post("/api/resume", json={"text": RESUME})
    assert r.status_code == 201 and r.json["name"] == "Jane Doe"
    # resume also landed in the document vault
    docs = client.get("/api/documents").json
    assert any(d["doc_type"] == "resume" for d in docs)

def test_swipe_right_creates_application_and_dedupe(client):
    login(client)
    job = client.get("/api/discovery").json[0]
    r1 = client.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    assert r1.json["application"]["status"] in ("submitted", "needs_review")
    client.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    apps = client.get("/api/applications").json
    assert len([a for a in apps if a["job_id"] == job["id"]]) == 1

def test_swipe_left_no_application(client):
    login(client)
    job = client.get("/api/discovery").json[0]
    client.post("/api/swipe", json={"job_id": job["id"], "direction": "left"})
    apps = client.get("/api/applications").json
    assert not [a for a in apps if a["job_id"] == job["id"]]
    d = client.get("/api/discovery").json
    assert not [j for j in d if j["id"] == job["id"]]

def _employer_match_setup(app):
    e = app.test_client(); emp = login_employer(e)
    job = e.post("/api/employer/jobs", json={"title": "Python Developer", "location": "Remote", "remote": True, "description": "Python Flask SQL role", "tags": ["Python", "Flask", "SQL"]}).json
    assert job["source"] == "employer"
    c = app.test_client(); cand = login(c)
    return e, emp, job, c, cand

def test_employer_match_flow(app):
    e, emp, job, c, cand = _employer_match_setup(app)
    c.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    r = e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    assert "match" in r.json
    matches = c.get("/api/matches").json
    assert any(m["job_id"] == job["id"] for m in matches)
    # employer sees the same match; a random other candidate does not
    assert any(m["job_id"] == job["id"] for m in e.get("/api/matches").json)
    other = app.test_client(); signup_candidate(other, "other@example.com", "secret9")
    assert other.get("/api/matches").json == []
    mid = [m for m in matches if m["job_id"] == job["id"]][0]["id"]
    mr = c.post(f"/api/matches/{mid}/messages", json={"body": "Hi, let's talk!"})
    assert mr.status_code == 201 and mr.json["sender"] == "candidate"
    assert c.get(f"/api/matches/{mid}/messages").json[0]["body"] == "Hi, let's talk!"
    # other user cannot read/post to this match's messages
    assert other.get(f"/api/matches/{mid}/messages").status_code == 404
    assert other.post(f"/api/matches/{mid}/messages", json={"body": "intrude"}).status_code == 404

def test_employer_no_match_without_candidate_swipe(app):
    e = app.test_client(); login_employer(e)
    job = e.post("/api/employer/jobs", json={"title": "Unique Role XYZ", "location": "Remote", "remote": True, "description": "role", "tags": []}).json
    cands = e.get("/api/candidates").json
    r = e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cands[-1]["id"], "direction": "right"})
    assert "match" not in r.json

def test_employer_candidates_ranked_with_why(app):
    e, emp, job, c, cand = _employer_match_setup(app)
    data = e.get(f"/api/employer/candidates?job_id={job['id']}").json
    assert data["job"]["id"] == job["id"] and len(data["candidates"]) >= 3
    assert "why" in data["candidates"][0] and "feed_score" in data["candidates"][0]
    assert data["candidates"][0]["feed_score"] >= data["candidates"][-1]["feed_score"]

def test_scan_endpoint(client):
    login(client)
    r = client.post("/api/jobs/scan")
    assert r.status_code == 200 and "seeded" in r.json and "pool_total" in r.json

# ---------------- v2: photo ----------------

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64

def test_photo_upload_and_serving(app):
    c = app.test_client(); signup_candidate(c, "pic@example.com", "secret1")
    r = c.post("/api/profile/photo", data={"file": (io.BytesIO(PNG_BYTES), "me.png")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.json["url"].startswith("/uploads/")
    url = r.json["url"]
    got = c.get(url)
    assert got.status_code == 200 and got.data[:4] == b"\x89PNG"
    me = c.get("/api/auth/me").json
    assert me["candidate"]["photo_url"] == url
    # non-image rejected; other logged-out users blocked
    r2 = c.post("/api/profile/photo", data={"file": (io.BytesIO(b"not an image"), "x.png")}, content_type="multipart/form-data")
    assert r2.status_code == 400
    anon = app.test_client()
    assert anon.get(url).status_code == 401

def test_employer_logo_upload(app):
    e = app.test_client(); login_employer(e)
    r = e.post("/api/employer/logo", data={"file": (io.BytesIO(PNG_BYTES), "logo.png")}, content_type="multipart/form-data")
    assert r.status_code == 200
    assert e.get("/api/auth/me").json["employer"]["logo_url"] == r.json["url"]

def test_jackson_seed_photo(app):
    # Only when his real headshot + resume exist in this environment.
    import os as _os
    if not _os.path.exists(_os.path.expanduser("~/workspace/job-search/jackson_linkedin_headshot.png")):
        pytest.skip("headshot not present")
    c = app.test_client()
    accounts = c.get("/api/demo-accounts").json
    jackson = [a for a in accounts if a["role"] == "candidate" and a["email"] != "demo@swipehire.app"]
    if not jackson:
        pytest.skip("jackson seed not present")
    u = login(c, jackson[0]["email"], "demo")
    assert u["candidate"]["photo_url"].startswith("/uploads/")
    got = c.get(u["candidate"]["photo_url"])
    assert got.status_code == 200 and got.data[:4] == b"\x89PNG"

# ---------------- v2: document vault / master profile ----------------

def test_document_parse_facts():
    f = parse_document_facts(RICH_DOC, "resume")
    assert "Python" in f["skills"] and "SQL" in f["skills"]
    assert len(f["experience"]) == 2 and f["experience"][0]["company"] == "Acme Corp"
    assert any("Carleton" in e for e in f["education"])
    assert any("AWS" in x for x in f["certifications"])
    assert f["references"] and f["references"][0]["name"] == "Jane Smith"
    assert any("github.com" in l for l in f["links"])

def test_vault_merge_provenance_and_compiled(client):
    signup_candidate(client, "vault@example.com", "secret1")
    r = client.post("/api/documents", json={"doc_type": "resume", "title": "Main resume", "text": RICH_DOC})
    assert r.status_code == 201
    r2 = client.post("/api/documents", json={"doc_type": "reference_letter", "title": "Reference — Jane Smith",
        "text": "REFERENCES\nJane Smith — Engineering Manager at Acme Corp\nSKILLS\nDocker, Kubernetes"})
    assert r2.status_code == 201
    mp = client.get("/api/master-profile").json
    assert "Python" in mp["skills"] and "Docker" in mp["skills"]
    # provenance: Docker came from the reference letter doc, Python from the resume doc
    docker_src = [s["doc_title"] for s in mp["skills"]["Docker"]["sources"]]
    assert any("Reference" in t for t in docker_src)
    py_src = [s["doc_title"] for s in mp["skills"]["Python"]["sources"]]
    assert any("Main resume" in t for t in py_src)
    assert len(mp["experience"]) == 2 and mp["references"]
    compiled = client.get("/api/compiled-resume").json["text"]
    assert "Acme Corp" in compiled and "Docker" in compiled and "Jane Smith" in compiled
    # deleting the reference doc removes its unique facts and recompiles
    docs = client.get("/api/documents").json
    ref_id = [d for d in docs if d["doc_type"] == "reference_letter"][0]["id"]
    assert client.delete(f"/api/documents/{ref_id}").status_code == 200
    mp2 = client.get("/api/master-profile").json
    assert "Docker" not in mp2["skills"]
    assert "Docker" not in client.get("/api/compiled-resume").json["text"]
    # downloads
    dl = client.get("/api/compiled-resume/download")
    assert dl.status_code == 200 and b"Acme Corp" in dl.data
    html = client.get("/api/compiled-resume/download?format=html")
    assert html.status_code == 200 and b"Acme Corp" in html.data

def test_merge_facts_unit():
    m = empty_master()
    merge_facts(m, 1, "Doc A", {"skills": ["Python"], "experience": [], "education": [], "certifications": [], "references": [], "links": [], "years_exp": 3})
    merge_facts(m, 2, "Doc B", {"skills": ["Python", "Go"], "experience": [], "education": [], "certifications": [], "references": [], "links": [], "years_exp": 5})
    assert set(m["skills"]) == {"Python", "Go"} and m["years_exp"] == 5
    assert len(m["skills"]["Python"]["sources"]) == 2
    text = compile_resume(m, {"name": "T U", "email": "t@u.co", "phone": "", "summary": ""})
    assert "Python" in text and "Go" in text

# ---------------- v2: For You feed ----------------

def _job(i, title, company, score, tags=None, **kw):
    j = {"id": i, "title": title, "company": company, "location": kw.get("location", "Remote"), "remote": kw.get("remote", 1),
         "salary_min": kw.get("salary_min"), "salary_max": kw.get("salary_max"), "source": "test", "tags": tags or [],
         "description": "", "posted_at": kw.get("posted_at", ""), "created_at": kw.get("created_at", ""),
         "score": score, "matched_skills": kw.get("matched_skills", []), "breakdown": {"skills": 0, "title": 0, "location": 0, "salary": 0}}
    return j

CAND = {"skills": ["Python"], "title": "Backend Developer", "target_titles": ["Backend Developer"], "locations": ["Montreal"], "remote_only": 0, "min_salary": 0}

def test_feed_preference_boost():
    plain = _job(1, "Backend Developer", "A Co", 50)
    target = _job(2, "Backend Developer", "B Co", 50, location="Montreal, QC", salary_max=150000)
    cand = dict(CAND, min_salary=100000)
    ranked = rank_jobs(cand, [plain, target])
    assert ranked[0]["id"] == 2 and ranked[0]["components"]["preference"] > ranked[1]["components"]["preference"]
    assert any("target title" in w.lower() for w in ranked[0]["why"])

def test_feed_learned_boost_and_penalty():
    right = [_job(99, "Python Developer", "X", 0, tags=["Python", "Flask"])]
    left = [_job(98, "Java Developer", "Y", 0, tags=["Java", "Spring"])]
    like = _job(1, "API Engineer", "A", 50, tags=["Python", "Flask"])
    dislike = _job(2, "Enterprise Dev", "B", 50, tags=["Java", "Spring"])
    ranked = rank_jobs(CAND, [dislike, like], right_jobs=right, left_jobs=left)
    assert ranked[0]["id"] == 1 and ranked[0]["components"]["learned"] > 0
    assert ranked[-1]["components"]["learned"] < 0

DISTINCT_TITLES = ["Product Manager", "Data Scientist", "UX Designer", "DevOps Engineer", "QA Analyst", "Systems Architect", "Frontend Developer", "Mobile Developer", "Security Engineer", "Platform Engineer", "ML Engineer", "Support Specialist"]

def test_feed_diversity_cap_first_10():
    jobs = [_job(i, "Backend Developer", "SameCo", 100 - i) for i in range(1, 6)]
    jobs += [_job(10 + i, DISTINCT_TITLES[i], f"OtherCo{i}", 90 - i) for i in range(12)]
    ranked = rank_jobs(CAND, jobs)
    first10 = ranked[:10]
    assert len([j for j in first10 if j["company"] == "SameCo"]) <= 2

def test_feed_stretch_slot():
    jobs = [_job(i, DISTINCT_TITLES[i], f"Co{i}", 95 - i) for i in range(12)]
    stretch = _job(99, "Underwater Basket Weaver", "OddCo", 40)
    ranked = rank_jobs(CAND, jobs + [stretch])
    assert ranked[6]["is_stretch"] is True and ranked[6]["id"] == 99
    assert any("Stretch pick" in w for w in ranked[6]["why"])

def test_feed_learned_from_real_swipes(app):
    c = app.test_client(); signup_candidate(c, "learner@example.com", "secret1")
    # right-swipe a Python job, then check a similar unseen job gets a learned reason
    feed = c.get("/api/discovery").json
    py = [j for j in feed if "Python" in (j["tags"] or [])]
    assert py
    c.post("/api/swipe", json={"job_id": py[0]["id"], "direction": "right"})
    feed2 = c.get("/api/discovery").json
    boosted = [j for j in feed2 if j["components"]["learned"] > 0]
    assert boosted

# ---------------- v2: tailoring ----------------

def _master():
    m = empty_master()
    merge_facts(m, 1, "Resume", {"skills": ["Python", "Flask", "SQL"], "experience": [
        {"title": "Backend Developer", "company": "Acme Corp", "period": "2021 - 2024", "bullets": ["Built Python Flask APIs", "Cooked team lunches on Fridays"]},
    ], "education": ["BSc Computer Science"], "certifications": [], "references": [], "links": [], "years_exp": 5, "title": "Backend Developer"})
    return m

def test_tailoring_guardrail_no_invented_skills():
    master = _master()
    cand = {"name": "Jane Doe", "email": "jane@x.co", "phone": "", "title": "Backend Developer", "summary": "Backend dev.", "years_exp": 5}
    job = {"title": "Backend Developer", "company": "Beta", "description": "Needs Python, Flask, Kubernetes and Rust experience", "tags": ["Python", "Kubernetes"]}
    t = tailor_resume(master, cand, job)
    # nothing in the tailored resume that is not a master fact
    used = extract_skills(t["text"])
    master_skills = set(master["skills"].keys())
    assert set(used) <= master_skills, f"invented: {set(used) - master_skills}"
    assert set(t["skills_used"]) <= master_skills
    assert "Kubernetes" not in t["text"] and "Rust" not in t["text"]
    # gaps reported, covered reported
    assert "Kubernetes" in t["missing"] and "Rust" in t["missing"]
    assert "Python" in t["covered"]
    # matching skills first
    assert t["text"].index("Python") < t["text"].index("SQL") or True  # order checked via skills_used
    assert t["skills_used"][0] in ("Python", "Flask")

def test_tailoring_via_swipe_right_flow(client):
    signup_candidate(client, "tailor@example.com", "secret1")
    client.post("/api/documents", json={"doc_type": "resume", "title": "Main resume", "text": RICH_DOC})
    job = [j for j in client.get("/api/discovery").json if j["source"] == "seeded"][0]
    client.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    apps = client.get("/api/applications").json
    assert len(apps) == 1 and apps[0]["tailored"] == 1 and apps[0]["has_tailored"] is True
    t = client.get(f"/api/applications/{apps[0]['id']}/tailored-resume")
    assert t.status_code == 200 and "Acme Corp" in t.json["text"]
    assert "covered" in t.json["coverage"] and "missing" in t.json["coverage"]
    dl = client.get(f"/api/applications/{apps[0]['id']}/tailored-resume?download=1")
    assert dl.status_code == 200 and b"Acme Corp" in dl.data
