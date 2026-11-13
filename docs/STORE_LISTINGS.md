# SwipeHire extension — store listings packet (Phase B, Nov 13, 2026)

Everything a store submission needs, in one place. The packages are
built from the single source tree in `extension/` by
`extension/build_variants.py`:

| Package | Built file | What it is |
|---|---|---|
| Chrome | `extension/dist/chrome.zip` | Source tree as-is (MV3). Same file set the app serves at `/extension/download`. |
| Edge | `extension/dist/edge.zip` | **Byte-identical to chrome.zip** — Edge Add-ons accepts the same Chromium MV3 package unchanged. |
| Firefox | `extension/dist/firefox.zip` | Same files; only `manifest.json` is transformed (below). |

Rebuild: `python3 extension/build_variants.py` (stdlib only, fails
loudly if the source manifest's shape drifts from what the transform
expects). `extension/dist/` is git-ignored build output.

## Firefox compatibility pass (static, Nov 13, 2026)

Method: inventoried every `chrome.*` call in `background.js`,
`content.js`, `popup.js` and checked for service-worker-only
assumptions. **No extension JS was changed** — manifest transform +
these documented gaps only.

API surface actually used (13 entry points): `chrome.storage.local`
(get/set/remove), `chrome.tabs` (create/update/remove),
`chrome.alarms` (create/onAlarm), `chrome.runtime` (onMessage,
onInstalled, onStartup, sendMessage), plus `fetch` to the SwipeHire API.

**Works as-is under Firefox's `chrome.*` compatibility layer:**
- Every API above is in Firefox's supported MV3 set, promises included.
  No `browser.*`-only or Chrome-only API is used anywhere.
- No service-worker-only constructs exist in the code: no
  `importScripts`, `self.skipWaiting`, `clients.claim`, no Cache
  Storage, no in-memory state that must survive — the queue processor
  keeps ALL durable state (`proc`, `pace`, `currentJob`, run log) in
  `chrome.storage.local`, and its only in-memory value is a re-entrancy
  lock that is safe to lose on suspension.
- Content scripts use just `chrome.runtime.sendMessage` +
  `chrome.storage.local.get`, and already tolerate a dead background
  (sendMessage is try/caught with a storage fallback) — exactly the
  behaviour an event page that isn't awake yet requires.
- The popup is a plain page using storage/tabs/sendMessage. Plain
  `fetch` + Bearer token to the API works the same in both browsers.

**Event-page-risky (manifest transform handles the shape; behaviour
must be smoke-tested on the signed build before listing publicly):**
- Firefox runs the background as an **event page** that suspends when
  idle and wakes on events. `background.js` is written wake-safe
  (state in storage, tick() re-entrant, alarms + onStartup +
  onInstalled all re-kick the processor), but two things can only be
  verified live on Firefox: (1) the 0.5-minute `chrome.alarms` tick —
  both browsers clamp alarm periods at their own floors, and a clamped
  tick only ever makes claiming *slower*, never faster, so the pacing
  caps stay safe either way; (2) a claim in flight when the event page
  suspends mid-application — the existing 150-second watchdog parks
  the claim as "Needs you" rather than retrying blindly, which is the
  designed failure mode, but it will fire more often if Firefox
  suspends aggressively. Expect Firefox to park slightly more jobs as
  Needs-you than Chrome until this is observed in practice.
- `host_permissions`: on current Firefox (127+) MV3 host permissions
  are granted at install; on older Firefox the user may have to grant
  site access from the add-ons manager before content scripts run on
  the boards. Support answer if a Firefox user reports "the driver
  never starts": grant the add-on access to the job-board sites.
- Firefox requires the add-on id (`browser_specific_settings.gecko.id`,
  set by the transform to `swipehire@swipehire.app`) for signing and
  for `chrome.storage` continuity across updates — do not change it
  between submissions.

**Changed for Firefox:** manifest only — `background.service_worker`
→ `background.scripts: ["background.js"]`, plus the Gecko id block.
`data_collection_permissions` is deliberately NOT baked into the
manifest (it is optional there); AMO collects the data declaration
during submission — see the owner steps for exactly what to declare.

**Known package gap (both stores):** the package ships **no icon
assets** and the manifest has no `icons` key, so listings and the
toolbar show the browser's default puzzle-piece icon. Submissions are
accepted without one, but a 128×128 (Chrome/Edge) / 96×96 (AMO) icon
should be added before a public listing push — flagged, not built
here (no icon exists anywhere in the project yet).

## Submission packet (copy-paste ready)

**Product name:** SwipeHire Autonomous Apply

**Short description (117 chars, fits the 132-char store field):**
> Applies to your queued SwipeHire jobs for you, in your own logged-in browser — and parks anything that needs a human.

