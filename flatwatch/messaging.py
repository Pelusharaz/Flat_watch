"""Authenticated chat between tenants and landlords, tied to one listing.

- Only the two participants can read a conversation (checked on every call).
- Contact details are never shown; people talk here until THEY choose to share.
- Spam/scam protection: rate limits, link risk labels, payment-request warnings,
  report and block. New-message emails never include the message text.
"""
from __future__ import annotations

import time
from typing import Optional

from . import emails, mailer
from .auth import PermissionDenied, User
from .security import message_flags, rate_limited, sanitize_text


def _participant(db, conv_id: int, user: User):
    c = db.one("SELECT * FROM conversations WHERE id=?", (conv_id,))
    if not c or user.id not in (c["tenant_id"], c["landlord_id"]):
        raise PermissionDenied("Not your conversation.")
    return c


def is_blocked(db, a: int, b: int) -> bool:
    return db.one("SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
                  (a, b, b, a)) is not None


def start_conversation(db, tenant: User, listing_id: str) -> tuple[Optional[int], str]:
    if tenant.role != "tenant":
        return None, "Only tenants can start a conversation about a listing."
    l = db.one("SELECT owner_id, status FROM listings WHERE id=?", (listing_id,))
    if not l or not l["owner_id"]:
        return None, "This listing is from another website – contact the advertiser there."
    if l["status"] != "active":
        return None, "This listing is no longer available."
    if is_blocked(db, tenant.id, l["owner_id"]):
        return None, "You can't contact this landlord."
    existing = db.one("SELECT id FROM conversations WHERE listing_id=? AND tenant_id=?", (listing_id, tenant.id))
    if existing:
        return existing["id"], ""
    if rate_limited(db, f"newconv:{tenant.id}", limit=15, window_s=86_400):
        return None, "You've contacted many landlords today – try again tomorrow."
    now = time.time()
    cur = db.execute("INSERT INTO conversations(listing_id, tenant_id, landlord_id, created_at, last_message_at) "
                     "VALUES(?,?,?,?,?)", (listing_id, tenant.id, l["owner_id"], now, now))
    return cur.lastrowid, ""


def send(db, user: User, conv_id: int, body: str) -> tuple[bool, str]:
    c = _participant(db, conv_id, user)
    other = c["landlord_id"] if user.id == c["tenant_id"] else c["tenant_id"]
    if is_blocked(db, user.id, other):
        return False, "This conversation is blocked."
    text = sanitize_text(body)
    if not text:
        return False, "Empty message."
    if rate_limited(db, f"msg:{user.id}", limit=30, window_s=600):
        return False, "You're sending messages too fast – wait a few minutes."
    from .antispam import duplicate_message
    if duplicate_message(db, user.id, text):
        return False, "You've sent this exact message to many people – please write a personal message."
    flags = ",".join(message_flags(text))
    now = time.time()
    db.execute("INSERT INTO messages(conversation_id, sender_id, body, flags, created_at) VALUES(?,?,?,?,?)",
               (conv_id, user.id, text, flags, now))
    db.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (now, conv_id))
    # email ping (no content), at most one per conversation per hour
    o = db.one("SELECT email, name, email_opt_out FROM users WHERE id=?", (other,))
    import json
    row_l = db.one("SELECT payload FROM listings WHERE id=?", (c["listing_id"],)) if c["listing_id"] else None
    t = json.loads(row_l["payload"]).get("title", "your listing") if row_l else "a flatmate match"
    if o and not o["email_opt_out"]:
        subject, html, txt = emails.new_message_email(o["name"], user.name, t)
        mailer.enqueue(db, o["email"], subject, html, txt, dedupe_key=f"msg:{conv_id}:{other}:{int(now // 3600)}")
    return True, ""


def conversations(db, user: User) -> list[dict]:
    rows = db.query(
        "SELECT c.*, l.payload, "
        " (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id AND m.sender_id<>? AND m.read_at IS NULL) AS unread, "
        " (SELECT body FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_body, "
        " ut.name AS tenant_name, ul.name AS landlord_name "
        "FROM conversations c LEFT JOIN listings l ON l.id=c.listing_id "
        "JOIN users ut ON ut.id=c.tenant_id JOIN users ul ON ul.id=c.landlord_id "
        "WHERE c.tenant_id=? OR c.landlord_id=? ORDER BY c.last_message_at DESC",
        (user.id, user.id, user.id))
    import json
    out = []
    for r in rows:
        d = dict(r)
        d["listing_title"] = json.loads(r["payload"]).get("title", "") if r["payload"] else "👥 Flatmate chat"
        d["other_name"] = r["landlord_name"] if user.id == r["tenant_id"] else r["tenant_name"]
        d["other_id"] = r["landlord_id"] if user.id == r["tenant_id"] else r["tenant_id"]
        d["blocked"] = is_blocked(db, user.id, d["other_id"])
        out.append(d)
    return out


