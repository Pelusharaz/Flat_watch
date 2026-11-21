"""Tenant profiles, saved searches and continuous discovery with email notifications.

Flow:
  1. Tenant saves a search -> run_initial(): rank all active listings, email the best ("first" email).
  2. The search stays active. The worker calls run_new_listings() every cycle:
     new listings are scored against every active search; each (search, listing) pair is
     recorded in `notified`, so nobody gets the same place twice.
     - frequency 'instant' and score >= instant_threshold -> email now
     - otherwise the match waits for the daily / weekly digest
  3. send_digests() sends due daily / weekly summaries.
  4. Tenants can pause, edit, change frequency or unsubscribe (one-click link in every email).
"""
from __future__ import annotations

import time
from typing import Optional

from . import emails, listings, mailer
from .agent import evaluate
from .models import Criteria, Evaluation, profile_from_criteria
from .photos import first_thumb

DIGEST_EVERY = {"daily": 86_400, "weekly": 7 * 86_400}


# ----------------------------------------------------------- tenant profile --
def get_profile(db, user_id: int) -> Criteria:
    r = db.one("SELECT criteria FROM tenant_profiles WHERE user_id=?", (user_id,))
    try:
        return Criteria.model_validate_json(r["criteria"]) if r else Criteria(profile_id=f"user{user_id}")
    except Exception:  # noqa: BLE001
        return Criteria(profile_id=f"user{user_id}")


def save_profile(db, user_id: int, c: Criteria) -> None:
    c = c.model_copy(update={"profile_id": f"user{user_id}"})
    db.execute("INSERT OR REPLACE INTO tenant_profiles(user_id, criteria, published, updated_at) VALUES(?,?,?,?)",
               (user_id, c.model_dump_json(), int(c.publish), time.time()))


def published_profiles(db) -> list:
    out = []
    for r in db.query("SELECT tp.user_id, tp.criteria FROM tenant_profiles tp JOIN users u ON u.id=tp.user_id "
                      "WHERE tp.published=1 AND u.role='tenant'"):
        try:
            p = profile_from_criteria(Criteria.model_validate_json(r["criteria"]), user_id=r["user_id"])
            email = db.one("SELECT email FROM users WHERE id=?", (r["user_id"],))["email"]
            p.sample = email.endswith("@sample.flatwatch.hu")
            out.append(p)
        except Exception:  # noqa: BLE001
            continue
    return out


