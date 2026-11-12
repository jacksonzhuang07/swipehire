"""For You ranking.

Candidate feed rank = base match score + preference fit + learned signals from
the user's own swipe history + freshness, then a diversity pass (max 2 per
company / near-identical title in the first 10) and exploration slots
(~every 7th card is a labelled stretch pick). Per-user: one user's swipes
never affect another user's feed.

Employer mirror: candidates for a job ranked by fit + the employer's own
swipe history on candidates.
"""
import re
from datetime import datetime, timezone
from .scoring import score_job

def _words(s):
    return set(re.findall(r"[a-z]+", (s or "").lower())) - {"senior", "junior", "lead", "the", "and", "for"}

def _title_key(title):
    w = sorted(_words(title) - {"remote", "hybrid"})
    return " ".join(w[:3])

def _freshness(job):
    raw = job.get("posted_at") or job.get("created_at") or ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except Exception:
        return 1, ""
    if age_days <= 2:
        return 5, "Posted in the last 2 days"
    if age_days <= 7:
        return 3, "Posted this week"
    if age_days <= 30:
        return 1, ""
    return 0, ""

def learned_from_jobs(jobs):
    """Aggregate skills / title words / companies from a list of job dicts."""
    skills, twords, companies = {}, {}, {}
    for j in jobs:
        for t in j.get("tags", []) or []:
            skills[t.lower()] = skills.get(t.lower(), 0) + 1
        for w in _words(j.get("title", "")):
            twords[w] = twords.get(w, 0) + 1
        c = (j.get("company") or "").lower()
        if c:
            companies[c] = companies.get(c, 0) + 1
    return {"skills": skills, "title_words": twords, "companies": companies}

def learned_from_candidates(cands):
    skills, twords = {}, {}
    for c in cands:
        for s in c.get("skills", []) or []:
            skills[s.lower()] = skills.get(s.lower(), 0) + 1
        for w in _words(c.get("title", "")):
            twords[w] = twords.get(w, 0) + 1
    return {"skills": skills, "title_words": twords, "companies": {}}

def preference_fit(candidate, job, breakdown):
    """0..20 explicit preference component + reasons."""
    pts, reasons = 0, []
    targets = [t.lower() for t in candidate.get("target_titles", []) or []]
    jt = (job.get("title") or "").lower()
    if any(t and t in jt for t in targets):
        pts += 8
        reasons.append("Matches your target title")
    locs = [l.lower() for l in candidate.get("locations", []) or []]
    jloc = (job.get("location") or "").lower()
    if candidate.get("remote_only") and job.get("remote"):
        pts += 4
        reasons.append("Remote — matches your remote-only preference")
    elif locs and any(l in jloc for l in locs):
        pts += 4
        reasons.append("In one of your preferred locations")
    mins = candidate.get("min_salary") or 0
    if mins and job.get("salary_max") and job["salary_max"] >= mins:
        pts += 4
        reasons.append("Salary meets your minimum")
    kw = [t.lower() for t in targets]
    if kw and any(any(w in jt for w in _words(t)) for t in kw) and pts < 8:
        pts += 4
    return pts, reasons

def _learned(job, right, left):
    pts, reasons = 0.0, []
    jskills = {t.lower() for t in job.get("tags", []) or []}
    hit_r = jskills & set(right["skills"])
    hit_l = jskills & (set(left["skills"]) - set(right["skills"]))
    if hit_r:
        pts += min(9, 3 * len(hit_r))
        reasons.append("You right-swiped similar roles (" + ", ".join(sorted(hit_r)[:2]) + ")")
    jt_words = _words(job.get("title", ""))
    if jt_words & set(right["title_words"]):
        pts += 4
    comp = (job.get("company") or "").lower()
    if comp and comp in right["companies"]:
        pts += 2
    if hit_l:
        pts -= min(8, 2 * len(hit_l))
    if jt_words and jt_words <= set(left["title_words"]) and not (jt_words & set(right["title_words"])):
        pts -= 3
    if comp and comp in left["companies"] and comp not in right["companies"]:
        pts -= 3
    return pts, reasons

