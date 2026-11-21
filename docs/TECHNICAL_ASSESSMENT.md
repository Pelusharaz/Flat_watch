# Flat-Watch Debrecen – Technical Assessment & Implementation Plan

*Scope: critical review of the hackathon prototype, what was implemented in this iteration, and a
plan to turn it into a secure, time-saving rental platform for tenants and landlords.*

> **Cost and legal notes are estimates, not advice.** Prices of third-party services change – verify
> before committing. The legal points (anti-discrimination, scraping, GDPR) should be reviewed by a
> Hungarian lawyer before launch.

---

## Changelog – version 3 (this iteration)
* **UI/UX redesign:** multi-page navigation (`st.navigation`), design system (`flatwatch/ui_style.py`), tenant and
  landlord dashboards, photo-first cards with a match ring, details pop-up with a photo gallery and score bars,
  chat bubbles with a live-updating chat window, mobile-friendly CSS.
* **Photos visible:** the earlier sample photos were flat colour blocks. Sample listings now use rendered interior
  illustrations (`tools/render_sample_photos.py`, bundled in `data/sample_photos/`). Real photos can't be scraped
  from other sites, for copyright and privacy reasons.
* **Chat everywhere:** tenant ↔ tenant chats for flatmate matches, 10 sample listings now owned by demo landlords,
  seeded demo conversations, a “Start a new chat” panel, landlord invitations open the chat directly.
* **Test mail server:** pure-Python SMTP catcher (`flatwatch/mailserver.py`) started by the app on localhost:1025;
  the 📬 Test inbox page renders every email. “✨ Simulate a new matching listing” proves the alert pipeline end
  to end. Fixed: a saved search that had never run sent one instant email per existing match. It now sends a
  single “first matches” email.
* **Anti-spam module (`flatwatch/antispam.py`):** honeypot + timing + disposable-email checks at sign-up, saved-search
  caps and dedupe, daily instant-email cap, posting limits, moderation queue (`review` status) for spammy or
  scam-like listings, auto-hide after 3 reports, duplicate-message blocking, bans, and an admin moderation page.
* **Safe upgrades:** automatic migration of version-2 databases.
* **Tests:** 50 automated tests, plus a headless run of all 28 page views for 4 roles and the main click paths.

## 0. Executive summary

| | Before this iteration | After this iteration |
|---|---|---|
| Users | None – one shared “criteria” record | Accounts with tenant / landlord roles, verified email, scrypt passwords, lockout, password reset, optional TOTP 2FA, sessions, RBAC, GDPR export & delete |
| Matching | Pass/fail rules, commute a hard wall, pets/smoking excluded places, **nationality used to exclude people** | Weighted, configurable score (`data/scoring.json`), continuous commute curve with Near / Average / Far, soft pet/smoking penalties, **nationality removed**, full explanation (per-criterion scores, positives, negatives, missing info), learns from 👍/👎 |
| Discovery | Simulated feed while the browser is open | Saved searches + background worker: first-results email, instant alerts, daily/weekly digests, dedupe, pause/edit/unsubscribe |
| Listings | JSON files | One normalised table for every source, duplicate detection, price history, freshness (reconfirm reminders, auto-expiry) |
| Photos | None | Upload, validation, EXIF/GPS stripping, resize + compress + thumbnails, categories, ordering, gallery, placeholders |
| Communication | Copy-paste message + contact details shown | In-app chat per listing, unread counts, read receipts, link/payment-scam warnings, rate limits, report, block, contact details hidden |
| Tests | 37 | **50 automated tests** (scoring, security, auth, chat, photos, notifications, freshness, landlord matching) + page smoke tests |

**The single most important limitation that remains:** Streamlit is excellent for this prototype but is
not the right long-term frontend for a public, security-sensitive product. Login lives in the browser
tab's session (a refresh logs you out), chat is refreshed by polling rather than pushed, and there is no
proper HTTP API for a mobile app. The recommended next step is the **FastAPI + PostgreSQL** architecture in §4,
reusing all of the `flatwatch/` domain code (it has no Streamlit dependency).

---

## 1. Existing architecture assessment (hackathon version)

```
Streamlit app.py ──► flatwatch/agent.py (extract → cost → commute → rules → scam → message)
      │                     │
      │                     ├─ extract.py  (regex / optional LLM)
      │                     ├─ commute.py + transit.py (OSM stops, haversine estimates)
      │                     └─ scam.py (rule flags + optional LLM)
      ├─ watcher.py (thread replaying data/listings.json while the page is open)
      └─ SQLite: evaluations / kv / profiles (one global user)
```

