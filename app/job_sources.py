import json, re, urllib.request, html
from .resume_parser import extract_skills

def _clean(t):
    t = html.unescape(re.sub(r"<[^>]+>", " ", t or ""))
    return " ".join(t.split())

def _salary_from_text(salary):
    nums = re.findall(r"\$?([\d,]{4,})", salary or "")
    smin = smax = None
    if len(nums) >= 2:
        smin, smax = int(nums[0].replace(",", "")), int(nums[1].replace(",", ""))
    elif len(nums) == 1:
        smin = smax = int(nums[0].replace(",", ""))
    return smin, smax

def normalize_remotive(item):
    desc = _clean(item.get("description", ""))
    tags = item.get("tags") or []
    skills = extract_skills(item.get("title", "") + " " + desc + " " + " ".join(tags))
    salary = item.get("salary") or ""
    smin, smax = _salary_from_text(salary)
    return {"external_id": str(item.get("id") or item.get("url")), "title": item.get("title", "").strip(), "company": item.get("company_name", "").strip(), "location": item.get("candidate_required_location") or "Remote", "remote": 1, "salary_min": smin, "salary_max": smax, "salary_text": salary, "description": desc[:1200], "url": item.get("url", ""), "source": "remotive", "tags": list(dict.fromkeys(tags + skills))[:12], "posted_at": item.get("publication_date", "") or ""}

def normalize_arbeitnow(item):
    desc = _clean(item.get("description", ""))
    tags = item.get("tags") or []
    skills = extract_skills(item.get("title", "") + " " + desc + " " + " ".join(tags))
    loc = ", ".join(item.get("location") if isinstance(item.get("location"), list) else [item.get("location") or ""])
    posted = ""
    if item.get("created_at"):
        try:
            import datetime
            posted = datetime.datetime.fromtimestamp(int(item["created_at"]), datetime.timezone.utc).isoformat()
        except Exception:
            posted = ""
    return {"external_id": str(item.get("slug") or item.get("url")), "title": (item.get("title") or "").strip(), "company": (item.get("company_name") or "").strip(), "location": loc or "Unspecified", "remote": 1 if item.get("remote") else 0, "salary_min": None, "salary_max": None, "salary_text": "", "description": desc[:1200], "url": item.get("url", ""), "source": "arbeitnow", "tags": list(dict.fromkeys(tags + skills))[:12], "posted_at": posted}

def normalize_jobicy(item):
    desc = _clean(item.get("jobDescription", "") or item.get("jobExcerpt", ""))
    tags = []
    for k in ("jobIndustry", "jobType", "jobLevel"):
        v = item.get(k)
        if isinstance(v, list):
            tags += v
        elif v:
            tags.append(v)
    skills = extract_skills(item.get("jobTitle", "") + " " + desc + " " + " ".join(tags))
    salary = ""
    smin = item.get("salaryMin") or None
    smax = item.get("salaryMax") or None
    cur = item.get("salaryCurrency") or ""
    if smin or smax:
        salary = f"{cur} {smin or ''}-{smax or ''}".strip()
    return {"external_id": str(item.get("id") or item.get("jobSlug") or item.get("url")), "title": (item.get("jobTitle") or "").strip(), "company": (item.get("companyName") or "").strip(), "location": item.get("jobGeo") or "Remote", "remote": 1, "salary_min": int(smin) if smin else None, "salary_max": int(smax) if smax else None, "salary_text": salary, "description": desc[:1200], "url": item.get("url", ""), "source": "jobicy", "tags": list(dict.fromkeys(tags + skills))[:12], "posted_at": item.get("pubDate", "") or ""}

def normalize_remoteok(item):
    # RemoteOK asks API consumers to credit + link back to the job URL — we always keep the original url.
    desc = _clean(item.get("description", ""))
    tags = item.get("tags") or []
    skills = extract_skills(item.get("position", "") + " " + desc + " " + " ".join(tags))
    smin = item.get("salary_min") or None
    smax = item.get("salary_max") or None
    salary = ""
    if smin or smax:
        salary = f"${smin or ''} - ${smax or ''}"
    return {"external_id": str(item.get("id") or item.get("slug") or item.get("url")), "title": (item.get("position") or "").strip(), "company": (item.get("company") or "").strip(), "location": item.get("location") or "Remote", "remote": 1, "salary_min": int(smin) if smin else None, "salary_max": int(smax) if smax else None, "salary_text": salary, "description": desc[:1200], "url": item.get("url", ""), "source": "remoteok", "tags": list(dict.fromkeys(tags + skills))[:12], "posted_at": item.get("date", "") or ""}

