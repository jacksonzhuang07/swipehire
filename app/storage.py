"""Upload storage layer — two backends, selected by environment.

- blob  : BLOB_READ_WRITE_TOKEN set → Vercel Blob via its HTTP API
          (stdlib urllib only — no new dependency). The connected store
          ("swipehire-uploads") is a PRIVATE store, so every call follows
          the private-store contract in the Vercel Blob docs:
            * PUT declares the access level with the header
              `x-vercel-blob-access: private` (the access value must
              match the store — omitting it is what made production
              uploads fail with HTTP 400 in v7).
            * Objects are NOT publicly fetchable: the URL returned by
              PUT (*.private.blob.vercel-storage.com) requires the
              read-write token. Bytes are read back with a GET on that
              URL carrying `Authorization: Bearer <token>`.
            * Delete is POST /delete with the URL list, same Bearer auth.
          The token lives only on the server; it is never sent to a
          browser and never logged (error paths log the API's response
          body, never request headers).
- local : no token → files under get_uploads_dir(), exactly as before
          (dev and tests; zero behaviour change).

Access control: ENFORCED on both backends. The app's own
/uploads/<name> route is session-gated (@login_required); on the blob
backend it fetches the object server-side with the token and streams
the bytes to the already-authorized user — the blob URL itself is
never handed to the browser, and a private-store URL is useless
without the token anyway. (This replaces the v7 posture, where /uploads
302-redirected to a blob URL under the mistaken assumption the store
was public.)
"""
import json, logging, os, urllib.error, urllib.parse, urllib.request

log = logging.getLogger(__name__)

BLOB_API = "https://blob.vercel-storage.com"
API_VERSION = "7"
BLOB_ACCESS = "private"  # the connected store is a private store


class BlobAPIError(Exception):
    """A Blob API call failed. Carries the HTTP status and the API's own
    error detail (parsed from its JSON response body) so failures are
    debuggable from logs instead of surfacing as a bare HTTPError."""

    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__(f"Blob API error (HTTP {status}): {detail}")


def blob_enabled():
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))


def backend_name():
    return "blob" if blob_enabled() else "local"


def _token():
    return os.environ["BLOB_READ_WRITE_TOKEN"]


def _error_detail(body):
    """Best-effort extraction of the API's error message from a response
    body (Blob errors are JSON like {"error": {"code", "message"}})."""
    text = (body or b"").decode("utf-8", "replace").strip()
    try:
        parsed = json.loads(text)
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return err["message"]
        if isinstance(parsed, dict) and parsed.get("message"):
            return parsed["message"]
    except ValueError:
        pass
    return text or "(empty error body)"


def _http(method, url, data=None, headers=None, raw=False):
    """Single HTTP choke point for the Blob API (mocked in tests).

    Default: parses the response body as JSON and returns it.
    raw=True: returns (body_bytes, content_type) — for fetching blob
    objects. On HTTPError the response body is read and raised as a
    BlobAPIError carrying the API's own message (and logged); the
    request headers — which hold the token — are never logged."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type") if r.headers else None
    except urllib.error.HTTPError as e:
        detail = _error_detail(e.read())
        log.error("blob API %s %s failed: HTTP %s: %s",
                  method, urllib.parse.urlparse(url).netloc, e.code, detail)
        raise BlobAPIError(e.code, detail) from e
    if raw:
        return body, ctype
    return json.loads(body) if body else {}


def blob_put(pathname, data, content_type=None):
    """Upload bytes to the private Vercel Blob store; returns the
    private URL from the put response ({url, pathname, contentType,
    ...}). The URL requires the read-write token to fetch — see
    blob_get. The `x-vercel-blob-access: private` header is required by
    the documented API: the declared access must match the store."""
    url = BLOB_API + "/?pathname=" + urllib.parse.quote(pathname, safe="")
    headers = {"Authorization": f"Bearer {_token()}",
               "x-api-version": API_VERSION,
               "x-vercel-blob-access": BLOB_ACCESS,
               "x-add-random-suffix": "0",
               "x-allow-overwrite": "1"}
    if content_type:
        headers["x-content-type"] = content_type
    resp = _http("PUT", url, data=data, headers=headers)
    return resp["url"]


def blob_get(url):
    """Fetch a private blob object's bytes server-side: a GET against
    the blob URL with the read-write token as Authorization (the
    documented private-download contract). Returns (bytes,
    content_type). Raises BlobAPIError on failure."""
    return _http("GET", url,
                 headers={"Authorization": f"Bearer {_token()}"},
                 raw=True)


def blob_delete(urls):
    """Delete blob objects by URL (Blob delete API; same Bearer token
    auth on a private store). Raises BlobAPIError on HTTP failure —
    callers that must not break catch it (see delete_upload)."""
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
    own session-gated /uploads/<name> route: it serves the local file,
    or — when the owning record has a blob URL — fetches the private
    blob server-side and streams the bytes. Templates and API payloads
    render identically on both backends, and the blob URL (and token)
    never reach the browser."""
    return f"/uploads/{name}" if name else ""
