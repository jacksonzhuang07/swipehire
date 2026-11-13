"""Phase A Stripe integration tests (docs/MONETIZATION.md).

The seam, made real — with Stripe's HTTP boundary mocked at
billing._stripe_http exactly the way tests/test_storage.py mocks
storage._http (no network, no real keys):

- checkout (keys set) creates a real Checkout Session: form fields
  carry mode=subscription, inline price_data (amount/currency/interval
  from app/billing.py, BILLING_CURRENCY everywhere), and the local
  identity in metadata + client_reference_id; the session URL comes back.
- checkout (keys unset) is byte-for-byte the Phase 2 behaviour — the
  existing tests in test_billing.py pin that; nothing here re-pins it.
- the webhook is the ONLY Pro-granting path: correctly-signed
  checkout.session.completed grants (candidate AND employer cases) and
  stores the Stripe ids; customer.subscription.deleted revokes; a bad
  signature or a stale timestamp is a 400 with zero state change;
  unknown events are 200 no-ops.
- end to end: the /api/me/likers gate flips 402 → 200 off a webhook
  grant alone (no direct DB plan writes in that test).
- the stripe_customer_id / stripe_subscription_id migrations are
  idempotent on a pre-Phase-A database (boot twice).
"""
import hashlib, hmac, io, json, os, sys, time
import urllib.parse
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app, billing
from app.db import get_conn, init_db

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64
MP = "multipart/form-data"
SK = "sk_test_phasea"
WHSEC = "whsec_phasea_secret"

@pytest.fixture()
def app_(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db"), testing=True)

@pytest.fixture()
def client(app_):
    return app_.test_client()

@pytest.fixture()
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", SK)
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_phasea")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    return monkeypatch

@pytest.fixture()
def stripe_http(monkeypatch):
    """Mock billing._stripe_http; records the call, returns a session."""
    calls = []
    def fake(method, url, data=None, headers=None):
        calls.append({"method": method, "url": url, "data": data, "headers": headers or {}})
        return {"id": "cs_test_123", "url": "https://checkout.stripe.com/c/pay/cs_test_123"}
    monkeypatch.setattr(billing, "_stripe_http", fake)
    return calls

def signup(client, email, role="candidate", name="User", password="password123", company="Acme"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password, "role": role, "name": name, "company": company})
    assert r.status_code == 201, r.data
    return r.json["user"]

def user_id(app_, email):
    conn = get_conn(app_.config["DB_PATH"])
    row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return row["id"]

def user_row(app_, email):
    conn = get_conn(app_.config["DB_PATH"])
    row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return dict(row)

def employer_row(app_, employer_id):
    conn = get_conn(app_.config["DB_PATH"])
    row = conn.execute("SELECT * FROM employers WHERE id=?", (employer_id,)).fetchone()
    conn.close()
    return dict(row)

def make_employer(app_, email="emp@stripe.com", company="Stripe Co", job_title="Stripe Role"):
    e = app_.test_client()
    u = signup(e, email, role="employer", company=company)
    r = e.post("/api/employer/logo", data={"file": (io.BytesIO(PNG_BYTES), "logo.png")}, content_type=MP)
    assert r.status_code == 200, r.data
    job = e.post("/api/employer/jobs", json={"title": job_title, "description": "Python role", "tags": ["Python"]}).json
    return e, u, job

# ---------------- checkout session creation ----------------

