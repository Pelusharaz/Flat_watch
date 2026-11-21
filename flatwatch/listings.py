"""Listing repository: one normalised table for every source (landlord portal, pasted
site listings, partner feeds, demo samples) + duplicate detection, price history and freshness."""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from .commute import locate
from .config import settings
from .extract import extract_details, strip_accents
from .models import RawListing
from .security import new_token
from .sources.simulator import _read

DEMO_HELD_BACK = 6     # the last N sample listings are released over time by the worker (demo of new arrivals)


def fingerprint(l: RawListing) -> str:
    """Same flat posted twice (other site / repost): area + size + rounded price + title words."""
    words = sorted(set(re.findall(r"[a-z]{4,}", strip_accents(l.title.lower()))))[:6]
    return f"{strip_accents(l.location.lower()).split(',')[1].strip() if ',' in l.location else l.location.lower()}" \
           f"|{l.size_m2 or 0}|{round(l.price_huf, -4)}|{' '.join(words)}"


def _row_listing(row) -> RawListing:
    l = RawListing.model_validate_json(row["payload"])
    l.sample = bool(row["sample"])
    return l


def upsert(db, l: RawListing, owner_id: Optional[int] = None, status: str = "active") -> str:
    """Insert or update. Records price changes and marks duplicates. Returns the listing id."""
    now = time.time()
    d, _ = extract_details(l)
    point, _ = locate(l.location, d.neighborhood)
    fp = fingerprint(l)
    old = db.one("SELECT * FROM listings WHERE id=?", (l.id,))
    payload = l.model_dump_json()
    if old:
        if old["price_huf"] != l.price_huf:
            db.execute("INSERT INTO price_history(listing_id, price_huf, changed_at) VALUES(?,?,?)",
                       (l.id, l.price_huf, now))
        db.execute("UPDATE listings SET payload=?, price_huf=?, offer_type=?, fingerprint=?, lat=?, lon=?, "
                   "updated_at=?, last_verified_at=? WHERE id=?",
                   (payload, l.price_huf, d.offer_type, fp, point and point[0], point and point[1], now, now, l.id))
        return l.id
    dup = db.one("SELECT id FROM listings WHERE fingerprint=? AND status='active' AND id<>?", (fp, l.id))
    db.execute(
        "INSERT INTO listings(id, owner_id, source, status, sample, payload, price_huf, offer_type, fingerprint, "
        "duplicate_of, lat, lon, created_at, updated_at, last_verified_at, reconfirm_token) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (l.id, owner_id, l.source, status, int(l.sample), payload, l.price_huf, d.offer_type, fp,
         dup["id"] if dup else None, point and point[0], point and point[1], now, now, now, new_token()))
    db.execute("INSERT INTO price_history(listing_id, price_huf, changed_at) VALUES(?,?,?)", (l.id, l.price_huf, now))
    return l.id


def get(db, listing_id: str) -> Optional[RawListing]:
    row = db.one("SELECT * FROM listings WHERE id=?", (listing_id,))
    return _row_listing(row) if row else None


def row(db, listing_id: str):
    return db.one("SELECT * FROM listings WHERE id=?", (listing_id,))


def active(db, since: Optional[float] = None) -> list[RawListing]:
    sql = "SELECT * FROM listings WHERE status='active' AND duplicate_of IS NULL"
    params: tuple = ()
    if since:
        sql += " AND created_at > ?"
        params = (since,)
    return [_row_listing(r) for r in db.query(sql + " ORDER BY created_at DESC", params)]


def owned_by(db, user_id: int) -> list:
    return db.query("SELECT * FROM listings WHERE owner_id=? AND status<>'removed' ORDER BY created_at DESC", (user_id,))


def set_status(db, listing_id: str, status: str) -> None:
    db.execute("UPDATE listings SET status=?, updated_at=? WHERE id=?", (status, time.time(), listing_id))


def reconfirm(db, listing_id: Optional[str] = None, token: Optional[str] = None) -> bool:
    """Landlord confirms the place is still available (button or emailed link)."""
    r = db.one("SELECT id FROM listings WHERE id=? OR (reconfirm_token IS NOT NULL AND reconfirm_token=?)",
               (listing_id or "", token or "-"))
    if not r:
        return False
    db.execute("UPDATE listings SET last_verified_at=?, reminder_sent_at=NULL, status='active', reconfirm_token=? "
               "WHERE id=?", (time.time(), new_token(), r["id"]))
    return True


def price_history(db, listing_id: str) -> list:
    return db.query("SELECT price_huf, changed_at FROM price_history WHERE listing_id=? ORDER BY changed_at",
                    (listing_id,))


def seed_samples(db) -> None:
    """First run: load the sample listings. The last few are held back ('queued') and released by
    the worker one by one, to demo continuous discovery + email alerts."""
    if db.kv_get("seeded") or not settings.include_samples:
        return
    samples = _read(settings.listings_file, sample=True)
    for i, l in enumerate(samples):
        upsert(db, l, status="queued" if i >= len(samples) - DEMO_HELD_BACK else "active")
    # real listings pasted with the previous version of the app
    for l in _read(settings.my_listings_file, sample=False):
        upsert(db, l)
    db.kv_set("seeded", True)


def release_next_queued(db) -> Optional[str]:
    r = db.one("SELECT id FROM listings WHERE status='queued' ORDER BY rowid LIMIT 1")
    if not r:
        return None
    now = time.time()
    db.execute("UPDATE listings SET status='active', created_at=?, updated_at=?, last_verified_at=? WHERE id=?",
               (now, now, now, r["id"]))
    return r["id"]


def stats(db) -> dict:
    out = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) AS n FROM listings GROUP BY status")}
    out["duplicates"] = db.one("SELECT COUNT(*) AS n FROM listings WHERE duplicate_of IS NOT NULL")["n"]
    return out


def median_per_m2(db) -> Optional[float]:
    from .scam import market_median_per_m2
    return market_median_per_m2(_row_listing(r) for r in db.query("SELECT * FROM listings WHERE sample=1 OR status='active'"))


def publish(db, l: RawListing, owner_id: int) -> tuple[Optional[str], str]:
    """Landlord portal save: anti-spam limits + content check. Returns (status, message).
    status None = rejected; 'review' = held for moderation; 'active' = live."""
    from . import antispam
    from .scam import assess
    is_new = row(db, l.id) is None
    if is_new:
        err = antispam.listing_post_problem(db, owner_id, fingerprint(l), l.id)
        if err:
            return None, err
    d, _ = extract_details(l)
    scam, _ = assess(l, d, [x for x in active(db) if x.id != l.id], median_per_m2(db))
    reason = antispam.listing_review_reason(l, scam)
    upsert(db, l, owner_id=owner_id, status="review" if reason else "active")
    if reason:
        db.execute("UPDATE listings SET status='review', review_reason=? WHERE id=?", (reason, l.id))
        return "review", f"Held for a quick review before it goes live: {reason}."
    if not is_new:
        db.execute("UPDATE listings SET status='active', review_reason=NULL WHERE id=? AND status='review'", (l.id,))
    return "active", "Live! Tenants with a matching saved search will be emailed automatically."