Strengths worth keeping: the agent pipeline and its trace, Hungarian-text extraction with LLM fallback,
scam rules with repost detection, line-and-stop commute estimates, two-sided (tenant ↔ poster) matching.
All of that is preserved.

## 2. Functionality inventory (now)

| Area | Feature | Where |
|---|---|---|
| Matching | Weighted scoring, hard vs soft constraints, explanations, feedback learning | `scoring.py`, `agent.py`, `data/scoring.json` |
| Matching | Walk / bike / bus-tram times to every campus, line + stops | `commute.py`, `transit.py` |
| Matching | Apartment / private room / bed space, shared-living gender rules | `models.py`, `scoring.py` |
| Matching | Landlord-side compatible tenants (same engine, tenant's preferences) | `tenants.py` |
| Matching | Flatmate matching (mutual) | `flatmates.py` |
| Trust | Scam detection: advance payment, owner abroad, foreign WhatsApp, too cheap, reposts | `scam.py` |
| Trust | Duplicate detection, price history, freshness reminders/expiry | `listings.py`, `worker.py` |
| Accounts | Register, verify email, login, lockout, reset, 2FA, sessions, RBAC, GDPR | `auth.py`, `security.py` |
| Chat | Per-listing conversations, unread, read receipts, flags, report, block, invites | `messaging.py` |
| Photos | Upload pipeline, gallery, sample photos | `photos.py` |
| Discovery | Saved searches, first/instant/digest emails, unsubscribe | `searches.py`, `emails.py`, `mailer.py`, `worker.py` |
| UX | Matches with filters/sort, details page, map, shortlist + compare, account page | `app.py` |
| Landlord | Post/edit, ✨ description writer, photo manager, reconfirm, rented, compatible tenants + invite | `app.py`, `describe.py` |

## 3. Technical limitations identified

**Fixed in this iteration**
1. **Nationality used to rank and exclude tenants** – a discrimination risk (EU Race Equality Directive
   2000/43/EC; Hungarian Equal Treatment Act). Removed from every model, form, sample and score; a test
   guarantees it stays out.
2. Gender preference allowed on **whole apartments** – now only accepted for shared rooms/beds (flatmate
   privacy), enforced in code and in the Postgres `CHECK` constraint.
3. Commute and pets/smoking were binary pass/fail → now continuous, configurable soft scoring.
4. No accounts; contact details exposed in listings → accounts, RBAC, in-app chat, contacts hidden.
5. Discovery only while the page is open → background worker + email.
6. Untested email/scam link handling: a test caught that bare short links (`bit.ly/abc`) were missed – fixed.
7. A password rule rejected valid passwords for one-letter email names – caught by tests, fixed.

**Still open (by design for a prototype)**
| # | Limitation | Impact | Fix |
|---|---|---|---|
| L1 | Streamlit session = browser tab; refresh logs out; no secure HttpOnly cookie | UX + security | FastAPI with HttpOnly, SameSite=Lax session cookies + CSRF tokens (§9) |
| L2 | Chat refreshes by polling every 4 s | Load, latency | WebSockets (FastAPI) or a managed service (§9) |
| L3 | SQLite single file | Concurrency, no geo queries | PostgreSQL + PostGIS (`docs/schema_postgres.sql`) |
| L4 | Commute = distance + direct lines, no timetable | ±5–10 min error | GTFS router (OpenTripPlanner) or Google Routes API, cached per (district, campus) |
| L5 | Photos on local disk | No CDN, lost if server dies | S3-compatible storage (Cloudflare R2) + CDN |
| L6 | Verification/reset codes shown on screen in dev mode | Only safe without SMTP | Set `SMTP_HOST`, `DEV_SHOW_CODES=0` |
| L7 | ~~No admin UI for reports~~ | – | **Done in v3:** moderation page, review queue, bans |
| L8 | Deleting a tenant also deletes the conversation for the landlord | Landlord loses history | Soft-delete + anonymise sender instead |
| L9 | No malware scan on uploads | Low (images are re-encoded) | ClamAV only if non-image uploads are added |

## 4. Recommended architecture (production)

Keep it a **modular monolith** – no microservices are needed at Debrecen scale (thousands of users,
not millions).

```
            ┌───────────────── Cloudflare (DNS, TLS, WAF, CDN for photos) ─────────────────┐
Browser ───►│  Next.js (or the current Streamlit as an internal admin tool)                │
 / mobile   └──────────────┬───────────────────────────────────────────────────────────────┘
                           │ HTTPS, HttpOnly session cookie, CSRF token
                  ┌────────▼────────┐        ┌──────────────────────────┐
                  │  FastAPI (API)   │◄──────►│ PostgreSQL 16 + PostGIS   │
                  │  flatwatch/*     │        └──────────────────────────┘
                  │  WebSocket chat  │        ┌──────────────────────────┐
                  └───┬─────────┬───┘        │ Redis: rate limits, chat │
                      │         │            │ pub/sub, job queue        │
          ┌───────────▼──┐  ┌───▼─────────┐  └──────────────────────────┘
          │ Worker (RQ /  │  │ Object store │   Email: Brevo / Postmark / SES
          │ arq): matching│  │ (R2/S3)      │   LLM: optional, cached per listing
          │ digests, mail │  └─────────────┘   Routing: OTP/GTFS, cached
          └──────────────┘
```

* **Reuse:** `flatwatch/` (models, scoring, agent, auth, messaging, searches, listings, photos) is
  framework-free – FastAPI routes call the same functions. Swap `Database` for SQLAlchemy/psycopg.
* **Hosting (rough, verify):** one Hetzner/DigitalOcean VM (≈ €10–25/month) running API + worker +
  Postgres + Redis via Docker Compose is enough for launch; move Postgres to a managed service when
  revenue allows. Photos on Cloudflare R2 (generous free tier, no egress fees).

## 5. Database schema modifications

Implemented in SQLite (`flatwatch/db.py`) and specified for PostgreSQL in **`docs/schema_postgres.sql`**:

* **New tables:** `users`, `sessions`, `tenant_profiles`, `saved_searches`, `listings` (normalised, all
  sources), `price_history`, `photos`, `favorites`, `feedback`, `conversations`, `messages`, `blocks`,
  `reports`, `notified` (PK = search × listing → no duplicate alerts), `email_queue` (unique `dedupe_key`),
  `rate_events`, `audit_log`.
* **Normalised listing fields:** location (private full address + public district + `geography` point),
  price + currency, rooms, m², furnishing, availability date, pets, smoking, amenities, photos, landlord,
  source + source reference, `last_verified_at`.
* **Constraints:** gender preference only for shared offers; message length; unique conversation per
  (listing, tenant); foreign keys with `ON DELETE CASCADE` for personal data (GDPR erasure).
* **Indexes:** GiST on location, (status, type, price), trigram on title for fuzzy duplicates.
* **Removed:** `nationality`, `pref_nationalities` everywhere.

## 6. API endpoint requirements (FastAPI, next phase)

| Method & path | Role | Purpose |
|---|---|---|
| `POST /auth/register`, `/auth/verify`, `/auth/login`, `/auth/logout`, `/auth/reset/request`, `/auth/reset/confirm` | public | Account lifecycle (rate limited) |
| `POST /auth/2fa/setup`, `/auth/2fa/confirm`, `DELETE /auth/2fa` | user | TOTP |
| `GET/PUT /me/profile` | tenant | Tenant profile + preferences |
| `GET/POST /me/searches`, `PATCH/DELETE /me/searches/{id}` | tenant | Saved searches, frequency, pause |
| `GET /matches?type=&sort=&page=` | tenant | Ranked listings with score breakdown |
| `GET /listings/{id}` | user | Detail incl. commute table, photos, price history |
| `POST /listings`, `PATCH /listings/{id}`, `POST /listings/{id}/reconfirm`, `DELETE /listings/{id}` | landlord (owner) | Manage listings |
| `POST /listings/{id}/photos`, `PATCH/DELETE /photos/{id}` | landlord (owner) | Photo management (pre-signed upload URL) |
| `GET /listings/{id}/compatible-tenants` | landlord (owner) | Two-sided matching |
| `POST /favorites/{listing_id}`, `DELETE …`, `GET /favorites` | tenant | Shortlist |
| `POST /feedback` | tenant | 👍/👎 + reason (personalisation) |
| `GET /conversations`, `POST /conversations` (tenant → listing), `POST /conversations/invite` (landlord) | user | Chat |
| `GET/POST /conversations/{id}/messages`, `WS /ws/chat` | participant | Messages, real time |
| `POST /reports`, `POST /blocks/{user_id}` | user | Trust & safety |
| `GET /unsubscribe/{token}`, `GET /reconfirm/{token}` | public (token) | One-click email links |
| `GET /me/export`, `DELETE /me` | user | GDPR |
| `GET /admin/reports`, `PATCH /admin/listings/{id}` | admin | Moderation |

OpenAPI docs are generated automatically by FastAPI. Every endpoint: Pydantic validation (already the
data layer), auth dependency, ownership check (`auth.require_listing_owner`), rate limit.

## 7. Matching algorithm improvements (implemented)

**Hard constraints** (exclude): unwanted type of place · total cost > budget × 1.15 · commute > max × 1.25 ·
*shared rooms/beds only:* the flatmates' gender request, or the tenant's own flatmate-gender preference.

**Soft weighted criteria** (0–100 each, weights in `data/scoring.json`, normalised):

| Criterion | Default weight | How it is scored |
|---|---:|---|
| Budget | 30 | curve on cost/budget: ≤80% → 100, 100% → 75, 110% → 30 |
| Commute | 30 | curve on minutes/max: 40-min max → 10–15 min 100, 25 min 85, 35 min 60, 40 min 45 |
| Move-in | 10 | days available after your date: 0 → 100, 14 → 80, 31 → 40, 60 → 0 |
| Size / rooms | 8 | apartments only |
| Furnishing | 8 | unfurnished when needed → 30, unknown → 60 |
| Pets | 6 | not allowed → 20 (moderate, not exclusion), unknown → 60 |
| Smoking | 4 | not allowed → 30, unknown → 75 |
| Language | 4 | flatmates' requested language not shared → 25 |
| Amenities | 6 | share of wanted amenities present |

* Criteria that don't apply (no pet, non-smoker, no amenity wishes) are **left out of the average**
  instead of counting as 100% – this was inflating every score.
* **Commute categories** Near (≤45% of max) / Average (≤80%) / Far (≤100%) / Too far.
* **Travel modes:** walking, cycling, public transport (direct line + stops).
* **Decision:** ≥85 match (instant email) · ≥65 maybe · lower or hard fail → excluded (still visible with reason).
* **Explainability:** every evaluation stores per-criterion scores, “why it fits”, “what lowers it”,
  “missing information” and the agent trace.
* **Personalisation:** 👎 with a reason multiplies that criterion's weight for this user by 1.15 (capped ×2);
  resettable.
* **Two-sided:** landlords see tenants scored with the *tenant's* preferences by the same engine.

**Nationality (1C):** not collected, not stored, not scored. Neutral alternatives: *language* (practical
for shared living, scored softly) and *“can provide the documents a lease needs”* (a yes/no shown to
landlords, never scored, never asks which documents). If a landlord legally needs residence documents for
a contract, that check belongs at contract time, offline, not in a public matching filter.

**Next:** learning-to-rank from feedback + contact/viewing outcomes once there is enough data (≥ a few
thousand labelled interactions); until then the transparent weighted model is the better choice.

## 8. Data acquisition strategy

### A. Official APIs / feeds
* **ingatlan.com** (largest Hungarian portal) offers listing management and XML import *for advertisers*,
  but no public API for third parties to pull listings. Its group's terms (money.hu, operated by
  ingatlan.com Zrt.) prohibit business use of the portal content, including data collection, without
  written permission. **ingatlan.com does license anonymised data to partners** (e.g. the KSH rent index), so
  a **partnership / licensed feed** is the realistic route. Contact their partner team.