def test_checkout_candidate_session_fields(client, app_, stripe_env, stripe_http):
    u = signup(client, "buyer@stripe.com")
    uid = user_id(app_, "buyer@stripe.com")
    r = client.post("/api/billing/checkout", json={"plan": "candidate_pro"})
    assert r.status_code == 200, r.data
    assert r.json["url"] == "https://checkout.stripe.com/c/pay/cs_test_123"
    assert len(stripe_http) == 1
    call = stripe_http[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.stripe.com/v1/checkout/sessions"
    assert call["headers"]["Authorization"] == f"Bearer {SK}"
    qs = urllib.parse.parse_qs(call["data"].decode())
    assert qs["mode"] == ["subscription"]
    assert qs["line_items[0][quantity]"] == ["1"]
    assert qs["line_items[0][price_data][currency]"] == ["cad"]
    assert qs["line_items[0][price_data][unit_amount]"] == ["2400"]
    assert qs["line_items[0][price_data][recurring][interval]"] == ["month"]
    assert qs["line_items[0][price_data][product_data][name]"] == ["Candidate Pro"]
    assert qs["metadata[user_id]"] == [str(uid)]
    assert qs["metadata[plan]"] == ["candidate_pro"]
    assert qs["subscription_data[metadata][user_id]"] == [str(uid)]
    assert qs["client_reference_id"] == [f"user:{uid}"]
    assert qs["customer_email"] == ["buyer@stripe.com"]
    assert "checkout=success" in qs["success_url"][0]
    assert "checkout=cancelled" in qs["cancel_url"][0]
    # checkout alone grants NOTHING — only the webhook does
    assert user_row(app_, "buyer@stripe.com")["plan"] == "free"

def test_checkout_employer_session_fields(app_, stripe_env, stripe_http):
    e, eu, job = make_employer(app_)
    emp_id = eu["employer"]["id"]
    r = e.post("/api/billing/checkout", json={"plan": "recruiter_pro"})
    assert r.status_code == 200, r.data
    assert r.json["url"] == "https://checkout.stripe.com/c/pay/cs_test_123"
    qs = urllib.parse.parse_qs(stripe_http[0]["data"].decode())
    assert qs["line_items[0][price_data][unit_amount]"] == ["14900"]
    assert qs["line_items[0][price_data][currency]"] == ["cad"]
    assert qs["line_items[0][price_data][product_data][name]"] == ["Recruiter Pro"]
    assert qs["metadata[employer_id]"] == [str(emp_id)]
    assert qs["metadata[plan]"] == ["recruiter_pro"]
    assert qs["client_reference_id"] == [f"employer:{emp_id}"]
    assert employer_row(app_, emp_id)["plan"] == "free"

def test_checkout_audience_mismatch_never_calls_stripe(client, stripe_env, stripe_http):
    signup(client, "mm@stripe.com")  # a candidate
    r = client.post("/api/billing/checkout", json={"plan": "recruiter_pro"})
    assert r.status_code == 400 and r.json["error"] == "plan_audience_mismatch"
    assert stripe_http == []

def test_checkout_stripe_failure_is_502_not_a_grant(client, app_, stripe_env, monkeypatch):
    signup(client, "fail@stripe.com")
    def boom(method, url, data=None, headers=None):
        raise billing.StripeAPIError(402, "Your card was declined.")
    monkeypatch.setattr(billing, "_stripe_http", boom)
    r = client.post("/api/billing/checkout", json={"plan": "candidate_pro"})
    assert r.status_code == 502 and r.json["error"] == "stripe_error"
    assert user_row(app_, "fail@stripe.com")["plan"] == "free"

# ---------------- webhook ----------------

def _sign(body, ts=None, secret=WHSEC):
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), str(ts).encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"

def _post_event(client, event, header=None):
    body = json.dumps(event).encode()
    return client.post("/api/billing/webhook", data=body, content_type="application/json",
                       headers={"Stripe-Signature": _sign(body) if header is None else header})

def _completed(meta, customer="cus_123", subscription="sub_123", ref=None):
    return {"type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_123", "metadata": meta,
                                "customer": customer, "subscription": subscription,
                                "client_reference_id": ref}}}

def test_webhook_grants_candidate_pro_and_flips_likers_gate(app_, stripe_env):
    e, eu, job = make_employer(app_)
    c = app_.test_client(); cand = signup(c, "gate@stripe.com")
    e.post("/api/employer/swipe", json={"job_id": job["id"], "candidate_id": cand["candidate"]["id"], "direction": "right"})
    assert c.get("/api/me/likers").status_code == 402  # gate closed before payment
    uid = user_id(app_, "gate@stripe.com")
    r = _post_event(c, _completed({"user_id": str(uid), "plan": "candidate_pro"}, ref=f"user:{uid}"))
    assert r.status_code == 200 and r.json["received"] is True
    row = user_row(app_, "gate@stripe.com")
    assert row["plan"] == "pro" and row["plan_started_at"]
    assert row["stripe_customer_id"] == "cus_123" and row["stripe_subscription_id"] == "sub_123"
    # the gate flips off the webhook grant alone — no DB plan writes here
    r = c.get("/api/me/likers")
    assert r.status_code == 200 and r.json["likers"][0]["company"] == "Stripe Co"

def test_webhook_grants_employer_pro(app_, stripe_env):
    e, eu, job = make_employer(app_, email="empwh@stripe.com")
    emp_id = eu["employer"]["id"]
    r = _post_event(e, _completed({"employer_id": str(emp_id), "plan": "recruiter_pro"},
                                  customer="cus_emp", subscription="sub_emp", ref=f"employer:{emp_id}"))
    assert r.status_code == 200
    row = employer_row(app_, emp_id)
    assert row["plan"] == "pro" and row["plan_started_at"]
    assert row["stripe_customer_id"] == "cus_emp" and row["stripe_subscription_id"] == "sub_emp"
    # and the employer-side reveal gate opens too
    assert e.get(f"/api/employer/jobs/{job['id']}/likers").status_code == 200

