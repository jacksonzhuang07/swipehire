"""Monetization config + plan helpers (Phase 2) and the Stripe
integration (Phase A, Nov 13, 2026).

Source: docs/MONETIZATION.md / research report
~/workspace/research_notes/swipehire-monetization-strategy-20261112-1848.

ALL prices live here — the UI renders them from GET /api/billing/plans and
never hard-codes a number.

Payments posture — the honest seam, now with real Stripe behind it:
- While STRIPE_SECRET_KEY is unset, checkout returns 503
  {payments_not_configured} and the UI falls back to the waitlist
  (POST /api/billing/interest). Nothing about that path changed.
- Once the key is set, POST /api/billing/checkout creates a REAL Stripe
  Checkout Session (inline price_data — no pre-created Stripe products)
  and returns its URL; the browser leaves for Stripe-hosted checkout.
- Pro is granted ONLY by the signed Stripe webhook
  (POST /api/billing/webhook → checkout.session.completed) and revoked
  by customer.subscription.deleted. No endpoint, toggle, or test helper
  in production code grants Pro any other way; tests that need a Pro
  account either set plan columns directly (Phase 2 style) or drive the
  webhook with a correctly-signed payload (Phase A style).

Stripe is talked to with stdlib urllib only (same posture as
app/storage.py): form-encoded POSTs, Bearer key, and the key is never
logged — error paths log Stripe's own error message only.
"""
import hashlib, hmac, json, logging, os, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# ---- prices (per month unless noted; currency = BILLING_CURRENCY) ----
CANDIDATE_PRO_PRICE = 24          # research band $19–$29 (auto-apply agent tools)
RECRUITER_PRO_PRICE = 149         # research band $135–$199 per seat (Wellfound)
SPONSORED_ALACARTE_PRICE = 200   # per 30 days (Wellfound Job Ads benchmark)
SPONSORED_DAYS = 30               # a sponsorship (incl. Pro's) runs 30 days
FREE_QUEUE_ALLOWANCE = 5          # free candidates: queued_extension apps/day
BOOST_HOURS = 24                  # Candidate Pro boost duration

# ---- Stripe config (env-only; see docs/MONETIZATION.md checklist) ----
BILLING_CURRENCY = "cad"          # every amount sent to Stripe uses this
STRIPE_API_BASE = "https://api.stripe.com/v1"
WEBHOOK_TOLERANCE_SECONDS = 300   # Stripe's recommended 5-minute window

PLAN_CATALOG = [
    {
        "key": "candidate_pro",
        "audience": "candidate",
        "name": "Candidate Pro",
        "price": CANDIDATE_PRO_PRICE,
        "period": "month",
        "tagline": "Pay for time, not access — the autonomous apply agent, uncapped.",
        "features": [
            "See who liked you (recruiter identities behind your like count)",
            "Unlimited autonomous applies (free plan: 5 queued per day)",
            "24-hour profile Boost — rank ahead in employer decks",
            "Everything in Free: swiping, matches, messaging",
        ],
    },
    {
        "key": "recruiter_pro",
        "audience": "employer",
        "name": "Recruiter Pro",
        "price": RECRUITER_PRO_PRICE,
        "period": "seat/month",
        "tagline": "One recruiter profile = one seat. Access and attention, not outcomes.",
        "features": [
            "See who liked each job (candidate identities behind per-job counts)",
            "1 active Sponsored job at a time, labelled Sponsored",
            f"Sponsored job à la carte: ${SPONSORED_ALACARTE_PRICE} / {SPONSORED_DAYS} days",
            "Everything in Free: unlimited postings, swiping, matches, messaging",
        ],
    },
]

_PLAN_BY_KEY = {p["key"]: p for p in PLAN_CATALOG}

def plan_by_key(key):
    return _PLAN_BY_KEY.get(key or "")

def pro_required_payload(plan_key):
    """The standard 402 body for a Pro gate."""
    p = _PLAN_BY_KEY[plan_key]
    return {"error": "pro_required", "plan": plan_key, "price": p["price"]}

def stripe_secret_key():
    return os.environ.get("STRIPE_SECRET_KEY") or ""

def stripe_publishable_key():
    return os.environ.get("STRIPE_PUBLISHABLE_KEY") or ""

def stripe_webhook_secret():
    return os.environ.get("STRIPE_WEBHOOK_SECRET") or ""

def payments_configured():
    return bool(stripe_secret_key())

def is_pro(plan_value):
    return (plan_value or "free") == "pro"

def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def future_str(hours=0, days=0):
    return (datetime.now(timezone.utc) + timedelta(hours=hours, days=days)).strftime("%Y-%m-%d %H:%M:%S")

def is_future(ts):
    """True when a stored UTC timestamp (SQLite format) is still ahead."""
    return bool(ts) and str(ts) > now_str()

