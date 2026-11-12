import os, sys, json, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.resume_parser import parse_resume
from app.scoring import score_job
from app.job_sources import normalize_remotive, normalize_arbeitnow, seed_jobs, scan_boards
from app.db import init_db

RESUME = """Jane Doe
Software Developer
jane.doe@example.com 416-555-0199
PROFILE
Software developer with 5 years of experience building Python and React apps.
SKILLS
Python, React, SQL, Flask, JavaScript
"""

@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path/"t.db"), testing=True)
    return app.test_client()

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
    cand = {"skills":["Python","Flask","SQL"],"title":"Backend Developer","locations":[],"remote_only":0,"min_salary":0,"target_titles":[]}
    hi = score_job(cand, {"title":"Backend Developer","tags":["Python","Flask","SQL"],"description":"Python Flask SQL","location":"Remote","remote":1,"salary_min":100000})
    lo = score_job(cand, {"title":"Chef","tags":["Cooking"],"description":"kitchen","location":"Far","remote":0,"salary_min":None})
    assert hi["score"] > lo["score"] and hi["score"] >= 50
    assert "Python" in hi["matched_skills"]

def test_normalize_remotive():
    j = normalize_remotive({"id":123,"title":"Dev","company_name":"Acme","candidate_required_location":"Remote","salary":"$100,000 - $120,000","description":"<p>Python role</p>","url":"https://x.example/1","tags":["python"]})
    assert j["source"]=="remotive" and j["salary_min"]==100000 and j["salary_max"]==120000 and j["remote"]==1 and "Python" in str(j["tags"])

def test_normalize_arbeitnow():
    j = normalize_arbeitnow({"slug":"abc","title":"Data Role","company_name":"Beta","location":["Berlin"],"remote":True,"description":"SQL and Python","url":"https://x.example/2","tags":[]})
    assert j["source"]=="arbeitnow" and j["external_id"]=="abc" and j["remote"]==1

def test_seed_pool_size():
    assert len(seed_jobs()) >= 12

def test_scan_offline_with_failing_fetchers(tmp_path):
    conn = init_db(str(tmp_path/"s.db"))
    def boom(): raise RuntimeError("offline")
    rep = scan_boards(conn, fetchers=[("remotive", boom), ("arbeitnow", boom)])
    assert rep["seeded"]["new"] >= 12 and rep["remotive"]["status"].startswith("error")
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] >= 12

def test_health_and_discovery(client):
    assert client.get("/api/health").json["ok"] is True
    assert client.get("/").status_code == 200
    d = client.get("/api/discovery").json
    assert len(d) >= 12 and "score" in d[0]
    assert d[0]["score"] >= d[-1]["score"]  # sorted

def test_resume_upload_flow(client):
    r = client.post("/api/resume", json={"text": RESUME})
    assert r.status_code == 201 and r.json["name"] == "Jane Doe"

def test_swipe_right_creates_application_and_dedupe(client):
    cand = client.get("/api/candidates").json[0]
    job = client.get(f"/api/discovery?candidate_id={cand['id']}").json[0]
    r1 = client.post("/api/swipe", json={"candidate_id":cand["id"],"job_id":job["id"],"direction":"right"})
    assert r1.json["application"]["status"] in ("submitted","needs_review")
    r2 = client.post("/api/swipe", json={"candidate_id":cand["id"],"job_id":job["id"],"direction":"right"})
    # dedupe: second swipe is ignored at swipe layer, application count stays 1 for this job
    apps = client.get(f"/api/applications?candidate_id={cand['id']}").json
    assert len([a for a in apps if a["job_id"]==job["id"]]) == 1

def test_swipe_left_no_application(client):
    cand = client.get("/api/candidates").json[0]
    job = client.get(f"/api/discovery?candidate_id={cand['id']}").json[0]
    client.post("/api/swipe", json={"candidate_id":cand["id"],"job_id":job["id"],"direction":"left"})
    apps = client.get(f"/api/applications?candidate_id={cand['id']}").json
    assert not [a for a in apps if a["job_id"]==job["id"]]
    # passed job disappears from discovery
    d = client.get(f"/api/discovery?candidate_id={cand['id']}").json
    assert not [j for j in d if j["id"]==job["id"]]

def test_employer_match_flow(client):
    emp = client.get("/api/employers").json[0]
    job = client.post("/api/employer/jobs", json={"employer_id":emp["id"],"title":"Python Developer","location":"Remote","remote":True,"description":"Python Flask SQL role","tags":["Python","Flask","SQL"]}).json
    assert job["source"]=="employer"
    # candidate swipes right on employer job
    cand = client.get("/api/candidates").json[0]
    client.post("/api/swipe", json={"candidate_id":cand["id"],"job_id":job["id"],"direction":"right"})
    # employer swipes right on candidate -> match
    r = client.post("/api/employer/swipe", json={"employer_id":emp["id"],"job_id":job["id"],"candidate_id":cand["id"],"direction":"right"})
    assert "match" in r.json
    matches = client.get("/api/matches").json
    assert any(m["job_id"]==job["id"] and m["candidate_id"]==cand["id"] for m in matches)
    # message stub stores in DB
    mid = [m for m in matches if m["job_id"]==job["id"]][0]["id"]
    mr = client.post(f"/api/matches/{mid}/messages", json={"sender":"employer","body":"Hi, let's talk!"})
    assert mr.status_code == 201
    assert client.get(f"/api/matches/{mid}/messages").json[0]["body"] == "Hi, let's talk!"

def test_employer_no_match_without_candidate_swipe(client):
    emp = client.get("/api/employers").json[0]
    job = client.post("/api/employer/jobs", json={"employer_id":emp["id"],"title":"Unique Role XYZ","location":"Remote","remote":True,"description":"role","tags":[]}).json
    cand = client.get("/api/candidates").json[-1]
    r = client.post("/api/employer/swipe", json={"employer_id":emp["id"],"job_id":job["id"],"candidate_id":cand["id"],"direction":"right"})
    assert "match" not in r.json

def test_scan_endpoint(client):
    r = client.post("/api/jobs/scan")
    assert r.status_code == 200 and "seeded" in r.json
