import os, sqlite3, json
def get_db_path():
    return os.environ.get("SWIPEHIRE_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "swipehire.db")
def get_conn(path=None):
    p = path or get_db_path()
    if p.startswith("/tmp") or os.path.dirname(p):
        d=os.path.dirname(p)
        if d: os.makedirs(d, exist_ok=True)
    conn=sqlite3.connect(p); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON"); return conn
SCHEMA="""
CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT,phone TEXT,title TEXT,skills TEXT DEFAULT '[]',years_exp REAL DEFAULT 0,summary TEXT,resume_text TEXT,is_demo INTEGER DEFAULT 0,target_titles TEXT DEFAULT '[]',locations TEXT DEFAULT '[]',remote_only INTEGER DEFAULT 0,min_salary INTEGER DEFAULT 0,created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS employers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,company TEXT NOT NULL,email TEXT,created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,external_id TEXT NOT NULL,title TEXT NOT NULL,company TEXT NOT NULL,location TEXT DEFAULT '',remote INTEGER DEFAULT 0,salary_min INTEGER,salary_max INTEGER,salary_text TEXT DEFAULT '',description TEXT DEFAULT '',url TEXT DEFAULT '',source TEXT NOT NULL,tags TEXT DEFAULT '[]',employer_id INTEGER,created_at TEXT DEFAULT (datetime('now')),UNIQUE(source,external_id));
CREATE TABLE IF NOT EXISTS swipes(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,job_id INTEGER NOT NULL,direction TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,job_id INTEGER NOT NULL,status TEXT NOT NULL,method TEXT NOT NULL,proof TEXT DEFAULT '',answers TEXT DEFAULT '{}',needs_review TEXT DEFAULT '[]',created_at TEXT DEFAULT (datetime('now')),updated_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS employer_swipes(id INTEGER PRIMARY KEY AUTOINCREMENT,employer_id INTEGER NOT NULL,job_id INTEGER NOT NULL,candidate_id INTEGER NOT NULL,direction TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(employer_id,job_id,candidate_id));
CREATE TABLE IF NOT EXISTS matches(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,employer_id INTEGER NOT NULL,job_id INTEGER NOT NULL,created_at TEXT DEFAULT (datetime('now')),UNIQUE(candidate_id,job_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,match_id INTEGER NOT NULL,sender TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT DEFAULT (datetime('now')));
"""
def init_db(path=None):
    conn=get_conn(path); conn.executescript(SCHEMA); conn.commit(); return conn
def row_to_dict(r):
    if r is None: return None
    d=dict(r)
    for k in ("skills","tags","target_titles","locations","answers","needs_review"):
        if k in d and isinstance(d[k],str):
            try: d[k]=json.loads(d[k])
            except Exception: d[k]=[]
    return d
