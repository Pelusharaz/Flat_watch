"""Spam & abuse protection – every limit in one place.

Layers
  1. Sign-up bots      : honeypot field, minimum form-fill time, disposable-email block,
                         global sign-up rate limit, email verification before any action.
  2. Search spam       : max saved searches per user, max new searches per hour, identical
                         searches rejected, "check now" throttled, max instant emails per day
                         (the rest go to the digest), pasted outside listings limited per day.
  3. Listing spam/scams: max active listings + new posts per day per landlord, duplicate of
                         your own listing rejected, scam/spam content check on posting -> high
                         risk goes to a moderation queue ('review') instead of going live,
                         listings auto-hidden after 3 independent reports.
  4. Message spam      : messages per 10 min, new conversations per day, the same text sent to
                         many people is blocked, payment/link warnings shown to the receiver.
  5. Moderation        : admins see reports and the review queue; can approve, remove, ban.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Optional

from .security import rate_limited

LIMITS = {
    "saved_searches_max": 5,
    "new_searches_per_hour": 3,
    "check_now_per_hour": 6,
    "instant_emails_per_day": 10,
    "outside_listings_per_day": 10,
    "active_listings_max": 10,
    "new_listings_per_day": 5,
    "reports_to_hide": 3,
    "same_message_per_hour": 5,
    "min_form_seconds": 2.5,
}

DISPOSABLE_DOMAINS = {
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "sharklasers.com", "tempmail.com",
    "temp-mail.org", "yopmail.com", "trashmail.com", "getnada.com", "dispostable.com", "maildrop.cc",
    "throwawaymail.com", "fakeinbox.com", "mintemail.com", "mohmal.com", "emailondeck.com",
    "tempmailo.com", "burnermail.io", "spamgourmet.com", "mailnesia.com",
}


# ------------------------------------------------------------------ sign-up --
def signup_problem(email: str, honeypot: str = "", form_seconds: Optional[float] = None) -> Optional[str]:
    if honeypot:
        return "Sign-up failed."                                  # bots fill the hidden field
    if form_seconds is not None and form_seconds < LIMITS["min_form_seconds"]:
        return "That was very fast – please try again."           # bots submit instantly
    domain = (email or "").split("@")[-1].lower().strip()
    if domain in DISPOSABLE_DOMAINS:
        return "Please use a permanent email address (temporary inboxes are not accepted)."
    return None


# ------------------------------------------------------------------ searches --
def criteria_hash(criteria_json: str) -> str:
    """Hash of what a search actually looks for (ignores ids and learned weights)."""
    import json
    d = json.loads(criteria_json)
    for k in ("profile_id", "weights_override", "user_name", "about", "publish", "documents_ready"):
        d.pop(k, None)
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


def can_create_search(db, user_id: int, criteria_json: str) -> Optional[str]:
    n = db.one("SELECT COUNT(*) n FROM saved_searches WHERE user_id=?", (user_id,))["n"]
    if n >= LIMITS["saved_searches_max"]:
        return f"You can have up to {LIMITS['saved_searches_max']} saved searches – delete or edit one."
    for r in db.query("SELECT criteria FROM saved_searches WHERE user_id=?", (user_id,)):
        if criteria_hash(r["criteria"]) == criteria_hash(criteria_json):
            return "You already have a search with exactly these settings."
    if rate_limited(db, f"newsearch:{user_id}", LIMITS["new_searches_per_hour"], 3600):
        return "Too many new searches – try again in an hour."
    return None


def can_check_now(db, user_id: int) -> bool:
    return not rate_limited(db, f"checknow:{user_id}", LIMITS["check_now_per_hour"], 3600)


def instant_email_allowed(db, user_id: int) -> bool:
    """Max N instant alerts per user per day; extra matches wait for the digest."""
    return not rate_limited(db, f"instant:{user_id}", LIMITS["instant_emails_per_day"], 86_400)


def can_paste_outside(db, user_id: int) -> bool:
    return not rate_limited(db, f"outside:{user_id}", LIMITS["outside_listings_per_day"], 86_400)


# ------------------------------------------------------------------ listings --
SPAMMY = re.compile(r"(https?://|www\.|bit\.ly|t\.me/|wa\.me/|telegram|whatsapp|@[a-z0-9-]+\.(com|hu)|"
                    r"\+?\d[\d\s/-]{8,}\d)", re.I)


def listing_post_problem(db, user_id: int, fingerprint: str, listing_id: str) -> Optional[str]:
    """Hard limits checked BEFORE saving a new listing."""
    active = db.one("SELECT COUNT(*) n FROM listings WHERE owner_id=? AND status IN ('active','review')", (user_id,))["n"]
    if active >= LIMITS["active_listings_max"]:
        return f"You can have up to {LIMITS['active_listings_max']} active listings."
    if db.one("SELECT 1 FROM listings WHERE owner_id=? AND fingerprint=? AND id<>? AND status IN ('active','review')",
              (user_id, fingerprint, listing_id)):
        return "You've already posted this place – edit the existing listing instead."
    if rate_limited(db, f"newlisting:{user_id}", LIMITS["new_listings_per_day"], 86_400):
        return "Daily posting limit reached – try again tomorrow."
    return None


def listing_review_reason(listing, scam_report) -> Optional[str]:
    """Content check AFTER extraction: returns a reason to hold the listing for moderation."""
    text = f"{listing.title} {listing.description}"
    reasons = []
    if scam_report.level == "high":
        reasons.append("scam signals: " + "; ".join(f.detail for f in scam_report.flags[:3]))
    if SPAMMY.search(text):
        reasons.append("contact details or links in the text (use the in-app chat)")
    letters = [c for c in listing.title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7 and len(letters) > 12:
        reasons.append("title in capital letters")
    return "; ".join(reasons) or None


def auto_hide_reported(db, listing_id: str) -> bool:
    n = db.one("SELECT COUNT(DISTINCT reporter_id) n FROM reports WHERE listing_id=? AND status='open'",
               (listing_id,))["n"]
    if n >= LIMITS["reports_to_hide"]:
        db.execute("UPDATE listings SET status='review', review_reason=? WHERE id=? AND status='active'",
                   (f"{n} user reports", listing_id))
        return True
    return False


# ------------------------------------------------------------------ messages --
def duplicate_message(db, user_id: int, body: str) -> bool:
    """The same text sent to many conversations = spam."""
    h = hashlib.sha1(re.sub(r"\s+", " ", body.strip().lower()).encode()).hexdigest()[:16]
    return rate_limited(db, f"msgbody:{user_id}:{h}", LIMITS["same_message_per_hour"], 3600)


# --------------------------------------------------------------- moderation --
def review_queue(db) -> list:
    return db.query("SELECT * FROM listings WHERE status='review' ORDER BY updated_at DESC")


def open_reports(db) -> list:
    return db.query("SELECT r.*, u.name AS reporter, t.name AS target FROM reports r "
                    "LEFT JOIN users u ON u.id=r.reporter_id LEFT JOIN users t ON t.id=r.target_user_id "
                    "WHERE r.status='open' ORDER BY r.created_at DESC")


def approve_listing(db, listing_id: str) -> None:
    db.execute("UPDATE listings SET status='active', review_reason=NULL, updated_at=? WHERE id=?",
               (time.time(), listing_id))


def remove_listing(db, listing_id: str) -> None:
    db.execute("UPDATE listings SET status='removed', updated_at=? WHERE id=?", (time.time(), listing_id))


def close_report(db, report_id: int) -> None:
    db.execute("UPDATE reports SET status='closed' WHERE id=?", (report_id,))


def ban_user(db, user_id: int) -> None:
    db.execute("UPDATE users SET banned=1 WHERE id=?", (user_id,))
    db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    db.execute("UPDATE listings SET status='removed' WHERE owner_id=?", (user_id,))
