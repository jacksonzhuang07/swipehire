import os, sqlite3, json

def get_db_path():
    return os.environ.get("SWIPEHIRE_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "swipehire.db")

def get_uploads_dir():
    """Directory for the LOCAL storage backend (see app/storage.py).

    On Vercel the local backend lands in /tmp and is EPHEMERAL — that is
    why production sets BLOB_READ_WRITE_TOKEN, which switches uploads to
    the persistent Vercel Blob backend. Local disk remains the dev/test
    backend and needs no token."""
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        d = "/tmp/swipehire_uploads"
    else:
        d = os.environ.get("SWIPEHIRE_UPLOADS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uploads")
    os.makedirs(d, exist_ok=True)
    return d

# ---------------------------------------------------------------------------
# Hosted backend: Turso / libSQL (used only when TURSO_DATABASE_URL and
# TURSO_AUTH_TOKEN are both set). Everything else in the app talks to a
# sqlite3-shaped connection: execute(sql, params) -> cursor with
# fetchone/fetchall/lastrowid, executescript, commit, close, and rows that
# support BOTH r[0] and r["column"] plus dict(row). The `libsql` package is
# sqlite3-shaped already except that its rows are plain tuples and its
# cursor.description is the only source of column names — the small wrapper
# below adapts exactly those two things (and nothing else).
# ---------------------------------------------------------------------------

def turso_configured():
    return bool(os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"))

class _Row:
    """sqlite3.Row stand-in: index + name access, keys() so dict(row) works."""
    __slots__ = ("_cols", "_vals", "_map")
    def __init__(self, cols, vals):
        self._cols = list(cols)
        self._vals = tuple(vals)
        self._map = {}
        for i, c in enumerate(self._cols):
            self._map.setdefault(c, i)
    def __getitem__(self, key):
        if isinstance(key, str):
            return self._vals[self._map[key]]
        return self._vals[key]
    def __iter__(self):
        return iter(self._vals)
    def __len__(self):
        return len(self._vals)
    def keys(self):
        return list(self._cols)
    def get(self, key, default=None):
        i = self._map.get(key)
        return default if i is None else self._vals[i]

class _Cursor:
    """Eager cursor: rows are fetched at execute() time (app-scale data)."""
    def __init__(self, cols, rows, lastrowid=None, rowcount=-1):
        self._rows = [_Row(cols, r) for r in rows]
        self._i = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self.description = [(c,) + (None,) * 6 for c in cols]
    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        r = self._rows[self._i]
        self._i += 1
        return r
    def fetchall(self):
        out = self._rows[self._i:]
        self._i = len(self._rows)
        return out
    def __iter__(self):
        return iter(self.fetchall())

class TursoConn:
    """sqlite3-shaped connection backed by libsql (Turso hosted or local file).

    url: a libsql://… Turso URL in production; a plain file path also works
    (used by the unit tests to exercise this exact code path offline)."""
    def __init__(self, url=None, auth_token=None):
        import libsql  # lazy: local-SQLite users never need the dependency
        self._url = url or os.environ.get("TURSO_DATABASE_URL")
        token = auth_token if auth_token is not None else os.environ.get("TURSO_AUTH_TOKEN")
        kwargs = {}
        if token:
            kwargs["auth_token"] = token
        self._c = libsql.connect(self._url, **kwargs)
        try:
            self._c.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
    @staticmethod
    def _cols_of(cur):
        try:
            return [d[0] for d in (cur.description or ())]
        except Exception:
            return []
    def execute(self, sql, params=()):
        cur = self._c.execute(sql, tuple(params or ()))
        cols = self._cols_of(cur)
        try:
            rows = cur.fetchall() or []  # libsql returns None for non-SELECTs
        except Exception:
            rows = []
        if rows and not cols:  # pragma-style fallback: synthesize positional names
            cols = [str(i) for i in range(len(rows[0]))]
        return _Cursor(cols, rows, getattr(cur, "lastrowid", None), getattr(cur, "rowcount", -1))
    def executescript(self, script):
        # libsql's executescript runs the whole script; return an empty cursor.
        self._c.executescript(script)
        return _Cursor([], [])
    def commit(self):
        self._c.commit()
    def rollback(self):
        try:
            self._c.rollback()
        except Exception:
            pass
    def close(self):
        try:
            self._c.close()
        except Exception:
            pass

def get_conn(path=None):
    # Hosted backend wins whenever it is configured — the file path argument
    # (kept for the local/test call sites) is intentionally ignored then.
    if turso_configured():
        return TursoConn()
    p = path or get_db_path()
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'candidate',name TEXT DEFAULT '',candidate_id INTEGER,employer_id INTEGER,is_demo INTEGER DEFAULT 0,created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT,phone TEXT,title TEXT,skills TEXT DEFAULT '[]',years_exp REAL DEFAULT 0,summary TEXT,resume_text TEXT,is_demo INTEGER DEFAULT 0,target_titles TEXT DEFAULT '[]',locations TEXT DEFAULT '[]',remote_only INTEGER DEFAULT 0,min_salary INTEGER DEFAULT 0,user_id INTEGER,photo TEXT DEFAULT '',photo_blob_url TEXT DEFAULT '',video TEXT DEFAULT '',video_blob_url TEXT DEFAULT '',master_profile TEXT DEFAULT '{}',compiled_resume TEXT DEFAULT '',created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS employers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,company TEXT NOT NULL,email TEXT,user_id INTEGER,logo TEXT DEFAULT '',logo_blob_url TEXT DEFAULT '',video TEXT DEFAULT '',video_blob_url TEXT DEFAULT '',created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,external_id TEXT NOT NULL,title TEXT NOT NULL,company TEXT NOT NULL,location TEXT DEFAULT '',remote INTEGER DEFAULT 0,work_mode TEXT DEFAULT 'unspecified',media TEXT DEFAULT '',media_blob_url TEXT DEFAULT '',media_type TEXT DEFAULT '',salary_min INTEGER,salary_max INTEGER,salary_text TEXT DEFAULT '',description TEXT DEFAULT '',url TEXT DEFAULT '',source TEXT NOT NULL,tags TEXT DEFAULT '[]',employer_id INTEGER,posted_at TEXT DEFAULT '',apply_type TEXT DEFAULT 'external',requires_login INTEGER DEFAULT 0,created_at TEXT DEFAULT (datetime('now')),UNIQUE(source,external_id));
CREATE TABLE IF NOT EXISTS recruiter_swipes(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,employer_id INTEGER NOT NULL,direction TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,employer_id));
CREATE TABLE IF NOT EXISTS swipes(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,job_id INTEGER NOT NULL,direction TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,job_id INTEGER NOT NULL,status TEXT NOT NULL,method TEXT NOT NULL,proof TEXT DEFAULT '',answers TEXT DEFAULT '{}',needs_review TEXT DEFAULT '[]',tailored_resume TEXT DEFAULT '',coverage TEXT DEFAULT '{}',tailored INTEGER DEFAULT 0,created_at TEXT DEFAULT (datetime('now')),updated_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS employer_swipes(id INTEGER PRIMARY KEY AUTOINCREMENT,employer_id INTEGER NOT NULL,job_id INTEGER NOT NULL,candidate_id INTEGER NOT NULL,direction TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(employer_id,job_id,candidate_id));
CREATE TABLE IF NOT EXISTS matches(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,employer_id INTEGER NOT NULL,job_id INTEGER NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,match_id INTEGER NOT NULL,sender TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,doc_type TEXT NOT NULL DEFAULT 'other',title TEXT DEFAULT '',filename TEXT DEFAULT '',blob_url TEXT DEFAULT '',text TEXT DEFAULT '',facts TEXT DEFAULT '{}',created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS login_codes(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,code_hash TEXT NOT NULL,attempts INTEGER DEFAULT 0,used INTEGER DEFAULT 0,expires_at TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS connections(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,candidate_id INTEGER,board TEXT NOT NULL,identifier TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending_session',created_at TEXT DEFAULT (datetime('now')),UNIQUE(user_id,board));
CREATE TABLE IF NOT EXISTS ext_tokens(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,token_hash TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')),last_used_at TEXT,revoked INTEGER DEFAULT 0);
"""

# Columns added in v2 — also applied to pre-existing v1 databases.
_MIGRATIONS = [
    ("users", "is_demo", "INTEGER DEFAULT 0"),
    ("candidates", "user_id", "INTEGER"),
    ("candidates", "photo", "TEXT DEFAULT ''"),
    ("candidates", "photo_blob_url", "TEXT DEFAULT ''"),
    ("employers", "logo_blob_url", "TEXT DEFAULT ''"),
    ("documents", "blob_url", "TEXT DEFAULT ''"),
    ("candidates", "master_profile", "TEXT DEFAULT '{}'"),
    ("candidates", "compiled_resume", "TEXT DEFAULT ''"),
    ("employers", "user_id", "INTEGER"),
    ("employers", "logo", "TEXT DEFAULT ''"),
    ("jobs", "posted_at", "TEXT DEFAULT ''"),
    ("jobs", "apply_type", "TEXT DEFAULT 'external'"),
    ("jobs", "requires_login", "INTEGER DEFAULT 0"),
    ("applications", "tailored_resume", "TEXT DEFAULT ''"),
    ("applications", "coverage", "TEXT DEFAULT '{}'"),
    ("applications", "tailored", "INTEGER DEFAULT 0"),
    # v9 (feature amendments N1/N3): profile videos, job work_mode + media.
    ("candidates", "video", "TEXT DEFAULT ''"),
    ("candidates", "video_blob_url", "TEXT DEFAULT ''"),
    ("employers", "video", "TEXT DEFAULT ''"),
    ("employers", "video_blob_url", "TEXT DEFAULT ''"),
    ("jobs", "work_mode", "TEXT DEFAULT 'unspecified'"),
    ("jobs", "media", "TEXT DEFAULT ''"),
    ("jobs", "media_blob_url", "TEXT DEFAULT ''"),
    ("jobs", "media_type", "TEXT DEFAULT ''"),
]

def init_db(path=None):
    conn = get_conn(path)
    conn.executescript(SCHEMA)
    for table, col, ddl in _MIGRATIONS:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    conn.commit()
    backfill_classification(conn)
    backfill_work_mode(conn)
    return conn

def backfill_work_mode(conn):
    """Fill jobs.work_mode for rows ingested before the field existed
    (v9/N3). Only upgrades 'unspecified'/empty rows whose stored signals
    derive to a concrete mode — idempotent, safe on every boot."""
    from .job_sources import derive_work_mode
    changed = 0
    for r in conn.execute("SELECT id,remote,location,title,description,work_mode FROM jobs").fetchall():
        if r["work_mode"] and r["work_mode"] != "unspecified":
            continue
        mode = derive_work_mode(dict(r))
        if mode != "unspecified":
            conn.execute("UPDATE jobs SET work_mode=? WHERE id=?", (mode, r["id"]))
            changed += 1
    if changed:
        conn.commit()
    return changed

def backfill_classification(conn):
    """(Re)classify every job whose stored classification doesn't match its
    URL — covers rows ingested before classification existed."""
    from .classify import classify_job
    changed = 0
    for r in conn.execute("SELECT id,url,apply_type,requires_login FROM jobs").fetchall():
        at, rl = classify_job(r["url"])
        if r["apply_type"] != at or int(r["requires_login"] or 0) != rl:
            conn.execute("UPDATE jobs SET apply_type=?,requires_login=? WHERE id=?", (at, rl, r["id"]))
            changed += 1
    if changed:
        conn.commit()
    return changed

_JSON_KEYS = ("skills", "tags", "target_titles", "locations", "answers", "needs_review", "master_profile", "coverage", "facts")

def row_to_dict(r):
    if r is None:
        return None
    d = dict(r)
    for k in _JSON_KEYS:
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                d[k] = {} if k in ("master_profile", "coverage", "facts") else []
    return d
