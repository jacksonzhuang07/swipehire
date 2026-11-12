# SwipeHire — QA User Stories, Use Cases & Coverage

QA pass: Nov 12, 2026 · App at commit `b38808a` + QA fix `9fa4a4a` · Suite: **105 tests before → 190 after** (85 new in `tests/test_adversarial.py`).

Every story below is grounded in the actual routes in `app/__init__.py` and the UI in `app/static/app.js`. Coverage codes: ✅ covered by named test · 🟡 partial · ❌ GAP (no test; reason given).

Test files referenced:
- `tests/test_swipehire.py` — core flows, parsing/scoring units, v2 vault/feed/tailoring, v4 email-code + cron
- `tests/test_launch.py` — classifier, honest statuses, Connections, extension pairing/queue/report, delete account, launch pages, Turso wrapper
- `tests/test_autonomous.py` — extension report semantics (needs_human, queue payload)
- `tests/test_storage.py` — Blob/local storage backends, upload proxying
- `tests/test_layout.py` — rendered-layout checks (Playwright, 3 widths)
- `tests/test_adversarial.py` — **new this pass**: IDOR, role confusion, bad input, auth abuse, upload abuse, injection/XSS
- `tests/e2e_extension.py` — standalone (not pytest-collected): real Chromium + real extension against fixture ATS/LinkedIn pages

---

## 1. Candidate signs up with a password

- **Actor:** visitor · **Goal:** create a candidate account and land signed-in on the For You feed.
- **Main flow:** POST `/api/auth/signup {email, password, role:"candidate", name}` → 201, session cookie set, candidate row created and linked (`users.candidate_id`), response is the `/api/auth/me` payload.
- **Edge/failure:** invalid email → 400; password < 4 chars → 400; duplicate email → 409; unknown role → 400; non-string email/password → 400 (BUG-3 fix); non-object JSON body → 400 (BUG-2 fix).
- **Coverage:** ✅ `test_signup_login_logout`, `test_data_isolation_between_users` (test_swipehire); ✅ `test_signup_wrong_types_and_validation`, `test_non_dict_json_body_public_endpoints` (test_adversarial).

## 2. Passwordless email-code sign-in

- **Actor:** visitor or returning user · **Goal:** sign in with a 6-digit emailed code, no password.
- **Main flow:** POST `/api/auth/code/request {email}` → code generated, stored **hashed only** with 10-min expiry, emailed (Brevo SMTP in prod; demo mode surfaces the code on-screen, clearly labelled). POST `/api/auth/code/verify {email, code}` → session set; unknown email creates an account on the spot (role from request, default candidate, unusable random password).
- **Edge/failure:** wrong code → generic 401 (no existence leak — request response is identical for known/unknown emails); code is single-use; max 5 attempts then even the correct code is refused; expired code → 401; **rate limit: 3 requests per email per 15 min → 429**, other emails unaffected; a code issued for email A cannot verify email B; non-string fields → 400/401, never 500.
- **Coverage:** ✅ `test_code_happy_path_existing_user`, `test_code_new_email_creates_account`, `test_code_request_generic_for_unknown_email`, `test_code_wrong_rejected_then_correct_works`, `test_code_single_use`, `test_code_max_attempts`, `test_code_expired_rejected` (expiry forced via direct DB update), `test_code_stored_hashed_only`, `test_code_rate_limit`, `test_password_login_still_works_after_code_signup` (test_swipehire); ✅ `test_code_for_one_email_cannot_verify_another`, `test_code_verify_wrong_types` (test_adversarial).
- ❌ **GAP:** real email deliverability (Brevo in production) is not testable locally — tests capture mail via `mailer.LAST_SENT`. Verified manually at launch instead.

## 3. Login / logout