* **Jófogás / alberlet.hu / other portals:** same approach – ask for a partner feed. Unofficial
  third-party scrapers exist (e.g. on Apify) but are unaffiliated and do not grant rights.
* **Agencies and student-housing operators:** many publish XML feeds for portals; ask them to point the
  same feed at Flat-Watch (zero marginal cost for them). Common formats: portal-specific XML, Kyero-style XML.
* **University / dormitory offices:** official, trusted source of student rooms – high value for trust.

### B. Web scraping – recommendation: **don't build the business on it**
* **Legal:** ingatlan.com has litigated against a company that reused its database (Reálmonitor). A
  Budapest appeal court ruled in Reálmonitor's favour in December 2025, but the dispute was still
  ongoing, and ingatlan.com's position is that copying its database without consent is unlawful. EU
  *sui generis* database rights, website terms and GDPR (listings contain personal data: names, phone
  numbers) all apply. Expect cease-and-desist letters.
* **Technical:** Scrapy (static pages) or Playwright (JS pages) work, but break whenever markup
  changes, and must never bypass CAPTCHAs, logins or rate limits.
* **If used at all:** only for *link-out discovery* with explicit permission, respecting robots.txt,
  ≤1 request/s, storing only non-personal facts plus the link, and deleting on request.

### C. Landlord portal – primary source (implemented)
Direct submissions with photos, structured fields (no parsing errors), owner accountability, reconfirmation.
Grow it with: free listing for private landlords, “import from my ingatlan.com ad” helper (landlord pastes
their own ad – they own the content), student-union partnerships.

