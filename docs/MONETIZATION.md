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
- `candidates.boost_until`, `jobs.sponsored_until` (nullable UTC timestamps).
- `billing_interest(email, role, plan, created_at)` — the waitlist seam.

## Billing seam (payments NOT live)

- `POST /api/billing/checkout` → **503 `{error:'payments_not_configured'}`**
  while `STRIPE_SECRET_KEY` is unset. The UI falls back to "Payments
  launching soon — join the waitlist" → `POST /api/billing/interest`.
- No fake checkout exists, and **nothing grants Pro without a payment**
  (no demo toggle; tests set plan columns directly in the DB).

## Activation checklist — going live with real payments

Blocked on Jackson (no-spend rule + identity steps only he can do):

1. **Stripe account** — Jackson signs up with his identity/banking
   (the one-sitting signup noted in memory). Products/prices created to
   match `app/billing.py`: Candidate Pro $24/mo, Recruiter Pro $149/seat/mo,
   Sponsored $200 one-time (30 days).
2. **Keys into Vercel env**: `STRIPE_SECRET_KEY`, publishable key, and a
   webhook signing secret; then build the checkout-session + webhook
   handlers behind the existing seam (webhook sets `plan`/`plan_started_at`
   on payment, clears on cancellation — the ONLY code path that grants Pro).
3. **Vercel Pro plan** — required once traffic/commercial use outgrows
   Hobby terms (paid step, Jackson's call).
4. **Email tier upgrade** — Brevo free is 300 emails/month; upgrade before
   waitlist/receipt mail volume matters (paid step, Jackson's call).
5. Verify in production: checkout → webhook → plan flips → gates open;
   cancellation → plan returns to free → gates close. Like counts stay
   visible throughout (they are never the paid part).

Deliberately NOT in the model (research §5): placement/success fees,
pay-per-match, pay-per-application — attribution, gaming and lumpiness
problems; revisit only for a vetted high-salary vertical where hires can
be verified.
