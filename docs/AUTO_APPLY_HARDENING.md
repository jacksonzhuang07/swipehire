# Auto-Apply Hardening — Phase 2

What was built for each item of the approved robustness plan
(2026-11-17), including which premises turned out to be already true.
Gauntlet before/after: **9/19 → 19/19, zero critical failures in both**
(full scoreboard in `docs/AUTO_APPLY_TESTING.md`).

## 1) Reusable answer bank

**Premise check:** did not exist. Unknown questions parked the whole
application, and answers lived only inside each application's payload.

**Built:**
- `answer_bank` table (per candidate): question text, normalized key,
  answer, use count.
- `app/answers.py` — question normalization: lowercase → synonym-phrase
  mapping → punctuation strip → stopword removal → sorted token key.
  When a canonical family token (`workauth`, `sponsorship`, …) is
  present it *is* the key, so "Are you authorized to work in Canada?",
  "Legally entitled to work here?" and "Work authorisation status" all
  resolve to one entry. The extension mirrors the same normalizer in JS
  (verified equivalent in node).
- API: `GET/POST/DELETE /api/candidate/answer-bank` (user-managed),
  `POST /api/ext/answer-lookup` (extension token): exact normalized-key
  match, then fuzzy token-Jaccard ≥ 0.6.
- Driver: pre-flight scan (item 6) looks up every unanswered required
  question — payload keys first, then the server bank — and fills from
  the bank (text/select/radio/checkbox) before deciding to park.
- **Truthfulness invariant preserved:** bank entries are created only
  from user-supplied answers/facts via the API; the driver never invents
  an answer, and a question with no payload fact and no banked answer
  still parks as `needs_human` (gauntlet `unknown-required`).

## 2) Hardened confirmation

**Premise check:** partially true — the old driver checked confirmation
*text* only, and reported `submitted` on that single signal.

**Built:** multi-signal confirmation after a real submit click:
- **Strong** (text match AND a structural signal — URL change, form
  hidden, or submit button gone) → `submitted`.
- **Weak/none** after a real click → new distinct state
  `submitted_unverified` (never presented to the user as confirmed; the
  note tells them to verify on the board before re-applying).
- The `submit_clicked` event is persisted to the server *before* the
  click (see item 3), so even a crash mid-click is recoverable.
- *Future work (out of scope, noted per plan):* reconciling
  `submitted_unverified` against ATS confirmation email.

## 3) Crash recovery / no double-apply

**Premise check:** did not exist — a browser crash after the submit
click left no server-side trace, so a retry could double-apply.

**Built:** server-side per-application state machine in
`applications.ext_state` (`claimed → filling → submit_clicked →
confirmed/parked`) with a 15-minute claim lease
(`ext_claim_expires_at`):
- The queue only hands out applications in a claimable state; a fresh
  claim is refused while a lease is live.
- `submit_clicked` is recorded before the click; the queue NEVER
  re-serves an application in that state — after the lease expires it is
  parked as `needs_human` with a duplicate-protection reason, for a
  human to verify on the board instead of re-submitting.
- Regression-tested in `tests/test_hardening.py`.

## 4) Per-ATS drivers + health shutdown

**Premise check:** only the generic driver existed; no success tracking.

**Built:**
- Dedicated drivers for **Greenhouse, Lever, Ashby** (selected by host,
  stable selector chains first — e.g. Lever `#application-form`, Ashby
  `_systemfield_*`), generic driver as fallback everywhere.
- Board health tracked **server-side** (the server sees every outcome
  report across all of a user's browsers; an extension-side counter
  would fragment per install): `board_health` rolling window of the
  last 20 decisive outcomes per board. Below 30% success over ≥10
  decisive outcomes → the board's driver auto-disables: queued jobs for
  that board park as `needs_human` with a driver-disabled reason
  instead of burning applications. `GET /api/ext/board-health`,
  `POST /api/candidate/board-health/reenable` to re-enable manually.

## 5) Pacing — deltas only

**Premise check:** TRUE as stated in the plan — the ≥90s gap, burst cap
5/30min, daily caps (LinkedIn 30 / Indeed 20 / ATS 40) and the
account-warning auto-pause all already existed and were verified in
code, not rebuilt.

**Built (only the two missing deltas):**
- **Randomized jitter:** +0–45s uniform jitter on top of the 90s floor
  between submissions.
- **48-hour hard halt:** an account-warning signal now sets a timed
  48h halt (`autoHaltUntil`); the queue processor refuses to run until
  it expires or the user explicitly overrides it in the popup
  (`sh-setauto {overrideHalt: true}`).
- Invariants guarded by `tests/test_extension_js.py` (the 90s floor,
  burst cap, daily caps, jitter presence, halt wiring) so a future edit
  can't silently weaken them.

## 6) Pre-flight checks

**Premise check:** did not exist — the driver discovered unanswerable
questions only after filling (or mis-filling) earlier fields.

**Built:**
- **Liveness:** the queue processor HEAD/GET-checks the posting URL
  before driving; HTTP 404/410 parks the application without opening
  the flow.
- **Required-field enumeration:** before touching any field on each
  step, the driver scans required fields against the payload + answer
  bank (item 1); anything unanswerable parks cheaply as `needs_human`
  *before* the form is driven.
- **Diagnostics:** park/fail reports carry a page visible-text excerpt
  (the cheaper diagnostic allowed by the plan; screenshots were not
  gold-plated in).

## Dry-run mode (Phase 1, for completeness)

Popup toggle → `dryRun` in extension storage → the driver fills,
attaches, walks steps and verifies field states but never clicks the
final submit; it reports `verified_dry_run` with a per-field report,
stored server-side in `applications.dry_run_report` without the status
ever becoming `submitted`. Dry-runs don't consume pacing budget.
Verified end-to-end by `tests/e2e_gauntlet.py --mode dryrun`.
