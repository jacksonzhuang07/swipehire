import re, json
def _as_list(v):
    if isinstance(v,list): return v
    try: return json.loads(v or "[]")
    except Exception: return []
def score_job(candidate, job):
    cskills={s.lower() for s in _as_list(candidate.get("skills"))}
    jtags={t.lower() for t in _as_list(job.get("tags"))}
    desc=(job.get("description") or "").lower()
    # job skills = tags + skills mentioned in description/title
    jskills=set(jtags)
    matched=sorted([s for s in cskills if s in jskills or s in desc or s in (job.get("title") or "").lower()], key=str.lower)
    skill_score=0
    if jskills or matched:
        denom=max(len(jskills),1) if jskills else 6
        skill_score=min(50, round(50*len(matched)/max(denom, len(matched), 1))) if matched else 0
        if matched and not jskills: skill_score=min(50, 12*len(matched))
    # title
    title_score=0
    ct=(candidate.get("title") or "").lower(); jt=(job.get("title") or "").lower()
    targets=[t.lower() for t in _as_list(candidate.get("target_titles"))]
    hay=[ct]+targets
    twords=set(re.findall(r"[a-z]+", jt))
    for h in hay:
        hw=set(re.findall(r"[a-z]+", h))-{ "senior","junior","lead" }
        if hw and hw & twords: title_score=max(title_score, round(25*len(hw&twords)/len(hw)))
    if any(t and t in jt for t in targets): title_score=25
    # location / remote
    loc_score=0
    locs=[l.lower() for l in _as_list(candidate.get("locations"))]
    jloc=(job.get("location") or "").lower()
    if job.get("remote"): loc_score=15
    elif locs and any(l in jloc for l in locs): loc_score=15
    elif not locs: loc_score=8
    elif candidate.get("remote_only"): loc_score=0
    else: loc_score=5
    # salary
    sal_score=5
    mins=candidate.get("min_salary") or 0
    if mins:
        if job.get("salary_max") and job["salary_max"]>=mins: sal_score=10
        elif job.get("salary_min") and job["salary_min"]>=mins: sal_score=10
        elif job.get("salary_max"): sal_score=2
        else: sal_score=5
    else: sal_score=10 if job.get("salary_min") else 5
    total=max(0,min(100, skill_score+title_score+loc_score+sal_score))
    # present matched skills in candidate's original casing
    orig={s.lower():s for s in _as_list(candidate.get("skills"))}
    return {"score": total, "matched_skills":[orig.get(m,m) for m in matched], "breakdown":{"skills":skill_score,"title":title_score,"location":loc_score,"salary":sal_score}}