# ------------------------------------------------------------ saved searches --
def create_search(db, user_id: int, name: str, c: Criteria, frequency: str = "instant",
                  instant_threshold: int = 85, min_score: int = 65) -> int:
    cur = db.execute(
        "INSERT INTO saved_searches(user_id, name, criteria, frequency, instant_threshold, min_score, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (user_id, name[:80] or "My search", c.model_dump_json(), frequency, instant_threshold, min_score, time.time()))
    return cur.lastrowid


def create_search_checked(db, user_id: int, name: str, c: Criteria, frequency: str = "instant",
                          instant_threshold: int = 85, min_score: int = 65) -> tuple[Optional[int], str]:
    from .antispam import can_create_search
    err = can_create_search(db, user_id, c.model_dump_json())
    if err:
        return None, err
    return create_search(db, user_id, name, c, frequency, instant_threshold, min_score), ""


def update_search(db, user_id: int, search_id: int, **fields) -> None:
    allowed = {"name", "criteria", "active", "frequency", "instant_threshold", "min_score"}
    for k, v in fields.items():
        if k in allowed:
            if k == "criteria" and isinstance(v, Criteria):
                v = v.model_dump_json()
            db.execute(f"UPDATE saved_searches SET {k}=? WHERE id=? AND user_id=?", (v, search_id, user_id))  # k whitelisted


def delete_search(db, user_id: int, search_id: int) -> None:
    db.execute("DELETE FROM saved_searches WHERE id=? AND user_id=?", (search_id, user_id))


def user_searches(db, user_id: int) -> list:
    return db.query("SELECT * FROM saved_searches WHERE user_id=? ORDER BY id", (user_id,))


def unsubscribe(db, token: str) -> bool:
    r = db.one("SELECT id FROM users WHERE unsub_token=?", (token,))
    if not r:
        return False
    db.execute("UPDATE users SET email_opt_out=1 WHERE id=?", (r["id"],))
    db.execute("UPDATE saved_searches SET frequency='off' WHERE user_id=?", (r["id"],))
    return True


# ---------------------------------------------------------------- matching --
def rank(db, c: Criteria, candidates=None, min_score: int = 0, with_message: bool = False) -> list[Evaluation]:
    """Score listings for one tenant, best first (blocked scams and hard fails at the end)."""
    cands = candidates if candidates is not None else listings.active(db)
    med = listings.median_per_m2(db)
    history = listings.active(db)
    evs = [evaluate(l, c, [h for h in history if h.id != l.id], med, with_message=with_message) for l in cands]
    evs = [e for e in evs if e.match_score >= min_score or e.decision in ("skip", "blocked")]
    order = {"alert": 0, "maybe": 1, "skip": 2, "blocked": 3}
    return sorted(evs, key=lambda e: (order[e.decision], -e.match_score))


def _email_user(db, search, kind: str, evs: list[Evaluation]) -> bool:
    u = db.one("SELECT email, name, unsub_token, email_opt_out FROM users WHERE id=?", (search["user_id"],))
    if not u or u["email_opt_out"] or not evs:
        return False
    items = [(e, first_thumb(db, e.listing.id)) for e in evs[:6]]
    subject, html, text = emails.matches_email(u["name"], kind, items, search["name"], u["unsub_token"])
    key = f"{kind}:{search['id']}:" + ",".join(e.listing.id for e in evs[:6])
    return mailer.enqueue(db, u["email"], subject, html, text, dedupe_key=key)


def _record(db, search_id: int, ev: Evaluation, channel: str) -> bool:
    cur = db.execute("INSERT OR IGNORE INTO notified(search_id, listing_id, score, channel, created_at) VALUES(?,?,?,?,?)",
                     (search_id, ev.listing.id, ev.match_score, channel, time.time()))
    return cur.rowcount == 1


def run_initial(db, search_id: int) -> int:
    """Right after a search is saved: email the best current matches. Returns how many."""
    s = db.one("SELECT * FROM saved_searches WHERE id=?", (search_id,))
    c = Criteria.model_validate_json(s["criteria"])
    good = [e for e in rank(db, c) if e.decision in ("alert", "maybe") and e.match_score >= s["min_score"]]
    fresh = [e for e in good if _record(db, search_id, e, "first")]
    if fresh and s["frequency"] != "off":
        _email_user(db, s, "first", fresh)
    db.execute("UPDATE saved_searches SET last_run_at=? WHERE id=?", (time.time(), search_id))
    return len(fresh)


def run_new_listings(db) -> dict:
    """Score every listing not yet seen by each active search. Instant emails for top matches."""
    stats = {"searches": 0, "instant": 0, "queued_for_digest": 0}
    all_active = listings.active(db)
    for s in db.query("SELECT * FROM saved_searches WHERE active=1"):
        if s["last_run_at"] is None:                     # never ran: one "first matches" email, not a flood
            run_initial(db, s["id"])
            stats["searches"] += 1
            continue
        seen = {r["listing_id"] for r in db.query("SELECT listing_id FROM notified WHERE search_id=?", (s["id"],))}
        new = [l for l in all_active if l.id not in seen]
        db.execute("UPDATE saved_searches SET last_run_at=? WHERE id=?", (time.time(), s["id"]))
        if not new:
            continue
        stats["searches"] += 1
        c = Criteria.model_validate_json(s["criteria"])
        for e in rank(db, c, candidates=new):
            if e.decision not in ("alert", "maybe") or e.match_score < s["min_score"]:
                _record(db, s["id"], e, "none")          # seen, not good enough – never re-evaluated
                continue
            from .antispam import instant_email_allowed
            if (s["frequency"] == "instant" and e.match_score >= s["instant_threshold"]
                    and instant_email_allowed(db, s["user_id"])):
                if _record(db, s["id"], e, "instant"):
                    _email_user(db, s, "instant", [e])
                    stats["instant"] += 1
            else:
                if _record(db, s["id"], e, "digest"):
                    stats["queued_for_digest"] += 1
    return stats


def send_digests(db, now: Optional[float] = None) -> int:
    now = now or time.time()
    sent = 0
    for s in db.query("SELECT * FROM saved_searches WHERE active=1 AND frequency IN ('daily','weekly','instant')"):
        every = DIGEST_EVERY.get(s["frequency"], 86_400)      # instant searches get a daily round-up of the rest
        last = s["last_digest_at"] or s["created_at"]
        if now - last < every:
            continue
        rows = db.query("SELECT listing_id FROM notified WHERE search_id=? AND channel='digest' AND created_at>?",
                        (s["id"], last))
        db.execute("UPDATE saved_searches SET last_digest_at=? WHERE id=?", (now, s["id"]))
        ids = [r["listing_id"] for r in rows]
        cands = [l for l in (listings.get(db, i) for i in ids) if l]
        if not cands:
            continue
        c = Criteria.model_validate_json(s["criteria"])
        evs = [e for e in rank(db, c, candidates=cands) if e.decision in ("alert", "maybe")]
        if evs and _email_user(db, s, "daily" if s["frequency"] != "weekly" else "weekly", evs):
            sent += 1
    return sent
