"""Document vault -> Master Profile.

Every uploaded/pasted supporting document is parsed into facts; facts from all
documents merge into one Master Profile where EVERY fact keeps provenance
(the document id + title it came from). A clean Compiled Master Resume is then
assembled from merged facts only.
"""
import json, re
from .resume_parser import parse_resume, extract_skills, extract_years, EMAIL_RE

DOC_TYPES = ("resume", "cover_letter", "reference_letter", "project_writeup", "transcript", "certification", "other")

_SECTION_RE = re.compile(r"^\s*(EXPERIENCE|WORK EXPERIENCE|EMPLOYMENT|EDUCATION|CERTIFICATIONS?|CERTIFICATES|REFERENCES?|PROJECTS?|SKILLS|SUMMARY|PROFILE|ABOUT)\s*:?\s*$", re.I)
_DATE_RANGE_RE = re.compile(r"(\b(19|20)\d{2}\b\s*[–—-]\s*(Present|present|\b(19|20)\d{2}\b)?)")
_URL_RE = re.compile(r"https?://[^\s)\]]+|(?:www\.)[^\s)\]]+|linkedin\.com/[^\s)\]]+|github\.com/[^\s)\]]+", re.I)
_CERT_HINTS = ("certified", "certificate", "certification", "aws ", "azure", "pmp", "scrum", "cpa", "comptia", "google cloud", "oracle certified")

def _sections(text):
    """Split text into {SECTION: [lines]} using ALL-CAPS-ish headers."""
    secs, cur = {}, "BODY"
    secs[cur] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = _SECTION_RE.match(line)
        if m:
            cur = m.group(1).upper()
            if cur.startswith("WORK") or cur == "EMPLOYMENT":
                cur = "EXPERIENCE"
            elif cur.startswith("REFERENCE"):
                cur = "REFERENCES"
            elif cur.startswith("CERTIF"):
                cur = "CERTIFICATIONS"
            elif cur.startswith("PROJECT"):
                cur = "PROJECTS"
            secs.setdefault(cur, [])
            continue
        secs[cur].append(raw)
    return secs

def _entry_header(line):
    """'Title | Company | 2021 - 2024' or 'Title at Company' -> dict or None."""
    line = line.strip().strip("-•* ")
    if not line or len(line) > 160:
        return None
    parts = [p.strip() for p in re.split(r"\s\|\s|\s·\s", line) if p.strip()]
    period = ""
    if parts and _DATE_RANGE_RE.search(parts[-1]):
        period = parts.pop()
    title, company = "", ""
    if len(parts) >= 2:
        title, company = parts[0], parts[1]
    elif " at " in line.lower():
        idx = line.lower().index(" at ")
        title, company = line[:idx].strip(), line[idx + 4:].strip()
        dm = _DATE_RANGE_RE.search(company)
        if dm:
            period = dm.group(1)
            company = company[:dm.start()].strip(" ,")
    elif _DATE_RANGE_RE.search(line) and any(h in line.lower() for h in ("developer", "engineer", "analyst", "manager", "consultant", "designer", "architect", "specialist", "lead", "intern", "administrator")):
        dm = _DATE_RANGE_RE.search(line)
        period = dm.group(1)
        title = line[:dm.start()].strip(" ,-")
    if title:
        return {"title": title, "company": company, "period": period, "bullets": []}
    return None

def extract_experience(text):
    secs = _sections(text)
    entries = []
    for sec_name in ("EXPERIENCE", "PROJECTS"):
        cur = None
        for raw in secs.get(sec_name, []):
            line = raw.strip()
            if not line:
                cur = None if sec_name == "PROJECTS" else cur
                continue
            hdr = _entry_header(line)
            if hdr:
                hdr["kind"] = "project" if sec_name == "PROJECTS" else "job"
                entries.append(hdr)
                cur = hdr
                continue
            bullet = line.lstrip("-•* ").strip()
            if cur is not None and bullet:
                cur["bullets"].append(bullet)
            elif bullet and sec_name == "EXPERIENCE":
                # free-floating line under EXPERIENCE with no header: keep as bullet of a generic entry
                cur = {"title": "", "company": "", "period": "", "bullets": [bullet], "kind": "job"}
                entries.append(cur)
    return [e for e in entries if e["bullets"] or e["title"]]