### D. Normalisation (implemented)
All sources map to one `RawListing`/`listings` schema; the extractor fills gaps from Hungarian text
(“szoba kiadó”, “rezsi”, “kaució”, “lánynak”…), structured fields from the portal override guesses.

### E. Freshness (implemented)
* Reconfirmation email after 14 days (`REVERIFY_DAYS`), one-click “still available” link,
  auto-expire after 30 days (`EXPIRE_DAYS`), “Rented – hide it” button.
* Duplicate detection by fingerprint (area + size + rounded price + title words); duplicates are hidden
  but linked. Next: trigram similarity + perceptual photo hashes.
* Price history on every change (shown to tenants).
* Near-real-time without extra infrastructure: landlord posts are matched on the next worker cycle
  (30 s); partner feeds polled every 15–60 min; webhooks where partners support them.

## 9. Authentication and messaging architecture

**Implemented (prototype):** scrypt password hashes (stdlib, no extra dependency), 6-digit email codes
(hashed, 15-min expiry), account lockout after 5 failures, per-email rate limits, generic login errors,
timing-equalised unknown-user path, no account enumeration on reset, all sessions revoked after a
reset, RFC 6238 TOTP 2FA, server-side session table (token hashes only), role checks + ownership
checks, audit log, GDPR export/delete.

