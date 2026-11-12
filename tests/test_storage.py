"""v7 — Vercel Blob storage layer.

Local backend is the default (no token in the test env) and must behave
exactly as before. The blob backend is exercised with the single HTTP
choke point (storage._http) mocked — no token exists in this environment
and none is needed: put returns a fake public URL, delete is recorded.
"""
import io, os, sqlite3, sys, urllib.parse
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app import storage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
BLOB_BASE = "https://teststore.public.blob.vercel-storage.com/"


class FakeBlobHTTP:
    """Records Blob API calls; PUT returns a fake public URL, DELETE ok."""
    def __init__(self, fail_delete=False):
        self.puts = []     # (pathname, data, headers)
        self.deletes = []  # [url, ...]
        self.fail_delete = fail_delete

    def __call__(self, method, url, data=None, headers=None):
        if method == "PUT":
            pathname = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["pathname"][0]
            self.puts.append((pathname, data, headers or {}))
            return {"url": BLOB_BASE + pathname, "pathname": pathname}
        if method == "POST" and url.endswith("/delete"):
            if self.fail_delete:
                raise RuntimeError("blob API down")
            import json as _json
            self.deletes.extend(_json.loads(data)["urls"])
            return {}
        raise AssertionError(f"unexpected blob call {method} {url}")


@pytest.fixture()
def blob(monkeypatch):
    fake = FakeBlobHTTP()
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_testtoken")
    monkeypatch.setattr(storage, "_http", fake)
    return fake


@pytest.fixture()
def app_(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db"), testing=True)


@pytest.fixture()
def client(app_):
    return app_.test_client()


def signup(client, email="cand@x.com", role="candidate", name="Cand"):
    r = client.post("/api/auth/signup", json={"email": email, "password": "pw1234", "role": role, "name": name})
    assert r.status_code == 201, r.data
    return r.json["user"]


def _db(app_):
    return sqlite3.connect(app_.config["DB_PATH"])


# ---------- unit: backend selection + wire shape ----------

def test_backend_selection(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    assert storage.backend_name() == "local" and not storage.blob_enabled()
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "x")
    assert storage.backend_name() == "blob" and storage.blob_enabled()


def test_blob_put_wire_shape(blob):
    url = storage.blob_put("candidates_1_ab12cd.png", PNG, "image/png")
    assert url == BLOB_BASE + "candidates_1_ab12cd.png"
    pathname, data, headers = blob.puts[0]
    assert pathname == "candidates_1_ab12cd.png" and data == PNG
    assert headers["Authorization"] == "Bearer vercel_blob_rw_testtoken"
    assert headers["x-api-version"] == "7"
    assert headers["x-content-type"] == "image/png"


def test_save_upload_local_writes_file(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    out = storage.save_upload(str(tmp_path), "a_b_1.png", PNG)
    assert out is None
    assert (tmp_path / "a_b_1.png").read_bytes() == PNG


def test_save_upload_blob_skips_disk(blob, tmp_path):
    url = storage.save_upload(str(tmp_path), "a_b_1.png", PNG)
    assert url == BLOB_BASE + "a_b_1.png"
    assert not (tmp_path / "a_b_1.png").exists()


def test_delete_upload_tolerates_blob_failure(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "x")
    def boom(*a, **kw):
        raise RuntimeError("nope")
    monkeypatch.setattr(storage, "_http", boom)
    storage.delete_upload(blob_url=BLOB_BASE + "x.png")  # must not raise


# ---------- app: health ----------

def test_health_reports_local_storage(client, monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    h = client.get("/api/health").json
    assert h["version"] == 7 and h["storage"] == "local"


def test_health_reports_blob_storage(client, blob):
    assert client.get("/api/health").json["storage"] == "blob"


# ---------- app: photo on the blob backend ----------

def test_photo_upload_blob_stores_url_and_view_redirects(client, app_, blob):
    user = signup(client)
    cid = user["candidate"]["id"]
    r = client.post("/api/profile/photo", data={"file": (io.BytesIO(PNG), "me.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    name = r.json["filename"]
    assert r.json["url"] == f"/uploads/{name}"  # display URL unchanged
    conn = _db(app_)
    stored_name, stored_url = conn.execute("SELECT photo, photo_blob_url FROM candidates WHERE id=?", (cid,)).fetchone()
    conn.close()
    assert stored_name == name
    assert stored_url == BLOB_BASE + name  # put's returned URL is on the record
    assert blob.puts and blob.puts[0][0] == name
    # The /uploads route redirects to the blob URL on this backend.
    v = client.get(f"/uploads/{name}")
    assert v.status_code == 302 and v.headers["Location"] == stored_url
    # API payloads resolve through the same route.
    assert client.get("/api/candidate").json["photo_url"] == f"/uploads/{name}"


def test_photo_upload_local_serves_bytes(client, app_, monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    user = signup(client)
    cid = user["candidate"]["id"]
    r = client.post("/api/profile/photo", data={"file": (io.BytesIO(PNG), "me.png")},
                    content_type="multipart/form-data")
    name = r.json["filename"]
    v = client.get(f"/uploads/{name}")
    assert v.status_code == 200 and v.data == PNG
    conn = _db(app_)
    assert conn.execute("SELECT photo_blob_url FROM candidates WHERE id=?", (cid,)).fetchone()[0] == ""
    conn.close()


# ---------- app: documents on the blob backend ----------

def _add_doc(client):
    r = client.post("/api/documents", json={"doc_type": "resume", "title": "Resume",
                                            "text": "Jane Dev\nPython developer\nSKILLS\nPython, SQL"})
    assert r.status_code == 201, r.data
    return r.json["id"]


def test_document_mirror_and_delete_calls_blob_delete(client, app_, blob):
    signup(client)
    did = _add_doc(client)
    conn = _db(app_)
    blob_url = conn.execute("SELECT blob_url FROM documents WHERE id=?", (did,)).fetchone()[0]
    conn.close()
    assert blob_url.startswith(BLOB_BASE + "documents_")
    assert any(p.startswith("documents_") for p, _, _ in blob.puts)
    r = client.delete(f"/api/documents/{did}")
    assert r.status_code == 200
    assert blob.deletes == [blob_url]


def test_document_delete_tolerates_blob_failure(client, app_, monkeypatch):
    fake = FakeBlobHTTP(fail_delete=True)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_testtoken")
    monkeypatch.setattr(storage, "_http", fake)
    signup(client)
    did = _add_doc(client)
    r = client.delete(f"/api/documents/{did}")
    assert r.status_code == 200 and r.json["deleted"] == did
    conn = _db(app_)
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE id=?", (did,)).fetchone()[0] == 0
    conn.close()


# ---------- app: delete-my-account removes blob objects ----------

def test_delete_account_deletes_blob_objects(client, app_, blob):
    user = signup(client, email="gone@x.com")
    cid = user["candidate"]["id"]
    client.post("/api/profile/photo", data={"file": (io.BytesIO(PNG), "me.png")},
                content_type="multipart/form-data")
    did = _add_doc(client)
    conn = _db(app_)
    photo_url = conn.execute("SELECT photo_blob_url FROM candidates WHERE id=?", (cid,)).fetchone()[0]
    doc_url = conn.execute("SELECT blob_url FROM documents WHERE id=?", (did,)).fetchone()[0]
    conn.close()
    assert photo_url and doc_url
    r = client.delete("/api/account")
    assert r.status_code == 200, r.data
    assert photo_url in blob.deletes and doc_url in blob.deletes
    assert client.post("/api/auth/login", json={"email": "gone@x.com", "password": "pw1234"}).status_code == 401
