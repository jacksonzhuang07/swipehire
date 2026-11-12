"""Truthful resume tailoring.

A tailored resume is assembled ONLY from Master Profile facts: the candidate's
own titles, summary, skills, experience bullets, education and certifications.
Job keywords the candidate does not have are reported as **gaps** — never
inserted into the resume.
"""
import re
from .resume_parser import extract_skills

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "at", "our", "you", "will", "are", "is", "we", "your", "this", "that", "work", "team", "role", "job", "experience", "years", "senior", "junior", "lead"}

def job_keywords(job):
    text = " ".join([job.get("title", "") or "", job.get("description", "") or "", " ".join(job.get("tags") or [])])
    return extract_skills(text)

def _words(s):
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]*", (s or "").lower()) if w not in _STOP and len(w) > 2}

def tailor_resume(master, candidate, job):
    """Returns dict: text, covered, missing (gaps), skills_used, headline_title."""
    master_skills = sorted((master or {}).get("skills", {}).keys(), key=str.lower)
    master_lower = {s.lower(): s for s in master_skills}
    keywords = job_keywords(job)
    covered = [k for k in keywords if k.lower() in master_lower]
    covered_disp = [master_lower[k.lower()] for k in covered]
    missing = [k for k in keywords if k.lower() not in master_lower]

    # Headline title: the candidate's OWN title with the most word overlap with the job title (facts only).
    job_words = _words(job.get("title", ""))
    own_titles = list((master or {}).get("titles", []))
    if candidate.get("title") and candidate["title"] not in own_titles:
        own_titles.insert(0, candidate["title"])
    for e in (master or {}).get("experience", []):
        if e.get("title") and e["title"] not in own_titles:
            own_titles.append(e["title"])
    headline = ""
    best = -1
    for t in own_titles:
        ov = len(_words(t) & job_words)
        if ov > best:
            best, headline = ov, t
    if not headline:
        headline = own_titles[0] if own_titles else "Candidate"

    # Skills reordered: job-matching (covered) first, then the rest — all from master.
    covered_set = {c.lower() for c in covered_disp}
    ordered_skills = covered_disp + [s for s in master_skills if s.lower() not in covered_set]

    # Experience bullets reordered by keyword/word relevance to the job.
    kw_words = set()
    for k in keywords:
        kw_words |= _words(k)
    kw_words |= {w for w in _words(job.get("title", ""))}
    entries = []
    for e in (master or {}).get("experience", []):
        bullets = list(e.get("bullets", []))
        scored = sorted(enumerate(bullets), key=lambda ib: (-len(_words(ib[1]) & kw_words), ib[0]))
        ordered = [b for _, b in scored]
        top = max([len(_words(b) & kw_words) for b in bullets], default=0)
        entries.append((top, e, ordered))
    entries.sort(key=lambda x: -x[0])

    name = candidate.get("name") or "Candidate"
    contact = " | ".join([x for x in (candidate.get("email", ""), candidate.get("phone", "")) if x] + list((master or {}).get("links", [])))
    lines = [name, headline]
    if contact:
        lines.append(contact)
    yrs = (master or {}).get("years_exp", 0) or candidate.get("years_exp", 0)
    if candidate.get("summary"):
        lines += ["", "SUMMARY", candidate["summary"]]
    if covered_disp:
        lines.append(f"Most relevant to this role: {', '.join(covered_disp)}" + (f" · {yrs:g} years of experience" if yrs else ""))
    if ordered_skills:
        lines += ["", "SKILLS (most relevant first)", ", ".join(ordered_skills)]
    if entries:
        lines += ["", "EXPERIENCE (most relevant first)"]
        for _, e, bullets in entries:
            head = " | ".join([x for x in (e.get("title", ""), e.get("company", ""), e.get("period", "")) if x])
            if head:
                lines.append(head)
            for b in bullets:
                lines.append(f"- {b}")
    if (master or {}).get("education"):
        lines += ["", "EDUCATION"] + list(master["education"])
    if (master or {}).get("certifications"):
        lines += ["", "CERTIFICATIONS"] + [f"- {c}" for c in master["certifications"]]
    text = "\n".join(lines).strip() + "\n"
    return {"text": text, "covered": covered_disp, "missing": missing, "gaps": missing,
            "skills_used": ordered_skills, "headline_title": headline}
