import os, sqlite3, json

def get_db_path():
    return os.environ.get("SWIPEHIRE_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "swipehire.db")

def get_uploads_dir():
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        d = "/tmp/swipehire_uploads"
    else:
        d = os.environ.get("SWIPEHIRE_UPLOADS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uploads")
    os.makedirs(d, exist_ok=True)
    return d

def get_conn(path=None):
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
CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT,phone TEXT,title TEXT,skills TEXT DEFAULT '[]',years_exp REAL DEFAULT 0,summary TEXT,resume_text TEXT,is_demo INTEGER DEFAULT 0,target_titles TEXT DEFAULT '[]',locations TEXT DEFAULT '[]',remote_only INTEGER DEFAULT 0,min_salary INTEGER DEFAULT 0,user_id INTEGER,photo TEXT DEFAULT '',master_profile TEXT DEFAULT '{}',compiled_resume TEXT DEFAULT '',created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS employers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,company TEXT NOT NULL,email TEXT,user_id INTEGER,logo TEXT DEFAULT '',created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,external_id TEXT NOT NULL,title TEXT NOT NULL,company TEXT NOT NULL,location TEXT DEFAULT '',remote INTEGER DEFAULT 0,salary_min INTEGER,salary_max INTEGER,salary_text TEXT DEFAULT '',description TEXT DEFAULT '',url TEXT DEFAULT '',source TEXT NOT NULL,tags TEXT DEFAULT '[]',employer_id INTEGER,posted_at TEXT DEFAULT '',created_at TEXT DEFAULT (datetime('now')),UNIQUE(source,external_id));
CREATE TABLE IF NOT EXISTS swipes(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,job_id INTEGER NOT NULL,direction TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,job_id INTEGER NOT NULL,status TEXT NOT NULL,method TEXT NOT NULL,proof TEXT DEFAULT '',answers TEXT DEFAULT '{}',needs_review TEXT DEFAULT '[]',tailored_resume TEXT DEFAULT '',coverage TEXT DEFAULT '{}',tailored INTEGER DEFAULT 0,created_at TEXT DEFAULT (datetime('now')),updated_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS employer_swipes(id INTEGER PRIMARY KEY AUTOINCREMENT,employer_id INTEGER NOT NULL,job_id INTEGER NOT NULL,candidate_id INTEGER NOT NULL,direction TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(employer_id,job_id,candidate_id));
CREATE TABLE IF NOT EXISTS matches(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,employer_id INTEGER NOT NULL,job_id INTEGER NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,match_id INTEGER NOT NULL,sender TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,doc_type TEXT NOT NULL DEFAULT 'other',title TEXT DEFAULT '',filename TEXT DEFAULT '',text TEXT DEFAULT '',facts TEXT DEFAULT '{}',created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS login_codes(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,code_hash TEXT NOT NULL,attempts INTEGER DEFAULT 0,used INTEGER DEFAULT 0,expires_at TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')));
"""

# Columns added in v2 — also applied to pre-existing v1 databases.
_MIGRATIONS = [
    ("users", "is_demo", "INTEGER DEFAULT 0"),
    ("candidates", "user_id", "INTEGER"),
    ("candidates", "photo", "TEXT DEFAULT ''"),
    ("candidates", "master_profile", "TEXT DEFAULT '{}'"),
    ("candidates", "compiled_resume", "TEXT DEFAULT ''"),
    ("employers", "user_id", "INTEGER"),
    ("employers", "logo", "TEXT DEFAULT ''"),
    ("jobs", "posted_at", "TEXT DEFAULT ''"),
    ("applications", "tailored_resume", "TEXT DEFAULT ''"),
    ("applications", "coverage", "TEXT DEFAULT '{}'"),
    ("applications", "tailored", "INTEGER DEFAULT 0"),
]

def init_db(path=None):
    conn = get_conn(path)
    conn.executescript(SCHEMA)
    for table, col, ddl in _MIGRATIONS:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    conn.commit()
    return conn

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
