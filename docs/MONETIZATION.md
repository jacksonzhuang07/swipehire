# SwipeHire — Monetization Model (Phase 2, built Nov 12, 2026)

Strategy source: `~/workspace/research_notes/swipehire-monetization-strategy-20261112-1848/report.md`
(§1–§6). One-line version the evidence supports: **free swiping / matching /
messaging forever on both sides; candidates pay for time (the agent),
employers pay for access and attention (seats, reveals, sponsorship);
nobody pays for outcomes.**

## Prices (single source of truth: `app/billing.py`)

| Line | Price | Notes |
|---|---|---|
| Candidate Pro | **$24/month** | Research band $19–$29 (Sonara/LoopCV/JobCopilot). Anchored on the autonomous apply agent — standalone value, works before any employer joins. |
| Recruiter Pro | **$149/seat/month** | Research band $135–$199/seat (Wellfound). SwipeHire's model is 1 recruiter profile per account, so 1 account = 1 seat by construction. |
| Sponsored job | **Included in Recruiter Pro** (1 active at a time); à la carte **$200 / 30 days** once payments live | Wellfound Job Ads benchmark ($200/ad). À-la-carte is a config constant only until checkout exists. |
| Candidate Boost | Pro-tier mechanic (24h) | À la carte ($3–$8, Tinder Boost band) is a later supplement, never the base. |

The UI never hard-codes a price: it renders `GET /api/billing/plans`.

## What is gated — and what is NEVER gated

The free core stays free (research §3/§4: counts are the teaser that creates
demand; gating access kills liquidity):

| Feature | Free | Pro |
|---|---|---|
| Signup, swiping, matches, messaging | ✅ | ✅ |
| Posting jobs (unlimited) | ✅ | ✅ |
| Like **counts** (candidate profile / per-job / recruiter profile) | ✅ visible | ✅ |
| Liker **identities** — candidate side (who right-swiped you) | 🔒 blurred + count | Candidate Pro |
| Liker **identities** — per job (who right-swiped this job) | 🔒 counts only | Recruiter Pro |
| Autonomous apply queue (`queued_extension`) | 5 per day | Candidate Pro: no product cap (extension safety caps still apply) |
| `prepared` applications | ✅ never gated | ✅ |
| Sponsored job (1 active, labelled "Sponsored") | — | Recruiter Pro |
| 24h candidate Boost (deck ordering only, never match eligibility; not labelled to employers) | — | Candidate Pro |

Gates return `402 {error:'pro_required', plan:'candidate_pro'|'recruiter_pro', price}`.
The queue allowance returns `402 {error:'queue_allowance_exhausted', ...}`;
the swipe (like) itself is still recorded — only the queue entry is refused.

## Data

- `users.plan` / `employers.plan` (`'free'|'pro'`) + `plan_started_at`.
- `users.stripe_customer_id` / `users.stripe_subscription_id` and the
  same pair on `employers` (Phase A) — written by the webhook at grant
  time so subscription lifecycle events can find the account even
  without metadata.
- `candidates.boost_until`, `jobs.sponsored_until` (nullable UTC timestamps).
- `billing_interest(email, role, plan, created_at)` — the waitlist seam.

## Billing — Stripe integration (Phase A, built Nov 13, 2026)

The integration is BUILT; what remains is keys (see the checklist).

- `POST /api/billing/checkout` (signed-in, plan must match the account's
  audience): while `STRIPE_SECRET_KEY` is unset → **503
  `{error:'payments_not_configured'}`** and the UI falls back to
  "Payments launching soon — join the waitlist" → `POST
  /api/billing/interest`. Once the key exists, checkout creates a real
  **Stripe Checkout Session** (`mode=subscription`, inline `price_data`
  from `app/billing.py` in CAD — **no pre-created Stripe products or
  prices needed**) carrying the local identity in session metadata,
  subscription metadata, and `client_reference_id`, and returns
  `{url}`; the browser redirects to Stripe-hosted checkout and returns
  to `/?checkout=success|cancelled`.
- `POST /api/billing/webhook` — **the ONLY code path that grants or
  revokes Pro.** Verifies `Stripe-Signature` (HMAC-SHA256 over
  `timestamp.raw_body`, 5-minute tolerance, constant-time compare);
  invalid → 400, zero state change.
  `checkout.session.completed` → grant Pro + store Stripe ids.
  `customer.subscription.deleted` → revoke to free. All other events →
  200 no-op.
- No fake checkout exists anywhere, and paying in the browser grants
  nothing by itself — Pro flips only when the signed webhook lands.

## Activation checklist — going live with real payments

The code is done (Phase A). Four steps remain, all of them Jackson's
(no-spend rule + identity steps only he can do):

1. **Activate the Stripe account** — sign up / finish activation with
   his identity and banking. No products or prices to create: checkout
   uses inline `price_data` from `app/billing.py` (Candidate Pro
   $24/mo, Recruiter Pro $149/seat/mo, CAD).
2. **Paste the keys into the Vercel env**: `STRIPE_SECRET_KEY`,
   `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET` (the webhook
   secret comes from step 3). Setting `STRIPE_SECRET_KEY` is what
   flips checkout from the 503 waitlist seam to real sessions.
3. **Register the webhook endpoint in Stripe**: URL
   `https://swipehire-jade.vercel.app/api/billing/webhook`, events
   `checkout.session.completed` and `customer.subscription.deleted`.
   Copy its signing secret into `STRIPE_WEBHOOK_SECRET` (step 2).
4. **One live test purchase + cancel**: buy Candidate Pro with a real
   card, confirm `plan` flips to `pro`, `plan_started_at` and the
   Stripe ids are stored, and the likers reveal opens; then cancel the
   subscription in Stripe and confirm the account returns to `free`
   and the gates close. Like counts stay visible throughout (they are
   never the paid part).

Separate paid decisions, NOT required by the integration (Jackson's
call, see the free-alternatives research): Vercel Pro once
traffic/commercial use outgrows Hobby terms; an email tier upgrade
before receipt volume matters (Brevo free is 300 emails/DAY, verified
Nov 12, 2026).

Deliberately NOT in the model (research §5): placement/success fees,
pay-per-match, pay-per-application — attribution, gaming and lumpiness
problems; revisit only for a vetted high-salary vertical where hires can
be verified.