**Full description:**
> SwipeHire is Tinder-style job matching: upload your resume, swipe
> right on jobs, and this extension does the applying. It is
> SwipeHire's executor — required for login-required jobs (LinkedIn,
> Indeed, Workday-style portals) and the form executor for ATS
> application forms (Greenhouse, Lever, Ashby, Workable,
> SmartRecruiters).
>
> How it works: right-swipe jobs in SwipeHire and they queue for the
> extension. Pair once with a token from SwipeHire → Connections. The
> extension then claims queued applications one at a time, opens each
> job in a tab it manages, fills the form from your own profile facts
> and your truthfully tailored resume, and submits — end to end, in
> YOUR browser, under YOUR existing logins. SwipeHire never sees or
> stores your job-board passwords.
>
> Honest statuses, always: an application is only ever marked
> "submitted" when a real confirmation is detected (thank-you page,
> confirmation banner, or confirmation URL). Everything else is
> reported as what it is — prepared, filling, failed, or parked.
>
> Needs a human? It parks, it never pushes through. A CAPTCHA, a login
> wall or security checkpoint, a verification-code prompt, or a
> required question your profile can't answer parks the job as "Needs
> you" with the reason recorded. An account-warning signal pauses ALL
> autonomous applying until you resume it from the popup.
>
> Paced like a careful human: at least 90 seconds between
> submissions, a rolling cap of 5 submissions per 30 minutes, and
> daily caps per board (LinkedIn 30, Indeed 20, ATS 40). The popup
> shows today's counters and a run log of the last 50 events.

**Category:** Chrome Web Store / Edge Add-ons → **Productivity**.
Firefox AMO → **Other** (AMO has no jobs/productivity category;
confirm the closest fit in the submission form).

**Privacy policy URL:** https://swipehire-jade.vercel.app/privacy

### Per-permission justifications (stores require one per permission)

Manifest `permissions`:

| Permission | Justification |
|---|---|
| `storage` | Stores the pairing token, the SwipeHire API address, the autonomous-apply on/pause state, today's pacing counters (per-board submission counts), the currently-claimed job, and a 50-entry run log. Nothing else is persisted. |
| `tabs` | The queue processor opens each claimed job in a background tab it manages, navigates that tab to the employer's application page, and closes it when the driver reports an outcome. It never reads or touches the user's other tabs. |
| `alarms` | A 30-second repeating alarm drives the queue processor: claim the next queued application when pacing allows, and run the watchdog that parks a claim if a page never reports back (150s). |

`host_permissions` + content-script matches (one justification per host
group — the same hosts appear in both):

| Host(s) | Justification |
|---|---|
| `https://swipehire-jade.vercel.app/*` | The SwipeHire API the user pairs with: fetch the queued applications (`GET /api/ext/queue`) and report outcomes (`POST /api/ext/report`), authenticated with the user's pairing token. |
| `http://localhost/*`, `http://localhost:5050/*`, `http://127.0.0.1/*` | Development/self-hosted SwipeHire instances only — the popup lets the user point the extension at their own SwipeHire server instead of the hosted one. No data goes to localhost unless the user configures it. |
| `*://*.linkedin.com/*`, `*://*.indeed.com/*`, `*://*.indeed.ca/*` | The login-required job boards the extension exists for: the content script drives LinkedIn Easy Apply and the Indeed apply flow on jobs the user queued, inside the user's own logged-in session. |
| `*://*.greenhouse.io/*`, `*://*.lever.co/*`, `*://*.ashbyhq.com/*`, `*://*.workable.com/*`, `*://*.smartrecruiters.com/*` | Public ATS application forms: the generic driver fills and submits these forms from the user's queued application package (profile facts + tailored resume). |
| `*://*.myworkdayjobs.com/*` | Workday portals: the driver only attempts a plain, already-signed-in application form; account creation is never attempted and a sign-in wall parks the job as "Needs you". |

## Owner-only steps that remain

Everything above is prepared. These steps need Jackson personally
(accounts in his name; see the everything-for-free research):

**Microsoft Edge Add-ons (free):**
1. Sign in to Microsoft Partner Center with his **personal Microsoft
   account** and enroll in the Microsoft Edge program as an
   **Individual** developer (no registration fee; country and account
   type cannot be changed after enrollment — pick carefully).
2. Partner Center → Edge → Create new extension → upload
   `extension/dist/edge.zip` (rebuild first with
   `python3 extension/build_variants.py`).
3. Fill the listing from the packet above: descriptions, category
   (Productivity), privacy policy URL, and the per-permission
   justifications in the certification notes.
4. Submit for certification (Microsoft states up to ~7 business days).

**Firefox Add-ons / AMO (free):**
1. Create a Mozilla account (his email) and accept the AMO developer
   terms.
2. Submit `extension/dist/firefox.zip` as a new add-on (listed).
   **Data-collection declaration:** declare truthfully that the add-on
   transmits data the user provides — queued application details,
   application answers, and outcome reports — to the SwipeHire server
   the user paired it with, and fetches their tailored resume text
   from that server. That is the add-on's core function, performed
   only for the signed-in user's own account.
3. Automated validation/signing is usually quick; a manual review may
   follow. Smoke-test the signed build on the pacing/watchdog points
   in the compatibility section before announcing Firefox support.

**Chrome Web Store (deferred, not free):** a one-time US$5 developer
registration with no waiver (per the free-alternatives research) —
`extension/dist/chrome.zip` is ready to upload the day that changes.
Until then Chrome users install the same zip via load-unpacked, as
the extension README describes.

## Housekeeping note

The zip the app serves at `/extension/download`
(`app/static/swipehire-extension.zip`) is a separately committed copy
of the Chrome variant, not an output of `build_variants.py`. When the
extension version bumps, rebuild the store zips here AND refresh that
static copy from `dist/chrome.zip` so the two never drift.
