import json, re, urllib.request, html
from .resume_parser import extract_skills

def _clean(t):
    t=html.unescape(re.sub(r"<[^>]+>"," ", t or ""))
    return " ".join(t.split())

def normalize_remotive(item):
    desc=_clean(item.get("description",""))
    tags=item.get("tags") or []
    skills=extract_skills(item.get("title","")+" "+desc+" "+" ".join(tags))
    salary=item.get("salary") or ""
    nums=re.findall(r"\$?([\d,]{4,})", salary)
    smin=smax=None
    if len(nums)>=2: smin, smax = int(nums[0].replace(",","")), int(nums[1].replace(",",""))
    elif len(nums)==1: smin=smax=int(nums[0].replace(",",""))
    return {"external_id":str(item.get("id") or item.get("url")),"title":item.get("title","").strip(),"company":item.get("company_name","").strip(),"location":item.get("candidate_required_location") or "Remote","remote":1,"salary_min":smin,"salary_max":smax,"salary_text":salary,"description":desc[:1200],"url":item.get("url",""),"source":"remotive","tags":list(dict.fromkeys(tags+skills))[:12]}

def normalize_arbeitnow(item):
    desc=_clean(item.get("description",""))
    tags=item.get("tags") or []
    skills=extract_skills(item.get("title","")+" "+desc+" "+" ".join(tags))
    loc=", ".join(item.get("location") if isinstance(item.get("location"),list) else [item.get("location") or ""])
    return {"external_id":str(item.get("slug") or item.get("url")),"title":(item.get("title") or "").strip(),"company":(item.get("company_name") or "").strip(),"location":loc or "Unspecified","remote":1 if item.get("remote") else 0,"salary_min":None,"salary_max":None,"salary_text":"","description":desc[:1200],"url":item.get("url",""),"source":"arbeitnow","tags":list(dict.fromkeys(tags+skills))[:12]}