def rank_jobs(candidate, jobs, right_jobs=(), left_jobs=()):
    """jobs: list of dicts already carrying score/breakdown/matched_skills.
    Returns ranked list with feed_score, why[], is_stretch."""
    right = learned_from_jobs(right_jobs)
    left = learned_from_jobs(left_jobs)
    items = []
    for j in jobs:
        pref, pref_reasons = preference_fit(candidate, j, j.get("breakdown", {}))
        learn, learn_reasons = _learned(j, right, left)
        fresh, fresh_reason = _freshness(j)
        # Real-source jobs outrank the curated seeded fallback when comparable:
        # seeded jobs take a flat penalty so they surface only when real
        # inventory runs thin (their source is labelled on the card).
        seeded_penalty = -15 if j.get("source") == "seeded" else 0
        feed_score = round(j.get("score", 0) * 0.7 + pref + learn + fresh + seeded_penalty, 1)
        why = []
        if j.get("matched_skills"):
            why.append(f"Matches {len(j['matched_skills'])} of your skills: {', '.join(j['matched_skills'][:3])}")
        why += pref_reasons
        why += learn_reasons
        if fresh_reason:
            why.append(fresh_reason)
        if not why:
            why.append(f"Match score {j.get('score', 0)}/100 against your profile")
        item = dict(j)
        item.update({"feed_score": feed_score, "why": why[:3], "is_stretch": False,
                     "components": {"base": j.get("score", 0), "preference": pref, "learned": round(learn, 1), "freshness": fresh, "seeded_penalty": seeded_penalty}})
        items.append(item)
    items.sort(key=lambda x: -x["feed_score"])
    # Diversity pass: at most 2 per company and 2 per near-identical title in the first 10.
    selected, deferred = [], []
    comp_count, title_count = {}, {}
    for it in items:
        if len(selected) < 10:
            c = (it.get("company") or "").lower()
            tk = _title_key(it.get("title", ""))
            if comp_count.get(c, 0) >= 2 or title_count.get(tk, 0) >= 2:
                deferred.append(it)
                continue
            comp_count[c] = comp_count.get(c, 0) + 1
            title_count[tk] = title_count.get(tk, 0) + 1
        selected.append(it)
    ranked = selected + deferred
    # Exploration: every 7th slot (index 6, 13, 20...) gets a stretch pick pulled from beyond the top 10.
    tail = [it for it in ranked[10:] if 20 <= it.get("score", 0) <= 65]
    pos = 6
    used = 0
    while pos < len(ranked) and used < len(tail):
        cand = tail[used]
        used += 1
        ranked.remove(cand)
        cand = dict(cand)
        cand["is_stretch"] = True
        cand["why"] = (["Stretch pick — outside your usual pattern, worth a look"] + cand["why"])[:3]
        ranked.insert(pos, cand)
        pos += 7
    return ranked

def rank_candidates_for_job(employer, job, candidates, right_cands=(), left_cands=()):
    """Employer mirror of the For You ranker."""
    right = learned_from_candidates(right_cands)
    left = learned_from_candidates(left_cands)
    out = []
    for c in candidates:
        s = score_job(c, job)
        cskills = {x.lower() for x in c.get("skills", []) or []}
        learn, lreasons = 0.0, []
        hit_r = cskills & set(right["skills"])
        hit_l = cskills & (set(left["skills"]) - set(right["skills"]))
        if hit_r:
            learn += min(9, 3 * len(hit_r))
            lreasons.append("Similar to candidates you right-swiped (" + ", ".join(sorted(hit_r)[:2]) + ")")
        if hit_l:
            learn -= min(8, 2 * len(hit_l))
        why = []
        if s["matched_skills"]:
            why.append(f"Has {len(s['matched_skills'])} skills this job needs: {', '.join(s['matched_skills'][:3])}")
        if c.get("years_exp"):
            why.append(f"{c['years_exp']:g} years of experience")
        why += lreasons
        if not why:
            why.append(f"Fit score {s['score']}/100 for this job")
        item = dict(c)
        item.update(s)
        item.update({"feed_score": round(s["score"] * 0.7 + learn + min(10, s["breakdown"].get("title", 0) * 0.4), 1),
                     "why": why[:3], "is_stretch": False})
        out.append(item)
    out.sort(key=lambda x: -x["feed_score"])
    return out
