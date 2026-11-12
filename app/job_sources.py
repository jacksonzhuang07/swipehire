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
    req = urllib.request.Request(url, headers={"User-Agent": "SwipeHire/4.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _fetch_text(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "SwipeHire/4.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

# ---------------- v4 sources: Himalayas + We Work Remotely ----------------
# The Muse public API was probed 2026-11-12: every www endpoint 404s and
# api-v2 is Cloudflare-walled (403) — its fetcher stays behind a flag below.
# Working Nomads' public endpoints (/api/exposed/jobs/, /jobs.json) also 404;
# no working no-key endpoint was found, so there is no fetcher for it.

def normalize_himalayas(item):
    desc = _clean(item.get("description", "") or item.get("excerpt", ""))
    cats = [c.replace("-", " ").title() for c in (item.get("categories") or [])]
    skills = extract_skills(item.get("title", "") + " " + desc + " " + " ".join(cats))
    locs = item.get("locationRestrictions") or []
    smin = item.get("minSalary") or None
    smax = item.get("maxSalary") or None
    salary = ""
    if smin or smax:
        cur = item.get("currency") or "USD"
        salary = f"{cur} {smin or ''}–{smax or ''} / {item.get('salaryPeriod') or 'annual'}"
    posted = ""
    if item.get("pubDate"):
        try:
            import datetime
            posted = datetime.datetime.fromtimestamp(int(item["pubDate"]), datetime.timezone.utc).isoformat()
        except Exception:
            posted = ""
    return {"external_id": str(item.get("guid") or item.get("applicationLink") or item.get("title")),
            "title": (item.get("title") or "").strip(), "company": (item.get("companyName") or "").strip(),
            "location": ", ".join(locs) if locs else "Remote", "remote": 1,
            "salary_min": int(smin) if smin else None, "salary_max": int(smax) if smax else None,
            "salary_text": salary, "description": desc[:1200],
            "url": item.get("applicationLink") or item.get("guid") or "",
            "source": "himalayas", "tags": list(dict.fromkeys(cats + skills))[:12], "posted_at": posted}

def fetch_himalayas(pages=5, per_page=20):
    """Himalayas jobs API — cursor pagination (20/page server-side)."""
    out, seen, cursor = [], set(), None
    for _ in range(pages):
        url = f"https://himalayas.app/jobs/api?limit={per_page}"
        if cursor:
            url += f"&cursor={cursor}"
        data = _fetch(url)
        for x in data.get("jobs", []):
            j = normalize_himalayas(x)
            if j["external_id"] not in seen and j["title"]:
                seen.add(j["external_id"])
                out.append(j)
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return out

def _wwr_item_to_dict(item):
    d = {}
    for child in item:
        d[child.tag] = (child.text or "").strip()
    return d

def normalize_wwr(raw):
    """We Work Remotely RSS item (dict of tag->text). Title is 'Company: Role'."""
    full = raw.get("title", "")
    if ": " in full:
        company, title = full.split(": ", 1)
    else:
        company, title = "", full
    desc = _clean(raw.get("description", ""))
    skills = extract_skills(title + " " + desc)
    posted = ""
    if raw.get("pubDate"):
        try:
            import datetime, email.utils
            posted = email.utils.parsedate_to_datetime(raw["pubDate"]).isoformat()
        except Exception:
            posted = ""
    tags = [raw["category"]] if raw.get("category") else []
    return {"external_id": str(raw.get("guid") or raw.get("link")), "title": title.strip(),
            "company": company.strip(), "location": raw.get("region") or "Remote", "remote": 1,
            "salary_min": None, "salary_max": None, "salary_text": "", "description": desc[:1200],
            "url": raw.get("link", ""), "source": "weworkremotely",
            "tags": list(dict.fromkeys(tags + skills))[:12], "posted_at": posted}

def parse_wwr_feed(xml_text):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    return [normalize_wwr(_wwr_item_to_dict(it)) for it in channel.findall("item")]

WWR_FEEDS = [
    "https://weworkremotely.com/remote-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
]

def fetch_weworkremotely(feeds=None):
    out, seen = [], set()
    for url in (feeds or WWR_FEEDS):
        try:
            jobs = parse_wwr_feed(_fetch_text(url))
        except Exception:
            continue
        for j in jobs:
            if j["external_id"] not in seen and j["title"]:
                seen.add(j["external_id"])
                out.append(j)
    if not out:
        raise RuntimeError("weworkremotely: all feeds failed")
    return out

def normalize_themuse(item):
    company = (item.get("company") or {}).get("name", "")
    locations = [l.get("name", "") for l in item.get("locations", [])]
    refs = item.get("refs") or {}
    desc = _clean((item.get("contents") or "")[:4000])
    skills = extract_skills(item.get("name", "") + " " + desc)
    return {"external_id": str(item.get("id") or refs.get("landing_page")), "title": (item.get("name") or "").strip(),
            "company": company.strip(), "location": ", ".join([l for l in locations if l]) or "Unspecified",
            "remote": 1 if any("remote" in l.lower() or "flexible" in l.lower() for l in locations) else 0,
            "salary_min": None, "salary_max": None, "salary_text": "", "description": desc[:1200],
            "url": refs.get("landing_page", ""), "source": "themuse",
            "tags": skills[:12], "posted_at": item.get("publication_date", "") or ""}

def fetch_themuse(pages=2):
    """DISABLED by default (SWIPEHIRE_ENABLE_MUSE=1 to try): the public API
    404s / is Cloudflare-walled as of 2026-11-12. Kept so it can be flipped
    back on if The Muse restores no-key access."""
    out = []
    for page in range(pages):
        data = _fetch(f"https://www.themuse.com/api/public/jobs?page={page}")
        out += [normalize_themuse(x) for x in data.get("results", []) if x.get("name")]
    return out

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
        from .classify import classify_job
        _at, _rl = classify_job(j.get("url", ""))
        cur = conn.execute("INSERT OR IGNORE INTO jobs(external_id,title,company,location,remote,salary_min,salary_max,salary_text,description,url,source,tags,employer_id,posted_at,apply_type,requires_login) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (j["external_id"], j["title"], j["company"], j.get("location", ""), int(j.get("remote") or 0), j.get("salary_min"), j.get("salary_max"), j.get("salary_text", ""), j.get("description", ""), j.get("url", ""), j["source"], json.dumps(j.get("tags", [])), j.get("employer_id"), j.get("posted_at", "") or "", _at, _rl))
        new += cur.rowcount
    conn.commit()
    return new

def default_fetchers():
    fetchers = [("remotive", fetch_remotive), ("arbeitnow", fetch_arbeitnow), ("jobicy", fetch_jobicy),
                ("remoteok", fetch_remoteok), ("himalayas", fetch_himalayas), ("weworkremotely", fetch_weworkremotely)]
    import os
    if os.environ.get("SWIPEHIRE_ENABLE_MUSE"):
        fetchers.append(("themuse", fetch_themuse))
    return fetchers

def scan_boards(conn, fetchers=None):
    """Run live adapters; always ensure seed pool exists. Returns per-source report."""
    if fetchers is None:
        fetchers = default_fetchers()
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
