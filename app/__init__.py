import os, json, uuid, functools, secrets, sys, mimetypes
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template, g, session, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from .db import init_db, get_conn, row_to_dict, get_db_path, get_uploads_dir
from .mailer import send_signin_code
from .resume_parser import parse_resume, extract_text_from_pdf
from .scoring import score_job
from .job_sources import scan_boards, insert_jobs
from .apply_engine import apply_for_job, maybe_create_match, maybe_match_from_recruiter_like
from .job_sources import WORK_MODES
from .seed_data import seed_all
from .documents import parse_document_facts, rebuild_master, DOC_TYPES
from .feed import rank_jobs, rank_candidates_for_job
from . import storage
from . import billing

PHOTO_EXTS = {".png", ".jpg", ".jpeg"}
VIDEO_EXTS = {".mp4", ".webm", ".mov"}
DOC_EXTS = {".txt", ".md", ".pdf"}
MAX_PHOTO_BYTES = 3 * 1024 * 1024
MAX_VIDEO_BYTES = 25 * 1024 * 1024
# Pre-v9 the whole request cap was 8MB (Flask 413'd anything bigger).
# Videos need up to 25MB, so the global cap is now 32MB and the text
# endpoints (documents / resume) enforce the old 8MB ceiling themselves.
LEGACY_TEXT_CAP = 8 * 1024 * 1024

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
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
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

    @app.errorhandler(OverflowError)
    def _overflow(_e):
        # A Python int too large for a SQLite INTEGER (e.g. a 30-digit id in
        # a URL path or query param) cannot name a real row.
        return jsonify({"error": "not found"}), 404

    def _json_body(required=False):
        """Request JSON body as a dict. Non-object JSON (list/string/number)
        is never valid for this API: returns {} normally, or None when
        required=True so the view can answer 400 instead of crashing."""
        d = request.get_json(force=True, silent=True)
        if isinstance(d, dict):
            return d
        return None if required else {}

    def _coerce_id(v):
        """int id from a JSON value, or None when it isn't int-like."""
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _coerce_salary(v):
        """Salary field: int, or None for empty; raises ValueError on junk."""
        if v is None or v == "":
            return None
        return int(v)

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
        c["photo_url"] = storage.display_url(c.get("photo"))
        c["video_url"] = storage.display_url(c.get("video"))
        c.pop("resume_text", None)
        c.pop("compiled_resume", None)
        # Phase 2: boost state is the candidate's own business — it orders
        # employer decks but is never labelled to employers as paid.
        c.pop("boost_until", None)
        return c

    def employer_media(e):
        """Effective recruiter-profile media (v9/N1): a profile VIDEO wins
        over the picture, per the card rule 'video when present, image
        otherwise'. Returns (name, kind) or (None, '')."""
        if e.get("video"):
            return e["video"], "video"
        if e.get("logo"):
            return e["logo"], "image"
        return None, ""

    def employer_payload(e):
        """Employer dict as shipped to clients, with media + like counts."""
        if not e:
            return None
        e = dict(e)
        e["logo_url"] = storage.display_url(e.get("logo"))
        e["video_url"] = storage.display_url(e.get("video"))
        name, kind = employer_media(e)
        e["media_url"] = storage.display_url(name)
        e["media_type"] = kind
        e["has_media"] = bool(name)
        # N2 (per-entity like counts): recruiter PROFILE likes = candidate
        # right-swipes on the recruiter card itself.
        e["likes_count"] = db().execute(
            "SELECT COUNT(*) c FROM recruiter_swipes WHERE employer_id=? AND direction='right'",
            (e["id"],)).fetchone()["c"]
        return e

    def job_media_fields(j, emp_media=None):
        """Effective media for a job dict: the job's own media wins, else
        it inherits the owning recruiter profile's media (v9/N1)."""
        if j.get("media"):
            return storage.display_url(j["media"]), j.get("media_type") or ""
        if emp_media and emp_media[0]:
            return storage.display_url(emp_media[0]), emp_media[1]
        return "", ""

    def _employer_media_map():
        out = {}
        for r in db().execute("SELECT id, logo, video FROM employers").fetchall():
            out[r["id"]] = employer_media(row_to_dict(r))
        return out

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
        email = email.strip().lower() if isinstance(email, str) else ""
        if not email or "@" not in email:
            return None, (jsonify({"error": "valid email required"}), 400)
        if not isinstance(password, str) or len(password) < 4:
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
        return jsonify({"ok": True, "app": "SwipeHire", "version": 8,
                        "storage": storage.backend_name()})

    @app.route("/uploads/<path:filename>")
    @login_required()
    def uploads(filename):
        name = os.path.basename(filename)
        # Blob backend: the store is PRIVATE, so the blob URL is never
        # handed to the browser — fetch the object server-side with the
        # token and stream the bytes to this session-authorized user.
        # Local backend: serve the file from disk, as before.
        for sql in ("SELECT photo_blob_url u FROM candidates WHERE photo=?",
                    "SELECT video_blob_url u FROM candidates WHERE video=?",
                    "SELECT logo_blob_url u FROM employers WHERE logo=?",
                    "SELECT video_blob_url u FROM employers WHERE video=?",
                    "SELECT media_blob_url u FROM jobs WHERE media=?"):
            r = db().execute(sql, (name,)).fetchone()
            if r and r["u"]:
                try:
                    data, ctype = storage.blob_get(r["u"])
                except Exception:
                    app.logger.exception("blob fetch failed for upload %s", name)
                    return jsonify({"error": "stored file temporarily unavailable"}), 502
                return Response(data, content_type=ctype
                                or mimetypes.guess_type(name)[0]
                                or "application/octet-stream")
        return send_from_directory(app.config["UPLOADS_DIR"], name)

    # ---------- auth ----------
    @app.route("/api/auth/signup", methods=["POST"])
    def signup():
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
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
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
        email = d.get("email").strip().lower() if isinstance(d.get("email"), str) else ""
        r = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        pw = d.get("password") if isinstance(d.get("password"), str) else ""
        if not r or not check_password_hash(r["password_hash"], pw):
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
        d = _json_body()
        email = d.get("email").strip().lower() if isinstance(d.get("email"), str) else ""
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
        d = _json_body()
        email = d.get("email").strip().lower() if isinstance(d.get("email"), str) else ""
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
            name = d.get("name").strip() if isinstance(d.get("name"), str) and d.get("name").strip() else email.split("@")[0]
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
            cand = public_candidate(row_to_dict(r))
            if cand:
                # N2: candidate PROFILE likes = recruiter right-swipes
                # received (job-context employer right-swipes).
                cand["likes_count"] = db().execute(
                    "SELECT COUNT(*) c FROM employer_swipes WHERE candidate_id=? AND direction='right'",
                    (cand["id"],)).fetchone()["c"]
                # Phase 2: Candidate Pro lives on the USER row; boost state
                # is candidate-only (public_candidate stripped it above, so
                # re-attach it here for the owner).
                cand["plan"] = u.get("plan") or "free"
                raw = row_to_dict(r)
                cand["boost_until"] = raw.get("boost_until")
                cand["boost_active"] = billing.is_future(raw.get("boost_until"))
            payload["candidate"] = cand
        if u.get("employer_id"):
            r = db().execute("SELECT * FROM employers WHERE id=?", (u["employer_id"],)).fetchone()
            payload["employer"] = employer_payload(row_to_dict(r))
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
        if request.content_length and request.content_length > LEGACY_TEXT_CAP:
            return jsonify({"error": "file too large (max 8MB)"}), 413
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
            t = request.get_json().get("text", "")
            return t if isinstance(t, str) else ""
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
        return _save_profile_media("candidates", my_candidate()["id"])

    @app.route("/api/employer/logo", methods=["POST"])
    @login_required("employer")
    def upload_logo():
        return _save_profile_media("employers", my_employer()["id"])

    def _media_kind_and_check(ext, data):
        """Validate an uploaded media file by extension + magic bytes.
        Returns (kind, error_response): kind is 'image' or 'video'."""
        if ext in PHOTO_EXTS:
            if len(data) > MAX_PHOTO_BYTES:
                return None, (jsonify({"error": "image too large (max 3MB)"}), 413)
            if not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff")):
                return None, (jsonify({"error": "file is not a real PNG/JPEG image"}), 400)
            return "image", None
        if ext in VIDEO_EXTS:
            if len(data) > MAX_VIDEO_BYTES:
                return None, (jsonify({"error": "video too large (max 25MB)"}), 413)
            if ext == ".webm":
                ok = data.startswith(b"\x1a\x45\xdf\xa3")
            else:  # .mp4 / .mov: ISO-BMFF 'ftyp' box at offset 4
                ok = len(data) > 8 and data[4:8] == b"ftyp"
            if not ok:
                return None, (jsonify({"error": "file is not a real video of that type"}), 400)
            return "video", None
        return None, (jsonify({"error": "only .png/.jpg images or .mp4/.webm/.mov videos allowed"}), 400)

    def _store_media_file(f, prefix):
        """Validate + persist one uploaded media file through the storage
        layer. Returns (name, blob_url, kind, error_response)."""
        ext = os.path.splitext((f.filename or "").lower())[1]
        if ext == ".jpeg":
            ext = ".jpg"
        data = f.read()
        if not data:
            return None, None, None, (jsonify({"error": "empty file"}), 400)
        kind, err = _media_kind_and_check(ext, data)
        if err:
            return None, None, None, err
        name = f"{prefix}_{uuid.uuid4().hex[:10]}{ext}"
        if kind == "image":
            ctype = "image/png" if ext == ".png" else "image/jpeg"
        else:
            ctype = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}[ext]
        blob_url = storage.save_upload(app.config["UPLOADS_DIR"], name, data, ctype)
        return name, blob_url or "", kind, None

    def _save_profile_media(table, row_id):
        """Candidate photo/video and recruiter logo/video share one shape:
        an IMAGE lands in photo|logo (+_blob_url), a VIDEO in video
        (+video_blob_url). Replacing a file cleans up the old object."""
        if "file" not in request.files or not request.files["file"].filename:
            return jsonify({"error": "file required"}), 400
        img_col = "photo" if table == "candidates" else "logo"
        name, blob_url, kind, err = _store_media_file(request.files["file"], f"{table}_{row_id}")
        if err:
            return err
        column = img_col if kind == "image" else "video"
        blob_col = f"{column}_blob_url"
        old = db().execute(f"SELECT {column}, {blob_col} FROM {table} WHERE id=?", (row_id,)).fetchone()
        db().execute(f"UPDATE {table} SET {column}=?, {blob_col}=? WHERE id=?", (name, blob_url, row_id))
        db().commit()
        if old and old[column] and old[column] != name:
            storage.delete_upload(app.config["UPLOADS_DIR"], name=old[column], blob_url=old[blob_col])
        return jsonify({"ok": True, "url": storage.display_url(name), "filename": name, "media_type": kind})

    @app.route("/api/candidate/preferences", methods=["POST"])
    @login_required("candidate")
    def prefs():
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
        c = my_candidate()
        for key in ("target_titles", "locations"):
            if key in d and not isinstance(d[key], list):
                return jsonify({"error": f"{key} must be a list"}), 400
        try:
            min_salary = _coerce_salary(d.get("min_salary")) or 0
        except (TypeError, ValueError):
            return jsonify({"error": "min_salary must be a number"}), 400
        db().execute("UPDATE candidates SET target_titles=?,locations=?,remote_only=?,min_salary=? WHERE id=?",
                     (json.dumps(d.get("target_titles", c["target_titles"])), json.dumps(d.get("locations", c["locations"])), 1 if d.get("remote_only") else 0, min_salary, c["id"]))
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
            d = _json_body()
            board = d.get("board").strip().lower() if isinstance(d.get("board"), str) else ""
            identifier = d.get("identifier").strip() if isinstance(d.get("identifier"), str) else ""
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
        # Queue eligibility (v6): everything the extension can drive —
        # login-required jobs queued for it (queued_extension /
        # extension_required) PLUS prepared no-login ATS-form jobs, for
        # which the extension acts as the form executor. 'external' jobs
        # (unknown structure) are never queued.
        rows = db().execute(
            "SELECT a.*, j.title, j.company, j.url, j.source, j.apply_type FROM applications a JOIN jobs j ON j.id=a.job_id "
            "WHERE a.candidate_id=? AND (a.status='queued_extension' "
            "OR (a.status='prepared' AND (a.method='extension_required' OR j.apply_type='ats_form'))) ORDER BY a.id",
            (u["candidate_id"],)).fetchall()
        cand = db().execute("SELECT * FROM candidates WHERE id=?", (u["candidate_id"],)).fetchone()
        cand = row_to_dict(cand) if cand else {}
        out = []
        for r in rows:
            d = row_to_dict(r)
            answers = dict(d.get("answers") or {})
            # Enrich with structured facts derived live from the stored
            # profile (facts only — nothing is invented here either).
            full = answers.get("full_name") or cand.get("name") or ""
            if full and "first_name" not in answers:
                parts = full.strip().split()
                answers["first_name"] = parts[0]
                if len(parts) > 1:
                    answers["last_name"] = " ".join(parts[1:])
            if "salary_expectation" not in answers and cand.get("min_salary"):
                answers["salary_expectation"] = str(cand["min_salary"])
            if "years_of_experience" not in answers:
                master = cand.get("master_profile") or {}
                yrs = master.get("years_exp") or cand.get("years_exp")
                if yrs:
                    answers["years_of_experience"] = ("%g" % float(yrs))
            out.append({"application_id": d["id"], "status": d["status"], "method": d["method"],
                        "job_title": d["title"], "company": d["company"], "job_url": d["url"],
                        "source": d["source"], "apply_type": d["apply_type"],
                        "answers": answers, "tailored_resume": d.get("tailored_resume") or ""})
        return jsonify({"queue": out})

    @app.route("/api/ext/report", methods=["POST"])
    def ext_report():
        """Extension reports progress. 'submitted' is accepted ONLY here —
        an explicit report from the extension (its Mark-submitted button or
        a detected confirmation page). The web app never claims it alone."""
        u = _ext_user()
        if not u:
            return jsonify({"error": "invalid or revoked extension token"}), 401
        d = _json_body()
        event = d.get("event").strip().lower() if isinstance(d.get("event"), str) else ""
        if event not in ("opened", "filling", "filled", "submitted", "failed", "needs_human"):
            return jsonify({"error": "event must be opened|filling|filled|submitted|failed|needs_human"}), 400
        r = db().execute("SELECT * FROM applications WHERE id=? AND candidate_id=?", (_coerce_id(d.get("application_id")), u["candidate_id"])).fetchone()
        if not r:
            return jsonify({"error": "application not found"}), 404
        app_row = row_to_dict(r)
        note = d.get("note").strip()[:500] if isinstance(d.get("note"), str) else ""
        if event == "submitted":
            db().execute("UPDATE applications SET status='submitted', updated_at=datetime('now'), proof=? WHERE id=?",
                         (f"Submitted — confirmed by the SwipeHire extension working in your own logged-in browser.{(' Note: ' + note) if note else ''}", app_row["id"]))
        elif event == "needs_human":
            # The autonomous driver parked this job: CAPTCHA, login wall,
            # security checkpoint, or a required question with no stored
            # answer. The reason goes to needs_review AND the proof text.
            needs = list(app_row.get("needs_review") or [])
            reason = note or "Extension needs a human (no reason given)."
            if reason not in needs:
                needs.append(reason)
            db().execute("UPDATE applications SET status='needs_human', needs_review=?, updated_at=datetime('now'), proof=? WHERE id=?",
                         (json.dumps(needs), f"{app_row.get('proof','')} [Extension parked this — needs you: {reason}]", app_row["id"]))
        elif event == "failed":
            db().execute("UPDATE applications SET status='needs_review', updated_at=datetime('now'), proof=? WHERE id=?",
                         (f"{app_row.get('proof','')} [Extension reported it could not apply{(': ' + note) if note else ''} — needs your review.]", app_row["id"]))
        elif event == "opened":
            # Claiming a prepared job promotes it into the extension queue.
            new_status = "queued_extension" if app_row["status"] == "prepared" else app_row["status"]
            db().execute("UPDATE applications SET status=?, updated_at=datetime('now'), proof=? WHERE id=?",
                         (new_status, f"{app_row.get('proof','')} [Extension: opened.]", app_row["id"]))
        else:  # filling / filled: progress only, status unchanged
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
        # Collect stored uploads (local name + blob URL) BEFORE the rows
        # go, so the objects can be deleted too. Blob deletion is
        # best-effort and never blocks the account deletion.
        doomed_uploads = []
        if cand_id:
            r = conn.execute("SELECT photo, photo_blob_url, video, video_blob_url FROM candidates WHERE id=?", (cand_id,)).fetchone()
            if r and r["photo"]:
                doomed_uploads.append((r["photo"], r["photo_blob_url"]))
            if r and r["video"]:
                doomed_uploads.append((r["video"], r["video_blob_url"]))
            doomed_uploads += [(None, r["blob_url"]) for r in
                               conn.execute("SELECT blob_url FROM documents WHERE candidate_id=? AND blob_url != ''", (cand_id,)).fetchall()]
        if emp_id:
            r = conn.execute("SELECT logo, logo_blob_url, video, video_blob_url FROM employers WHERE id=?", (emp_id,)).fetchone()
            if r and r["logo"]:
                doomed_uploads.append((r["logo"], r["logo_blob_url"]))
            if r and r["video"]:
                doomed_uploads.append((r["video"], r["video_blob_url"]))
            doomed_uploads += [(r["media"], r["media_blob_url"]) for r in
                               conn.execute("SELECT media, media_blob_url FROM jobs WHERE employer_id=? AND media != ''", (emp_id,)).fetchall()]
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
            conn.execute("DELETE FROM recruiter_swipes WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM swipes WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM applications WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM documents WHERE candidate_id=?", (cand_id,))
            conn.execute("DELETE FROM candidates WHERE id=?", (cand_id,))
        if emp_id:
            conn.execute("DELETE FROM matches WHERE employer_id=?", (emp_id,))
            conn.execute("DELETE FROM employer_swipes WHERE employer_id=?", (emp_id,))
            conn.execute("DELETE FROM recruiter_swipes WHERE employer_id=?", (emp_id,))
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
        for name, blob_url in doomed_uploads:
            storage.delete_upload(app.config["UPLOADS_DIR"], name=name, blob_url=blob_url)  # never raises
        session.clear()
        return jsonify({"ok": True, "deleted_user": u["email"]})

    # ---------- document vault / master profile / compiled resume ----------
    def _add_document(candidate_id, doc_type, title, filename, text):
        facts = parse_document_facts(text, doc_type)
        cur = db().execute("INSERT INTO documents(candidate_id,doc_type,title,filename,text,facts) VALUES(?,?,?,?,?,?)",
                           (candidate_id, doc_type, title or filename or doc_type, filename, text[:60000], json.dumps(facts)))
        db().commit()
        did = cur.lastrowid
        if storage.blob_enabled():
            # Mirror the document bytes to Blob under an unguessable
            # pathname so the file itself survives ephemeral disks too.
            # Best-effort: the text in the DB is the source of truth, so
            # a failed mirror never breaks the upload.
            try:
                blob_name = f"documents_{candidate_id}_{did}_{uuid.uuid4().hex[:10]}.txt"
                url = storage.blob_put(blob_name, text[:60000].encode("utf-8"), "text/plain")
                db().execute("UPDATE documents SET blob_url=? WHERE id=?", (url, did))
                db().commit()
            except Exception:
                app.logger.warning("blob mirror failed for document %s", did, exc_info=True)
        rebuild_master(db(), candidate_id)
        return did

    @app.route("/api/documents", methods=["GET", "POST"])
    @login_required("candidate")
    def documents():
        c = my_candidate()
        if request.method == "POST":
            if request.content_length and request.content_length > LEGACY_TEXT_CAP:
                return jsonify({"error": "file too large (max 8MB)"}), 413
            jbody = _json_body() if request.is_json else {}
            doc_type = (jbody.get("doc_type") if request.is_json else request.form.get("doc_type")) or "other"
            if doc_type not in DOC_TYPES:
                return jsonify({"error": f"doc_type must be one of {DOC_TYPES}"}), 400
            title = ""
            filename = ""
            if request.is_json:
                text = jbody.get("text", "")
                if not isinstance(text, str):
                    return jsonify({"error": "text must be a string"}), 400
                title = jbody.get("title", "") if isinstance(jbody.get("title", ""), str) else ""
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
        r = db().execute("SELECT id, blob_url FROM documents WHERE id=? AND candidate_id=?", (did, c["id"])).fetchone()
        if not r:
            return jsonify({"error": "document not found"}), 404
        blob_url = r["blob_url"]
        db().execute("DELETE FROM documents WHERE id=?", (did,))
        db().commit()
        if blob_url:
            storage.delete_upload(blob_url=blob_url)  # never raises
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
        # v9/N5: HARD filters (query params) applied before ranking —
        # q: keyword across title/company/tags/description; location:
        # substring of the job's location; work_mode: exact match.
        q = (request.args.get("q") or "").strip().lower()
        loc = (request.args.get("location") or "").strip().lower()
        mode = (request.args.get("work_mode") or "").strip().lower()
        if mode and mode not in ("remote", "hybrid", "in_person", "unspecified"):
            return jsonify({"error": "work_mode must be remote|hybrid|in_person|unspecified"}), 400
        rows = db().execute("SELECT * FROM jobs WHERE id NOT IN (SELECT job_id FROM swipes WHERE candidate_id=?)", (c["id"],)).fetchall()
        emp_media = _employer_media_map()
        out = []
        for r in rows:
            j = row_to_dict(r)
            if mode and (j.get("work_mode") or "unspecified") != mode:
                continue
            if loc and loc not in (j.get("location") or "").lower():
                continue
            if q:
                hay = " ".join([j.get("title") or "", j.get("company") or "",
                                j.get("description") or "", " ".join(j.get("tags") or [])]).lower()
                if q not in hay:
                    continue
            j.update(score_job(c, j))
            j["media_url"], j["media_type"] = job_media_fields(j, emp_media.get(j.get("employer_id")))
            # Phase 2: sponsorship is a payload fact, labelled on the card —
            # never disguised as organic rank (see below).
            j["sponsored"] = billing.is_future(j.get("sponsored_until"))
            out.append(j)
        right_jobs = [row_to_dict(r) for r in db().execute("SELECT j.* FROM jobs j JOIN swipes s ON s.job_id=j.id WHERE s.candidate_id=? AND s.direction='right'", (c["id"],)).fetchall()]
        left_jobs = [row_to_dict(r) for r in db().execute("SELECT j.* FROM jobs j JOIN swipes s ON s.job_id=j.id WHERE s.candidate_id=? AND s.direction='left'", (c["id"],)).fetchall()]
        ranked = rank_jobs(c, out, right_jobs, left_jobs)
        # Sponsored jobs sort ahead while active (stable: organic order is
        # preserved inside each group, and the card says "Sponsored").
        ranked.sort(key=lambda j: 0 if j.get("sponsored") else 1)
        return jsonify(ranked)

    @app.route("/api/swipe", methods=["POST"])
    @login_required("candidate")
    def swipe():
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
        c = my_candidate()
        job_r = db().execute("SELECT * FROM jobs WHERE id=?", (_coerce_id(d.get("job_id")),)).fetchone()
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
            # Phase 2 gate: FREE candidates may queue at most
            # FREE_QUEUE_ALLOWANCE autonomous (queued_extension) applies per
            # day. 'prepared' applications are NEVER gated. The like itself
            # is already recorded above; only the queue entry is refused,
            # with a 402 the client can turn into an upgrade prompt.
            if not billing.is_pro(g.user.get("plan")) and job.get("requires_login"):
                from .apply_engine import extension_connected, SCORE_GATE
                already = db().execute("SELECT id FROM applications WHERE candidate_id=? AND job_id=?", (c["id"], job["id"])).fetchone()
                if not already and extension_connected(db(), g.user["id"]) and score_job(fresh, job)["score"] >= SCORE_GATE:
                    used = db().execute(
                        "SELECT COUNT(*) c FROM applications WHERE candidate_id=? AND status='queued_extension' AND date(created_at)=date('now')",
                        (c["id"],)).fetchone()["c"]
                    if used >= billing.FREE_QUEUE_ALLOWANCE:
                        m = maybe_create_match(db(), c["id"], job["id"])
                        payload = {"error": "queue_allowance_exhausted", "plan": "candidate_pro",
                                   "price": billing.CANDIDATE_PRO_PRICE, "allowance": billing.FREE_QUEUE_ALLOWANCE,
                                   "swipe": "right"}
                        if m:
                            payload["match"] = m
                        return jsonify(payload), 402
            app_obj, created = apply_for_job(db(), fresh, job)
            result["application"] = app_obj
            result["created"] = created
            result["needs_extension"] = bool(app_obj and app_obj.get("method") == "extension_required")
            m = maybe_create_match(db(), c["id"], job["id"])
            if m:
                result["match"] = m
        return jsonify(result)

    # ---------- v9/N4: recruiter deck + v9/N2: like counts ----------
    @app.route("/api/recruiters")
    @login_required("candidate")
    def recruiters():
        """Ranked recruiter (employer) cards for this candidate: only
        employers with at least one open job, minus recruiters the
        candidate already swiped (either direction). Ranking: skill
        overlap with the employer's open jobs first, then open-job
        count — deterministic, no swipe-history leakage across users."""
        c = my_candidate()
        cand_skills = {s.lower() for s in (c.get("skills") or [])}
        swiped = {r["employer_id"] for r in db().execute(
            "SELECT employer_id FROM recruiter_swipes WHERE candidate_id=?", (c["id"],)).fetchall()}
        out = []
        for r in db().execute("SELECT * FROM employers ORDER BY id").fetchall():
            e = row_to_dict(r)
            if e["id"] in swiped:
                continue
            jobs = [row_to_dict(j) for j in db().execute(
                "SELECT * FROM jobs WHERE employer_id=? ORDER BY id DESC", (e["id"],)).fetchall()]
            if not jobs:
                continue
            tags = set()
            for j in jobs:
                tags |= {t.lower() for t in (j.get("tags") or [])}
            overlap = sorted(cand_skills & tags)
            payload = employer_payload(e)
            why = []
            if overlap:
                why.append(f"Hiring for your skills: {', '.join(overlap[:3])}")
            why.append(f"{len(jobs)} open role{'s' if len(jobs) != 1 else ''} at {e['company']}")
            payload.update({
                "employer_id": e["id"],
                "open_jobs": len(jobs),
                "job_titles": [j["title"] for j in jobs[:3]],
                "matched_skills": overlap,
                "feed_score": round(len(overlap) * 10 + min(len(jobs), 5), 1),
                "why": why[:3],
            })
            out.append(payload)
        out.sort(key=lambda x: -x["feed_score"])
        return jsonify(out)

    @app.route("/api/recruiter-swipe", methods=["POST"])
    @login_required("candidate")
    def recruiter_swipe():
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
        c = my_candidate()
        emp_id = _coerce_id(d.get("employer_id"))
        if not db().execute("SELECT id FROM employers WHERE id=?", (emp_id,)).fetchone():
            return jsonify({"error": "recruiter not found"}), 404
        direction = d.get("direction")
        if direction not in ("left", "right"):
            return jsonify({"error": "direction must be left/right"}), 400
        db().execute("INSERT OR IGNORE INTO recruiter_swipes(candidate_id,employer_id,direction) VALUES(?,?,?)",
                     (c["id"], emp_id, direction))
        db().commit()
        result = {"swipe": direction}
        if direction == "right":
            # Mutual like via the recruiter card? A match forms on a job —
            # but a recruiter like NEVER creates an application.
            m = maybe_match_from_recruiter_like(db(), c["id"], emp_id)
            if m:
                result["match"] = m
        return jsonify(result)

    @app.route("/api/me/likes")
    @login_required("candidate")
    def my_likes():
        """v9/N2: this candidate profile's received-like count =
        recruiter (job-context) right-swipes received."""
        c = my_candidate()
        n = db().execute("SELECT COUNT(*) c FROM employer_swipes WHERE candidate_id=? AND direction='right'",
                         (c["id"],)).fetchone()["c"]
        return jsonify({"likes": n, "likes_count": n})

    # ---------- Phase 2: billing, gates, sponsored, boost ----------
    @app.route("/api/billing/plans")
    def billing_plans():
        """Public plan catalog — the ONLY source of prices for the UI."""
        return jsonify({"plans": billing.PLAN_CATALOG,
                        "payments_configured": billing.payments_configured(),
                        "sponsored_alacarte": {"price": billing.SPONSORED_ALACARTE_PRICE, "days": billing.SPONSORED_DAYS},
                        "free_queue_allowance": billing.FREE_QUEUE_ALLOWANCE})

    @app.route("/api/billing/checkout", methods=["POST"])
    @login_required()
    def billing_checkout():
        """Honest seam: NO Stripe account exists yet (Jackson's identity/
        banking step), so checkout is 503 — never a fake checkout, and
        nobody is marked Pro without a payment."""
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
        plan = billing.plan_by_key(d.get("plan"))
        if not plan:
            return jsonify({"error": "unknown plan"}), 400
        if not billing.payments_configured():
            return jsonify({"error": "payments_not_configured",
                            "message": "Payments are launching soon — join the waitlist and we'll email you."}), 503
        return jsonify({"error": "checkout_not_implemented",
                        "message": "Payment keys are configured but the checkout integration is not built yet."}), 501

    @app.route("/api/billing/interest", methods=["POST"])
    @login_required()
    def billing_interest():
        """Waitlist seam while payments are unconfigured."""
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
        plan = billing.plan_by_key(d.get("plan"))
        if not plan:
            return jsonify({"error": "unknown plan"}), 400
        db().execute("INSERT OR IGNORE INTO billing_interest(email,role,plan) VALUES(?,?,?)",
                     (g.user["email"], g.user["role"], plan["key"]))
        db().commit()
        return jsonify({"recorded": True, "plan": plan["key"]}), 201

    @app.route("/api/me/likers")
    @login_required("candidate")
    def my_likers():
        """WHO right-swiped this candidate (recruiter + job). The COUNT is
        free (v9/N2); the IDENTITIES are the Candidate Pro reveal."""
        if not billing.is_pro(g.user.get("plan")):
            return jsonify(billing.pro_required_payload("candidate_pro")), 402
        c = my_candidate()
        rows = db().execute(
            "SELECT es.created_at AS liked_at, e.id AS employer_id, e.name, e.company, e.logo, e.video, "
            "j.id AS job_id, j.title AS job_title FROM employer_swipes es "
            "JOIN employers e ON e.id=es.employer_id JOIN jobs j ON j.id=es.job_id "
            "WHERE es.candidate_id=? AND es.direction='right' ORDER BY es.id DESC", (c["id"],)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            name, kind = employer_media(d)
            d["media_url"] = storage.display_url(name)
            d["media_type"] = kind
            d.pop("logo", None); d.pop("video", None)
            out.append(d)
        return jsonify({"likers": out})

    @app.route("/api/me/boost", methods=["POST"])
    @login_required("candidate")
    def my_boost():
        """Candidate Pro: 24h boost — ranks ahead in employer decks for
        jobs the candidate fits. Labelled to the candidate only."""
        if not billing.is_pro(g.user.get("plan")):
            return jsonify(billing.pro_required_payload("candidate_pro")), 402
        c = my_candidate()
        until = billing.future_str(hours=billing.BOOST_HOURS)
        db().execute("UPDATE candidates SET boost_until=? WHERE id=?", (until, c["id"]))
        db().commit()
        return jsonify({"boost_until": until, "boost_active": True})

    @app.route("/api/employer/jobs/<int:jid>/likers")
    @login_required("employer")
    def employer_job_likers(jid):
        """WHO right-swiped this job. Count is free (v9/N2); identities
        are the Recruiter Pro reveal."""
        emp = my_employer()
        job = db().execute("SELECT * FROM jobs WHERE id=? AND employer_id=?", (jid, emp["id"])).fetchone()
        if not job:
            return jsonify({"error": "job not found"}), 404
        if not billing.is_pro(emp.get("plan")):
            return jsonify(billing.pro_required_payload("recruiter_pro")), 402
        rows = db().execute(
            "SELECT c.* FROM swipes s JOIN candidates c ON c.id=s.candidate_id "
            "WHERE s.job_id=? AND s.direction='right' ORDER BY s.id DESC", (jid,)).fetchall()
        return jsonify({"likers": [public_candidate(row_to_dict(r)) for r in rows]})

    @app.route("/api/employer/jobs/<int:jid>/sponsor", methods=["POST"])
    @login_required("employer")
    def employer_job_sponsor(jid):
        """Recruiter Pro includes ONE active sponsored job at a time:
        sponsoring a second job moves the sponsorship. Sponsored jobs
        sort ahead in candidate discovery and are labelled Sponsored."""
        emp = my_employer()
        job = db().execute("SELECT * FROM jobs WHERE id=? AND employer_id=?", (jid, emp["id"])).fetchone()
        if not job:
            return jsonify({"error": "job not found"}), 404
        if not billing.is_pro(emp.get("plan")):
            return jsonify(billing.pro_required_payload("recruiter_pro")), 402
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
        if d.get("active"):
            db().execute("UPDATE jobs SET sponsored_until=NULL WHERE employer_id=?", (emp["id"],))
            until = billing.future_str(days=billing.SPONSORED_DAYS)
            db().execute("UPDATE jobs SET sponsored_until=? WHERE id=?", (until, jid))
        else:
            until = None
            db().execute("UPDATE jobs SET sponsored_until=NULL WHERE id=?", (jid,))
        db().commit()
        out = row_to_dict(db().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())
        out["sponsored"] = billing.is_future(out.get("sponsored_until"))
        out["media_url"], out["media_type"] = job_media_fields(out, employer_media(emp))
        return jsonify(out)

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
            # Two shapes: JSON (as before) or multipart form (when a media
            # file is attached to the posting, v9/N1).
            if request.files and "media" in request.files or request.form:
                form = request.form
                tags_raw = form.get("tags", "")
                d = {"title": form.get("title", ""), "location": form.get("location", ""),
                     "description": form.get("description", ""), "url": form.get("url", ""),
                     "salary_text": form.get("salary_text", ""),
                     "salary_min": form.get("salary_min"), "salary_max": form.get("salary_max"),
                     "remote": form.get("remote") in ("1", "true", "on", "yes"),
                     "work_mode": form.get("work_mode", ""),
                     "tags": [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []}
            else:
                d = _json_body(required=True)
                if d is None:
                    return jsonify({"error": "JSON object body required"}), 400
            if not isinstance(d.get("title"), str) or not d["title"]:
                return jsonify({"error": "title required"}), 400
            if "tags" in d and not isinstance(d["tags"], list):
                return jsonify({"error": "tags must be a list"}), 400
            try:
                salary_min = _coerce_salary(d.get("salary_min"))
                salary_max = _coerce_salary(d.get("salary_max"))
            except (TypeError, ValueError):
                return jsonify({"error": "salary_min/salary_max must be numbers"}), 400
            work_mode = d.get("work_mode") if isinstance(d.get("work_mode"), str) else ""
            work_mode = work_mode.strip().lower()
            if work_mode and work_mode not in WORK_MODES:
                return jsonify({"error": "work_mode must be remote|hybrid|in_person|unspecified"}), 400
            # v9/N1 MANDATORY MEDIA (D1): a job may only be published with a
            # picture or a video — attached to this posting, or inherited
            # from the recruiter profile. Pre-existing (seeded/imported)
            # jobs are grandfathered; this rule governs new posts only.
            media_file = request.files.get("media") if request.files else None
            has_attached = bool(media_file and media_file.filename)
            emp = my_employer()  # fresh row: media may have just been set
            if not has_attached and not employer_media(emp)[0]:
                return jsonify({"error": "media required: attach a picture or video to this job, or add one to your recruiter profile first — jobs cannot be published without media"}), 400
            remote = 1 if d.get("remote") else 0
            if work_mode == "remote":
                remote = 1
            elif work_mode in ("hybrid", "in_person"):
                remote = 0
            j = {"external_id": f"employer-{emp['id']}-{d['title'][:20].replace(' ', '-').lower()}-{uuid.uuid4().hex[:6]}", "title": d["title"], "company": emp["company"], "location": d.get("location", "") if isinstance(d.get("location"), str) else "", "remote": remote, "work_mode": work_mode or None, "salary_min": salary_min, "salary_max": salary_max, "salary_text": d.get("salary_text", "") if isinstance(d.get("salary_text"), str) else "", "description": d.get("description", "") if isinstance(d.get("description"), str) else "", "url": d.get("url", "") if isinstance(d.get("url"), str) else "", "source": "employer", "tags": d.get("tags", []), "employer_id": emp["id"]}
            insert_jobs(db(), [j])
            row = db().execute("SELECT * FROM jobs WHERE employer_id=? ORDER BY id DESC LIMIT 1", (emp["id"],)).fetchone()
            job = row_to_dict(row)
            if has_attached:
                name, blob_url, kind, err = _store_media_file(media_file, f"jobs_{job['id']}")
                if err:
                    return err
                db().execute("UPDATE jobs SET media=?, media_blob_url=?, media_type=? WHERE id=?", (name, blob_url, kind, job["id"]))
                db().commit()
                job = row_to_dict(db().execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
            job["media_url"], job["media_type"] = job_media_fields(job, employer_media(emp))
            job["likes_count"] = 0
            job["sponsored"] = False
            return jsonify(job), 201
        rows = db().execute("SELECT * FROM jobs WHERE employer_id=? ORDER BY id DESC", (emp["id"],)).fetchall()
        emp_m = employer_media(emp)
        out = []
        for r in rows:
            j = row_to_dict(r)
            # v9/N2: per-JOB like count = candidate right-swipes on this job.
            j["likes_count"] = db().execute(
                "SELECT COUNT(*) c FROM swipes WHERE job_id=? AND direction='right'", (j["id"],)).fetchone()["c"]
            j["media_url"], j["media_type"] = job_media_fields(j, emp_m)
            j["sponsored"] = billing.is_future(j.get("sponsored_until"))
            out.append(j)
        return jsonify(out)

    @app.route("/api/employer/jobs/<int:jid>/media", methods=["POST"])
    @login_required("employer")
    def employer_job_media(jid):
        """Attach/replace a picture or video on one of my job postings."""
        emp = my_employer()
        row = db().execute("SELECT * FROM jobs WHERE id=? AND employer_id=?", (jid, emp["id"])).fetchone()
        if not row:
            return jsonify({"error": "job not found for this employer"}), 404
        if "file" not in request.files or not request.files["file"].filename:
            return jsonify({"error": "file required"}), 400
        name, blob_url, kind, err = _store_media_file(request.files["file"], f"jobs_{jid}")
        if err:
            return err
        old = row_to_dict(row)
        db().execute("UPDATE jobs SET media=?, media_blob_url=?, media_type=? WHERE id=?", (name, blob_url, kind, jid))
        db().commit()
        if old.get("media") and old["media"] != name:
            storage.delete_upload(app.config["UPLOADS_DIR"], name=old["media"], blob_url=old.get("media_blob_url"))
        job = row_to_dict(db().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())
        job["media_url"], job["media_type"] = job_media_fields(job, employer_media(emp))
        return jsonify({"ok": True, "media_url": job["media_url"], "media_type": kind, "job": job})

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
        boost = {r["id"]: r["boost_until"] for r in rows}
        cands = [public_candidate(row_to_dict(r)) for r in rows]
        # Phase 2: private ranking-only boost flag (stripped by the ranker;
        # employers never see boost state in the payload).
        for cdict in cands:
            cdict["_boost_active"] = billing.is_future(boost.get(cdict["id"]))
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
        d = _json_body(required=True)
        if d is None:
            return jsonify({"error": "JSON object body required"}), 400
        emp = my_employer()
        job_r = db().execute("SELECT * FROM jobs WHERE id=? AND employer_id=?", (_coerce_id(d.get("job_id")), emp["id"])).fetchone()
        if not job_r:
            return jsonify({"error": "job not found for this employer"}), 404
        job = row_to_dict(job_r)
        direction = d.get("direction")
        if direction not in ("left", "right"):
            return jsonify({"error": "direction must be left/right"}), 400
        cand_id = _coerce_id(d.get("candidate_id"))
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
            else:
                # v9/N4: the candidate liked the RECRUITER profile itself —
                # that mutual like also matches, on this job.
                m = maybe_match_from_recruiter_like(db(), cand_id, emp["id"], job_id=job["id"])
                if m:
                    result["match"] = m
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
            d["candidate_photo_url"] = storage.display_url(d.get("candidate_photo"))
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
            d = _json_body(required=True)
            if d is None:
                return jsonify({"error": "JSON object body required"}), 400
            if not isinstance(d.get("body"), str) or not d["body"]:
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
