# Auto-Apply Testing — Gauntlet, Dry-Runs, Real Boards

Measured results for the autonomous auto-apply engine (Chrome extension),
run 2026-11-17 against the real extension in Playwright Chromium, paired
to a local Flask server. Fixtures record ground truth in `localStorage`
when their submit handler actually fires; scoring compares the server-side
application status against that ground truth, not just against the
extension's own claims.

**Headline numbers**

| Suite | Before fixes | After fixes |
|---|---|---|
| Hostile gauntlet (19 fixtures) | 9/19 pass, 0 critical | **19/19 pass, 0 critical** |
| Original e2e (3 fixtures) | 3/3 pass | 3/3 pass (regression-checked) |
| Dry-run mode check | n/a (new) | Pass — 4 fields filled, 0 missed, never submitted |
| Pytest suite | 246 pass | 264 pass |

A **critical** failure = a fixture that was expected to park for a human
actually submitted. There were **zero criticals in every run**, before and
after fixes: the safety stops (unknown required question, CAPTCHA) held
even when the completion machinery around them was broken.

## Gauntlet scoreboard (tests/e2e_gauntlet.py, tests/fixtures/gauntlet/)

Expected `submitted` means the fixture's submit handler must have fired
(ground truth). Expected `needs_human` means the application must park
and the form must NOT have submitted.

| Fixture | Models | Expected | Before | After |
|---|---|---|---|---|
| lever-style | Lever (`#application-form`, `cards[][]`, phone country select) | submitted | FAIL (parked) | submitted ✓ |
| ashby-style | Ashby (`_systemfield_*` inputs, HTTPS/HSTS host) | submitted | FAIL (parked) | submitted ✓ |
| workable-style | Workable (labels via `for`, phone country code) | submitted | submitted | submitted ✓ |
| smartrecruiters-style | SmartRecruiters (bare "Apply" final button) | submitted | FAIL (parked) | submitted ✓ |
| multistep-3page | Generic 3-step wizard | submitted | submitted | submitted ✓ |
| iframe-form | Application form embedded in an iframe | submitted | FAIL (parked) | submitted ✓ |
| custom-dropdown | Div-based dropdown widgets (not `<select>`) | submitted | FAIL (parked) | submitted ✓ |
| radio-heavy | Work-auth + sponsorship radio groups | submitted | FAIL (parked) | submitted ✓ |
| unknown-required | Unknown REQUIRED question | needs_human | needs_human ✓ | needs_human ✓ |
| unknown-optional | Unknown optional question | submitted | submitted | submitted ✓ |
| consent-checkboxes | Required consent/certify checkboxes | submitted | submitted | submitted ✓ |
| delayed-render | Form injected ~2s after page load | submitted | FAIL (parked) | submitted ✓ |
| resume-radio | "Use existing resume" radio instead of upload | submitted | submitted | submitted ✓ |
| easy-apply-extra | LinkedIn Easy Apply, 4 steps + review page | submitted | submitted | submitted ✓ |
| indeed-style | Indeed "Apply now" entry → 3-step flow | submitted | FAIL (parked) | submitted ✓ |
| redirect-confirm | Submit redirects to a separate thank-you page | submitted | submitted | submitted ✓ |
| labels-div | Div-only labels, generic field names (f1–f5) | submitted | FAIL (parked) | submitted ✓ |
| select-native | Native selects incl. Yes/No selects | submitted | submitted | submitted ✓ |
| banked-question | Required question answerable only via the answer bank | submitted | FAIL (parked) | submitted ✓ |

Notes on scoring honesty:

- The BEFORE column is the old extension (commit `4efc967`) against the
  same fixtures. Two harness bugs were found and fixed while measuring
  (an unreadable ground-truth read was initially miscounted as a
  submission, and `jobs.ashbyhq.com` is HSTS-preloaded so the Ashby
  fixture needs the local TLS fixture server). The BEFORE numbers above
  are from the corrected harness.
- `redirect-confirm` scores on tracker status + the driver's recorded
  confirmation evidence ("Landed on …/redirect-confirm.html" + thank-you
  text). Its `localStorage` ground-truth write races the redirect and
  does not reliably persist in the test profile, so that one fixture's
  ground truth is marked unreadable rather than counted either way.

## Dry-run mode check

`tests/e2e_gauntlet.py --mode dryrun` drives the Lever-style fixture with
dry-run ON: the driver filled 4/4 fields, walked the form, reported
`verified_dry_run` with a per-field report stored server-side, and the
application stayed `queued_extension` — the fixture's submit handler
never fired. Dry-run runs do not consume pacing budget.

## Real-board dry-runs (live postings, dry-run mode ONLY)

Runner: `tests/e2e_realboards.py`. Six live postings, driven repeatedly
on 2026-11-17 while the driver was being fixed; the table is the final
run's outcome, with run-to-run notes where they matter. Zero real
submissions were made or attempted (`submitted_anything: []` in every
run). A CAPTCHA/login park on a real board is a legitimate, expected
outcome — it is recorded, never worked around.

