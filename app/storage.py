"""Upload storage layer — two backends, selected by environment.

- blob  : BLOB_READ_WRITE_TOKEN set → Vercel Blob via its HTTP API
          (stdlib urllib only — no new dependency). Objects are stored
          under the same unguessable pathnames used locally, and the
          returned public blob URL is recorded on the DB row next to the
          filename, so deletion/serving know where the bytes live.
- local : no token → files under get_uploads_dir(), exactly as before
          (dev and tests; zero behaviour change).

Access-control honesty: Vercel Blob objects on this store are
PUBLIC-IF-KNOWN — anyone holding the full URL can fetch the file; there
is no per-request authorization check like the session-gated local
/uploads route provides. We do NOT claim otherwise: protection on the
blob backend is only the unguessable pathname (owner id + uuid suffix),
the same posture as an unlisted link. See BUILD_NOTES (v7).
"""
import json, logging, os, urllib.parse, urllib.request

log = logging.getLogger(__name__)

BLOB_API = "https://blob.vercel-storage.com"
API_VERSION = "7"


def blob_enabled():
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))


def backend_name():
    return "blob" if blob_enabled() else "local"


def _token():
    return os.environ["BLOB_READ_WRITE_TOKEN"]


def _http(method, url, data=None, headers=None):
    """Single HTTP choke point for the Blob API (mocked in tests)."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    return json.loads(body) if body else {}


def blob_put(pathname, data, content_type=None):
    """Upload bytes to Vercel Blob; returns the public URL from the
    put response ({url, pathname, contentType, ...})."""
    url = BLOB_API + "/?pathname=" + urllib.parse.quote(pathname, safe="")
    headers = {"Authorization": f"Bearer {_token()}",
               "x-api-version": API_VERSION,
               "x-add-random-suffix": "0",
               "x-allow-overwrite": "1"}
    if content_type:
        headers["x-content-type"] = content_type
    resp = _http("PUT", url, data=data, headers=headers)
    return resp["url"]


def blob_delete(urls):
    """Delete blob objects by URL (Blob delete API). Raises on HTTP
    failure — callers that must not break catch it (see delete_upload)."""
    urls = [u for u in (urls or []) if u]
    if not urls:
        return
    _http("POST", BLOB_API + "/delete",
          data=json.dumps({"urls": urls}).encode(),
          headers={"Authorization": f"Bearer {_token()}",
                   "x-api-version": API_VERSION,
                   "Content-Type": "application/json"})


def save_upload(uploads_dir, name, data, content_type=None):
    """Persist one upload under its unguessable name. Returns the blob
    URL on the blob backend, or None on the local backend (file written
    under uploads_dir, exactly as the app behaved before v7)."""
    if blob_enabled():
        return blob_put(name, data, content_type)
    os.makedirs(uploads_dir, exist_ok=True)
    with open(os.path.join(uploads_dir, name), "wb") as fh:
        fh.write(data)
    return None


def delete_upload(uploads_dir=None, name=None, blob_url=None):
    """Best-effort delete on both backends. NEVER raises: a failed remote
    delete must not break account/document deletion (it is logged, and
    the object may be orphaned — see BUILD_NOTES)."""
    if blob_url:
        try:
            blob_delete([blob_url])
        except Exception:
            log.warning("blob delete failed for %s — object may be orphaned",
                        blob_url, exc_info=True)
    if name and uploads_dir:
        try:
            os.remove(os.path.join(uploads_dir, os.path.basename(name)))
        except OSError:
            pass


def display_url(name):
    """The one helper every photo/logo URL goes through. Always the app's
    own /uploads/<name> route: it serves the local file, or 302-redirects
    to the blob URL when the owning record has one — so templates and API
    payloads render identically on both backends."""
    return f"/uploads/{name}" if name else ""
