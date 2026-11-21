# 🏠 Flat-Watch Debrecen

**Secure, time-saving rentals in Debrecen – whole apartments, private rooms and bed spaces – for tenants and landlords.**

An agent checks every place against your profile: real monthly cost, walk / bike / tram-bus time to your
campus (which line, which stop), scam signs, and whether you and the flatmates fit each other. Every score is
explained. Saved searches keep running and email you when a new place fits. Landlords (owners, or tenants
subletting a room or bed) post once with photos and see only tenants who fit. Everyone talks through a
private in-app chat.

📄 Technical assessment & roadmap: [`docs/TECHNICAL_ASSESSMENT.md`](docs/TECHNICAL_ASSESSMENT.md) ·
🗄 Production schema: [`docs/schema_postgres.sql`](docs/schema_postgres.sql)

---

## Run it (Windows PowerShell)

```powershell
cd path\to\flat-watch
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m pytest -q                  # 50 tests should pass
python -m streamlit run app.py
```
Optional, once: `python tools\fetch_osm_stops.py` for every Debrecen bus/tram/trolleybus line (needs internet).

> **Upgrading?** Your existing `flatwatch.db` is migrated automatically. To see the new sample photos,
> demo landlords and demo chats, delete `flatwatch.db` (and the `static\photos` folder) once and restart.

## Demo accounts – password `Debrecen-demo-2026`

| Account | Role | What to try |
|---|---|---|
| `anna@demo.flatwatch.hu` | Tenant | Home → Matches (photos, *Details*, score breakdown) → Shortlist & compare → Messages (2 landlords + 1 flatmate chat) → Flatmates → **📬 Test inbox → ✨ Simulate a new matching listing** |
| `gabor@demo.flatwatch.hu` | Owner (3 places) | Dashboard → reply to Anna → My listings (photos, price, reconfirm) → Compatible tenants → invite |
| `eva@…`, `peter@…` | Owners | More listings to chat about |
| `yasmin@…` | Tenant subletting a room | Room in a girls' flat – see who fits |
| `rahul@…` | Tenant subletting a bed | Bed space for a male flatmate |
| `admin@demo.flatwatch.hu` | Moderator | Review queue, reports, bans |

**Real-time chat demo:** open the app in a normal and a private browser window, log in as Anna in one and
Gábor in the other, and chat – new messages appear within ~3 seconds with ✓ sent / ✓✓ read.

## 📬 The test mail server (email alerts without an email account)

The app starts a small **built-in SMTP server** on `localhost:1025` (`flatwatch/mailserver.py`). The platform
sends every email over real SMTP to it, and the **📬 Test inbox** page shows them exactly as a mail client would –
nothing leaves your computer.

**See an alert arrive:**
1. Log in as Anna → **🎯 Search & alerts → Saved searches**. Anna already has an active search, and her
   *first matches* email is in the inbox.
2. Go to **📬 Test inbox → ✨ Simulate a new matching listing**: a new place that fits her is published, the
   matching engine runs, and a **“100% match: …”** instant alert appears in the inbox seconds later.
3. Without doing anything, the background worker releases a new sample listing every 30 s. Good matches
   trigger instant alerts, and the rest wait for the daily digest.

Verification and password-reset codes also land in this inbox, which is visible on the login page in development.
**Production:** set `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` (Gmail app password, Brevo, Postmark, SES…) and
`LOCAL_MAILSERVER=0`, `DEV_SHOW_CODES=0` – see `.env.example`. Prefer a separate tool? Mailpit works too
(`docker run -p 8025:8025 -p 1025:1025 axllent/mailpit`).

## 🛡 How spam is filtered (`flatwatch/antispam.py` – all limits in one place)

