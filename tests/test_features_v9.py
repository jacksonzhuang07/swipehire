"""Feature tests for the v9 amendments (docs/FEATURE_SPEC.md, N1–N5).

N3 work_mode + N5 hard discovery filters · N4 recruiter deck + recruiter
swipes + match-on-recruiter-like · N2 per-entity like counts · N1 media
(picture-or-video, mandatory on the employer side).
"""
import io, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.job_sources import derive_work_mode, insert_jobs
from app.db import get_conn, init_db

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128
WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 128
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
    return r

def make_employer(app_, email="emp@v9.com", company="V9 Co", job_title="V9 Role"):
    e = app_.test_client()
    u = signup(e, email, role="employer", company=company)
    give_media(e)
    job = e.post("/api/employer/jobs", json={"title": job_title, "description": "Python role", "tags": ["Python"]}).json
    return e, u, job

# ---------------- N3: work_mode ----------------

def test_derive_work_mode_rules():
    assert derive_work_mode({"work_mode": "hybrid", "remote": 1}) == "hybrid"      # explicit wins
    assert derive_work_mode({"remote": 1, "location": "Toronto (Hybrid)"}) == "remote"  # flag before text
    assert derive_work_mode({"remote": 0, "location": "Toronto, ON (Hybrid)"}) == "hybrid"
    assert derive_work_mode({"remote": 0, "location": "Remote-first", "description": "hybrid team"}) == "hybrid"
    assert derive_work_mode({"remote": 0, "location": "Montreal, QC"}) == "in_person"
    assert derive_work_mode({"remote": 0, "location": ""}) == "unspecified"
    assert derive_work_mode({"remote": 0, "location": "Unspecified"}) == "unspecified"
    assert derive_work_mode({"work_mode": "banana", "remote": 1}) == "remote"       # junk explicit ignored

def test_insert_and_post_set_work_mode(app_):
    conn = get_conn(app_.config["DB_PATH"])
    insert_jobs(conn, [{"external_id": "wm-1", "title": "Seeded Hybrid", "company": "X", "location": "Ottawa (Hybrid)",
                        "remote": 0, "description": "", "url": "", "source": "testsrc", "tags": []}])
    row = conn.execute("SELECT work_mode FROM jobs WHERE external_id='wm-1'").fetchone()
    assert row["work_mode"] == "hybrid"
    conn.close()
    e, u, job = make_employer(app_)
    assert job["work_mode"] == "unspecified"  # no signals in the posting
    j2 = e.post("/api/employer/jobs", json={"title": "Explicit Remote", "work_mode": "remote"}).json
    assert j2["work_mode"] == "remote" and j2["remote"] == 1
    j3 = e.post("/api/employer/jobs", json={"title": "Explicit Office", "work_mode": "in_person", "location": "Laval"}).json
    assert j3["work_mode"] == "in_person" and j3["remote"] == 0
    assert e.post("/api/employer/jobs", json={"title": "Bad Mode", "work_mode": "banana"}).status_code == 400

def test_work_mode_backfill_repairs_existing_rows(app_):
    conn = get_conn(app_.config["DB_PATH"])
    conn.execute("UPDATE jobs SET work_mode='unspecified' WHERE external_id='seed-1'")  # Toronto (Hybrid)
    conn.execute("UPDATE jobs SET work_mode='unspecified' WHERE external_id='seed-4'")  # Remote, remote=1
    conn.commit(); conn.close()
    init_db(app_.config["DB_PATH"])  # boot path re-runs the backfill
    conn = get_conn(app_.config["DB_PATH"])
    modes = {r["external_id"]: r["work_mode"] for r in conn.execute("SELECT external_id, work_mode FROM jobs WHERE external_id IN ('seed-1','seed-4')").fetchall()}
    conn.close()
    assert modes == {"seed-1": "hybrid", "seed-4": "remote"}

# ---------------- N5: hard discovery filters ----------------