def test_webhook_grant_via_client_reference_id_only(app_, stripe_env):
    c = app_.test_client(); signup(c, "ref@stripe.com")
    uid = user_id(app_, "ref@stripe.com")
    r = _post_event(c, _completed({}, ref=f"user:{uid}"))
    assert r.status_code == 200
    assert user_row(app_, "ref@stripe.com")["plan"] == "pro"

def test_webhook_subscription_deleted_revokes(app_, stripe_env):
    c = app_.test_client(); signup(c, "rev@stripe.com")
    uid = user_id(app_, "rev@stripe.com")
    assert _post_event(c, _completed({"user_id": str(uid), "plan": "candidate_pro"})).status_code == 200
    assert user_row(app_, "rev@stripe.com")["plan"] == "pro"
    deleted = {"type": "customer.subscription.deleted",
               "data": {"object": {"id": "sub_123", "customer": "cus_123",
                                   "metadata": {"user_id": str(uid), "plan": "candidate_pro"}}}}
    assert _post_event(c, deleted).status_code == 200
    assert user_row(app_, "rev@stripe.com")["plan"] == "free"

def test_webhook_deleted_without_metadata_resolves_by_stored_ids(app_, stripe_env):
    e, eu, job = make_employer(app_, email="empdel@stripe.com")
    emp_id = eu["employer"]["id"]
    assert _post_event(e, _completed({"employer_id": str(emp_id), "plan": "recruiter_pro"},
                                     customer="cus_emp", subscription="sub_emp")).status_code == 200
    assert employer_row(app_, emp_id)["plan"] == "pro"
    deleted = {"type": "customer.subscription.deleted",
               "data": {"object": {"id": "sub_emp", "customer": "cus_emp", "metadata": {}}}}
    assert _post_event(e, deleted).status_code == 200
    assert employer_row(app_, emp_id)["plan"] == "free"

def test_webhook_bad_signature_400_and_no_state_change(client, app_, stripe_env):
    signup(client, "bad@stripe.com")
    uid = user_id(app_, "bad@stripe.com")
    body = json.dumps(_completed({"user_id": str(uid), "plan": "candidate_pro"})).encode()
    r = client.post("/api/billing/webhook", data=body, content_type="application/json",
                    headers={"Stripe-Signature": _sign(body, secret="whsec_wrong")})
    assert r.status_code == 400
    assert user_row(app_, "bad@stripe.com")["plan"] == "free"
    r = client.post("/api/billing/webhook", data=body, content_type="application/json")  # no header at all
    assert r.status_code == 400
    assert user_row(app_, "bad@stripe.com")["plan"] == "free"

def test_webhook_stale_timestamp_400(client, app_, stripe_env):
    signup(client, "stale@stripe.com")
    uid = user_id(app_, "stale@stripe.com")
    body = json.dumps(_completed({"user_id": str(uid), "plan": "candidate_pro"})).encode()
    stale = _sign(body, ts=int(time.time()) - 3600)  # correctly signed, one hour old
    r = client.post("/api/billing/webhook", data=body, content_type="application/json",
                    headers={"Stripe-Signature": stale})
    assert r.status_code == 400
    assert user_row(app_, "stale@stripe.com")["plan"] == "free"

def test_webhook_unknown_event_is_200_noop(client, app_, stripe_env):
    signup(client, "noop@stripe.com")
    r = _post_event(client, {"type": "invoice.paid", "data": {"object": {"id": "in_1"}}})
    assert r.status_code == 200 and r.json["received"] is True
    assert user_row(app_, "noop@stripe.com")["plan"] == "free"

def test_webhook_rejects_everything_when_secret_unconfigured(client, app_, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    signup(client, "nosec@stripe.com")
    uid = user_id(app_, "nosec@stripe.com")
    body = json.dumps(_completed({"user_id": str(uid), "plan": "candidate_pro"})).encode()
    r = client.post("/api/billing/webhook", data=body, content_type="application/json",
                    headers={"Stripe-Signature": _sign(body)})
    assert r.status_code == 400
    assert user_row(app_, "nosec@stripe.com")["plan"] == "free"

# ---------------- migrations ----------------

def test_stripe_columns_migration_idempotent_on_pre_phasea_db(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL)")
    raw.execute("CREATE TABLE employers(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, company TEXT NOT NULL)")
    raw.commit(); raw.close()
    init_db(path)
    init_db(path)  # boot twice — must not raise, must not duplicate
    conn = get_conn(path)
    for table in ("users", "employers"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        assert cols.count("stripe_customer_id") == 1, (table, cols)
        assert cols.count("stripe_subscription_id") == 1, (table, cols)
    conn.close()