- **Actor:** registered user · **Goal:** session in, session out.
- **Main flow:** POST `/api/auth/login` → 200 + me-payload; POST `/api/auth/logout` clears the session; GET `/api/auth/me` → 401 when signed out.
- **Edge/failure:** wrong password → 401; SQL-injection strings in email/password → 401, no bypass, DB intact; non-object body → 400.
- **Coverage:** ✅ `test_signup_login_logout`, `test_unauthenticated_blocked`; ✅ `test_sqli_no_auth_bypass`, `test_login_wrong_types` (test_adversarial).

## 4. Demo accounts

- **Actor:** visitor · **Goal:** try the app instantly.
- **Main flow:** GET `/api/demo-accounts` (public) lists the labelled seeded accounts; `demo@swipehire.app` / `employer@swipehire.app`, password `demo`; Jackson's resume-seeded candidate account appears when his resume file exists in the environment.
- **Coverage:** ✅ `test_demo_and_jackson_seed_login`, `test_jackson_seed_photo` (test_swipehire).

## 5. Resume upload + supporting documents → Master Profile

- **Actor:** candidate · **Goal:** one compiled profile from many documents, every fact traceable to its source document.
- **Main flow:** POST `/api/resume` (JSON text or file) parses and fills the candidate, and files the resume in the vault. POST `/api/documents` (JSON text, form text, or file upload; `doc_type` ∈ resume/cover_letter/reference_letter/project_writeup/transcript/certification/other) extracts facts, stores the document (mirrored to Blob when enabled), and rebuilds the Master Profile with per-fact provenance (`sources`). GET `/api/master-profile`, GET `/api/compiled-resume` (+ `/download`, plain or `?format=html`, HTML-escaped server-side). DELETE `/api/documents/<id>` removes the document, its unique facts disappear, profile recompiles.
- **Edge/failure:** empty document → 400; bad `doc_type` → 400; unsupported file extension → 400; a text file renamed `.pdf` parses via the graceful fallback (no crash) → 201; non-string `text` → 400 (BUG-3 fix); deleting another user's document → 404.
- **Coverage:** ✅ `test_resume_upload_flow`, `test_vault_merge_provenance_and_compiled`, `test_merge_facts_unit`, `test_document_parse_facts`, `test_data_isolation_between_users` (test_swipehire); ✅ `test_documents_wrong_types`, `test_document_upload_abuse` (test_adversarial); ✅ blob mirror/delete in `test_document_mirror_and_delete_calls_blob_delete`, `test_document_delete_tolerates_blob_failure` (test_storage).
- 🟡 **Partial:** `/api/resume` via multipart file upload is exercised only through `/api/documents`; the resume endpoint's own file branch shares `_request_text` with the JSON branch that is covered.

## 6. Profile photo (and employer logo)

- **Actor:** candidate/employer · **Goal:** avatar on cards and matches.
- **Main flow:** POST `/api/profile/photo` (or `/api/employer/logo`) with a real PNG/JPEG ≤ 3 MB → stored via the storage layer (Blob in prod, local disk otherwise), URL `/uploads/<uuid-name>`; replaced images are cleaned up.
- **Edge/failure:** wrong extension → 400; fake image bytes → 400 (magic-byte check); empty/missing file → 400; > 3 MB → 413; anonymous fetch of the URL → 401 (see Finding F-1 for the signed-in sharing model).
- **Coverage:** ✅ `test_photo_upload_and_serving`, `test_employer_logo_upload`, `test_photo_upload_blob_stores_url_and_view_proxies_bytes`, `test_uploads_route_requires_login_on_blob_backend`, `test_photo_view_blob_get_failure_is_502` (test_swipehire/test_storage); ✅ `test_photo_upload_abuse`, `test_uploads_require_login_but_are_shared_with_signed_in_users` (test_adversarial).

## 7. Candidate preferences