def _fetch(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "SwipeHire/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

REMOTIVE_CATEGORIES = ["software-dev", "product", "data", "devops-sysadmin", "project-management", "design"]

def fetch_remotive(limit=50):
    """Remotive across several categories, deduped by external_id."""
    out, seen = [], set()
    for cat in REMOTIVE_CATEGORIES:
        try:
            data = _fetch(f"https://remotive.com/api/remote-jobs?category={cat}&limit={limit}")
        except Exception:
            continue
        for x in data.get("jobs", []):
            j = normalize_remotive(x)
            if j["external_id"] not in seen and j["title"]:
                seen.add(j["external_id"])
                out.append(j)
    if not out:
        data = _fetch("https://remotive.com/api/remote-jobs?limit=%d" % limit)
        out = [normalize_remotive(x) for x in data.get("jobs", [])]
    return out

def fetch_arbeitnow(pages=3, per_page_limit=100):
    out, seen = [], set()
    for page in range(1, pages + 1):
        data = _fetch(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        items = data.get("data", [])
        if not items:
            break
        for x in items[:per_page_limit]:
            j = normalize_arbeitnow(x)
            if j["external_id"] not in seen and j["title"]:
                seen.add(j["external_id"])
                out.append(j)
    return out

def fetch_jobicy(count=50):
    data = _fetch(f"https://jobicy.com/api/v2/remote-jobs?count={count}")
    return [normalize_jobicy(x) for x in data.get("jobs", []) if x.get("jobTitle")]

def fetch_remoteok(limit=100):
    data = _fetch("https://www.remoteok.com/api")
    items = [x for x in data if isinstance(x, dict) and x.get("position")]
    return [normalize_remoteok(x) for x in items[:limit]]

SEED_JOBS = [
 ("Solutions Architect", "CloudNorth", "Toronto, ON (Hybrid)", 0, 110000, 145000, "$110k–$145k CAD", "Design cloud solutions on Azure and AWS, lead client workshops, build Python automation and REST API integrations for enterprise clients.", ["AWS", "Azure", "Python", "REST APIs"]),
 ("Automation Consultant", "Omnitrans-style Logistics Co.", "Montreal, QC (Hybrid)", 0, 85000, 105000, "$85k–$105k CAD", "Own process automation: Python pipelines, OCR document processing, Power BI dashboards and stakeholder delivery.", ["Python", "Power BI", "OCR", "Process Automation"]),
 ("Data Analyst", "Fintech Montreal", "Montreal, QC", 0, 70000, 90000, "$70k–$90k CAD", "Build Power BI dashboards, SQL queries and Pandas ETL for product and finance teams.", ["SQL", "Power BI", "Pandas", "Data Analytics"]),
 ("Backend Developer (Python)", "Remote SaaS Co.", "Remote", 1, 95000, 130000, "$95k–$130k", "Build Flask/FastAPI services, PostgreSQL data models and CI/CD pipelines in a fully remote team.", ["Python", "Flask", "PostgreSQL", "Docker"]),
 ("Frontend Developer", "HealthTech Remote", "Remote", 1, 90000, 120000, "$90k–$120k", "React and TypeScript product work, design systems, API integration with a distributed team.", ["React", "TypeScript", "JavaScript", "CSS"]),
 ("Full Stack Developer", "Startup Hub TO", "Toronto, ON", 0, 90000, 125000, "$90k–$125k", "JavaScript, Node.js and React across the stack; ship features end to end with a small team.", ["JavaScript", "React", "Node.js", "SQL"]),
 ("DevOps Engineer", "CloudScale", "Remote", 1, 115000, 150000, "$115k–$150k", "Kubernetes, Terraform, AWS infrastructure, CI/CD and Linux systems at scale.", ["Kubernetes", "AWS", "Terraform", "Linux", "CI/CD"]),
 ("QA Automation Engineer", "Federal Digital Services", "Ottawa, ON (Hybrid)", 0, 80000, 100000, "$80k–$100k", "Selenium and Python test automation, regression suites, Agile delivery for public services.", ["Selenium", "Python", "Testing", "Agile"]),
 ("Technical Project Manager", "Enterprise Consulting Group", "Montreal, QC (Hybrid)", 0, 95000, 120000, "$95k–$120k", "Lead delivery teams, Jira/Confluence, stakeholder management and executive communication.", ["Project Management", "Jira", "Agile", "Stakeholder Management"]),
 ("Data Engineer", "Analytics Platform Inc.", "Remote", 1, 110000, 145000, "$110k–$145k", "Python and SQL pipelines, Airflow-style orchestration, cloud data warehouses, data governance.", ["Python", "SQL", "Data Governance", "Azure"]),
 ("Product Manager", "B2B SaaS Montreal", "Montreal, QC", 0, 100000, 135000, "$100k–$135k", "Own roadmap for a B2B SaaS product, discovery, analytics and cross-functional delivery.", ["Product Management", "Data Analytics", "Agile"]),
 ("Machine Learning Engineer", "AI Lab Remote", "Remote", 1, 130000, 170000, "$130k–$170k", "NLP and ML models in production, Python, LLM APIs and evaluation pipelines.", ["Python", "Machine Learning", "NLP", "LLM APIs"]),
 ("IT Support Specialist", "Growing MSP", "Montreal, QC", 0, 55000, 70000, "$55k–$70k", "Front-line support, Linux and Windows systems, ticketing and customer communication.", ["Linux", "Bash", "Git"]),
 ("Business Analyst", "Insurance Group QC", "Quebec City, QC (Hybrid)", 0, 75000, 95000, "$75k–$95k", "Requirements, Excel and SQL reporting, process mapping and stakeholder workshops.", ["Excel", "SQL", "Data Analytics"]),
]

def seed_jobs():
    out = []
    for i, (title, company, loc, remote, smin, smax, stext, desc, tags) in enumerate(SEED_JOBS, 1):
        out.append({"external_id": f"seed-{i}", "title": title, "company": company, "location": loc, "remote": remote, "salary_min": smin, "salary_max": smax, "salary_text": stext, "description": desc, "url": "", "source": "seeded", "tags": tags, "posted_at": ""})
    return out

def insert_jobs(conn, jobs):
    new = 0
    for j in jobs:
        if not j.get("title") or not j.get("company"):
            continue
        cur = conn.execute("INSERT OR IGNORE INTO jobs(external_id,title,company,location,remote,salary_min,salary_max,salary_text,description,url,source,tags,employer_id,posted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (j["external_id"], j["title"], j["company"], j.get("location", ""), int(j.get("remote") or 0), j.get("salary_min"), j.get("salary_max"), j.get("salary_text", ""), j.get("description", ""), j.get("url", ""), j["source"], json.dumps(j.get("tags", [])), j.get("employer_id"), j.get("posted_at", "") or ""))
        new += cur.rowcount
    conn.commit()
    return new

def scan_boards(conn, fetchers=None):
    """Run live adapters; always ensure seed pool exists. Returns per-source report."""
    if fetchers is None:
        fetchers = [("remotive", fetch_remotive), ("arbeitnow", fetch_arbeitnow), ("jobicy", fetch_jobicy), ("remoteok", fetch_remoteok)]
    report = {}
    for name, fn in fetchers:
        try:
            jobs = fn()
            report[name] = {"found": len(jobs), "new": insert_jobs(conn, jobs), "status": "ok"}
        except Exception as e:
            report[name] = {"found": 0, "new": 0, "status": f"error: {type(e).__name__}: {str(e)[:120]}"}
    seed_new = insert_jobs(conn, seed_jobs())
    report["seeded"] = {"found": len(SEED_JOBS), "new": seed_new, "status": "ok (curated fallback pool)"}
    report["total_new"] = sum(v.get("new", 0) for v in report.values() if isinstance(v, dict))
    report["pool_total"] = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    return report