**Production additions:** argon2id (`argon2-cffi`), HttpOnly + Secure + SameSite cookies, CSRF tokens on
state-changing requests, `Content-Security-Policy`, HIBP k-anonymity password check, WebAuthn/passkeys
(better than TOTP for most users), encrypted TOTP secrets (KMS), device/session list with revoke.

**Messaging:** conversations are bound to one listing; only participants can read; contact details hidden;
links shown as non-clickable text with risk labels; advance-payment phrases trigger a warning; 30
messages / 10 min and 15 new conversations / day limits; report + block; new-message emails contain no
message text. Real-time: FastAPI WebSocket + Redis pub/sub (≈ no extra cost) – or a managed service
(Stream, Ably, Pusher) if the team prefers not to run it.

**End-to-end encryption – recommendation: not now.** E2EE would stop the platform from (a) detecting scam
messages (“send the deposit via Western Union”), (b) acting on abuse reports with evidence, and
(c) recovering history when a user changes device – while the threat it addresses (the operator reading
messages) is better handled by TLS in transit, encryption at rest, strict staff access with audit logs,
and short retention (e.g. delete conversations 12 months after the last message). If it is ever required,
use a proven protocol (Signal protocol via libsignal / Matrix Olm-Megolm) with **user-initiated
reporting** that discloses the reported messages – never a homemade scheme.

## 10. Email notification architecture (implemented)

```
saved search ──► run_initial() ──► "first matches" email
                     │
worker (every 30 s) ─┼─► run_new_listings(): new listing × active search → score
                     │        ≥ instant threshold & frequency=instant → instant email
                     │        otherwise → notified(channel='digest')
                     ├─► send_digests(): daily / weekly round-ups
                     ├─► freshness(): reminders / expiry
                     └─► process_queue(): SMTP (or data/outbox in dev), 3 retries
```
* **Dedupe:** `notified` primary key (search × listing) + `email_queue.dedupe_key`.
* **Content:** photo, price incl. utilities, area, specs, match %, reasons, link to the listing,
  scam-safety footer, one-click unsubscribe + manage link.
* **Controls:** per-search pause/resume, frequency (instant/daily/weekly/off), instant threshold,
  global opt-out.
* **Production:** a transactional email provider (Brevo / Postmark / Amazon SES – all have low-cost or free
  starter tiers; verify current pricing), SPF + DKIM + DMARC on the domain, `List-Unsubscribe` header,
  bounce/complaint webhooks → auto opt-out.

