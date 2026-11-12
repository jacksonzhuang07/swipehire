"""Regression test for the 2026-11-12 production outage.

Root cause: on the hosted Turso backend, the PRAGMA table_info view used by
init_db's migration guard can miss a column that already exists (a
concurrent cold start applied the migration first). The guard then issued
ALTER TABLE ... ADD COLUMN for an existing column, the backend raised
"duplicate column name", create_app() died at import, and every Vercel
invocation returned 500 FUNCTION_INVOCATION_FAILED.

Local SQLite tests never caught it: file DBs are created fresh by SCHEMA
(which already contains the new columns) and sqlite's table_info is exact.

This test simulates the hosted behaviour with a connection wrapper whose
table_info view is stale (it never reports the Phase-2 `plan` columns)
while the real database already has them, so every guarded ALTER raises
OperationalError — init_db must swallow those and boot cleanly.
"""
import os, sqlite3, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db as dbmod


class _StaleInfoCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class StaleTableInfoConn:
    """Wraps a real sqlite3 connection. PRAGMA table_info answers are
    filtered to hide the Phase-2 plan columns (the stale hosted view);
    everything else passes through untouched, so ALTERs against the real
    schema raise sqlite3.OperationalError('duplicate column name')."""

    HIDDEN = {"plan", "plan_started_at"}

    def __init__(self, real):
        self._real = real

    def executescript(self, script):
        return self._real.executescript(script)

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("PRAGMA TABLE_INFO"):
            rows = self._real.execute(sql).fetchall()
            return _StaleInfoCursor([r for r in rows if r[1] not in self.HIDDEN])
        return self._real.execute(sql, tuple(params or ()))

    def commit(self):
        return self._real.commit()


@pytest.fixture()
def stale_conn(tmp_path, monkeypatch):
    # A database that already carries the full current schema (as the
    # production Turso DB does after the first v9 boot applied it).
    path = str(tmp_path / "prod.db")
    real = sqlite3.connect(path)
    real.row_factory = sqlite3.Row
    real.executescript(dbmod.SCHEMA)
    real.commit()
    wrapped = StaleTableInfoConn(real)
    monkeypatch.setattr(dbmod, "get_conn", lambda p=None: wrapped)
    return real


def test_init_db_survives_stale_table_info(stale_conn):
    # Pre-fix this raised OperationalError: duplicate column name: plan.
    conn = dbmod.init_db()
    assert conn is not None
    # Boot is repeatable under the same stale view.
    dbmod.init_db()
    # And the columns really are present exactly once.
    cols = [r[1] for r in stale_conn.execute("PRAGMA table_info(users)").fetchall()]
    assert cols.count("plan") == 1
    cols = [r[1] for r in stale_conn.execute("PRAGMA table_info(employers)").fetchall()]
    assert cols.count("plan") == 1
