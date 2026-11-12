import os, json
from flask import Flask, request, jsonify, render_template, g
from .db import init_db, get_conn, row_to_dict, get_db_path
from .resume_parser import parse_resume, extract_text_from_pdf
from .scoring import score_job
from .job_sources import scan_boards, insert_jobs
from .apply_engine import apply_for_job, maybe_create_match
from .seed_data import seed_all

def _db_path():
    # Vercel: filesystem is read-only except /tmp
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        return "/tmp/swipehire.db"
    return get_db_path()

def create_app(db_path=None, testing=False):
    app = Flask(__name__)
    app.config["TESTING"] = testing
    path = db_path or ("/tmp/swipehire_test.db" if testing else _db_path())
    app.config["DB_PATH"] = path
    conn = init_db(path); seed_all(conn); conn.close()

    def db():
        if "db" not in g:
            g.db = get_conn(app.config["DB_PATH"])
        return g.db
    @app.teardown_appcontext
    def _close(exc):
        c = g.pop("db", None)
        if c: c.close()

    def cand(cid=None):
        c = db()
        if cid:
            r = c.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        else:
            r = c.execute("SELECT * FROM candidates ORDER BY is_demo ASC, id ASC LIMIT 1").fetchone()
        return row_to_dict(r) if r else None

    @app.route("/")
    def index(): return render_template("index.html")

    @app.route("/api/health")
    def health(): return jsonify({"ok": True, "app": "SwipeHire"})

    @app.route("/api/candidates", methods=["GET"])
    def list_candidates():
        rows = db().execute("SELECT * FROM candidates ORDER BY is_demo, id").fetchall()
        return jsonify([row_to_dict(r) for r in rows])

    @app.route("/api/candidate", methods=["GET"])
    def get_candidate():
        c = cand(request.args.get("candidate_id", type=int))
        if not c: return jsonify({"error":"no candidate"}), 404
        return jsonify(c)

    @app.route("/api/resume", methods=["POST"])
    def upload_resume():
        text = ""
        if "file" in request.files and request.files["file"].filename:
            f = request.files["file"]; data = f.read()
            if f.filename.lower().endswith(".pdf"): text = extract_text_from_pdf(data)
            else: text = data.decode("utf-8", errors="ignore")
        elif request.is_json and request.get_json(silent=True):
            text = request.get_json().get("text","")
        else:
            text = request.form.get("text","") or (request.get_data(as_text=True) or "")
        if not text.strip(): return jsonify({"error":"empty resume"}), 400
        p = parse_resume(text)
        cur = db().execute("INSERT INTO candidates(name,email,phone,title,skills,years_exp,summary,resume_text,is_demo) VALUES(?,?,?,?,?,?,?,?,0)",
            (p["name"],p["email"],p["phone"],p["title"],json.dumps(p["skills"]),p["years_exp"],p["summary"],p["resume_text"]))
        db().commit()
        return jsonify(cand(cur.lastrowid)), 201

    @app.route("/api/candidate/preferences", methods=["POST"])
    def prefs():
        d = request.get_json(force=True)
        c = cand(d.get("candidate_id"))
        if not c: return jsonify({"error":"no candidate"}), 404
        db().execute("UPDATE candidates SET target_titles=?,locations=?,remote_only=?,min_salary=? WHERE id=?",
            (json.dumps(d.get("target_titles", c["target_titles"])), json.dumps(d.get("locations", c["locations"])), 1 if d.get("remote_only") else 0, int(d.get("min_salary") or 0), c["id"]))
        db().commit(); return jsonify(cand(c["id"]))

    @app.route("/api/jobs/scan", methods=["POST"])
    def scan():
        return jsonify(scan_boards(db()))

    @app.route("/api/discovery")
    def discovery():
        c = cand(request.args.get("candidate_id", type=int))
        if not c: return jsonify({"error":"no candidate — upload a resume first"}), 404
        rows = db().execute("SELECT * FROM jobs WHERE id NOT IN (SELECT job_id FROM swipes WHERE candidate_id=?)", (c["id"],)).fetchall()
        out=[]
        for r in rows:
            j=row_to_dict(r); s=score_job(c,j); j.update(s); out.append(j)
        out.sort(key=lambda x: x["score"], reverse=True)
        return jsonify(out)

    @app.route("/api/swipe", methods=["POST"])
    def swipe():
        d=request.get_json(force=True); c=cand(d.get("candidate_id"))
        job_r=db().execute("SELECT * FROM jobs WHERE id=?", (d["job_id"],)).fetchone()
        if not c or not job_r: return jsonify({"error":"candidate or job not found"}), 404
        job=row_to_dict(job_r); direction=d.get("direction")
        if direction not in ("left","right"): return jsonify({"error":"direction must be left/right"}), 400
        db().execute("INSERT OR IGNORE INTO swipes(candidate_id,job_id,direction) VALUES(?,?,?)", (c["id"], job["id"], direction)); db().commit()
        result={"swipe":direction}
        if direction=="right":
            app_obj, created = apply_for_job(db(), c, job)
            result["application"]=app_obj; result["created"]=created
            m=maybe_create_match(db(), c["id"], job["id"])
            if m: result["match"]=m
        return jsonify(result)

    @app.route("/api/applications")
    def applications():
        c=cand(request.args.get("candidate_id", type=int))
        if not c: return jsonify([])
        rows=db().execute("SELECT a.*, j.title, j.company, j.url, j.source FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.candidate_id=? ORDER BY a.created_at DESC, a.id DESC", (c["id"],)).fetchall()
        return jsonify([row_to_dict(r) for r in rows])

    # Employer
    @app.route("/api/employers", methods=["GET","POST"])
    def employers():
        if request.method=="POST":
            d=request.get_json(force=True)
            cur=db().execute("INSERT INTO employers(name,company,email) VALUES(?,?,?)", (d.get("name","Hiring Manager"), d["company"], d.get("email",""))); db().commit()
            return jsonify(row_to_dict(db().execute("SELECT * FROM employers WHERE id=?", (cur.lastrowid,)).fetchone())), 201
        return jsonify([row_to_dict(r) for r in db().execute("SELECT * FROM employers ORDER BY id").fetchall()])

    @app.route("/api/employer/jobs", methods=["GET","POST"])
    def employer_jobs():
        if request.method=="POST":
            d=request.get_json(force=True)
            emp=db().execute("SELECT * FROM employers WHERE id=?", (d.get("employer_id"),)).fetchone() if d.get("employer_id") else db().execute("SELECT * FROM employers ORDER BY id LIMIT 1").fetchone()
            if not emp: return jsonify({"error":"no employer"}), 404
            emp=row_to_dict(emp)
            j={"external_id":f"employer-{emp['id']}-{d['title'][:20].replace(' ','-').lower()}-{os.urandom(3).hex()}","title":d["title"],"company":emp["company"],"location":d.get("location",""),"remote":1 if d.get("remote") else 0,"salary_min":d.get("salary_min"),"salary_max":d.get("salary_max"),"salary_text":d.get("salary_text",""),"description":d.get("description",""),"url":d.get("url",""),"source":"employer","tags":d.get("tags",[]),"employer_id":emp["id"]}
            n=insert_jobs(db(), [j])
            row=db().execute("SELECT * FROM jobs WHERE source='employer' AND employer_id=? ORDER BY id DESC LIMIT 1", (emp["id"],)).fetchone()
            return jsonify(row_to_dict(row)), 201
        eid=request.args.get("employer_id", type=int)
        q="SELECT * FROM jobs WHERE employer_id IS NOT NULL" + (" AND employer_id=?" if eid else "")
        rows=db().execute(q, (eid,) if eid else ()).fetchall()
        return jsonify([row_to_dict(r) for r in rows])

    @app.route("/api/employer/candidates")
    def employer_candidates():
        job_id=request.args.get("job_id", type=int)
        if not job_id:
            j=db().execute("SELECT * FROM jobs WHERE employer_id IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
            if not j: return jsonify({"job":None,"candidates":[]})
            job_id=j["id"]
        job=row_to_dict(db().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        # candidates not yet swiped by this employer for this job
        eid=request.args.get("employer_id", type=int) or job.get("employer_id") or 1
        rows=db().execute("SELECT * FROM candidates WHERE id NOT IN (SELECT candidate_id FROM employer_swipes WHERE employer_id=? AND job_id=?)", (eid, job_id)).fetchall()
        out=[]
        for r in rows:
            c=row_to_dict(r); s=score_job(c, job); c.update(s); out.append(c)
        out.sort(key=lambda x: x["score"], reverse=True)
        return jsonify({"job":job,"candidates":out})

    @app.route("/api/employer/swipe", methods=["POST"])
    def employer_swipe():
        d=request.get_json(force=True)
        job_r=db().execute("SELECT * FROM jobs WHERE id=?", (d["job_id"],)).fetchone()
        if not job_r: return jsonify({"error":"job not found"}), 404
        job=row_to_dict(job_r); eid=d.get("employer_id") or job.get("employer_id") or 1
        direction=d.get("direction")
        if direction not in ("left","right"): return jsonify({"error":"direction must be left/right"}), 400
        db().execute("INSERT OR IGNORE INTO employer_swipes(employer_id,job_id,candidate_id,direction) VALUES(?,?,?,?)", (eid, job["id"], d["candidate_id"], direction)); db().commit()
        result={"swipe":direction}
        if direction=="right":
            # match if candidate right-swiped/applied to this job
            s=db().execute("SELECT id FROM swipes WHERE candidate_id=? AND job_id=? AND direction='right'", (d["candidate_id"], job["id"])).fetchone()
            a=db().execute("SELECT id FROM applications WHERE candidate_id=? AND job_id=?", (d["candidate_id"], job["id"])).fetchone()
            if s or a:
                db().execute("INSERT OR IGNORE INTO matches(candidate_id,employer_id,job_id) VALUES(?,?,?)", (d["candidate_id"], eid, job["id"])); db().commit()
                m=db().execute("SELECT * FROM matches WHERE candidate_id=? AND job_id=?", (d["candidate_id"], job["id"])).fetchone()
                result["match"]=row_to_dict(m)
        return jsonify(result)

    @app.route("/api/matches")
    def matches():
        rows=db().execute("SELECT m.*, c.name AS candidate_name, c.title AS candidate_title, j.title AS job_title, j.company AS company FROM matches m JOIN candidates c ON c.id=m.candidate_id JOIN jobs j ON j.id=m.job_id ORDER BY m.id DESC").fetchall()
        return jsonify([row_to_dict(r) for r in rows])

    @app.route("/api/matches/<int:mid>/messages", methods=["GET","POST"])
    def messages(mid):
        if request.method=="POST":
            d=request.get_json(force=True)
            cur=db().execute("INSERT INTO messages(match_id,sender,body) VALUES(?,?,?)", (mid, d.get("sender","candidate"), d["body"])); db().commit()
            return jsonify(row_to_dict(db().execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone())), 201
        return jsonify([row_to_dict(r) for r in db().execute("SELECT * FROM messages WHERE match_id=? ORDER BY id", (mid,)).fetchall()])

    return app

app = None
def get_app():
    global app
    if app is None: app = create_app()
    return app
