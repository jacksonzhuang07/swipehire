"""Upload storage layer — two backends, selected by environment.

- blob  : BLOB_READ_WRITE_TOKEN set → Vercel Blob via its HTTP API
          (stdlib urllib only — no new dependency). The wire format
          mirrors the official @vercel/blob SDK (v2.9.0, src/api.ts
          `requestApi` + src/put.ts) request-for-request — v8.1: the
          earlier hand-rolled construction PUT to the *object* host
          with API version 7 and no store headers, and production
          rejected it (HTTP 400 "Invalid pathname"). The SDK instead:
            * PUTs to the API host `https://vercel.com/api/blob`
              with the pathname as a `?pathname=` query parameter
              (blob.vercel-storage.com is only the *object* host,
              built by the SDK's constructBlobUrl from store id +
              access + pathname — never an API endpoint).
            * Sends `x-api-version: 13` plus the requestApi headers
              `x-vercel-blob-store-id` (parsed out of the read-write
              token: `vercel_blob_rw_<storeId>_<secret>`, segment 4),
              `x-api-blob-request-id` and `x-api-blob-request-attempt`.
            * Declares the store's access level with the put header
              `x-vercel-blob-access: private` (the connected store
              "swipehire-uploads" is PRIVATE; omitting it is what made
              production uploads fail with HTTP 400 in v7), and the
              object's content type as `x-content-type`. It sends NO
              x-add-random-suffix / x-allow-overwrite headers unless
              the caller explicitly sets those options.
            * Deletes via POST {api}/delete with the same API headers
              and a JSON {"urls": [...]} body.
            * Reads objects back with a plain GET on the returned
              object URL carrying only `Authorization: Bearer <token>`
              (the SDK's get()) — private-store URLs are useless
              without the token.
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
import json, logging, os, secrets, time, urllib.error, urllib.parse, urllib.request

log = logging.getLogger(__name__)

BLOB_API = "https://vercel.com/api/blob"  # SDK: defaultVercelBlobApiUrl
API_VERSION = "13"  # SDK: BLOB_API_VERSION in @vercel/blob 2.9.0
BLOB_ACCESS = "private"  # the connected store is a private store
MAX_PATHNAME_LENGTH = 950  # SDK: MAXIMUM_PATHNAME_LENGTH (client-side check)


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


def _store_id():
    """Store id embedded in the read-write token. The SDK
    (parseStoreIdFromReadWriteToken) takes segment 4 of
    `vercel_blob_rw_<storeId>_<secret>` split on "_"."""
    parts = _token().split("_")
    return parts[3] if len(parts) > 3 else ""


def _api_headers():
    """The headers the SDK's requestApi puts on EVERY API call (PUT and
    delete alike): a per-request id `<storeId>:<epoch-ms>:<hex>`, the
    store id, the attempt counter, the API version, and Bearer auth.
    (fetch lowercases header names on the wire; HTTP header names are
    case-insensitive, and urllib's capitalisation is equivalent.)"""
    store_id = _store_id()
    request_id = f"{store_id}:{int(time.time() * 1000)}:{secrets.token_hex(6)}"
    return {"Authorization": f"Bearer {_token()}",
            "x-api-version": API_VERSION,
            "x-vercel-blob-store-id": store_id,
            "x-api-blob-request-id": request_id,
            "x-api-blob-request-attempt": "0"}


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
    blob_get.

    Mirrors the SDK's put() exactly: PUT {api}/?pathname=<urlencoded>
    (URLSearchParams encoding — urllib.parse.urlencode) with the
    requestApi headers plus the put headers createPutHeaders emits for
    a plain put(pathname, body, {access, contentType}) call:
    `x-vercel-blob-access` always, `x-content-type` when a content type
    is given, and NO x-add-random-suffix / x-allow-overwrite (the SDK
    sends those only when the caller explicitly sets the options;
    header-absent is the API's no-suffix / no-overwrite default, and
    our pathnames carry a uuid so collisions cannot occur)."""
    # SDK createPutOptions client-side validation, mirrored: pathname
    # required, <= 950 chars, and the one disallowed character
    # sequence is "//" (disallowedPathnameCharacters).
    if not pathname or len(pathname) > MAX_PATHNAME_LENGTH or "//" in pathname:
        raise ValueError(f"invalid blob pathname: {pathname!r}")
    url = BLOB_API + "/?" + urllib.parse.urlencode({"pathname": pathname})
    headers = _api_headers()
    headers["x-vercel-blob-access"] = BLOB_ACCESS
    if content_type:
        headers["x-content-type"] = content_type
    resp = _http("PUT", url, data=data, headers=headers)
    return resp["url"]


def blob_get(url):
    """Fetch a private blob object's bytes server-side: a GET against
    the blob URL with the read-write token as Authorization — exactly
    the SDK's get(), which sends no API headers on object fetches.
    Returns (bytes, content_type). Raises BlobAPIError on failure."""
    return _http("GET", url,
                 headers={"Authorization": f"Bearer {_token()}"},
                 raw=True)


def blob_delete(urls):
    """Delete blob objects by URL. Mirrors the SDK's del(): POST
    {api}/delete with the requestApi headers, a JSON {"urls": [...]}
    body and Content-Type: application/json. Raises BlobAPIError on
    HTTP failure — callers that must not break catch it (see
    delete_upload)."""
    urls = [u for u in (urls or []) if u]
    if not urls:
        return
    headers = _api_headers()
    headers["Content-Type"] = "application/json"
    _http("POST", BLOB_API + "/delete",
          data=json.dumps({"urls": urls}).encode(),
          headers=headers)


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