- **Actor:** candidate · **Goal:** target titles, locations, remote-only, salary floor steer the feed.
- **Main flow:** POST `/api/candidate/preferences` → stored (lists as JSON); ranking adds a preference component (target-title/location/salary fit) with human-readable `why` strings.
- **Edge/failure:** non-numeric `min_salary` → 400 (BUG-3 fix; previously 500); non-list `target_titles`/`locations` → 400 (BUG-5 fix; previously stored and silently degraded ranking); numeric-string salary (`"90000"`) accepted and coerced.
- **Coverage:** ✅ `test_feed_preference_boost` (ranking unit, test_swipehire); ✅ `test_preferences_wrong_types_do_not_corrupt` (test_adversarial).
- 🟡 **Partial:** no API-level test asserts a preferences change reorders a live `/api/discovery` response end-to-end (ranking effect is unit-covered; storage + validation are API-covered).

## 8. Discovery feed + For You ranking (candidate)

- **Actor:** candidate · **Goal:** a ranked deck of jobs not yet swiped, each with score + reasons.
- **Main flow:** GET `/api/discovery` → all unswiped jobs scored (`score_job`) then ranked (`rank_jobs`): base score + preference fit + learned boost/penalty from past right/left swipes + diversity cap (≤2 per company in the first 10) + one stretch pick + −15 penalty for seeded-pool jobs.
- **Edge/failure:** unauthenticated → 401; employer session → 403; swiped jobs disappear from the deck; a job posted with junk-typed fields can no longer 500 the feed (BUG-1 fix).
- **Coverage:** ✅ `test_health_and_discovery`, `test_feed_learned_from_real_swipes`, `test_feed_learned_boost_and_penalty`, `test_feed_diversity_cap_first_10`, `test_feed_stretch_slot`, `test_feed_seeded_penalty` (test_swipehire); layout at 390/1366/1920 ✅ `test_discovery_layout` (test_layout).

## 9. Swipe left / right

- **Actor:** candidate · **Goal:** pass, or apply.
- **Main flow:** POST `/api/swipe {job_id, direction}`. Left: recorded, no application, job leaves the deck. Right: swipe recorded (idempotent — re-swiping never duplicates the application), an application package is created by the apply engine, and a match is created if the employer already right-swiped this candidate for this job.
- **Edge/failure:** unknown/huge/negative/list `job_id` → 404; bad direction → 400; non-object body → 400.
- **Coverage:** ✅ `test_swipe_right_creates_application_and_dedupe`, `test_swipe_left_no_application` (test_swipehire); ✅ `test_swipe_bad_ids_and_direction` (test_adversarial).

## 10. Right-swipe apply outcomes by job class (honest-status model)

- **Actor:** candidate · **Goal:** apply — with a truthful status, never a fake "submitted".
- **Main flow:** job URL is classified (`app/classify.py`): LinkedIn/Indeed → `network_login` 🔒; Workday → `ats_account` 🔒; Greenhouse/Lever/Ashby/Workable/SmartRecruiters/Breezy/Recruitee → `ats_form`; anything else → `external`.
  - `ats_form` / `external` → application `prepared`, method `external_link`, proof text says **"Not yet submitted"**.
  - `network_login` / `ats_account` **without** a used extension → `prepared`, method `extension_required`, `needs_extension: true` (UI shows the "This one needs your login" modal with Install / Connections / Later).
  - `network_login` / `ats_account` **with** a paired-and-used extension → `queued_extension`.
  - `submitted` is reachable **only** via an extension report (story 19).
- **Coverage:** ✅ `test_classifier` (16 URL cases), `test_insert_jobs_stamps_classification`, `test_swipe_never_submitted_and_login_job_needs_extension`, `test_ats_job_prepared_not_submitted`, `test_ext_queue_and_report_flow` (test_launch).

## 11. Applications list + tailored resume

- **Actor:** candidate · **Goal:** see every prepared application and its truthful tailored resume.
- **Main flow:** GET `/api/applications` (status, method, proof, coverage; resume body withheld, `has_tailored` flag instead). GET `/api/applications/<id>/tailored-resume` → text + coverage (covered/missing keywords — gaps are reported, never invented); `?download=1` returns the .txt attachment.
- **Edge/failure:** another candidate's application id → 404; huge id → 404 (BUG-4 fix).
- **Coverage:** ✅ `test_tailoring_via_swipe_right_flow`, `test_tailoring_guardrail_no_invented_skills`, `test_data_isolation_between_users` (test_swipehire).

