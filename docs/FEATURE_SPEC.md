# SwipeHire — Feature Specification & Checklist

Source: Jackson's feature list, Nov 12, 2026 (this chat). Status column is
from code inspection + live production checks on Nov 12, 2026 — it is NOT
the full feature test. The full test runs against this confirmed spec.

**SPEC CONFIRMED by Jackson, Nov 12, 2026, with amendments** (media
mandatory on the employer side, like counts for both sides, hybrid /
in-person distinction, candidates swipe recruiters as well as jobs with a
Jobs/Recruiters filter). Resolved decisions and the new requirements they
create are at the bottom of this doc.

Status key: ✅ implemented · 🟡 partial · ❌ missing · 🔨 to build per the
confirmed amendments

Definitions used in this spec:
- **Recruiter profile** = the single employer account (company, media,
  hiring manager identity). One per employer account.
- **Job profile** = one job posting owned by a recruiter profile. A
  recruiter can hold many job profiles.
- **Pool / deck** = the swipeable stack shown to one user, ranked.
- **Liked you** = a candidate right-swiped one of your job profiles (which
  also creates their application package), or a recruiter right-swiped you
  for one of their jobs.

---

## Candidates

| # | Feature | Status | Notes / acceptance test |
|---|---------|--------|-------------------------|
| C1 | Account + login, stored server-side | ✅ | Password + email-code sign-in; persistent DB. Live-verified: probe account created, used, deleted. Test: fresh account survives logout/login. |
| C2 | Resume storage + parsing | ✅ | .pdf resume parsed into the profile; facts only, nothing invented. |
| C3 | Other documents → one Master Profile | ✅ | .txt/.md/.pdf vault (cover letters, references, write-ups, transcripts, certifications), compiled with per-fact provenance. |
| C4 | Profile image | ✅ | .png/.jpg; private storage, anonymous fetch 401 (live-verified). |
| C5 | Profile video | 🔨 | Not supported today (images only). Per D1: candidates get photo-or-video profiles. Test: upload .mp4, it plays on the profile and on employer-facing cards. |
| C6 | Finds jobs on the internet | ✅ | 743 live jobs from 6 boards + daily auto-rescan. |
| C7 | Identifies autonomously-appliable jobs | ✅ | Every job classified (no-account form / login-required 🔒 / external); badge on the card. |
| C8 | Extension prompt for browser-session applies | ✅ | Right-swiping a 🔒 job unpaired prompts install/pairing; paired → queued for autonomous in-browser application, no prompt. |
| C9 | Learns from swiping | ✅ | Ranker learns from right/left swipe patterns + profile match + stated preferences; swiped jobs leave the deck. |
| C10 | Filter by keyword | 🔨 | Missing today (target-title preference only biases ranking). Per N5: hard deck filter. Test: set a keyword; every card shown matches it. |
| C11 | Filter by location / distance | 🔨 | Preferred-locations ranking boost exists; no hard filter, no radius. Per N5: hard location filter (radius per D2 was not separately specified — city/region text match first). Test: out-of-area jobs disappear. |
| C12 | Filter by work mode: remote / hybrid / in-person | 🔨 | "Remote only" preference exists; jobs carry a remote yes/no flag only. Per D3/N3: add `work_mode` (remote/hybrid/in_person/unspecified), distinguished when source data allows. Test: each mode setting shows only that mode. |
| C13 | Swipe on recruiters | 🔨 | Today candidates swipe jobs only. Per D4/N4: recruiter cards enter the candidate deck. |
| C14 | See your like count | 🔨 | Per D2/N2: candidates see how many recruiter right-swipes they have received, Tinder-style. |
| C15 | Deck type filter: Jobs / Recruiters / Both | 🔨 | Per D4/N4: toggle on the candidate deck switches the pool between job cards, recruiter cards, or both mixed. |

## Employers / Recruiters

