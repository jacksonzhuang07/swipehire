import os, json, uuid, functools, secrets, sys
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template, g, session, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from .db import init_db, get_conn, row_to_dict, get_db_path, get_uploads_dir
from .mailer import send_signin_code
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
    _secret = os.environ.get("SWIPEHIRE_SECRET")
    app.secret_key = _secret or "swipehire-dev-secret-change-in-prod"
    app.config["PRODUCTION"] = os.environ.get("SWIPEHIRE_ENV") == "production"
    if app.config["PRODUCTION"] and not _secret:
        print("=" * 72, file=sys.stderr)
        print("WARNING: SWIPEHIRE_ENV=production but SWIPEHIRE_SECRET is NOT set —", file=sys.stderr)
        print("sessions are signed with the built-in DEV secret. Set SWIPEHIRE_SECRET", file=sys.stderr)
        print("to a long random value in the deployment environment before real use.", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
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

    def _insert_user(email, password_hash, role, name, company=""):
        cand_id = emp_id = None
        if role == "candidate":
            cur = db().execute("INSERT INTO candidates(name,email) VALUES(?,?)", (name or email.split("@")[0], email))
            cand_id = cur.lastrowid
        else:
            cur = db().execute("INSERT INTO employers(name,company,email) VALUES(?,?,?)", (name or "Hiring Manager", company or "My Company", email))
            emp_id = cur.lastrowid
        cur = db().execute("INSERT INTO users(email,password_hash,role,name,candidate_id,employer_id) VALUES(?,?,?,?,?,?)",
                           (email, password_hash, role, name or "", cand_id, emp_id))
        uid = cur.lastrowid
        if cand_id:
            db().execute("UPDATE candidates SET user_id=? WHERE id=?", (uid, cand_id))
        if emp_id:
            db().execute("UPDATE employers SET user_id=? WHERE id=?", (uid, emp_id))
        db().commit()
        return uid

    def _create_user_account(email, password, role, name, company=""):
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            return None, (jsonify({"error": "valid email required"}), 400)
        if not password or len(password) < 4:
            return None, (jsonify({"error": "password too short (min 4)"}), 400)
        if db().execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return None, (jsonify({"error": "email already registered"}), 409)
        return _insert_user(email, generate_password_hash(password), role, name, company), None

    # ---------- pages / health ----------
    @app.route("/")
    def index():
        return render_template("index.html", production=app.config["PRODUCTION"])

    @app.route("/privacy")
    def privacy():
        return render_template("privacy.html", production=app.config["PRODUCTION"])

    @app.route("/terms")
    def terms():
        return render_template("terms.html", production=app.config["PRODUCTION"])

    @app.route("/api/health")
    def health():
        return jsonify({"ok": True, "app": "SwipeHire", "version": 5})

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

    # ---------- passwordless email-code sign-in ----------
    CODE_TTL_MIN = 10
    CODE_MAX_ATTEMPTS = 5
    CODE_RATE_LIMIT = 3        # code requests ...
    CODE_RATE_WINDOW_MIN = 15  # ... per email per this many minutes

    @app.route("/api/auth/code/request", methods=["POST"])
    def code_request():
        d = request.get_json(force=True, silent=True) or {}
        email = (d.get("email") or "").strip().lower()
        if not email or "@" not in email or "." not in email.split("@")[-1]:
            return jsonify({"error": "valid email required"}), 400
        recent = db().execute("SELECT COUNT(*) c FROM login_codes WHERE email=? AND created_at >= datetime('now', ?)",
                              (email, f"-{CODE_RATE_WINDOW_MIN} minutes")).fetchone()["c"]
        if recent >= CODE_RATE_LIMIT:
            return jsonify({"error": "Too many code requests for this email — please wait a few minutes and try again."}), 429
        code = f"{secrets.randbelow(1000000):06d}"
        expires = (datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MIN)).isoformat()
        db().execute("INSERT INTO login_codes(email,code_hash,expires_at) VALUES(?,?,?)",
                     (email, generate_password_hash(code), expires))
        db().commit()
        # Delivery is identical whether or not an account exists (no existence leak).
        mode, delivered = send_signin_code(email, code)
        resp = {"ok": True,
                "message": "If an account exists for that email, a 6-digit sign-in code is on its way. It expires in 10 minutes.",
                "mail_mode": mode, "delivered": delivered}
        if mode == "demo":
            resp["demo_code"] = code
            resp["demo_note"] = "Demo mode: no email service connected yet, so here is your code."
        app.logger.info("sign-in code requested for %s via %s (delivered=%s)", email, mode, delivered)
        return jsonify(resp)

    @app.route("/api/auth/code/verify", methods=["POST"])
    def code_verify():
        d = request.get_json(force=True, silent=True) or {}
        email = (d.get("email") or "").strip().lower()
        code = str(d.get("code") or "").strip()
        generic = (jsonify({"error": "invalid or expired code"}), 401)
        if not email or not code:
            return generic
        row = db().execute("SELECT * FROM login_codes WHERE email=? AND used=0 ORDER BY id DESC LIMIT 1", (email,)).fetchone()
        if not row:
            return generic
        try:
            exp = datetime.fromisoformat(row["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except Exception:
            return generic
        if datetime.now(timezone.utc) > exp or row["attempts"] >= CODE_MAX_ATTEMPTS:
            return generic
        if not check_password_hash(row["code_hash"], code):
            db().execute("UPDATE login_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
            db().commit()
            return generic
        db().execute("UPDATE login_codes SET used=1 WHERE id=?", (row["id"],))
        db().commit()
        user = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        created = False
        if not user:
            role = d.get("role") if d.get("role") in ("candidate", "employer") else "candidate"
            name = (d.get("name") or "").strip() or email.split("@")[0]
            # Unusable random password: the account is code-login only until the user sets one.
            uid = _insert_user(email, generate_password_hash(secrets.token_urlsafe(32)), role, name, d.get("company", ""))
            created = True
        else:
            uid = user["id"]
        session["user_id"] = uid
        payload = _me_payload()
        return jsonify({"user": payload, "created": created}), (201 if created else 200)

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

    # ---------- connections (apply tiers) ----------
    BOARDS = ("linkedin", "indeed")

    @app.route("/api/connections", methods=["GET", "POST"])
    @login_required("candidate")
    def connections():
        """Tier 2 fallback bookkeeping: a candidate may record which board
        account a worker session will be established for. We store ONLY the
        board + the account identifier (username/email) the user types —
        NEVER a password; there is deliberately no password field anywhere
        in this flow."""
        u = g.user
        c = my_candidate()
        if request.method == "POST":
            d = request.get_json(force=True, silent=True) or {}
            board = (d.get("board") or "").strip().lower()
            identifier = (d.get("identifier") or "").strip()
            if board not in BOARDS:
                return jsonify({"error": "board must be linkedin or indeed"}), 400
            if not identifier:
                return jsonify({"error": "account username/email required"}), 400
            db().execute("INSERT INTO connections(user_id,candidate_id,board,identifier,status) VALUES(?,?,?,?,'pending_session') "
                         "ON CONFLICT(user_id,board) DO UPDATE SET identifier=excluded.identifier, status='pending_session'",
                         (u["id"], c["id"] if c else None, board, identifier))
            db().commit()
            r = db().execute("SELECT * FROM connections WHERE user_id=? AND board=?", (u["id"], board)).fetchone()
            return jsonify(row_to_dict(r)), 201
        rows = db().execute("SELECT * FROM connections WHERE user_id=? ORDER BY board", (u["id"],)).fetchall()
        return jsonify({"connections": [row_to_dict(r) for r in rows], "extension": _ext_state(u["id"])})

    def _ext_state(user_id):
        rows = db().execute("SELECT * FROM ext_tokens WHERE user_id=? AND revoked=0 ORDER BY id DESC", (user_id,)).fetchall()
        used = [r for r in rows if r["last_used_at"]]
        return {"has_token": bool(rows),
                "connected": bool(used),
                "last_used_at": used[0]["last_used_at"] if used else None}

    @app.route("/api/ext/pairing-token", methods=["POST", "DELETE"])
    @login_required("candidate")
    def pairing_token():
        """Generate (POST) or revoke (DELETE) the browser-extension pairing
        token. Only the hash is stored; the plaintext is returned ONCE."""
        if request.method == "DELETE":
            db().execute("UPDATE ext_tokens SET revoked=1 WHERE user_id=?", (g.user["id"],))
            db().commit()
            return jsonify({"ok": True, "revoked": True})
        token = "shx_" + secrets.token_urlsafe(24)
        db().execute("UPDATE ext_tokens SET revoked=1 WHERE user_id=?", (g.user["id"],))
        db().execute("INSERT INTO ext_tokens(user_id,token_hash) VALUES(?,?)",
                     (g.user["id"], generate_password_hash(token)))
        db().commit()
        return jsonify({"token": token, "note": "Copy this into the SwipeHire extension popup. It is shown once — generating a new one revokes the old."}), 201

    def _ext_user():
        """Authenticate an extension request by pairing token (NOT session)."""
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else (request.headers.get("X-Extension-Token") or "").strip()
        if not token:
            return None
        for r in db().execute("SELECT * FROM ext_tokens WHERE revoked=0").fetchall():
            if check_password_hash(r["token_hash"], token):
                db().execute("UPDATE ext_tokens SET last_used_at=datetime('now') WHERE id=?", (r["id"],))
                db().commit()
                u = db().execute("SELECT * FROM users WHERE id=?", (r["user_id"],)).fetchone()
                return dict(u) if u else None
        return None

    @app.route("/api/ext/queue")
    def ext_queue():
        u = _ext_user()
        if not u:
            return jsonify({"error": "invalid or revoked extension token"}), 401
        rows = db().execute(
            "SELECT a.*, j.title, j.company, j.url, j.source, j.apply_type FROM applications a JOIN jobs j ON j.id=a.job_id "
            "WHERE a.candidate_id=? AND (a.status='queued_extension' OR a.method='extension_required') ORDER BY a.id",
            (u["candidate_id"],)).fetchall()
        out = []
        for r in rows:
            d = row_to_dict(r)
            out.append({"application_id": d["id"], "status": d["status"], "method": d["method"],
                        "job_title": d["title"], "company": d["company"], "job_url": d["url"],
                        "source": d["source"], "apply_type": d["apply_type"],
                        "answers": d.get("answers") or {}, "tailored_resume": d.get("tailored_resume") or ""})
        return jsonify({"queue": out})

    @app.route("/api/ext/report", methods=["POST"])
    def ext_report():
        """Extension reports progress. 'submitted' is accepted ONLY here —
        an explicit report from the extension (its Mark-submitted button or
        a detected confirmation page). The web app never claims it alone."""
        u = _ext_user()
        if not u:
            return jsonify({"error": "invalid or revoked extension token"}), 401
        d = request.get_json(force=True, silent=True) or {}
        event = (d.get("event") or "").strip().lower()
        if event not in ("opened", "filled", "submitted", "failed"):
            return jsonify({"error": "event must be opened|filled|submitted|failed"}), 400
        r = db().execute("SELECT * FROM applications WHERE id=? AND candidate_id=?", (d.get("application_id"), u["candidate_id"])).fetchone()
        if not r:
            return jsonify({"error": "application not found"}), 404
        app_row = row_to_dict(r)
        note = (d.get("note") or "").strip()[:300]
        if event == "submitted":
            db().execute("UPDATE applications SET status='submitted', updated_at=datetime('now'), proof=? WHERE id=?",
                         (f"Submitted — confirmed by the SwipeHire extension working in your own logged-in browser.{(' Note: ' + note) if note else ''}", app_row["id"]))
        elif event == "failed":
            db().execute("UPDATE applications SET status='needs_review', updated_at=datetime('now'), proof=? WHERE id=?",
                         (f"{app_row.get('proof','')} [Extension reported it could not apply{(': ' + note) if note else ''} — needs your review.]", app_row["id"]))
        else:  # opened / filled: progress only, status unchanged
            db().execute("UPDATE applications SET updated_at=datetime('now'), proof=? WHERE id=?",
                         (f"{app_row.get('proof','')} [Extension: {event}.]", app_row["id"]))
        db().commit()
        return jsonify(row_to_dict(db().execute("SELECT * FROM applications WHERE id=?", (app_row["id"],)).fetchone()))

    @app.route("/extension/download")
    def extension_download():
        return send_from_directory(os.path.join(app.root_path, "static"), "swipehire-extension.zip",
                                   as_attachment=True, download_name="swipehire-extension.zip")

    @app.route("/api/connections/<board>", methods=["DELETE"])
    @login_required("candidate")
    def disconnect(board):
        board = (board or "").strip().lower()
        cur = db().execute("DELETE FROM connections WHERE user_id=? AND board=?", (g.user["id"], board))
        db().commit()
        if not cur.rowcount:
            return jsonify({"error": "connection not found"}), 404
        return jsonify({"ok": True, "disconnected": board})

    # ---------- delete account ----------
    @app.route("/api/account", methods=["DELETE"])
    @login_required()
    def delete_account():
        """Delete the signed-in user and ALL of their scoped data."""
        u = g.user
        conn = db()
        cand_id, emp_id = u.get("candidate_id"), u.get("employer_id")
        match_ids = set()
        if cand_id:
            match_ids |= {r["id"] for r in conn.execute("SELECT id FROM matches WHERE candidate_id=?", (cand_id,)).fetchall()}
        if emp_id:
            match_ids |= {r["id"] for r in conn.execute("SELECT id FROM matches WHERE employer_id=?", (emp_id,)).fetchall()}
        for mid in match_ids:
            conn.execute("DELETE FROM messages WHERE match_id=?", (mid,))
        if cand_id:
            conn.execute("DELETE FROM matches WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM employer_swipes WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM swipes WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM applications WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM documents WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM candidates WHERE id=?", (cand_id,))
        if emp_id:
            conn.execute("DELETE FROM matches WHERE employer_id=?", (emp_id,))
            conn.execute("DELETE FROM employer_swipes WHERE employer_id=?", (emp_id,))
            job_ids = [r["id"] for r in conn.execute("SELECT id FROM jobs WHERE employer_id=?", (emp_id,)).fetchall()]
            for jid in job_ids:
                conn.execute("DELETE FROM swipes WHERE job_id=?", (jid,))
                conn.execute("DELETE FROM applications WHERE job_id=?", (jid,))
                conn.execute("DELETE FROM employer_swipes WHERE job_id=?", (jid,))
                conn.execute("DELETE FROM matches WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM jobs WHERE employer_id=?", (emp_id,))
            conn.execute("DELETE FROM employers WHERE id=?", (emp_id,))
        conn.execute("DELETE FROM connections WHERE user_id=?", (u["id"],))
        conn.execute("DELETE FROM ext_tokens WHERE user_id=?", (u["id"],))
        conn.execute("DELETE FROM login_codes WHERE email=?", (u["email"],))
        conn.execute("DELETE FROM users WHERE id=?", (u["id"],))
        conn.commit()
        session.clear()
        return jsonify({"ok": True, "deleted_user": u["email"]})

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

    @app.route("/api/cron/rescan", methods=["GET"])
    def cron_rescan():
        """Daily Vercel Cron target: refresh the shared job pool.
        Guarded by CRON_SECRET when set (Vercel Cron sends it as
        Authorization: Bearer <CRON_SECRET>); open in demo when unset."""
        secret = os.environ.get("CRON_SECRET")
        if secret and request.headers.get("Authorization", "") != f"Bearer {secret}":
            return jsonify({"error": "unauthorized"}), 401
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
        result = {"swipe": direction, "needs_extension": False}
        if direction == "right":
            fresh = row_to_dict(db().execute("SELECT * FROM candidates WHERE id=?", (c["id"],)).fetchone())
            app_obj, created = apply_for_job(db(), fresh, job)
            result["application"] = app_obj
            result["created"] = created
            result["needs_extension"] = bool(app_obj and app_obj.get("method") == "extension_required")
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