def unread_count(db, user: User) -> int:
    return db.one(
        "SELECT COUNT(*) AS n FROM messages m JOIN conversations c ON c.id=m.conversation_id "
        "WHERE (c.tenant_id=? OR c.landlord_id=?) AND m.sender_id<>? AND m.read_at IS NULL",
        (user.id, user.id, user.id))["n"]


def messages(db, user: User, conv_id: int, mark_read: bool = True) -> list:
    _participant(db, conv_id, user)
    if mark_read:
        db.execute("UPDATE messages SET read_at=? WHERE conversation_id=? AND sender_id<>? AND read_at IS NULL",
                   (time.time(), conv_id, user.id))
    return db.query("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,))


def block(db, user: User, other_id: int) -> None:
    db.execute("INSERT OR IGNORE INTO blocks(blocker_id, blocked_id, created_at) VALUES(?,?,?)",
               (user.id, other_id, time.time()))
    db.audit(user.id, "block", str(other_id))


def unblock(db, user: User, other_id: int) -> None:
    db.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (user.id, other_id))


def report(db, user: User, reason: str, target_user_id: Optional[int] = None,
           conversation_id: Optional[int] = None, listing_id: Optional[str] = None) -> None:
    if conversation_id:
        _participant(db, conversation_id, user)
    db.execute("INSERT INTO reports(reporter_id, target_user_id, conversation_id, listing_id, reason, created_at) "
               "VALUES(?,?,?,?,?,?)", (user.id, target_user_id, conversation_id, listing_id,
                                       sanitize_text(reason, 500), time.time()))
    db.audit(user.id, "report", reason[:100])
    if listing_id:
        from .antispam import auto_hide_reported
        auto_hide_reported(db, listing_id)


def landlord_invite(db, landlord: User, listing_id: str, tenant_user_id: int, body: str) -> tuple[Optional[int], str]:
    """A landlord invites a compatible tenant (who published their profile) to view a place."""
    if landlord.role != "landlord":
        return None, "Only landlords can send invitations."
    l = db.one("SELECT owner_id FROM listings WHERE id=?", (listing_id,))
    if not l or l["owner_id"] != landlord.id:
        raise PermissionDenied("Not your listing.")
    t = db.one("SELECT tp.published FROM tenant_profiles tp JOIN users u ON u.id=tp.user_id "
               "WHERE tp.user_id=? AND u.role='tenant'", (tenant_user_id,))
    if not t or not t["published"]:
        return None, "This tenant doesn't accept invitations."
    if is_blocked(db, landlord.id, tenant_user_id):
        return None, "You can't contact this tenant."
    existing = db.one("SELECT id FROM conversations WHERE listing_id=? AND tenant_id=?", (listing_id, tenant_user_id))
    if existing:
        return existing["id"], "You already have a conversation with this tenant."
    if rate_limited(db, f"invite:{landlord.id}", limit=20, window_s=86_400):
        return None, "Daily invitation limit reached."
    now = time.time()
    cid = db.execute("INSERT INTO conversations(listing_id, tenant_id, landlord_id, created_at, last_message_at) "
                     "VALUES(?,?,?,?,?)", (listing_id, tenant_user_id, landlord.id, now, now)).lastrowid
    send(db, landlord, cid, body)
    return cid, ""


def start_flatmate_chat(db, user: User, other_user_id: int) -> tuple[Optional[int], str]:
    """Tenant <-> tenant chat for flatmate matches (the other person must have published their profile)."""
    if user.role != "tenant" or other_user_id == user.id:
        return None, "Flatmate chats are between tenants."
    other = db.one("SELECT u.role, tp.published FROM users u LEFT JOIN tenant_profiles tp ON tp.user_id=u.id "
                   "WHERE u.id=?", (other_user_id,))
    if not other or other["role"] != "tenant" or not other["published"]:
        return None, "This person isn't open to messages."
    if is_blocked(db, user.id, other_user_id):
        return None, "You can't contact this person."
    ex = db.one("SELECT id FROM conversations WHERE kind='flatmate' AND ((tenant_id=? AND landlord_id=?) OR "
                "(tenant_id=? AND landlord_id=?))", (user.id, other_user_id, other_user_id, user.id))
    if ex:
        return ex["id"], ""
    if rate_limited(db, f"newconv:{user.id}", limit=15, window_s=86_400):
        return None, "You've started many chats today – try again tomorrow."
    now = time.time()
    cid = db.execute("INSERT INTO conversations(kind, listing_id, tenant_id, landlord_id, created_at, last_message_at) "
                     "VALUES('flatmate', NULL, ?, ?, ?, ?)", (user.id, other_user_id, now, now)).lastrowid
    return cid, ""