| Board (live posting) | Outcome | Fields filled (verified) | Remaining blockers |
|---|---|---|---|
| Lever — Warner Music Group, Software Engineer | Parked: **CAPTCHA** on page | 0 (safety check precedes filling, by design) | CAPTCHA — a human must complete it, then re-queue |
| Lever — Zoox, Sr/Staff SWE Core Connectivity | Parked: **CAPTCHA** on page | 0 (as above) | CAPTCHA. (First run hit the 150s watchdog because the page itself never finished loading through the test proxy; later runs loaded and parked on the CAPTCHA.) |
| Ashby — Sift Stack, SWE Frontend | Parked: no drivable form | 0 | **Posting removed** — Ashby serves "Job not found" (it still showed a loading shell on the first pass) |
| Ashby — OpenAI, Founding Full Stack SWE | Parked: unanswerable required question | 0 recorded before park (pre-flight parks before the main fill) | One required **date-picker** question ("Pick date…", a start-date field) with no stored answer. The standard "Legal Name" field resolves after the matcher fix in this run's driver |
| Greenhouse — Cloudflare, Sr SWE Kubernetes | Parked: unanswerable required question | **1** — sponsorship combobox filled "No" from the answer bank and verified | "How did you hear about this job?" — no stored/banked answer exists; parks correctly (banking an answer once unblocks it forever) |
| Greenhouse — Greenhouse, Sr SWE | Parked: unanswerable required questions | **1** — work-auth combobox ("legal right to work in Canada") filled "Yes" and verified | A location-confirmation statement (BC/Ontario) and a pay-range acknowledgment question — both require the candidate's own answers |

What the real boards taught that fixtures didn't:

- **Greenhouse's current job board is all React-select comboboxes** —
  even Yes/No questions are combobox inputs with hidden required
  backing inputs. Filling them needs the real widget choreography
  (mousedown on the control → menu → mousedown-led option click);
  typing into the input does nothing durable. This now works, but it is
  timing-sensitive on slow pages: across repeated runs the same pick
  succeeded in roughly half the attempts before a two-round retry was
  added, after which both Greenhouse questions filled.
- **Pre-flight parks happen before the main fill by design**, so on
  boards with one unanswerable question the measurable fill count is
  the pre-flight/bank fills only. Full-form fill rates on real boards
  remain unmeasured for forms that park early — stated plainly rather
  than estimated.
- **Live postings churn**: one of six postings died between selection
  and measurement, and a second (Zoox/Lever) intermittently fails to
  finish loading through the test environment's proxy.
- The test environment itself needed work: the VM's egress proxy
  requires preemptive auth that Chromium won't send, so the runner
  chains a tiny localhost auth proxy (`ChainProxy` in the runner) —
  harness plumbing, not product code.

## Bugs found by the gauntlet and fixed

1. **Per-ATS DOM conventions** (Lever/Ashby): generic label matching
   missed board-specific field naming (`cards[][]`, `_systemfield_*`).
   Fixed with dedicated per-host drivers + selector fallback chains.
2. **Iframe traversal**: forms embedded in same-origin iframes were
   invisible to the root scan. Fixed by scanning iframe documents as
   additional roots (cross-origin frames are skipped safely).
3. **Custom dropdowns**: div-based dropdown widgets were not fillable.
   Fixed with a generic custom-dropdown filler (open widget → match
   option text → click).
4. **Radio groups (two bugs)**: (a) the pre-flight scan read `el.value`
   before the radio branch — a radio's `value` is its *option* value, so
   every group looked pre-answered and was never bank-filled; (b) only
   one radio in a group carries the HTML `required` attribute, so groups
   were truncated to a single option (the "No" option was unreachable).
   Fixed by grouping all members, group-required if any member is.
5. **Indeed-style entry button**: "Apply now" *opens* the flow but
   matched the submit patterns, so the driver "submitted" the entry
   button and parked. Fixed with an entry-button pre-step and a tiered
   submit search: strong labels (Submit / Send application) first;
   weak labels (Apply now / Apply) only when no Continue/Next exists;
   entry buttons no longer mask the confirmation structural signal.
6. **Delayed render**: forms injected after load were missed; step 0 now
   waits for the form to appear.
7. **Div-only labels**: `labelOf` gained div-label fallbacks so fields
   named `f1`–`f5` resolve through their visible labels.
8. **SmartRecruiters bare "Apply"**: added the exact-match `Apply`
   submit pattern (kept last-tier so it can't fire mid-flow).
9. **React-select comboboxes** (found on real Greenhouse/Ashby):
   combobox inputs were claimed by the text-fill path (value never
   sticks — React clears unselected text) or skipped entirely. Fixed
   with a dedicated picker: mousedown on the control container opens
   the menu (a synthetic `click()` doesn't), options are matched and
   clicked with a mousedown-led sequence, typing to filter is done
   without the blur event that closes the menu, and the whole pick is
   retried once for slow-hydrating pages. The answer-bank fill path
   routes comboboxes through the same picker.
10. **Widget backing inputs**: Greenhouse marks react-selects' hidden
    holder inputs `required` with no label; pre-flight parked on them
    ("required field") even when the visible control was answerable.
    Pre-flight now defers label-less/name-less/id-less required inputs
    to the post-fill check, which remains the guard.
11. **Matcher gaps from real forms**: "Legal Name" → full name;
    "Most recent job title" → current title; current/most-recent
    company → company; country (not "country code") → country.

## What still fails / remains unproven

- **Live LinkedIn/Indeed with a real account**: all LinkedIn/Indeed
  coverage is fixture-based. The real boards gate applications behind
  login, which the local harness must not automate. Unproven until
  Jackson's own piloted shakedown in his browser.
- **Cross-origin iframe forms**: skipped by design (cannot be inspected
  safely); such postings park rather than submit.
- **Real-board completion rates**: see the dry-run table above — parks
  for CAPTCHA/login on real boards are expected and frequent; the
  fill-rate numbers there are the honest current state. React-select
  combobox picking on live pages is timing-sensitive (mitigated with a
  two-round retry; both Greenhouse questions filled in the final run,
  and failed in some earlier runs) — expect occasional combobox parks
  on slow pages in production too; they park safely rather than
  mis-submitting.
- **Confirmation email reconciliation** (matching ATS confirmation mail
  to `submitted_unverified` applications) is future work, out of scope
  for this run.