def extract_education(text):
    secs = _sections(text)
    out = []
    for raw in secs.get("EDUCATION", []):
        line = raw.strip().strip("-•* ")
        if line and len(line) > 3:
            out.append(line)
    if not out:  # degree hints anywhere
        for line in (text or "").splitlines():
            ll = line.lower()
            if any(d in ll for d in ("bachelor", "master", "phd", "b.sc", "bsc", "m.sc", "msc", "mba", "associate degree", "diploma in")) and len(line.strip()) < 160:
                out.append(line.strip())
    return list(dict.fromkeys(out))[:6]

def extract_certifications(text):
    secs = _sections(text)
    out = []
    for raw in secs.get("CERTIFICATIONS", []):
        line = raw.strip().strip("-•* ")
        if line and len(line) > 2:
            out.append(line)
    for line in (text or "").splitlines():
        ll = line.lower().strip()
        if any(h in ll for h in _CERT_HINTS) and ("certif" in ll or ll.startswith(("aws", "pmp", "comptia"))) and len(ll) < 120:
            out.append(line.strip().strip("-•* "))
    return list(dict.fromkeys(out))[:10]

def extract_references(text):
    secs = _sections(text)
    out = []
    for raw in secs.get("REFERENCES", []):
        line = raw.strip().strip("-•* ")
        if not line or len(line) < 3 or "@" in line and len(line.split()) <= 2:
            continue
        parts = re.split(r"\s[—–-]\s|\s\|\s|,\s", line, maxsplit=1)
        name = parts[0].strip()
        rel = parts[1].strip() if len(parts) > 1 else ""
        if 1 <= len(name.split()) <= 5:
            out.append({"name": name, "relationship": rel, "line": line})
    return out[:6]

def parse_document_facts(text, doc_type="other"):
    text = (text or "").strip()
    base = parse_resume(text) if doc_type in ("resume", "transcript") else {}
    facts = {
        "skills": extract_skills(text),
        "experience": extract_experience(text),
        "education": extract_education(text),
        "certifications": extract_certifications(text),
        "references": extract_references(text),
        "links": list(dict.fromkeys(_URL_RE.findall(text)))[:8],
        "years_exp": extract_years(text),
    }
    if base:
        facts["name"] = base.get("name", "")
        facts["email"] = base.get("email", "")
        facts["phone"] = base.get("phone", "")
        facts["title"] = base.get("title", "")
        facts["summary"] = base.get("summary", "")
    return facts

def empty_master():
    return {"skills": {}, "experience": [], "education": [], "certifications": [], "references": [], "links": [], "titles": [], "years_exp": 0}

def merge_facts(master, doc_id, doc_title, facts):
    """Merge one document's facts into master, tagging provenance."""
    src = {"doc_id": doc_id, "doc_title": doc_title}
    for s in facts.get("skills", []):
        slot = master["skills"].setdefault(s, {"sources": []})
        if src not in slot["sources"]:
            slot["sources"].append(src)
    seen = {(e.get("title", "").lower(), e.get("company", "").lower()) for e in master["experience"]}
    for e in facts.get("experience", []):
        key = (e.get("title", "").lower(), e.get("company", "").lower())
        if key in seen and key != ("", ""):
            for ex in master["experience"]:
                if (ex.get("title", "").lower(), ex.get("company", "").lower()) == key:
                    for b in e.get("bullets", []):
                        if b not in ex["bullets"]:
                            ex["bullets"].append(b)
                    if src not in ex.setdefault("sources", []):
                        ex["sources"].append(src)
            continue
        seen.add(key)
        ne = dict(e)
        ne["sources"] = [src]
        master["experience"].append(ne)
    for field in ("education", "certifications"):
        have = {x.lower() for x in master[field]}
        for item in facts.get(field, []):
            if item.lower() not in have:
                have.add(item.lower())
                master[field].append(item)
                master.setdefault(field + "_sources", {})[item] = [src]
            else:
                master.setdefault(field + "_sources", {}).setdefault(item, [])
                if src not in master[field + "_sources"][item]:
                    master[field + "_sources"][item].append(src)
    have_refs = {r["name"].lower() for r in master["references"]}
    for r in facts.get("references", []):
        if r["name"].lower() not in have_refs:
            have_refs.add(r["name"].lower())
            nr = dict(r)
            nr["sources"] = [src]
            master["references"].append(nr)
    for link in facts.get("links", []):
        if link not in master["links"]:
            master["links"].append(link)
    for t in [facts.get("title", "")]:
        if t and t not in master["titles"]:
            master["titles"].append(t)
    master["years_exp"] = max(master.get("years_exp", 0), facts.get("years_exp", 0) or 0)
    return master