## 12. Connections page (Tier-2 board identifiers + extension state)

- **Actor:** candidate · **Goal:** record which board account is theirs; see extension connection state.
- **Main flow:** POST `/api/connections {board, identifier}` — board ∈ linkedin/indeed; **identifiers only: the API accepts and stores no password** (a `password` field in the payload is ignored and never persisted or echoed). GET returns the user's connections + extension state (`has_token`, `connected`, `last_used_at`). DELETE `/api/connections/<board>` removes one (404 if none).
- **Edge/failure:** unknown board → 400; missing/non-string identifier → 400; another user's connection is invisible and undeletable (404); employer session → 403.
- **Coverage:** ✅ `test_connections_crud_and_no_password_field`, `test_connections_isolation_and_role_gate` (test_launch); ✅ `test_connections_wrong_types` (test_adversarial).

## 13. Extension pairing token

- **Actor:** candidate · **Goal:** pair the browser extension without sharing any board password.
- **Main flow:** POST `/api/ext/pairing-token` → plaintext token returned **once**; only its hash is stored; generating a new token revokes the old. DELETE revokes all. Any successful token-authenticated call stamps `last_used_at` (that's what flips a later 🔒 swipe to `queued_extension`).
- **Coverage:** ✅ `test_pairing_token_hashed_and_single_active` (asserts the DB holds no raw token), `test_ext_isolation_and_revoke` (test_launch); ✅ `test_ext_endpoints_reject_session_cookie_and_bad_tokens` (test_adversarial).

## 14. Employer signup + posts a job

- **Actor:** employer · **Goal:** a posting that candidates can swipe on.
- **Main flow:** POST `/api/auth/signup {role:"employer", company}` → employer row created. POST `/api/employer/jobs {title, location, remote, salary_min/max, salary_text, description, url, tags}` → 201, job stamped `source:"employer"`, owned by this employer, classified, and visible in candidate discovery. GET `/api/employer/jobs` lists only own jobs. The seeded demo employer owns two showcase jobs so the demo works on first login.
- **Edge/failure:** missing/non-string title → 400; junk salaries → 400 (BUG-1 fix — previously stored and poisoned other users' feeds); non-list tags → 400.
- **Coverage:** ✅ `_employer_match_setup` flows (test_swipehire); ✅ `test_employer_job_wrong_types_do_not_poison_feeds` (test_adversarial).

## 15. Employer candidate feed + ranking

- **Actor:** employer · **Goal:** ranked candidates for one of their jobs.
- **Main flow:** GET `/api/employer/candidates?job_id=<own job>` (no `job_id` → latest own job; no jobs → `{job: null, candidates: []}`) → candidates not yet swiped for this job, ranked by `rank_candidates_for_job` with `why` + `feed_score`; public candidate view never includes resume text or compiled resume.
- **Edge/failure:** another employer's job id → 404; huge job id → 404 (BUG-4 fix); unparseable `job_id` falls back to latest job (lenient, Finding F-3); candidate session → 403.
- **Coverage:** ✅ `test_employer_candidates_ranked_with_why` (test_swipehire); ✅ `test_employer_b_cannot_touch_employer_a_job`, `test_employer_candidates_huge_job_id` (test_adversarial).
- 🟡 **Partial:** employer-side *learning* (feed re-ranking from the employer's own past swipes) is implemented in `rank_candidates_for_job` inputs but has no dedicated assertion test.

## 16. Employer swipe + mutual match (both directions)

- **Actor:** employer + candidate · **Goal:** a match exists only when both sides said yes.
- **Main flow:** POST `/api/employer/swipe {job_id, candidate_id, direction}` — job must belong to the employer (404 otherwise), candidate must exist (404). Match creation is symmetric: employer-right after candidate right-swipe/application creates it in the swipe response; candidate-right after employer-right creates it via `maybe_create_match`. No candidate interest → no match, ever. GET `/api/matches` is scoped to the caller.
- **Coverage:** ✅ `test_employer_match_flow`, `test_employer_no_match_without_candidate_swipe` (test_swipehire); ✅ cross-employer swipe → 404 in `test_employer_b_cannot_touch_employer_a_job` (test_adversarial).

## 17. Match messaging (both sides)

- **Actor:** matched candidate/employer · **Goal:** talk inside the match.
- **Main flow:** GET/POST `/api/matches/<id>/messages`; POST requires a non-empty string body → 201 with `sender` = caller's role; both parties read the same ordered thread.
- **Edge/failure:** non-party candidate **and** non-party employer → 404 for read and post (indistinguishable from nonexistent); empty/non-string body → 400; non-object body → 400 (BUG-2 fix); XSS payloads round-trip as plain JSON strings (UI escapes — Finding F-2).
- **Coverage:** ✅ `test_employer_match_flow` (test_swipehire); ✅ `test_match_messages_locked_to_parties`, `test_xss_in_message_body_roundtrips` (test_adversarial).

## 18. Account deletion (full cascade)

- **Actor:** either role · **Goal:** everything scoped to me is gone.
- **Main flow:** DELETE `/api/account` → deletes user, candidate/employer row, documents, swipes, applications, connections, extension tokens, login codes, matches + their messages; an employer's jobs and everything scoped to those jobs; stored uploads (local + Blob objects, best-effort); session cleared. The email can immediately re-register (hard delete, not soft).
- **Coverage:** ✅ `test_delete_account_removes_scoped_data` (test_launch), `test_delete_account_deletes_blob_objects` (test_storage); ✅ counterparty view — `test_candidate_deletion_cascades_to_counterparty`, `test_employer_deletion_removes_jobs_and_matches` (test_adversarial). Second DELETE → 401 (session already cleared).

## 19. Extension queue / report API

- **Actor:** the Chrome extension (token auth, **not** session) · **Goal:** pull work, report outcomes.
- **Main flow:** GET `/api/ext/queue` (Bearer or `X-Extension-Token` header) → this candidate's `queued_extension` jobs + prepared ATS-form jobs, with profile-derived answers (facts only) and the tailored resume. POST `/api/ext/report {application_id, event, note}` — events `opened` (promotes prepared → queued), `filling`/`filled` (progress only, status unchanged), `submitted` (**only** path to that status; requires the extension's explicit report), `failed` → `needs_review`, `needs_human` → parked with the reason recorded (CAPTCHA/login wall/checkpoint/unknown question).
- **Edge/failure:** no/wrong/revoked token → 401; a valid web session cookie alone → 401; someone else's application id → 404; bad event → 400; non-string note is dropped, not fatal; huge/list application id → 404 (BUG-4/3 fixes).
- **Coverage:** ✅ `test_ext_queue_and_report_flow`, `test_ext_isolation_and_revoke` (test_launch); ✅ `test_needs_human_parks_with_reason`, `test_filling_event_is_progress_only`, `test_opened_promotes_prepared_to_queued`, `test_queue_includes_ats_form_and_payload_is_complete`, `test_needs_human_requires_own_token` (test_autonomous); ✅ `test_ext_report_bad_input_with_valid_token` (test_adversarial); ✅ real-browser driver behavior on fixture pages — `tests/e2e_extension.py` (manual run, green at v6).
- ❌ **GAP (by design):** pacing/caps (≥90 s between submissions, burst/daily caps) live in the extension's background worker, not the API — not testable from the Flask suite. ❌ **GAP:** real LinkedIn/Indeed/ATS live pages — drivers are fixture-proven only; a live-board shakedown still needs the user's own logged-in browser session.

## 20. Job-pool rescan + cron auth

- **Actor:** signed-in user / Vercel Cron · **Goal:** keep the shared pool fresh.
- **Main flow:** POST `/api/jobs/scan` (any signed-in role) runs all board fetchers; one board failing never fails the scan (per-source status in the report). GET `/api/cron/rescan` is the daily cron target: when `CRON_SECRET` is set, requires `Authorization: Bearer <secret>` → 401 without/with wrong secret; when unset (demo), it runs openly.
- **Coverage:** ✅ `test_scan_endpoint`, `test_scan_offline_with_failing_fetchers`, `test_cron_rescan_runs_scan`, `test_cron_rescan_secret_guard` (test_swipehire).
- ❌ **GAP:** live board fetching (network) is stubbed/mocked in tests; production pool freshness is monitored via the health endpoint + cron logs, not tests.

## 21. Uploads access control

- **Actor:** any client · **Goal:** files reach exactly the people the product intends.
- **Behavior:** GET `/uploads/<name>` requires a session — anonymous → 401. Blob-backed objects are fetched server-side with the storage token and proxied; the private blob URL is never handed to the browser; a blob fetch failure → 502 (not a crash). Local backend serves from the uploads dir by basename (no path traversal — `../` → 404).
- **Coverage:** ✅ `test_photo_upload_and_serving` (anon 401), `test_uploads_route_requires_login_on_blob_backend`, `test_photo_view_blob_get_failure_is_502` (test_storage); ✅ traversal + sharing model in `test_uploads_require_login_but_are_shared_with_signed_in_users` (test_adversarial). See Finding F-1.

## 22. Privacy / Terms pages

- **Actor:** visitor · **Goal:** know what's stored (and what never is).
- **Main flow:** GET `/privacy`, GET `/terms` → 200, no login. Privacy states the never-store rule (no board passwords anywhere), the deletion cascade, and names the Law 25 privacy contact.
- **Coverage:** ✅ `test_privacy_terms_pages` (test_launch — asserts 200 + the "never store" statement).
- 🟡 **Partial:** the privacy-contact line itself has no content assertion.

## 23. Authentication perimeter (cross-cutting)

- **Behavior:** every protected endpoint returns 401 unauthenticated (34 routes enumerated); candidate sessions get 403 — never 500 — on employer-only endpoints and vice versa (22 route/role pairs).
- **Coverage:** ✅ `test_unauthenticated_everything_401`, `test_candidate_cannot_use_employer_endpoints`, `test_employer_cannot_use_candidate_endpoints` (test_adversarial); ✅ `test_role_isolation`, `test_unauthenticated_blocked` (test_swipehire).

---

## Bugs found & fixed during QA

Fixed in commit `9fa4a4a` ("input-validation hardening"), all in `app/__init__.py`, each with regression tests in `tests/test_adversarial.py`. All were confirmed with live probes against the pre-fix code before fixing.

| # | Severity | Bug | Evidence (pre-fix) | Fix |
|---|----------|-----|--------------------|-----|
| BUG-1 | **High** | **Cross-user DoS via job salary fields.** An employer could post a job with `salary_max: "xyz"`; the string was stored, and every candidate with a salary floor got a 500 on `/api/discovery` (`TypeError: '>=' not supported between 'str' and 'int'` in `score_job`); the employer's own candidate feed 500'd too. One junk posting broke the feed for everyone with `min_salary > 0`. | Probe: post job 201 → other user's discovery 500; traceback captured | `_coerce_salary` validation in POST `/api/employer/jobs` → junk salaries 400, numeric strings coerced, nothing stored |
| BUG-2 | Medium | **Non-object JSON bodies 500'd.** Sending a JSON array/string/number to signup, login, code request/verify, swipe, preferences, connections, documents, employer jobs, employer swipe, match messages, or ext report raised `AttributeError` on `.get`. | Probe: 500 on 12 endpoints | `_json_body()` helper: non-dict → 400 (or the endpoint's existing generic 401) |
| BUG-3 | Medium | **Wrong-typed scalar fields 500'd.** Non-string email/password at signup/login; `min_salary: "abc"` at preferences (`int()` ValueError); non-string board/identifier at connections; non-string document/resume `text`; non-string ext-report `note`; list `application_id` at ext report; non-string job `title` at employer jobs. | Probe: 500 on each | isinstance guards + coercion helpers at each site → 400/404 |
| BUG-4 | Medium | **Huge ids 500'd.** Ids larger than SQLite's int64 (e.g. a 26-digit id in a URL path, query param, or JSON body) raised `OverflowError` on parameter binding: document delete, tailored-resume, match messages, employer candidates, ext report, swipe. | Probe: 500; traceback `OverflowError: Python int too large to convert to SQLite INTEGER` | `@app.errorhandler(OverflowError)` → 404 (no such row can exist) + `_coerce_id` for JSON ids |
| BUG-5 | Low | **Preferences silently corrupted.** Non-list `target_titles`/`locations` were stored as-is and degraded ranking inputs without any error. | Probe: 200 + poisoned prefs stored | Non-list values → 400 in POST `/api/candidate/preferences` |

Also tightened in the same batch: match-message `body` must be a string (previously an integer body was silently stored via SQLite type coercion and returned as `"42"`).

## Findings (documented, not code bugs)

- **F-1 — `/uploads` is login-gated, not owner-gated (by design).** Any signed-in user who knows an upload filename can fetch it. This is required by the product: employers must see candidate photos and vice versa, and filenames are unguessable UUIDs (`<table>_<id>_<uuid10>.<ext>`). Anonymous access is 401 and traversal is impossible (basename only). Residual risk accepted: a filename leaked to another signed-in user exposes that photo/logo. Documents' *text* is never served via `/uploads` (DB + private Blob only).
- **F-2 — XSS posture is "store verbatim, escape at render" — verified.** The API stores and returns payloads (names, job titles, message bodies) as plain JSON data strings. Every user-data interpolation in `app/static/app.js` goes through `esc()` (defined line 3; verified by grep across all `innerHTML` sinks — cards, matches, messages, profile, connections, documents, master profile). The one server-rendered HTML surface, `/api/compiled-resume/download?format=html`, escapes via `_esc()` in `app/__init__.py`. No browser-level XSS test was written (per QA scope); the guarantee rests on the `esc()` audit.
- **F-3 — Lenient fallbacks (by design).** `/api/employer/candidates?job_id=abc` falls back to the employer's latest job instead of erroring (Flask `type=int` conversion); `job_id` given as a numeric string in swipe bodies is accepted. No security impact.
- **F-4 — Code-request rate limit exists and is tested** (3 per email per 15 min → 429; `test_code_rate_limit`). The verify endpoint's brute-force bound is the 5-attempt cap per code + 10-minute expiry (both tested). There is no per-IP throttle anywhere in the app — acceptable at current scale behind Vercel, noted for the public-launch checklist.

## Remaining gaps (untestable locally, stated plainly)

1. **Live job boards** — extension drivers and board fetchers are proven against fixtures/mocks only; real LinkedIn/Indeed/ATS DOM behavior needs a piloted session in the user's own logged-in browser.
2. **Real email delivery** — Brevo deliverability (incl. spam-folder behavior on non-Gmail inboxes) is outside the test suite; demo-mode code display is the local stand-in.
3. **Production backends** — Turso and Vercel Blob are covered by offline wire-shape tests (`test_storage.py`, `test_turso_conn_crud_and_init`); live-cloud behavior was verified manually at launch, not in CI.
4. **Extension pacing/caps** — enforced client-side in the extension background worker; no server-side test can observe them.
5. **Preferences → live reordering** and **employer feed learning** — ranking math is unit-tested; the two API-level end-to-end assertions are the only partial-coverage items left (stories 7 and 15).
