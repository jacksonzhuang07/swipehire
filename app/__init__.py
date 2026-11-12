import os, json, uuid, functools
from flask import Flask, request, jsonify, render_template, g, session, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from .db import init_db, get_conn, row_to_dict, get_db_path, get_uploads_dir
from .resume_parser import parse_resume, extract_text_from_pdf
from .scoring import score_job
from .job_sources import scan_boards, insert_jobs
from .apply_engine import apply_for_job, maybe_create_match
from .seed_data import seed_all
from .documents import parse_document_facts, rebuild_master, DOC_TYPES
from .feed import rank_jobs, rank_candidates_for_job

PHOTO_EXTS = {".png", ".jpg", ".jpeg"}
DOC_EXTS = {".txt", ".md", ".pdf"}
MAX_PHOTO_BYTES = 3 * 1024 * 1024

def _db_path():
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        return "/tmp/swipehire.db"
    return get_db_path()

def _uploads_for(db_path, testing):
    if testing:
        return os.path.join("/tmp", "swipehire_test_uploads_" + os.path.basename(db_path).replace(".", "_"))
    return get_uploads_dir()

def create_app(db_path=None, testing=False):
    app = Flask(__name__)
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.secret_key = os.environ.get("SWIPEHIRE_SECRET") or "swipehire-dev-secret-change-in-prod"
    path = db_path or ("/tmp/swipehire_test.db" if testing else _db_path())
    app.config["DB_PATH"] = path
    uploads = _uploads_for(path, testing)
    os.makedirs(uploads, exist_ok=True)
    app.config["UPLOADS_DIR"] = uploads
    conn = init_db(path)
    seed_all(conn, uploads_dir=uploads)
    # Build master profiles for any seeded candidates that lack one.
    for r in conn.execute("SELECT id FROM candidates WHERE master_profile IS NULL OR master_profile='{}'").fetchall():
        rebuild_master(conn, r["id"])
    conn.close()

    def db():
        if "db" not in g:
            g.db = get_conn(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def _close(exc):
        c = g.pop("db", None)
        if c:
            c.close()

    # ---------- auth helpers ----------
    def current_user():
        uid = session.get("user_id")
        if not uid:
            return None
        r = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None

    def login_required(role=None):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                u = current_user()
                if not u:
                    return jsonify({"error": "login required"}), 401
                if role and u["role"] != role:
                    return jsonify({"error": f"{role} account required"}), 403
                g.user = u
                return fn(*a, **kw)
            return wrapper
        return deco

    def my_candidate():
        u = g.user
        if not u.get("candidate_id"):
            return None
        r = db().execute("SELECT * FROM candidates WHERE id=?", (u["candidate_id"],)).fetchone()
        return row_to_dict(r) if r else None

    def my_employer():
        u = g.user
        if not u.get("employer_id"):
            return None
        r = db().execute("SELECT * FROM employers WHERE id=?", (u["employer_id"],)).fetchone()
        return row_to_dict(r) if r else None

    def public_candidate(c):
        if not c:
            return None
        c = dict(c)
        c["photo_url"] = f"/uploads/{c['photo']}" if c.get("photo") else ""
        c.pop("resume_text", None)
        c.pop("compiled_resume", None)
        return c

    def _create_user_account(email, password, role, name, company=""):
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            return None, (jsonify({"error": "valid email required"}), 400)
        if not password or len(password) < 4:
            return None, (jsonify({"error": "password too short (min 4)"}), 400)
        if db().execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return None, (jsonify({"error": "email already registered"}), 409)
        cand_id = emp_id = None
        if role == "candidate":
            cur = db().execute("INSERT INTO candidates(name,email) VALUES(?,?)", (name or email.split("@")[0], email))
            cand_id = cur.lastrowid
        else:
            cur = db().execute("INSERT INTO employers(name,company,email) VALUES(?,?,?)", (name or "Hiring Manager", company or "My Company", email))
            emp_id = cur.lastrowid
        cur = db().execute("INSERT INTO users(email,password_hash,role,name,candidate_id,employer_id) VALUES(?,?,?,?,?,?)",
                           (email, generate_password_hash(password), role, name or "", cand_id, emp_id))
        db().commit()
        return cur.lastrowid, None

    # ---------- pages / health ----------
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/health")
    def health():
        return jsonify({"ok": True, "app": "SwipeHire", "version": 2})

    @app.route("/uploads/<path:filename>")
    @login_required()
    def uploads(filename):
        return send_from_directory(app.config["UPLOADS_DIR"], os.path.basename(filename))

    # ---------- auth ----------
    @app.route("/api/auth/signup", methods=["POST"])
    def signup():
        d = request.get_json(force=True)
        role = d.get("role", "candidate")
        if role not in ("candidate", "employer"):
            return jsonify({"error": "role must be candidate or employer"}), 400
        uid, err = _create_user_account(d.get("email", ""), d.get("password", ""), role, d.get("name", ""), d.get("company", ""))
        if err:
            return err
        session["user_id"] = uid
        return jsonify({"user": _me_payload()}), 201

    @app.route("/api/auth/login", methods=["POST"])
    def login():
        d = request.get_json(force=True)
        email = (d.get("email") or "").strip().lower()
        r = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not r or not check_password_hash(r["password_hash"], d.get("password", "")):
            return jsonify({"error": "invalid email or password"}), 401
        session["user_id"] = r["id"]
        return jsonify({"user": _me_payload()})

    @app.route("/api/auth/logout", methods=["POST"])
    def logout():
        session.clear()
        return jsonify({"ok": True})

    def _me_payload():
        u = current_user()
        if not u:
            return None
        payload = {"id": u["id"], "email": u["email"], "role": u["role"], "name": u["name"]}
        if u.get("candidate_id"):
            r = db().execute("SELECT * FROM candidates WHERE id=?", (u["candidate_id"],)).fetchone()
            payload["candidate"] = public_candidate(row_to_dict(r))
        if u.get("employer_id"):
            r = db().execute("SELECT * FROM employers WHERE id=?", (u["employer_id"],)).fetchone()
            e = row_to_dict(r)
            if e:
                e["logo_url"] = f"/uploads/{e['logo']}" if e.get("logo") else ""
            payload["employer"] = e
        return payload

    @app.route("/api/demo-accounts")
    def demo_accounts():
        """Public list of the clearly-labelled seeded demo accounts (password 'demo'). Demo-grade only."""
        rows = db().execute("SELECT email, role, name FROM users WHERE is_demo=1").fetchall()
        out = []
        for r in rows:
            out.append({"email": r["email"], "role": r["role"], "name": r["name"]})
        return jsonify(out)

    @app.route("/api/auth/me")
    def me():
        p = _me_payload()
        if not p:
            return jsonify({"error": "login required"}), 401
        return jsonify(p)

    # ---------- candidate: profile / photo / preferences ----------
    @app.route("/api/candidate", methods=["GET"])
    @login_required("candidate")
    def get_candidate():
        return jsonify(public_candidate(my_candidate()))

    @app.route("/api/resume", methods=["POST"])
    @login_required("candidate")
    def upload_resume():
        c = my_candidate()
        text = _request_text()
        if not text or not text.strip():
            return jsonify({"error": "empty resume"}), 400
        p = parse_resume(text)
        db().execute("UPDATE candidates SET name=CASE WHEN ? != '' THEN ? ELSE name END,email=CASE WHEN ? != '' THEN ? ELSE email END,phone=?,title=?,skills=?,years_exp=?,summary=?,resume_text=? WHERE id=?",
                     (p["name"], p["name"], p["email"], p["email"], p["phone"], p["title"], json.dumps(p["skills"]), p["years_exp"], p["summary"], p["resume_text"], c["id"]))
        db().commit()
        _add_document(c["id"], "resume", "Primary resume", "", text)
        return jsonify(public_candidate(my_candidate())), 201

    def _request_text():
        if "file" in request.files and request.files["file"].filename:
            f = request.files["file"]
            data = f.read()
            if f.filename.lower().endswith(".pdf"):
                return extract_text_from_pdf(data)
            return data.decode("utf-8", errors="ignore")
        if request.is_json and request.get_json(silent=True):
            return request.get_json().get("text", "")
        return request.form.get("text", "") or (request.get_data(as_text=True) or "")

    def _request_file_text_and_name():
        if "file" in request.files and request.files["file"].filename:
            f = request.files["file"]
            ext = os.path.splitext(f.filename.lower())[1]
            if ext not in DOC_EXTS:
                return None, None, (jsonify({"error": f"unsupported file type {ext}; allowed: {sorted(DOC_EXTS)}"}), 400)
            data = f.read()
            text = extract_text_from_pdf(data) if ext == ".pdf" else data.decode("utf-8", errors="ignore")
            return text, f.filename, None
        return None, None, None

    @app.route("/api/profile/photo", methods=["POST"])
    @login_required("candidate")
    def upload_photo():
        return _save_image("candidates", "photo", my_candidate()["id"])

    @app.route("/api/employer/logo", methods=["POST"])
    @login_required("employer")
    def upload_logo():
        return _save_image("employers", "logo", my_employer()["id"])

    def _save_image(table, column, row_id):
        if "file" not in request.files or not request.files["file"].filename:
            return jsonify({"error": "file required"}), 400
        f = request.files["file"]
        ext = os.path.splitext(f.filename.lower())[1]
        if ext == ".jpeg":
            ext = ".jpg"
        if ext not in PHOTO_EXTS:
            return jsonify({"error": "only .png/.jpg images allowed"}), 400
        data = f.read()
        if len(data) > MAX_PHOTO_BYTES:
            return jsonify({"error": "image too large (max 3MB)"}), 413
        if not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff")):
            return jsonify({"error": "file is not a real PNG/JPEG image"}), 400
        name = f"{table}_{row_id}_{uuid.uuid4().hex[:10]}{ext}"
        with open(os.path.join(app.config["UPLOADS_DIR"], name), "wb") as fh:
            fh.write(data)
        db().execute(f"UPDATE {table} SET {column}=? WHERE id=?", (name, row_id))
        db().commit()
        return jsonify({"ok": True, "url": f"/uploads/{name}", "filename": name})

    @app.route("/api/candidate/preferences", methods=["POST"])
    @login_required("candidate")
    def prefs():
        d = request.get_json(force=True)
        c = my_candidate()
        db().execute("UPDATE candidates SET target_titles=?,locations=?,remote_only=?,min_salary=? WHERE id=?",
                     (json.dumps(d.get("target_titles", c["target_titles"])), json.dumps(d.get("locations", c["locations"])), 1 if d.get("remote_only") else 0, int(d.get("min_salary") or 0), c["id"]))
        db().commit()
        r = db().execute("SELECT * FROM candidates WHERE id=?", (c["id"],)).fetchone()
        return jsonify(public_candidate(row_to_dict(r)))

    # ---------- document vault / master profile / compiled resume ----------
    def _add_document(candidate_id, doc_type, title, filename, text):
        facts = parse_document_facts(text, doc_type)
        cur = db().execute("INSERT INTO documents(candidate_id,doc_type,title,filename,text,facts) VALUES(?,?,?,?,?,?)",
                           (candidate_id, doc_type, title or filename or doc_type, filename, text[:60000], json.dumps(facts)))
        db().commit()
        rebuild_master(db(), candidate_id)
        return cur.lastrowid

    @app.route("/api/documents", methods=["GET", "POST"])
    @login_required("candidate")
    def documents():
        c = my_candidate()
        if request.method == "POST":
            doc_type = (request.form.get("doc_type") if not request.is_json else request.get_json(silent=True).get("doc_type")) or "other"
            if doc_type not in DOC_TYPES:
                return jsonify({"error": f"doc_type must be one of {DOC_TYPES}"}), 400
            title = ""
            filename = ""
            if request.is_json:
                d = request.get_json()
                text, title = d.get("text", ""), d.get("title", "")
            else:
                title = request.form.get("title", "")
                text, filename, err = _request_file_text_and_name()
                if err:
                    return err
                if text is None:
                    text = request.form.get("text", "")
            if not text or not text.strip():
                return jsonify({"error": "empty document"}), 400
            did = _add_document(c["id"], doc_type, title, filename, text)
            r = db().execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
            return jsonify(row_to_dict(r)), 201
        rows = db().execute("SELECT id,candidate_id,doc_type,title,filename,facts,created_at FROM documents WHERE candidate_id=? ORDER BY id", (c["id"],)).fetchall()
        return jsonify([row_to_dict(r) for r in rows])

    @app.route("/api/documents/<int:did>", methods=["DELETE"])
    @login_required("candidate")
    def delete_document(did):
        c = my_candidate()
        r = db().execute("SELECT id FROM documents WHERE id=? AND candidate_id=?", (did, c["id"])).fetchone()
        if not r:
            return jsonify({"error": "document not found"}), 404
        db().execute("DELETE FROM documents WHERE id=?", (did,))
        db().commit()
        rebuild_master(db(), c["id"])
        return jsonify({"ok": True, "deleted": did})

    @app.route("/api/master-profile")
    @login_required("candidate")
    def master_profile():
        c = my_candidate()
        return jsonify(c.get("master_profile") or {})

    @app.route("/api/compiled-resume")
    @login_required("candidate")
    def compiled_resume():
        c = my_candidate()
        r = db().execute("SELECT compiled_resume FROM candidates WHERE id=?", (c["id"],)).fetchone()
        return jsonify({"text": r["compiled_resume"] or ""})

    @app.route("/api/compiled-resume/download")
    @login_required("candidate")
    def compiled_download():
        c = my_candidate()
        r = db().execute("SELECT compiled_resume FROM candidates WHERE id=?", (c["id"],)).fetchone()
        text = r["compiled_resume"] or ""
        if request.args.get("format") == "html":
            body = "<!doctype html><html><head><meta charset='utf-8'><title>Master Resume</title><style>body{font-family:Georgia,serif;max-width:760px;margin:40px auto;line-height:1.5;white-space:pre-wrap}</style></head><body>" + _esc(text) + "</body></html>"
            return Response(body, mimetype="text/html")
        return Response(text, mimetype="text/plain", headers={"Content-Disposition": "attachment; filename=master_resume.txt"})

    def _esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # ---------- jobs / discovery / swipe ----------
    @app.route("/api/jobs/scan", methods=["POST"])
    @login_required()
    def scan():
        return jsonify(scan_boards(db()))

    @app.route("/api/discovery")
    @login_required("candidate")
    def discovery():
        c = my_candidate()
        rows = db().execute("SELECT * FROM jobs WHERE id NOT IN (SELECT job_id FROM swipes WHERE candidate_id=?)", (c["id"],)).fetchall()
        out = []
        for r in rows:
            j = row_to_dict(r)
            j.update(score_job(c, j))
            out.append(j)
        right_jobs = [row_to_dict(r) for r in db().execute("SELECT j.* FROM jobs j JOIN swipes s ON s.job_id=j.id WHERE s.candidate_id=? AND s.direction='right'", (c["id"],)).fetchall()]
        left_jobs = [row_to_dict(r) for r in db().execute("SELECT j.* FROM jobs j JOIN swipes s ON s.job_id=j.id WHERE s.candidate_id=? AND s.direction='left'", (c["id"],)).fetchall()]
        return jsonify(rank_jobs(c, out, right_jobs, left_jobs))

    @app.route("/api/swipe", methods=["POST"])
    @login_required("candidate")
    def swipe():
        d = request.get_json(force=True)
        c = my_candidate()
        job_r = db().execute("SELECT * FROM jobs WHERE id=?", (d.get("job_id"),)).fetchone()
        if not job_r:
            return jsonify({"error": "job not found"}), 404
        job = row_to_dict(job_r)
        direction = d.get("direction")
        if direction not in ("left", "right"):
            return jsonify({"error": "direction must be left/right"}), 400
        db().execute("INSERT OR IGNORE INTO swipes(candidate_id,job_id,direction) VALUES(?,?,?)", (c["id"], job["id"], direction))
        db().commit()
        result = {"swipe": direction}
        if direction == "right":
            fresh = row_to_dict(db().execute("SELECT * FROM candidates WHERE id=?", (c["id"],)).fetchone())
            app_obj, created = apply_for_job(db(), fresh, job)
            result["application"] = app_obj
            result["created"] = created
            m = maybe_create_match(db(), c["id"], job["id"])
            if m:
                result["match"] = m
        return jsonify(result)

    @app.route("/api/applications")
    @login_required("candidate")
    def applications():
        c = my_candidate()
        rows = db().execute("SELECT a.*, j.title, j.company, j.url, j.source FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.candidate_id=? ORDER BY a.created_at DESC, a.id DESC", (c["id"],)).fetchall()
        out = []
        for r in rows:
            d = row_to_dict(r)
            d["has_tailored"] = bool(d.pop("tailored_resume", ""))
            out.append(d)
        return jsonify(out)

    @app.route("/api/applications/<int:aid>/tailored-resume")
    @login_required("candidate")
    def tailored_resume(aid):
        c = my_candidate()
        r = db().execute("SELECT a.*, j.title, j.company FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.candidate_id=?", (aid, c["id"])).fetchone()
        if not r:
            return jsonify({"error": "application not found"}), 404
        d = row_to_dict(r)
        if request.args.get("download"):
            return Response(d.get("tailored_resume") or "", mimetype="text/plain",
                            headers={"Content-Disposition": f"attachment; filename=tailored_resume_{aid}.txt"})
        return jsonify({"text": d.get("tailored_resume") or "", "coverage": d.get("coverage") or {}, "tailored": d.get("tailored", 0), "job_title": d.get("title"), "company": d.get("company")})

    # ---------- employer ----------
    @app.route("/api/candidates", methods=["GET"])
    @login_required("employer")
    def list_candidates():
        rows = db().execute("SELECT * FROM candidates ORDER BY is_demo, id").fetchall()
        return jsonify([public_candidate(row_to_dict(r)) for r in rows])

    @app.route("/api/employer/jobs", methods=["GET", "POST"])
    @login_required("employer")
    def employer_jobs():
        emp = my_employer()
        if request.method == "POST":
            d = request.get_json(force=True)
            if not d.get("title"):
                return jsonify({"error": "title required"}), 400
            j = {"external_id": f"employer-{emp['id']}-{d['title'][:20].replace(' ', '-').lower()}-{uuid.uuid4().hex[:6]}", "title": d["title"], "company": emp["company"], "location": d.get("location", ""), "remote": 1 if d.get("remote") else 0, "salary_min": d.get("salary_min"), "salary_max": d.get("salary_max"), "salary_text": d.get("salary_text", ""), "description": d.get("description", ""), "url": d.get("url", ""), "source": "employer", "tags": d.get("tags", []), "employer_id": emp["id"]}
            insert_jobs(db(), [j])
            row = db().execute("SELECT * FROM jobs WHERE employer_id=? ORDER BY id DESC LIMIT 1", (emp["id"],)).fetchone()
            return jsonify(row_to_dict(row)), 201
        rows = db().execute("SELECT * FROM jobs WHERE employer_id=? ORDER BY id DESC", (emp["id"],)).fetchall()
        return jsonify([row_to_dict(r) for r in rows])

    @app.route("/api/employer/candidates")
    @login_required("employer")
    def employer_candidates():
        emp = my_employer()
        job_id = request.args.get("job_id", type=int)
        if not job_id:
            j = db().execute("SELECT * FROM jobs WHERE employer_id=? ORDER BY id DESC LIMIT 1", (emp["id"],)).fetchone()
            if not j:
                return jsonify({"job": None, "candidates": []})
            job_id = j["id"]
        job_r = db().execute("SELECT * FROM jobs WHERE id=? AND employer_id=?", (job_id, emp["id"])).fetchone()
        if not job_r:
            return jsonify({"error": "job not found for this employer"}), 404
        job = row_to_dict(job_r)
        rows = db().execute("SELECT * FROM candidates WHERE id NOT IN (SELECT candidate_id FROM employer_swipes WHERE employer_id=? AND job_id=?)", (emp["id"], job_id)).fetchall()
        cands = [public_candidate(row_to_dict(r)) for r in rows]
        right_ids = [r["candidate_id"] for r in db().execute("SELECT candidate_id FROM employer_swipes WHERE employer_id=? AND direction='right'", (emp["id"],)).fetchall()]
        left_ids = [r["candidate_id"] for r in db().execute("SELECT candidate_id FROM employer_swipes WHERE employer_id=? AND direction='left'", (emp["id"],)).fetchall()]
        by_id = {c["id"]: c for c in [public_candidate(row_to_dict(r)) for r in db().execute("SELECT * FROM candidates").fetchall()]}
        right_cands = [by_id[i] for i in right_ids if i in by_id]
        left_cands = [by_id[i] for i in left_ids if i in by_id]
        ranked = rank_candidates_for_job(emp, job, cands, right_cands, left_cands)
        return jsonify({"job": job, "candidates": ranked})

    @app.route("/api/employer/swipe", methods=["POST"])
    @login_required("employer")
    def employer_swipe():
        d = request.get_json(force=True)
        emp = my_employer()
        job_r = db().execute("SELECT * FROM jobs WHERE id=? AND employer_id=?", (d.get("job_id"), emp["id"])).fetchone()
        if not job_r:
            return jsonify({"error": "job not found for this employer"}), 404
        job = row_to_dict(job_r)
        direction = d.get("direction")
        if direction not in ("left", "right"):
            return jsonify({"error": "direction must be left/right"}), 400
        cand_id = d.get("candidate_id")
        if not db().execute("SELECT id FROM candidates WHERE id=?", (cand_id,)).fetchone():
            return jsonify({"error": "candidate not found"}), 404
        db().execute("INSERT OR IGNORE INTO employer_swipes(employer_id,job_id,candidate_id,direction) VALUES(?,?,?,?)", (emp["id"], job["id"], cand_id, direction))
        db().commit()
        result = {"swipe": direction}
        if direction == "right":
            s = db().execute("SELECT id FROM swipes WHERE candidate_id=? AND job_id=? AND direction='right'", (cand_id, job["id"])).fetchone()
            a = db().execute("SELECT id FROM applications WHERE candidate_id=? AND job_id=?", (cand_id, job["id"])).fetchone()
            if s or a:
                db().execute("INSERT OR IGNORE INTO matches(candidate_id,employer_id,job_id) VALUES(?,?,?)", (cand_id, emp["id"], job["id"]))
                db().commit()
                m = db().execute("SELECT * FROM matches WHERE candidate_id=? AND job_id=?", (cand_id, job["id"])).fetchone()
                result["match"] = row_to_dict(m)
        return jsonify(result)

    # ---------- matches / messages ----------
    def _scoped_matches():
        u = g.user
        if u["role"] == "candidate":
            rows = db().execute("SELECT m.*, c.name AS candidate_name, c.title AS candidate_title, c.photo AS candidate_photo, j.title AS job_title, j.company AS company FROM matches m JOIN candidates c ON c.id=m.candidate_id JOIN jobs j ON j.id=m.job_id WHERE m.candidate_id=? ORDER BY m.id DESC", (u["candidate_id"],)).fetchall()
        else:
            rows = db().execute("SELECT m.*, c.name AS candidate_name, c.title AS candidate_title, c.photo AS candidate_photo, j.title AS job_title, j.company AS company FROM matches m JOIN candidates c ON c.id=m.candidate_id JOIN jobs j ON j.id=m.job_id WHERE m.employer_id=? ORDER BY m.id DESC", (u["employer_id"],)).fetchall()
        out = []
        for r in rows:
            d = row_to_dict(r)
            d["candidate_photo_url"] = f"/uploads/{d['candidate_photo']}" if d.get("candidate_photo") else ""
            out.append(d)
        return out

    @app.route("/api/matches")
    @login_required()
    def matches():
        return jsonify(_scoped_matches())

    @app.route("/api/matches/<int:mid>/messages", methods=["GET", "POST"])
    @login_required()
    def messages(mid):
        u = g.user
        m = db().execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
        if not m or (u["role"] == "candidate" and m["candidate_id"] != u["candidate_id"]) or (u["role"] == "employer" and m["employer_id"] != u["employer_id"]):
            return jsonify({"error": "match not found"}), 404
        if request.method == "POST":
            d = request.get_json(force=True)
            if not d.get("body"):
                return jsonify({"error": "body required"}), 400
            cur = db().execute("INSERT INTO messages(match_id,sender,body) VALUES(?,?,?)", (mid, u["role"], d["body"]))
            db().commit()
            return jsonify(row_to_dict(db().execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone())), 201
        return jsonify([row_to_dict(r) for r in db().execute("SELECT * FROM messages WHERE match_id=? ORDER BY id", (mid,)).fetchall()])

    return app

app = None
def get_app():
    global app
    if app is None:
        app = create_app()
    return app