| # | Feature | Status | Notes / acceptance test |
|---|---------|--------|-------------------------|
| E1 | Recruiter profile (one per account) | ✅ | Company identity + logo; live-verified. Media rule per N1 below. |
| E2 | Post jobs directly in SwipeHire | ✅ | Live-verified: post → appears in employer job list + candidate discovery. |
| E3 | Multiple job profiles, one recruiter profile | ✅ | Job selector in employer view; live-verified with 2 jobs. Test with 3: each deck scoped to its job. |
| E4 | See how many candidates liked your job | 🔨 | Not shown today; interest surfaces only as a Match. Per D2/N2: per-job like count on the recruiter profile/job list. |
| E5 | Swipe on candidates | ✅ | Ranked candidate deck per selected job; live-verified. |
| E6 | Candidate pool to swipe through | ✅ | Pool = all candidates not yet swiped for that job, ranked (skill fit + learned from the recruiter's past swipes). |
| E7 | Media mandatory: recruiter profile | 🔨 | Per D1/N1: a recruiter profile must carry a picture OR a video. |
| E8 | Media mandatory: each job profile | 🔨 | Per D1/N1: a job cannot be published without a picture or a video. Jobs posted in-app get a media upload; jobs with company media inherit the recruiter's. |

## Cross-cutting (already in place)

- Matches: mutual right-swipes (candidate↔job) create a Match with
  messaging from both sides — live-verified end-to-end.
- Honest apply statuses: the web app never claims "submitted"; only the
  extension's real browser submission can produce that status.
- Privacy: uploads are login-gated; account deletion cascades fully.

---

## Decisions — RESOLVED by Jackson, Nov 12, 2026

| # | Question | Jackson's answer |
|---|----------|------------------|
| D1 | Where does video apply; is media required? | Employer recruiter profiles and job profiles: **either a picture or a video, mandatory**. Candidates also get photo-or-video profiles. |
| D2 | Like visibility | **Both employers and candidates see their like count, much like Tinder.** |
| D3 | Work mode | **Distinguish hybrid vs in-person when available.** |
| D4 | Candidates swiping recruiters | **Candidates swipe jobs and recruiters, and can filter between jobs and/or recruiters.** |

### Model clarification — Jackson, Nov 12, 2026, 13:47 EST

The model has exactly **2 sides**:

1. **Candidate side** — one candidate profile per account.
2. **Recruiter side** — each recruiter account has **at most one recruiter
   profile**, plus any number of job postings under it. There is no
   multi-profile concept; the invariant is one employer profile per
   account (enforced by the schema: `employers.user_id` is the profile).

Like counts are Tinder-style and attach **per entity**:

- **Candidate profile** → received-like count = recruiter (job-context)
  right-swipes received on that candidate.
- **Each job posting** → received-like count = candidate right-swipes on
  that job (shown in the employer's job list/selector).
- **Recruiter profile** → received-like count = candidate right-swipes on
  the recruiter card (N4 recruiter swiping), shown on the employer
  dashboard / recruiter profile.

Candidates can therefore like two employer-side entity types — a job
posting or the recruiter profile — and each like lands on that entity's
count. Identities still surface only via Matches.

## New build requirements from the amendments

| # | Requirement | Covers |
|---|-------------|--------|
| N1 | Video upload + playback (candidate profile, recruiter profile, job profiles); image-or-video mandatory before an employer profile is complete or a job is published | C5, E7, E8 |
| N2 | Like counts, per entity (see Model clarification): candidate profile = recruiter right-swipes received; each job posting = candidate right-swipes on that job; recruiter profile = candidate right-swipes on the recruiter card (identities still revealed via Matches) | C14, E4 |
| N3 | `work_mode` field on jobs (remote / hybrid / in_person / unspecified), derived at ingest and at posting from available source signals | C12 |
| N4 | Recruiter cards in the candidate deck + Jobs / Recruiters / Both toggle. Right-swiping a recruiter = interest in that employer (counted on the recruiter profile's like count); a Match forms when the recruiter right-swipes that candidate for any of their jobs | C13, C15 |
| N5 | Hard deck filters for candidates: keyword, location, work mode | C10, C11, C12 |

Build order note: N3 + N5 together (filters need the field), N4 next
(new deck type), N1 (media pipeline), N2 (counts are cheap, can ship
anytime). Every item ships with tests; the full feature test at the end
runs this whole checklist against production.

**Status: N1–N5 BUILT Nov 12, 2026** (commits af04c78, 137c6cf, e9ed5c7 +
tests/test_features_v9.py, 21 tests; full suite green incl. layout).
