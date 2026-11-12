import json, os
from .resume_parser import parse_resume, extract_text_from_pdf
from .job_sources import insert_jobs, seed_jobs

DEMO_CANDIDATES = [
    {"name":"Demo Candidate — Maya Chen","email":"maya.demo@example.com","phone":"514-555-0101","title":"Frontend Developer","skills":["React","TypeScript","JavaScript","CSS","HTML"],"years_exp":4,"summary":"[Demo/sample candidate] Frontend developer focused on React product work.","is_demo":1},
    {"name":"Demo Candidate — Alex Rivera","email":"alex.demo@example.com","phone":"416-555-0102","title":"Data Engineer","skills":["Python","SQL","Pandas","Azure","Data Governance"],"years_exp":6,"summary":"[Demo/sample candidate] Data engineer building Python/SQL pipelines.","is_demo":1},
    {"name":"Demo Candidate — Sam Patel","email":"sam.demo@example.com","phone":"613-555-0103","title":"QA Automation Engineer","skills":["Python","Selenium","Testing","Agile"],"years_exp":3,"summary":"[Demo/sample candidate] QA automation engineer with Selenium suites.","is_demo":1},
]

def _insert_candidate(conn, p, is_demo=0):
    existing = None
    if p.get("email"):
        existing = conn.execute("SELECT id FROM candidates WHERE email=?", (p["email"],)).fetchone()
    if existing: return existing["id"]
    cur = conn.execute("INSERT INTO candidates(name,email,phone,title,skills,years_exp,summary,resume_text,is_demo) VALUES(?,?,?,?,?,?,?,?,?)",
        (p.get("name",""), p.get("email",""), p.get("phone",""), p.get("title",""), json.dumps(p.get("skills",[])), p.get("years_exp",0), p.get("summary",""), p.get("resume_text",""), is_demo))
    conn.commit(); return cur.lastrowid

def seed_all(conn):
    insert_jobs(conn, seed_jobs())
    # Jackson from real resume file if present — no invented facts, parse only.
    resume_path = os.path.expanduser("~/workspace/job-search/Jackson_Ray_Resume.pdf")
    if os.path.exists(resume_path):
        try:
            with open(resume_path,"rb") as f: text = extract_text_from_pdf(f.read())
            if text and len(text) > 200:
                p = parse_resume(text)
                if p.get("email"):
                    _insert_candidate(conn, p, is_demo=0)
        except Exception:
            pass
    for d in DEMO_CANDIDATES:
        _insert_candidate(conn, d, is_demo=1)
    # Demo employer
    if not conn.execute("SELECT id FROM employers LIMIT 1").fetchone():
        conn.execute("INSERT INTO employers(name,company,email) VALUES(?,?,?)", ("Demo Hiring Manager","Demo Employer Inc.","hiring@demo.example.com"))
        conn.commit()