# ---------------------------------------------------------------------------
# Stripe API client (stdlib urllib only — mirrors app/storage.py's _http:
# one choke point, JSON responses, HTTP errors raised with Stripe's own
# message, request headers — which hold the secret key — never logged).
# ---------------------------------------------------------------------------

class StripeAPIError(Exception):
    """A Stripe API call failed (HTTP status + Stripe's error message)."""

    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__(f"Stripe API error (HTTP {status}): {detail}")


def _stripe_error_detail(body):
    """Stripe errors are JSON like {"error": {"message": ...}}."""
    text = (body or b"").decode("utf-8", "replace").strip()
    try:
        parsed = json.loads(text)
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return err["message"]
    except ValueError:
        pass
    return text or "(empty error body)"


def _stripe_http(method, url, data=None, headers=None):
    """Single HTTP choke point for the Stripe API (mocked in tests)."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = _stripe_error_detail(e.read())
        log.error("stripe API %s %s failed: HTTP %s: %s",
                  method, urllib.parse.urlparse(url).path, e.code, detail)
        raise StripeAPIError(e.code, detail) from e
    return json.loads(body) if body else {}


def create_checkout_session(plan, *, success_url, cancel_url,
                            client_reference_id, metadata, customer_email):
    """Create a Stripe Checkout Session for a subscription plan.

    Inline price_data (product name + amount from the plan catalog, in
    BILLING_CURRENCY) — the Stripe account needs NO pre-created products
    or prices, so activation is paste-keys-and-go. The local identity
    travels in BOTH the session metadata and (via subscription_data) the
    subscription's metadata, plus client_reference_id, so the webhook
    can always tie a payment back to exactly one local account.
    Returns the parsed session dict (carries 'id' and 'url').
    """
    fields = [
        ("mode", "subscription"),
        ("success_url", success_url),
        ("cancel_url", cancel_url),
        ("client_reference_id", str(client_reference_id)),
        ("line_items[0][quantity]", "1"),
        ("line_items[0][price_data][currency]", BILLING_CURRENCY),
        ("line_items[0][price_data][unit_amount]", str(int(plan["price"]) * 100)),
        ("line_items[0][price_data][recurring][interval]", "month"),
        ("line_items[0][price_data][product_data][name]", plan["name"]),
    ]
    for k, v in (metadata or {}).items():
        fields.append((f"metadata[{k}]", str(v)))
        fields.append((f"subscription_data[metadata][{k}]", str(v)))
    if customer_email:
        fields.append(("customer_email", customer_email))
    data = urllib.parse.urlencode(fields).encode()
    headers = {"Authorization": f"Bearer {stripe_secret_key()}",
               "Content-Type": "application/x-www-form-urlencoded"}
    return _stripe_http("POST", STRIPE_API_BASE + "/checkout/sessions",
                        data=data, headers=headers)

# ---------------------------------------------------------------------------
# Webhook signature verification (Stripe-Signature: t=...,v1=...).
# Signed payload is f"{t}.{raw_body}"; HMAC-SHA256 with the webhook
# secret; 5-minute timestamp tolerance; constant-time compare.
# ---------------------------------------------------------------------------

def verify_webhook_signature(payload_bytes, sig_header, secret=None, now=None):
    """True only when the header carries a fresh timestamp and a v1
    signature that matches this exact raw body. Any doubt → False."""
    secret = stripe_webhook_secret() if secret is None else secret
    if not secret or not sig_header:
        return False
    ts_raw, sigs = None, []
    for part in str(sig_header).split(","):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if k == "t":
            ts_raw = v
        elif k == "v1":
            sigs.append(v)
    if ts_raw is None or not sigs:
        return False
    try:
        ts = int(ts_raw)
    except (TypeError, ValueError):
        return False
    now = int(time.time()) if now is None else now
    if abs(now - ts) > WEBHOOK_TOLERANCE_SECONDS:
        return False
    signed = ts_raw.encode("utf-8") + b"." + (payload_bytes or b"")
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


def parse_client_reference_id(ref):
    """'user:<id>' → ('users', id); 'employer:<id>' → ('employers', id);
    anything else → (None, None)."""
    try:
        kind, _, raw = str(ref or "").partition(":")
        if kind == "user":
            return "users", int(raw)
        if kind == "employer":
            return "employers", int(raw)
    except (TypeError, ValueError):
        pass
    return None, None


def identity_from_metadata(metadata):
    """Resolve the local account a Stripe metadata blob names:
    user_id → ('users', id), employer_id → ('employers', id)."""
    md = metadata or {}
    for key, table in (("user_id", "users"), ("employer_id", "employers")):
        raw = md.get(key)
        if raw not in (None, ""):
            try:
                return table, int(raw)
            except (TypeError, ValueError):
                return None, None
    return None, None