def _fetch(url, timeout=8):
    req=urllib.request.Request(url, headers={"User-Agent":"SwipeHire/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_remotive(limit=30):
    data=_fetch("https://remotive.com/api/remote-jobs?limit=%d" % limit)
    return [normalize_remotive(x) for x in data.get("jobs",[])]

def fetch_arbeitnow(limit=30):
    data=_fetch("https://www.arbeitnow.com/api/job-board-api")
    items=data.get("data",[])[:limit]
    return [normalize_arbeitnow(x) for x in items]

SEED_JOBS=[
 ("Solutions Architect","CloudNorth","Toronto, ON (Hybrid)",0,110000,145000,"$110k–$145k CAD","Design cloud solutions on Azure and AWS, lead client workshops, build Python automation and REST API integrations for enterprise clients.",["AWS","Azure","Python","REST APIs"]),
 ("Automation Consultant","Omnitrans-style Logistics Co.","Montreal, QC (Hybrid)",0,85000,105000,"$85k–$105k CAD","Own process automation: Python pipelines, OCR document processing, Power BI dashboards and stakeholder delivery.",["Python","Power BI","OCR","Process Automation"]),
 ("Data Analyst","Fintech Montreal","Montreal, QC",0,70000,90000,"$70k–$90k CAD","Build Power BI dashboards, SQL queries and Pandas ETL for product and finance teams.",["SQL","Power BI","Pandas","Data Analytics"]),
 ("Backend Developer (Python)","Remote SaaS Co.","Remote",1,95000,130000,"$95k–$130k","Build Flask/FastAPI services, PostgreSQL data models and CI/CD pipelines in a fully remote team.",["Python","Flask","PostgreSQL","Docker"]),
 ("Frontend Developer","HealthTech Remote","Remote",1,90000,120000,"$90k–$120k","React and TypeScript product work, design systems, API integration with a distributed team.",["React","TypeScript","JavaScript","CSS"]),
 ("Full Stack Developer","Startup Hub TO","Toronto, ON",0,90000,125000,"$90k–$125k","JavaScript, Node.js and React across the stack; ship features end to end with a small team.",["JavaScript","React","Node.js","SQL"]),
 ("DevOps Engineer","CloudScale","Remote",1,115000,150000,"$115k–$150k","Kubernetes, Terraform, AWS infrastructure, CI/CD and Linux systems at scale.",["Kubernetes","AWS","Terraform","Linux","CI/CD"]),
 ("QA Automation Engineer","Federal Digital Services","Ottawa, ON (Hybrid)",0,80000,100000,"$80k–$100k","Selenium and Python test automation, regression suites, Agile delivery for public services.",["Selenium","Python","Testing","Agile"]),
 ("Technical Project Manager","Enterprise Consulting Group","Montreal, QC (Hybrid)",0,95000,120000,"$95k–$120k","Lead delivery teams, Jira/Confluence, stakeholder management and executive communication.",["Project Management","Jira","Agile","Stakeholder Management"]),
 ("Data Engineer","Analytics Platform Inc.","Remote",1,110000,145000,"$110k–$145k","Python and SQL pipelines, Airflow-style orchestration, cloud data warehouses, data governance.",["Python","SQL","Data Governance","Azure"]),
 ("Product Manager","B2B SaaS Montreal","Montreal, QC",0,100000,135000,"$100k–$135k","Own roadmap for a B2B SaaS product, discovery, analytics and cross-functional delivery.",["Product Management","Data Analytics","Agile"]),
 ("Machine Learning Engineer","AI Lab Remote","Remote",1,130000,170000,"$130k–$170k","NLP and ML models in production, Python, LLM APIs and evaluation pipelines.",["Python","Machine Learning","NLP","LLM APIs"]),
 ("IT Support Specialist","Growing MSP","Montreal, QC",0,55000,70000,"$55k–$70k","Front-line support, Linux and Windows systems, ticketing and customer communication.",["Linux","Bash","Git"]),
 ("Business Analyst","Insurance Group QC","Quebec City, QC (Hybrid)",0,75000,95000,"$75k–$95k","Requirements, Excel and SQL reporting, process mapping and stakeholder workshops.",["Excel","SQL","Data Analytics"]),
]

def seed_jobs():
    out=[]
    for i,(title,company,loc,remote,smin,smax,stext,desc,tags) in enumerate(SEED_JOBS,1):
        out.append({"external_id":f"seed-{i}","title":title,"company":company,"location":loc,"remote":remote,"salary_min":smin,"salary_max":smax,"salary_text":stext,"description":desc,"url":"","source":"seeded","tags":tags})
    return out

def insert_jobs(conn, jobs):
    new=0
    for j in jobs:
        cur=conn.execute("INSERT OR IGNORE INTO jobs(external_id,title,company,location,remote,salary_min,salary_max,salary_text,description,url,source,tags,employer_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (j["external_id"],j["title"],j["company"],j.get("location",""),int(j.get("remote") or 0),j.get("salary_min"),j.get("salary_max"),j.get("salary_text",""),j.get("description",""),j.get("url",""),j["source"],json.dumps(j.get("tags",[])),j.get("employer_id")))
        new+=cur.rowcount
    conn.commit(); return new

def scan_boards(conn, fetchers=None):
    """Run live adapters; always ensure seed pool exists. Returns per-source report."""
    if fetchers is None: fetchers=[("remotive",fetch_remotive),("arbeitnow",fetch_arbeitnow)]
    report={}
    for name,fn in fetchers:
        try:
            jobs=fn(); report[name]={"found":len(jobs),"new":insert_jobs(conn,jobs),"status":"ok"}
        except Exception as e:
            report[name]={"found":0,"new":0,"status":f"error: {type(e).__name__}: {str(e)[:120]}"}
    seed_new=insert_jobs(conn, seed_jobs())
    report["seeded"]={"found":len(SEED_JOBS),"new":seed_new,"status":"ok (curated fallback pool)"}
    report["total_new"]=sum(v.get("new",0) for v in report.values() if isinstance(v,dict))
    return report
