"""Monetization config + plan helpers (Phase 2).

Source: docs/MONETIZATION.md / research report
~/workspace/research_notes/swipehire-monetization-strategy-20261112-1848.

ALL prices live here — the UI renders them from GET /api/billing/plans and
never hard-codes a number. Payments are NOT live yet: there is no Stripe
account (needs Jackson's identity/banking step), so checkout honestly
returns 503 until STRIPE_SECRET_KEY is configured and the checkout
integration is built. Nothing in this module — or anywhere — grants Pro
without a payment; tests set plan columns directly in the DB.
"""
import os
from datetime import datetime, timedelta, timezone

# ---- prices (USD, per month unless noted) ----
CANDIDATE_PRO_PRICE = 24          # research band $19–$29 (auto-apply agent tools)
RECRUITER_PRO_PRICE = 149         # research band $135–$199 per seat (Wellfound)
SPONSORED_ALACARTE_PRICE = 200   # per 30 days (Wellfound Job Ads benchmark)
SPONSORED_DAYS = 30               # a sponsorship (incl. Pro's) runs 30 days
FREE_QUEUE_ALLOWANCE = 5          # free candidates: queued_extension apps/day
BOOST_HOURS = 24                  # Candidate Pro boost duration

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

def payments_configured():
    return bool(os.environ.get("STRIPE_SECRET_KEY"))

def is_pro(plan_value):
    return (plan_value or "free") == "pro"

def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def future_str(hours=0, days=0):
    return (datetime.now(timezone.utc) + timedelta(hours=hours, days=days)).strftime("%Y-%m-%d %H:%M:%S")

def is_future(ts):
    """True when a stored UTC timestamp (SQLite format) is still ahead."""
    return bool(ts) and str(ts) > now_str()