| Layer | What stops it |
|---|---|
| **Fake sign-ups / bots** | Hidden honeypot field (bots fill it, humans can't see it) · forms submitted in under 2.5 s are rejected · disposable email domains (mailinator, yopmail…) blocked · sign-up rate limit · **email verification required before anything else** |
| **Search spam** | Max 5 saved searches per user · max 3 new searches per hour · identical searches rejected · “check now” limited to 6/hour · **max 10 instant alert emails per user per day** (the rest go to the digest) · outside-listing checks limited to 10/day · every pair of search and listing is emailed at most once |
| **Spam & scam listings** | Max 10 active listings and 5 new posts/day per landlord · re-posting the same place is rejected · on posting, the scam detector + spam rules (phone numbers/links in the text, all-caps titles, advance-payment wording, owner abroad, far too cheap…) send the listing to a **moderation queue** instead of going live · listings are hidden automatically after **3 independent reports** · duplicates across sources are hidden |
| **Message spam** | 30 messages per 10 min · 15 new conversations per day · the **same text sent to many people is blocked** · risky links shown as non-clickable text, payment requests trigger a warning · report & block · new-message emails never include the text |
| **Accounts** | Lockout after 5 wrong passwords · rate-limited login/reset/verification · banned users are logged out everywhere and their listings removed |
| **Moderation** | `admin` role: review queue (approve / remove / ban owner) and open reports |

## What's inside

| Area | Highlights |
|---|---|
| **UI** | Multi-page navigation, dashboards for tenants and landlords, photo-first listing cards with a match ring, details pop-up with photo gallery, score bars, chat bubbles, mobile-friendly layout |
| **Matching** | Configurable weights (`data/scoring.json`), commute *Near / Average / Far*, walk/bike/transit, soft pet & smoking penalties, explanations, learning from 👍/👎; nationality never used; gender only for shared rooms/beds |
| **Discovery** | Saved searches, first-matches email, instant alerts, daily/weekly digests, no duplicates, pause/edit/unsubscribe, background worker |
| **Chat** | Tenant ↔ landlord per listing, **tenant ↔ tenant for flatmate matches**, landlord invitations, unread badges, read receipts, safety warnings |
| **Photos** | Upload pipeline (validation, EXIF/GPS removal, resize, thumbnails), categories, ordering, gallery; sample listings use bundled interior illustrations (`tools/render_sample_photos.py`) |
| **Accounts** | Roles, email verification, scrypt hashing, lockout, reset, TOTP 2FA, sessions, GDPR export/delete |

## About the photos
Sample listings show **illustrations** (labelled “Illustration”) generated by `tools/render_sample_photos.py` –
we can't legally reuse photos of real flats from other websites. Real listings show the landlord's own
uploaded photos: log in as a landlord → **My listings → Photos → Add photos**.

## Configuration (`.env`)
| Setting | Default | Meaning |
|---|---|---|
| `SMTP_HOST` / `SMTP_PORT` / `LOCAL_MAILSERVER` | `localhost` / `1025` / `1` | Built-in test mail server. Production: your provider, `LOCAL_MAILSERVER=0` |
| `DEV_SHOW_CODES` | `1` | Show verification codes on screen. **0 in production** |
| `INCLUDE_SAMPLES` | `1` | Sample listings, photos, demo accounts. **0 in production** |
| `WORKER_IN_APP` / `WORKER_INTERVAL_S` | `1` / `30` | Background search inside the app (or run `python worker.py`) |
| `DEMO_FEED` | `1` | Release held-back sample listings over time |
| `LLM_PROVIDER` | `mock` | `anthropic` / `openai` (compatible) for smarter text reading; works fully without |
| `REVERIFY_DAYS` / `EXPIRE_DAYS` | `14` / `30` | Landlord reconfirmation reminder / auto-expiry |

## Project layout
```
app.py                    Streamlit UI (navigation, tenant / landlord / admin pages)
worker.py                 Standalone background worker
flatwatch/  ui_style.py   Design system (CSS + components)    antispam.py   Spam & abuse limits
            mailserver.py Built-in test SMTP server            devtools.py   “Simulate a new listing”
            scoring.py, agent.py, searches.py, listings.py, messaging.py, auth.py, security.py, photos.py,
            emails.py, mailer.py, tenants.py, flatmates.py, commute.py, transit.py, extract.py, scam.py, db.py …
data/       scoring.json, sample listings & profiles, sample_photos/, districts, campuses, transit stops
docs/       TECHNICAL_ASSESSMENT.md, schema_postgres.sql
tools/      render_sample_photos.py, mailserver.py, fetch_osm_stops.py
tests/      50 automated tests
```

## Manual checklist (things automated tests can't click)
- [ ] Sign up a new account → code in 📬 Test inbox → verify → log in
- [ ] Two browser windows: chat Anna ↔ Gábor, check ✓✓ read and the unread badge
- [ ] As a landlord, upload a photo from your phone → visible in the tenant's gallery
- [ ] ✨ Simulate a new matching listing → instant alert in the inbox → its link opens the listing