def rebuild_master(conn, candidate_id):
    """Rebuild master profile from scratch from the candidate's documents (so removals are honoured)."""
    cand = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    master = empty_master()
    docs = conn.execute("SELECT * FROM documents WHERE candidate_id=? ORDER BY id", (candidate_id,)).fetchall()
    for d in docs:
        try:
            facts = json.loads(d["facts"] or "{}")
        except Exception:
            facts = {}
        merge_facts(master, d["id"], d["title"] or d["filename"] or f"Document {d['id']}", facts)
    # Fallbacks from the candidate's own parsed resume fields (the primary profile itself).
    if cand is not None:
        try:
            own_skills = json.loads(cand["skills"] or "[]")
        except Exception:
            own_skills = []
        for s in own_skills:
            master["skills"].setdefault(s, {"sources": [{"doc_id": None, "doc_title": "Profile (primary resume parse)"}]})
        if cand["title"] and cand["title"] not in master["titles"]:
            master["titles"].insert(0, cand["title"])
        master["years_exp"] = max(master.get("years_exp", 0), cand["years_exp"] or 0)
    compiled = compile_resume(master, cand)
    conn.execute("UPDATE candidates SET master_profile=?, compiled_resume=? WHERE id=?",
                 (json.dumps(master), compiled, candidate_id))
    conn.commit()
    return master, compiled

def compile_resume(master, cand):
    """Assemble a clean resume text from merged facts only."""
    name = (cand["name"] if cand else "") or "Candidate"
    contact = []
    if cand is not None:
        if cand["email"]:
            contact.append(cand["email"])
        if cand["phone"]:
            contact.append(cand["phone"])
    contact += master.get("links", [])
    lines = [name]
    titles = master.get("titles", [])
    if titles:
        lines.append(titles[0])
    if contact:
        lines.append(" | ".join(contact))
    if cand is not None and cand["summary"]:
        lines += ["", "SUMMARY", cand["summary"]]
    yrs = master.get("years_exp", 0)
    if yrs:
        lines += ["", f"EXPERIENCE: {yrs:g} years"]
    skills = sorted(master.get("skills", {}).keys(), key=str.lower)
    if skills:
        lines += ["", "SKILLS", ", ".join(skills)]
    if master.get("experience"):
        lines += ["", "EXPERIENCE"]
        for e in master["experience"]:
            head = " | ".join([x for x in (e.get("title", ""), e.get("company", ""), e.get("period", "")) if x])
            if head:
                lines.append(head)
            for b in e.get("bullets", []):
                lines.append(f"- {b}")
    if master.get("education"):
        lines += ["", "EDUCATION"] + list(master["education"])
    if master.get("certifications"):
        lines += ["", "CERTIFICATIONS"] + [f"- {c}" for c in master["certifications"]]
    if master.get("references"):
        lines += ["", "REFERENCES"]
        for r in master["references"]:
            lines.append(f"- {r['name']}" + (f" — {r['relationship']}" if r.get("relationship") else ""))
    return "\n".join(lines).strip() + "\n"
