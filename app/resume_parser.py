import re, io
SKILLS=["Python","JavaScript","TypeScript","React","Node.js","Flask","Django","FastAPI","SQL","PostgreSQL","MySQL","Azure SQL","Microsoft Access","Power BI","Pandas","Selenium","REST APIs","REST API","API Integration","Git","Docker","Kubernetes","AWS","Azure","GCP","Linux","Bash","PowerShell","HTML","CSS","Streamlit","Supabase","Java","C++","C#","Go","Rust","Machine Learning","NLP","OCR","Tesseract OCR","Data Analytics","Data Governance","Agile","Jira","Confluence","Excel","VBA","PeopleSoft","PeopleCode","Project Management","Stakeholder Management","Process Automation","Systems Integration","Workflow Modernization","Tableau","MongoDB","Redis","GraphQL","Vue","Angular","Testing","CI/CD","Terraform","Figma","Product Management","Salesforce","SAP","Airflow","ETL","Tailwind CSS","Next.js","LLM APIs","Prompt Engineering","React Native","Spring","Kafka","Snowflake","Databricks"]
# longest first so REST APIs wins over REST API etc.
SKILLS_SORTED=sorted(SKILLS, key=len, reverse=True)
EMAIL_RE=re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE=re.compile(r"(?:\+?1[\s\-\.]?)?(?:\(?\d{3}\)?[\s\-\.])\d{3}[\s\-\.]\d{4}|\b\d{10}\b")
TITLE_HINTS=["consultant","engineer","developer","architect","analyst","manager","designer","scientist","administrator","specialist","lead","director","product","data"]

def extract_text_from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        r=PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception:
        pass
    # graceful fallback: pull printable strings
    try:
        txt=data.decode("utf-8", errors="ignore")
        return re.sub(r"[^\x09\x0a\x0d\x20-\x7e]+"," ",txt)
    except Exception:
        return ""

def extract_skills(text):
    low=text.lower(); found=[]
    for s in SKILLS_SORTED:
        if s.lower() in low and s not in found:
            # avoid REST API duplicate when REST APIs present
            if s=="REST API" and "REST APIs" in found: continue
            found.append(s)
    return found

def extract_years(text):
    m=re.findall(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b", text, re.I)
    if m: return float(max(float(x) for x in m))
    # date ranges like September 2025 – Present, 2022 - 2025: sum spans, cap 40
    years=re.findall(r"\b(20\d{2}|19\d{2})\b\s*[–—-]\s*(?:Present|present|(20\d{2}|19\d{2}))", text)
    total=0
    import datetime
    now=datetime.date.today().year
    for start,end in years:
        e=now if end=="" else int(end)
        try: total+=max(0,e-int(start))
        except: pass
    return float(min(total,40)) if total else 0.0

def parse_resume(text: str) -> dict:
    text=(text or "").strip()
    lines=[l.strip() for l in text.splitlines() if l.strip()]
    email_m=EMAIL_RE.search(text); phone_m=PHONE_RE.search(text)
    name=""
    if lines:
        cand=lines[0]
        if 2 <= len(cand.split()) <= 5 and "@" not in cand and len(cand)<60: name=cand.title() if cand.isupper() else cand
    title=""
    for l in lines[1:6]:
        ll=l.lower()
        if any(h in ll for h in TITLE_HINTS) and len(l)<120:
            title=l.split("·")[0].split("|")[0].strip(); break
    summary=""
    # PROFILE / SUMMARY section
    m=re.search(r"(?:PROFILE|SUMMARY|ABOUT)\s*\n(.{40,600}?)(?:\n[A-Z][A-Z &/]{3,}\n|\Z)", text, re.S|re.I)
    if m: summary=" ".join(m.group(1).split())[:600]
    elif len(lines)>2: summary=" ".join(lines[2:5])[:400]
    return {"name":name or "Unknown Candidate","email":email_m.group(0) if email_m else "","phone":phone_m.group(0) if phone_m else "","title":title,"skills":extract_skills(text),"years_exp":extract_years(text),"summary":summary,"resume_text":text[:20000]}