def test_discovery_keyword_filter(client):
    signup(client, "f1@v9.com")
    all_jobs = client.get("/api/discovery").json
    py = client.get("/api/discovery?q=python").json
    assert 0 < len(py) < len(all_jobs)
    for j in py:
        hay = " ".join([j["title"], j["company"], j["description"], " ".join(j["tags"])]).lower()
        assert "python" in hay

def test_discovery_location_filter(client):
    signup(client, "f2@v9.com")
    mtl = client.get("/api/discovery?location=montreal").json
    assert mtl and all("montreal" in j["location"].lower() for j in mtl)

def test_discovery_work_mode_filter(client):
    signup(client, "f3@v9.com")
    for mode in ("remote", "hybrid", "in_person"):
        rows = client.get(f"/api/discovery?work_mode={mode}").json
        assert rows, mode
        assert all(j["work_mode"] == mode for j in rows), mode
    combo = client.get("/api/discovery?work_mode=hybrid&location=toronto").json
    assert combo and all(j["work_mode"] == "hybrid" and "toronto" in j["location"].lower() for j in combo)
    assert client.get("/api/discovery?work_mode=banana").status_code == 400

# ---------------- N4: recruiter deck ----------------

def test_recruiters_listing_ranked_and_scoped(app_):
    c = app_.test_client(); signup(c, "r1@v9.com")
    recs = c.get("/api/recruiters").json
    demo = [r for r in recs if r["company"] == "Demo Employer Inc."]
    assert demo and demo[0]["open_jobs"] >= 2 and demo[0]["job_titles"]
    assert "media_url" in demo[0] and "feed_score" in demo[0]
    # an employer with NO jobs is not a swipeable recruiter card
    e2 = app_.test_client(); u2 = signup(e2, "nojob@v9.com", role="employer", company="NoJobs Co")
    assert all(r["employer_id"] != u2["employer"]["id"] for r in c.get("/api/recruiters").json)
    # already-swiped recruiters leave the deck
    c.post("/api/recruiter-swipe", json={"employer_id": demo[0]["employer_id"], "direction": "left"})
    assert all(r["employer_id"] != demo[0]["employer_id"] for r in c.get("/api/recruiters").json)

def test_recruiter_swipe_validation(app_):
    c = app_.test_client(); signup(c, "r2@v9.com")
    assert c.post("/api/recruiter-swipe", json={"employer_id": 987654, "direction": "right"}).status_code == 404
    recs = c.get("/api/recruiters").json
    assert c.post("/api/recruiter-swipe", json={"employer_id": recs[0]["employer_id"], "direction": "up"}).status_code == 400
    e = app_.test_client(); signup(e, "r3@v9.com", role="employer")
    assert e.post("/api/recruiter-swipe", json={"employer_id": recs[0]["employer_id"], "direction": "right"}).status_code == 403
    assert app_.test_client().get("/api/recruiters").status_code == 401

def test_recruiter_like_creates_no_application_then_matches(app_):
    e, eu, job = make_employer(app_)
    c = app_.test_client(); cand = signup(c, "r4@v9.com")
    r = c.post("/api/recruiter-swipe", json={"employer_id": eu["employer"]["id"], "direction": "right"})
    assert r.status_code == 200 and "match" not in r.json
    assert c.get("/api/applications").json == []          # a recruiter like is NOT an application
    assert c.get("/api/matches").json == []
    r2 = e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    assert "match" in r2.json and r2.json["match"]["job_id"] == job["id"]
    assert any(m["job_id"] == job["id"] for m in c.get("/api/matches").json)

def test_recruiter_like_second_completes_match(app_):
    e, eu, job = make_employer(app_, email="emp2@v9.com")
    c = app_.test_client(); cand = signup(c, "r5@v9.com")
    r = e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    assert "match" not in r.json  # candidate has not liked the job OR the recruiter yet
    r2 = c.post("/api/recruiter-swipe", json={"employer_id": eu["employer"]["id"], "direction": "right"})
    assert "match" in r2.json and r2.json["match"]["job_id"] == job["id"]

