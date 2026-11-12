import json
from .scoring import score_job
SCORE_GATE = 10  # below this, application is flagged needs_review instead of submitted
def _first(c):
    parts = (c.get("name") or "").strip().split()
    return parts[0] if parts else ""

def _last(c):
    parts = (c.get("name") or "").strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""

KNOWN_QUESTIONS = {
    "full_name": lambda c: c.get("name") or "",
    "first_name": _first,
    "last_name": _last,
    "email": lambda c: c.get("email") or "",
    "phone": lambda c: c.get("phone") or "",
    "years_of_experience": lambda c: str(c.get("years_exp") or ""),
    "current_title": lambda c: c.get("title") or "",
    "skills": lambda c: ", ".join(c.get("skills") or []),
    # The user's stored minimum-salary preference is the only salary fact
    # we hold; it is offered as the salary-expectation answer, never a
    # number invented per job.
    "salary_expectation": lambda c: str(c.get("min_salary") or ""),
}
UNKNOWN_QUESTIONS = ["Are you legally authorized to work in the job's country?", "What is your salary expectation?", "Are you willing to relocate?"]

def extension_connected(conn, user_id):
    """The extension counts as connected only once a pairing token issued to
    this user has actually been USED by the extension (last_used_at set) and
    not revoked."""
    if not user_id:
        return False
    r = conn.execute("SELECT id FROM ext_tokens WHERE user_id=? AND revoked=0 AND last_used_at IS NOT NULL LIMIT 1", (user_id,)).fetchone()
    return r is not None

def build_answers(candidate):
    answers, needs = {}, []
    for q, fn in KNOWN_QUESTIONS.items():
        v = fn(candidate)
        if v: answers[q] = v
        else: needs.append(q)
    # Never invent answers — unknown screening questions are logged, not filled.
    for q in UNKNOWN_QUESTIONS:
        needs.append(q)
    return answers, needs

def apply_for_job(conn, candidate, job):
    """Returns (application_dict, created_bool). Dedupe: never apply twice."""
    existing = conn.execute("SELECT * FROM applications WHERE candidate_id=? AND job_id=?", (candidate["id"], job["id"])).fetchone()
    if existing:
        from .db import row_to_dict
        return row_to_dict(existing), False
    scored = score_job(candidate, job)
    answers, needs = build_answers(candidate)
    # HONEST STATUS MODEL (launch): this engine assembles an application
    # package — it does not transmit anything. Nothing here may be recorded
    # as "submitted"; that status is reserved for a future executor that
    # returns a real submission confirmation (Tier 0 form executor /
    # Tier 1 browser extension / Tier 2 linked-account worker).
    if scored["score"] < SCORE_GATE:
        status, method = "needs_review", "score_gate"
        proof = f"Score {scored['score']} below gate {SCORE_GATE} — held for review, not submitted."
    elif job.get("requires_login"):
        # Login-required jobs (LinkedIn / Indeed / Workday-style portals) are
        # the ONLY ones the browser-extension add-on is involved in.
        if extension_connected(conn, candidate.get("user_id")):
            status, method = "queued_extension", "extension"
            proof = ("Queued for the SwipeHire browser extension — it opens this login-required job "
                     "in your own logged-in browser and syncs the result back. Not yet submitted.")
        else:
            status, method = "prepared", "extension_required"
            proof = ("Application package prepared (tailored resume + answers). This job requires your "
                     "login (job network / Workday-style portal) and the SwipeHire extension is not "
                     "connected yet — kept prepared, not submitted.")
    elif job.get("url"):
        status, method = "prepared", "external_link"
        proof = (f"Application package prepared (tailored resume + answers) for {job.get('source','')} job: {job['url']} "
                 "Not yet submitted — no executor has sent this.")
    else:
        status, method = "prepared", "package_only"
        proof = ("Application package prepared (tailored resume + answers). "
                 "Not yet submitted — no executor has sent this.")
    # Truthful tailoring: build a per-job resume from Master Profile facts only.
    tailored_text, coverage_json, tailored_flag = "", "{}", 0
    try:
        from .tailoring import tailor_resume
        master = candidate.get("master_profile") or {}
        if isinstance(master, str):
            master = json.loads(master or "{}")
        if not master:  # fall back to the candidate's own parsed fields — still facts only
            master = {"skills": {s: {"sources": []} for s in (candidate.get("skills") or [])},
                      "titles": [candidate.get("title", "")] if candidate.get("title") else [],
                      "years_exp": candidate.get("years_exp", 0), "experience": [], "education": [], "certifications": []}
        t = tailor_resume(master, candidate, job)
        tailored_text = t["text"]
        coverage_json = json.dumps({"covered": t["covered"], "missing": t["missing"], "gaps": t["gaps"]})
        tailored_flag = 1
        if t["missing"]:
            needs = list(needs) + [f"Keyword gap (not in your profile, left out of tailored resume): {k}" for k in t["missing"]]
    except Exception:
        pass
    cur = conn.execute("INSERT INTO applications(candidate_id,job_id,status,method,proof,answers,needs_review,tailored_resume,coverage,tailored) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (candidate["id"], job["id"], status, method, proof, json.dumps(answers), json.dumps(needs), tailored_text, coverage_json, tailored_flag))
    conn.commit()
    from .db import row_to_dict
    return row_to_dict(conn.execute("SELECT * FROM applications WHERE id=?", (cur.lastrowid,)).fetchone()), True

def maybe_create_match(conn, candidate_id, job_id):
    """If employer already right-swiped this candidate for this job, create match."""
    es = conn.execute("SELECT * FROM employer_swipes WHERE candidate_id=? AND job_id=? AND direction='right'", (candidate_id, job_id)).fetchone()
    if not es: return None
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    employer_id = es["employer_id"]
    conn.execute("INSERT OR IGNORE INTO matches(candidate_id,employer_id,job_id) VALUES(?,?,?)", (candidate_id, employer_id, job_id))
    conn.commit()
    m = conn.execute("SELECT * FROM matches WHERE candidate_id=? AND job_id=?", (candidate_id, job_id)).fetchone()
    from .db import row_to_dict
    return row_to_dict(m)
