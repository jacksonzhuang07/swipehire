"""Regression test: demo-employer seed media must be reachable on the
hosted (Blob) backend, not just on a local disk.

Prod bug (Nov 12, 2026 production checklist): the seed wrote the demo
employer's picture to the local uploads dir and set only employers.logo.
In production the /uploads proxy serves media from the private Blob
store via logo_blob_url — which was empty — and the local file sat on an
ephemeral serverless disk, so the recruiter card in the candidate deck
rendered a black box while the dashboard claimed "media on file".

The seed now goes through storage.save_upload (the real upload path)
and the repair branch heals rows already stuck with logo set but no
logo_blob_url on the blob backend.
"""
import os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db as dbmod
from app import seed_data, storage


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.get_conn(str(tmp_path / "t.db"))
    c.executescript(dbmod.SCHEMA)
    c.commit()
    return c


def _employer(conn, logo="", logo_blob_url=""):
    cur = conn.execute(
        "INSERT INTO employers(name,company,email,logo,logo_blob_url) VALUES(?,?,?,?,?)",
        ("Demo Hiring Manager", "Demo Employer Inc.", "hiring@demo.example.com",
         logo, logo_blob_url))
    conn.commit()
    return cur.lastrowid


def test_broken_prod_state_is_healed_on_blob_backend(conn, tmp_path, monkeypatch):
    emp_id = _employer(conn, logo="seed_demo_employer.png", logo_blob_url="")
    calls = {}

    def fake_save(uploads_dir, name, data, content_type=None):
        calls.update(name=name, data=data, content_type=content_type)
        return "https://blob.example/seed_demo_employer.png"

    monkeypatch.setattr(storage, "blob_enabled", lambda: True)
    monkeypatch.setattr(storage, "save_upload", fake_save)
    seed_data._seed_demo_employer_media(conn, emp_id, str(tmp_path))
    row = conn.execute("SELECT logo, logo_blob_url FROM employers WHERE id=?",
                       (emp_id,)).fetchone()
    assert row["logo"] == "seed_demo_employer.png"
    assert row["logo_blob_url"] == "https://blob.example/seed_demo_employer.png"
    assert calls["content_type"] == "image/png"
    assert calls["data"].startswith(b"\x89PNG")  # a real, decodable PNG


def test_local_backend_with_logo_is_left_alone(conn, tmp_path, monkeypatch):
    emp_id = _employer(conn, logo="seed_demo_employer.png", logo_blob_url="")
    monkeypatch.setattr(storage, "blob_enabled", lambda: False)
    monkeypatch.setattr(storage, "save_upload",
                        lambda *a, **k: pytest.fail("must not re-upload locally"))
    seed_data._seed_demo_employer_media(conn, emp_id, str(tmp_path))
    row = conn.execute("SELECT logo_blob_url FROM employers WHERE id=?",
                       (emp_id,)).fetchone()
    assert row["logo_blob_url"] == ""


def test_fresh_employer_gets_local_media(conn, tmp_path):
    emp_id = _employer(conn)
    seed_data._seed_demo_employer_media(conn, emp_id, str(tmp_path))
    row = conn.execute("SELECT logo, logo_blob_url FROM employers WHERE id=?",
                       (emp_id,)).fetchone()
    assert row["logo"] == "seed_demo_employer.png"
    assert row["logo_blob_url"] == ""  # local backend has no blob URL
    with open(os.path.join(str(tmp_path), "seed_demo_employer.png"), "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