## 11. Implementation roadmap

### Must-have (before any public launch) – ~4–6 weeks for 2 developers
| Feature | Purpose | Integration | Tech | Complexity | Depends on | Cost |
|---|---|---|---|---|---|---|
| FastAPI backend + secure cookies + CSRF | Close L1, enable mobile/web frontend | Wrap `flatwatch/*` | FastAPI, Uvicorn | M | – | €0 |
| PostgreSQL + PostGIS migration | Concurrency, geo queries | Replace `Database` | Postgres 16, Alembic | M | API | €0–25/mo |
| Real SMTP + domain auth | Deliverability | `mailer.py` already pluggable | Brevo/Postmark/SES | S | domain | €0–15/mo |
| Photo storage on R2/S3 + CDN | Durability, speed | `photos.py` save/serve | boto3, Cloudflare | S | – | ≈ €0–5/mo |
| Admin moderation (reports, listings) | Act on scams fast | `reports` table exists | API + simple UI | S | API | €0 |
| Privacy policy, ToS, DPA, retention jobs | GDPR | `audit_log`, delete/export exist | legal review | S | lawyer | one-off |
| Landlord-portal growth + 1 partner feed | Supply of listings | `listings.upsert` | XML/CSV importer | M | partner deal | depends |

### High priority – next 1–3 months
* Real-time chat over WebSockets (M) · Timetable-accurate commute via OpenTripPlanner + DKV GTFS or Google
  Routes, cached (M, €0–50/mo) · Landlord identity verification via a KYC provider (Veriff, Sumsub, Onfido…;
  roughly €1–3 per check – verify) and a “verified landlord” badge (M) · Viewing scheduling with calendar
  slots (M) · Application summary a tenant can share with one click (S) · Multilingual UI HU/EN (S–M) ·
  Perceptual-hash photo duplicate/scam detection (S) · Monitoring: Sentry + uptime + structured logs (S, €0–26/mo).

### Future enhancements – evaluate with data
* Floor plans (cheap – already a photo category), video walkthroughs (S, storage cost), **360° tours (L –
  low priority, little value for student rooms)**.
* Rental price history & market comparison per district (M – needs data volume).
* AI assistant to refine preferences (M; LLM cost) – only after the basic flow converts well.
* **Ratings for tenants and landlords – high risk:** retaliation, fake reviews, defamation and GDPR
  issues. If done: only after a verified, completed tenancy, mutual blind submission, moderation,
  right of reply. Recommend *not* building in year one.
* Property-ownership verification (land registry extract via TAKARNET is paid and restricted) – manual
  check for flagged listings first.
* Learning-to-rank once there is enough interaction data.

### Things deliberately *not* recommended
* Microservices, Kubernetes – unnecessary at this scale.
* E2EE chat (see §9).
* Scraping as the core data source (see §8B).
* Any nationality-based filter, even “soft”.

## 12. Product positioning check

**Secure:** verified emails, 2FA, private chat, hidden contacts, scam warnings, reports/blocks, fresh
listings, no protected-characteristic filters. *Missing for the promise to be fully true:* verified
landlords (KYC) and production hosting (L1, L3).

**Time-saving:** one profile → ranked, explained matches; searches keep running and email you; one-click
first message; landlords see only compatible tenants. *Missing:* viewing scheduling and timetable-accurate
commutes.

**Biggest business risk:** listing supply. Matching quality is worthless without listings; the landlord
portal plus 1–2 partner feeds must come before any advanced feature.

## 13. Test results

```
python -m pytest -q
..................................................                       [100%]
50 passed
```
Covered: commute curve and categories, soft vs hard constraints, configurable weights, feedback
personalisation, no-nationality guarantee, gender only for shared living, explanations, decisions on all
sample listings, Hungarian extraction, password hashing, TOTP, markdown/HTML escaping, link and payment
flags, registration → verification → login → lockout → reset, 2FA login, RBAC, GDPR export/delete, chat
privacy/unread/block/rate limit, email without message content, photo validation/resizing/EXIF stripping,
continuous discovery with dedupe and unsubscribe, instant alert on a new listing, freshness
reminders/expiry, price history, duplicates, landlord-side matching and invitations.
In addition, every page of the app (tenant and landlord) and the main click paths were executed in a
headless smoke test. The real browser UI must still be checked by hand (see README checklist).
