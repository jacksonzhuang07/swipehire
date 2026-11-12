import json, os, shutil
from werkzeug.security import generate_password_hash
from .resume_parser import parse_resume, extract_text_from_pdf
from .job_sources import insert_jobs, seed_jobs

# Clearly-labelled DEMO seed accounts (password "demo") — local/demo use only, documented in README.
DEMO_PASSWORD = "demo"

DEMO_CANDIDATES = [
    {"name": "Demo Candidate — Maya Chen", "email": "maya.demo@example.com", "phone": "514-555-0101", "title": "Frontend Developer", "skills": ["React", "TypeScript", "JavaScript", "CSS", "HTML"], "years_exp": 4, "summary": "[Demo/sample candidate] Frontend developer focused on React product work.", "is_demo": 1},
    {"name": "Demo Candidate — Alex Rivera", "email": "alex.demo@example.com", "phone": "416-555-0102", "title": "Data Engineer", "skills": ["Python", "SQL", "Pandas", "Azure", "Data Governance"], "years_exp": 6, "summary": "[Demo/sample candidate] Data engineer building Python/SQL pipelines.", "is_demo": 1},
    {"name": "Demo Candidate — Sam Patel", "email": "sam.demo@example.com", "phone": "613-555-0103", "title": "QA Automation Engineer", "skills": ["Python", "Selenium", "Testing", "Agile"], "years_exp": 3, "summary": "[Demo/sample candidate] QA automation engineer with Selenium suites.", "is_demo": 1},
]

def _insert_candidate(conn, p, is_demo=0):
    existing = None
    if p.get("email"):
        existing = conn.execute("SELECT id FROM candidates WHERE email=?", (p["email"],)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute("INSERT INTO candidates(name,email,phone,title,skills,years_exp,summary,resume_text,is_demo) VALUES(?,?,?,?,?,?,?,?,?)",
        (p.get("name", ""), p.get("email", ""), p.get("phone", ""), p.get("title", ""), json.dumps(p.get("skills", [])), p.get("years_exp", 0), p.get("summary", ""), p.get("resume_text", ""), is_demo))
    conn.commit()
    return cur.lastrowid

def _ensure_user(conn, email, role, name, candidate_id=None, employer_id=None):
    email = (email or "").strip().lower()
    if not email:
        return None
    row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if row:
        conn.execute("UPDATE users SET candidate_id=COALESCE(candidate_id,?), employer_id=COALESCE(employer_id,?) WHERE id=?",
                     (candidate_id, employer_id, row["id"]))
        conn.commit()
        return row["id"]
    cur = conn.execute("INSERT INTO users(email,password_hash,role,name,candidate_id,employer_id,is_demo) VALUES(?,?,?,?,?,?,1)",
                       (email, generate_password_hash(DEMO_PASSWORD), role, name, candidate_id, employer_id))
    conn.commit()
    return cur.lastrowid

def _seed_jackson_photo(conn, candidate_id, uploads_dir):
    """Copy Jackson's verified headshot (PNG/JPEG magic bytes checked) as his profile photo."""
    if not uploads_dir:
        return
    src = os.path.expanduser("~/workspace/job-search/jackson_linkedin_headshot.png")
    if not os.path.exists(src):
        return
    try:
        with open(src, "rb") as f:
            magic = f.read(12)
        if not (magic.startswith(b"\x89PNG") or magic.startswith(b"\xff\xd8\xff")):
            return
        ext = ".png" if magic.startswith(b"\x89PNG") else ".jpg"
        dest_name = f"seed_jackson{ext}"
        dest = os.path.join(uploads_dir, dest_name)
        if not os.path.exists(dest):
            shutil.copyfile(src, dest)
        conn.execute("UPDATE candidates SET photo=? WHERE id=? AND (photo IS NULL OR photo='')", (dest_name, candidate_id))
        conn.commit()
    except Exception:
        pass

def seed_all(conn, uploads_dir=None):
    insert_jobs(conn, seed_jobs())
    # Jackson from real resume file if present — no invented facts, parse only.
    jackson_id = None
    resume_path = os.path.expanduser("~/workspace/job-search/Jackson_Ray_Resume.pdf")
    if os.path.exists(resume_path):
        try:
            with open(resume_path, "rb") as f:
                text = extract_text_from_pdf(f.read())
            if text and len(text) > 200:
                p = parse_resume(text)
                if p.get("email"):
                    jackson_id = _insert_candidate(conn, p, is_demo=0)
        except Exception:
            pass
    if jackson_id:
        _seed_jackson_photo(conn, jackson_id, uploads_dir)
        row = conn.execute("SELECT name,email FROM candidates WHERE id=?", (jackson_id,)).fetchone()
        # Demo seed account for Jackson (password "demo", labelled in README).
        _ensure_user(conn, row["email"], "candidate", row["name"], candidate_id=jackson_id)
    for d in DEMO_CANDIDATES:
        _insert_candidate(conn, d, is_demo=1)
    # Instant try-out demo candidate account.
    demo_cand_id = _insert_candidate(conn, {"name": "Demo User (Sample Candidate)", "email": "demo@swipehire.app", "phone": "514-555-0100",
        "title": "Full Stack Developer", "skills": ["Python", "JavaScript", "React", "SQL", "Flask"], "years_exp": 5,
        "summary": "[Demo account] Sample candidate for trying SwipeHire instantly.", "is_demo": 1}, is_demo=1)
    _ensure_user(conn, "demo@swipehire.app", "candidate", "Demo User", candidate_id=demo_cand_id)
    # Demo employer + account.
    emp = conn.execute("SELECT id FROM employers WHERE company=?", ("Demo Employer Inc.",)).fetchone()
    if emp:
        emp_id = emp["id"]
    else:
        cur = conn.execute("INSERT INTO employers(name,company,email) VALUES(?,?,?)", ("Demo Hiring Manager", "Demo Employer Inc.", "hiring@demo.example.com"))
        conn.commit()
        emp_id = cur.lastrowid
    _ensure_user(conn, "employer@swipehire.app", "employer", "Demo Hiring Manager", employer_id=emp_id)