def test_recruiter_left_swipe_never_matches(app_):
    e, eu, job = make_employer(app_, email="emp3@v9.com")
    c = app_.test_client(); cand = signup(c, "r6@v9.com")
    c.post("/api/recruiter-swipe", json={"employer_id": eu["employer"]["id"], "direction": "left"})
    r = e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    assert "match" not in r.json

# ---------------- N2: like counts (per entity) ----------------

def test_candidate_profile_like_count(app_):
    e, eu, job = make_employer(app_, email="emp4@v9.com")
    c = app_.test_client(); cand = signup(c, "l1@v9.com")
    assert c.get("/api/me/likes").json["likes"] == 0
    e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    assert c.get("/api/me/likes").json["likes"] == 1
    assert c.get("/api/auth/me").json["candidate"]["likes_count"] == 1
    # a left swipe does not add a like
    e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "left"})
    assert c.get("/api/me/likes").json["likes"] == 1

def test_job_like_count(app_):
    e = app_.test_client()
    demo = e.post("/api/auth/login", json={"email": "employer@swipehire.app", "password": "demo"}).json["user"]
    jobs = e.get("/api/employer/jobs").json
    job = jobs[0]
    assert job["likes_count"] == 0
    for em in ("l2@v9.com", "l3@v9.com"):
        c = app_.test_client(); signup(c, em)
        c.post("/api/swipe", json={"job_id": job["id"], "direction": "right"})
    mine = [j for j in e.get("/api/employer/jobs").json if j["id"] == job["id"]][0]
    assert mine["likes_count"] == 2

def test_recruiter_profile_like_count(app_):
    e, eu, job = make_employer(app_, email="emp5@v9.com")
    assert e.get("/api/auth/me").json["employer"]["likes_count"] == 0
    c = app_.test_client(); signup(c, "l4@v9.com")
    c.post("/api/recruiter-swipe", json={"employer_id": eu["employer"]["id"], "direction": "right"})
    me = e.get("/api/auth/me").json["employer"]
    assert me["likes_count"] == 1 and me["has_media"] is True

# ---------------- N1: media ----------------

def test_job_post_requires_media(app_):
    e = app_.test_client(); signup(e, "m1@v9.com", role="employer", company="NoMedia Co")
    r = e.post("/api/employer/jobs", json={"title": "No Media Role"})
    assert r.status_code == 400 and "media" in r.json["error"].lower()
    assert e.get("/api/employer/jobs").json == []
    give_media(e)  # recruiter profile media unlocks posting (inheritance)
    ok = e.post("/api/employer/jobs", json={"title": "Now Fine"})
    assert ok.status_code == 201 and ok.json["media_url"]  # inherits profile media

def test_job_post_multipart_with_attached_media(app_):
    e = app_.test_client(); signup(e, "m2@v9.com", role="employer", company="Attach Co")
    r = e.post("/api/employer/jobs", data={"title": "Media Role", "location": "Remote", "remote": "true",
                                           "work_mode": "remote", "tags": "Python, Flask",
                                           "media": (io.BytesIO(PNG_BYTES), "job.png")}, content_type=MP)
    assert r.status_code == 201, r.data
    j = r.json
    assert j["media_type"] == "image" and j["media_url"].startswith("/uploads/")
    assert j["work_mode"] == "remote" and sorted(j["tags"]) == ["Flask", "Python"]
    # the file is login-gated like every upload
    assert app_.test_client().get(j["media_url"]).status_code == 401
    assert e.get(j["media_url"]).status_code == 200

def test_job_media_endpoint(app_):
    e, eu, job = make_employer(app_, email="emp6@v9.com")
    r = e.post(f"/api/employer/jobs/{job['id']}/media", data={"file": (io.BytesIO(MP4_BYTES), "clip.mp4")}, content_type=MP)
    assert r.status_code == 200 and r.json["media_type"] == "video"
    mine = [j for j in e.get("/api/employer/jobs").json if j["id"] == job["id"]][0]
    assert mine["media_type"] == "video"
    other = app_.test_client(); signup(other, "m3@v9.com", role="employer", company="Other Co")
    assert other.post(f"/api/employer/jobs/{job['id']}/media", data={"file": (io.BytesIO(PNG_BYTES), "x.png")}, content_type=MP).status_code == 404
    c = app_.test_client(); signup(c, "m4@v9.com")
    assert c.post(f"/api/employer/jobs/{job['id']}/media", data={"file": (io.BytesIO(PNG_BYTES), "x.png")}, content_type=MP).status_code == 403

def test_candidate_profile_video(app_):
    c = app_.test_client(); signup(c, "m5@v9.com")
    r = c.post("/api/profile/photo", data={"file": (io.BytesIO(MP4_BYTES), "intro.mp4")}, content_type=MP)
    assert r.status_code == 200 and r.json["media_type"] == "video"
    me = c.get("/api/auth/me").json
    assert me["candidate"]["video_url"] == r.json["url"]
    assert c.get(r.json["url"]).status_code == 200
    # employer-facing candidate payload carries the video too
    e = app_.test_client(); signup(e, "m6@v9.com", role="employer")
    row = [x for x in e.get("/api/candidates").json if x["email"] == "m5@v9.com"][0]
    assert row["video_url"] == r.json["url"]
    # images still work alongside (photo is a separate slot)
    r2 = c.post("/api/profile/photo", data={"file": (io.BytesIO(PNG_BYTES), "me.png")}, content_type=MP)
    assert r2.status_code == 200 and r2.json["media_type"] == "image"
    me = c.get("/api/auth/me").json
    assert me["candidate"]["photo_url"] == r2.json["url"] and me["candidate"]["video_url"] == r.json["url"]

def test_employer_profile_video_unlocks_posting(app_):
    e = app_.test_client(); signup(e, "m7@v9.com", role="employer", company="Video Co")
    r = e.post("/api/employer/logo", data={"file": (io.BytesIO(WEBM_BYTES), "intro.webm")}, content_type=MP)
    assert r.status_code == 200 and r.json["media_type"] == "video"
    me = e.get("/api/auth/me").json["employer"]
    assert me["has_media"] is True and me["media_type"] == "video" and me["video_url"] == r.json["url"]
    assert e.post("/api/employer/jobs", json={"title": "Video Co Role"}).status_code == 201

def test_media_type_and_size_abuse(client):
    signup(client, "m8@v9.com")
    # wrong types / fake payloads -> 400, never 500
    assert client.post("/api/profile/photo", data={"file": (io.BytesIO(b"GIF89a" + b"\x00" * 32), "x.gif")}, content_type=MP).status_code == 400
    assert client.post("/api/profile/photo", data={"file": (io.BytesIO(b"plain text video"), "x.mp4")}, content_type=MP).status_code == 400
    assert client.post("/api/profile/photo", data={"file": (io.BytesIO(b""), "x.mp4")}, content_type=MP).status_code == 400
    # video over the 25MB cap -> 413 (file itself, under the 32MB request cap)
    big = MP4_BYTES + b"\x00" * (25 * 1024 * 1024)
    assert client.post("/api/profile/photo", data={"file": (io.BytesIO(big), "big.mp4")}, content_type=MP).status_code == 413

def test_discovery_carries_inherited_job_media(app_):
    c = app_.test_client(); signup(c, "m9@v9.com")
    feed = c.get("/api/discovery").json
    emp_jobs = [j for j in feed if j["source"] == "employer"]
    assert emp_jobs and all(j["media_url"] for j in emp_jobs)  # inherit demo employer's seeded picture
    board_jobs = [j for j in feed if j["source"] == "seeded"]
    assert board_jobs and all(j["media_url"] == "" for j in board_jobs)
