"""Accounts: registration, email verification, login with lockout, sessions, password reset,
optional TOTP two-factor authentication, role checks and GDPR export / deletion."""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from typing import Optional

from . import emails, mailer
from .config import STATIC_DIR, settings
from .security import (hash_password, hash_token, new_code, new_token, password_problems, rate_limited,
                       totp_new_secret, totp_uri, totp_verify, verify_password)

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
CODE_TTL = 15 * 60
GENERIC_LOGIN_ERROR = "Invalid email or password."


@dataclass
class User:
    id: int
    email: str
    name: str
    role: str
    email_verified: bool
    totp_enabled: bool
    unsub_token: str
    email_opt_out: bool


def _user(row) -> Optional[User]:
    if not row:
        return None
    return User(id=row["id"], email=row["email"], name=row["name"], role=row["role"],
                email_verified=bool(row["email_verified"]), totp_enabled=bool(row["totp_enabled"]),
                unsub_token=row["unsub_token"] or "", email_opt_out=bool(row["email_opt_out"]))


def get_user(db, user_id: int) -> Optional[User]:
    return _user(db.one("SELECT * FROM users WHERE id=?", (user_id,)))


def _send_code(db, user_id: int, kind: str) -> str:
    code = new_code()
    col = "verify" if kind == "verify" else "reset"
    db.execute(f"UPDATE users SET {col}_code_hash=?, {col}_expires=? WHERE id=?",   # col is a fixed literal
               (hash_token(code), time.time() + CODE_TTL, user_id))
    row = db.one("SELECT email, name FROM users WHERE id=?", (user_id,))
    subject, html, text = emails.code_email(kind, row["name"], code)
    mailer.enqueue(db, row["email"], subject, html, text, dedupe_key=f"{kind}:{user_id}:{code}")
    mailer.process_queue(db)         # codes go out immediately, not on the next worker tick
    return code


# ------------------------------------------------------------- registration --
def register(db, email: str, name: str, password: str, role: str, honeypot: str = "",
             form_seconds: Optional[float] = None) -> tuple[bool, str, Optional[str]]:
    """Returns (ok, message, dev_code). dev_code is only returned in development mode."""
    from .antispam import signup_problem
    email = (email or "").strip().lower()
    bot = signup_problem(email, honeypot, form_seconds)
    if bot:
        return False, bot, None
    name = (name or "").strip()[:80]
    if role not in ("tenant", "landlord"):
        return False, "Choose tenant or landlord.", None
    if not EMAIL_RE.match(email):
        return False, "Enter a valid email address.", None
    if not name:
        return False, "Enter your name.", None
    problems = password_problems(password, email)
    if problems:
        return False, "Password needs: " + ", ".join(problems) + ".", None
    if rate_limited(db, "register", limit=30, window_s=3600):
        return False, "Too many sign-ups right now – try again later.", None
    if db.one("SELECT 1 FROM users WHERE email=?", (email,)):
        return False, "An account with this email already exists – log in or reset your password.", None
    cur = db.execute(
        "INSERT INTO users(email, name, role, pw_hash, unsub_token, created_at) VALUES(?,?,?,?,?,?)",
        (email, name, role, hash_password(password), new_token(), time.time()))
    code = _send_code(db, cur.lastrowid, "verify")
    db.audit(cur.lastrowid, "register", role)
    return True, "Account created – check your email for the 6-digit code.", _dev(code)


def _dev(code: str) -> Optional[str]:
    return code if (settings.dev_show_codes and (not settings.smtp_enabled or settings.local_mailserver)) else None


def verify_email(db, email: str, code: str) -> tuple[bool, str]:
    row = db.one("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),))
    if not row or rate_limited(db, f"verify:{email}", limit=10, window_s=900):
        return False, "Invalid or expired code."
    if row["verify_code_hash"] and row["verify_expires"] and row["verify_expires"] > time.time() \
            and hash_token((code or "").strip()) == row["verify_code_hash"]:
        db.execute("UPDATE users SET email_verified=1, verify_code_hash=NULL, verify_expires=NULL WHERE id=?",
                   (row["id"],))
        db.audit(row["id"], "email_verified")
        return True, "Email verified – you can log in now."
    return False, "Invalid or expired code."


def resend_verification(db, email: str) -> Optional[str]:
    row = db.one("SELECT id, email_verified FROM users WHERE email=?", ((email or "").strip().lower(),))
    if row and not row["email_verified"] and not rate_limited(db, f"resend:{email}", limit=3, window_s=900):
        return _dev(_send_code(db, row["id"], "verify"))
    return None


# -------------------------------------------------------------------- login --
def login(db, email: str, password: str, totp_code: str = "") -> tuple[bool, str, Optional[str], bool]:
    """Returns (ok, message, session_token, needs_totp)."""
    email = (email or "").strip().lower()
    if rate_limited(db, f"login:{email}", limit=20, window_s=900):
        return False, "Too many attempts – wait 15 minutes.", None, False
    row = db.one("SELECT * FROM users WHERE email=?", (email,))
    if not row:
        verify_password(password, hash_password("timing-equaliser"))   # same work as a real check
        return False, GENERIC_LOGIN_ERROR, None, False
    if row["banned"]:
        return False, "This account is suspended. Contact support if you think this is a mistake.", None, False
    if row["locked_until"] and row["locked_until"] > time.time():
        mins = int((row["locked_until"] - time.time()) // 60) + 1
        return False, f"Account temporarily locked after failed attempts – try again in {mins} min.", None, False
    if not verify_password(password, row["pw_hash"]):
        failed = row["failed_logins"] + 1
        locked = time.time() + settings.lockout_minutes * 60 if failed >= settings.max_failed_logins else None
        db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                   (0 if locked else failed, locked, row["id"]))
        db.audit(row["id"], "login_failed")
        return False, GENERIC_LOGIN_ERROR, None, False
    if not row["email_verified"]:
        return False, "Please verify your email first (enter the code we sent you).", None, False
    if row["totp_enabled"]:
        if not totp_code:
            return False, "Enter the 6-digit code from your authenticator app.", None, True
        if not totp_verify(row["totp_secret"], totp_code):
            db.audit(row["id"], "totp_failed")
            return False, "Wrong authenticator code.", None, True
    token = new_token()
    now = time.time()
    db.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, last_seen_at) VALUES(?,?,?,?,?)",
               (hash_token(token), row["id"], now, now + settings.session_days * 86400, now))
    db.execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login_at=? WHERE id=?", (now, row["id"]))
    db.audit(row["id"], "login")
    return True, "Welcome back!", token, False


def session_user(db, token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    th = hash_token(token)
    row = db.one("SELECT s.expires_at, u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (th,))
    if not row or row["expires_at"] < time.time() or row["banned"]:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
        return None
    db.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (time.time(), th))
    return _user(row)


def logout(db, token: Optional[str]) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(token),))


def logout_everywhere(db, user_id: int) -> None:
    db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


# ----------------------------------------------------------- password reset --
def request_reset(db, email: str) -> tuple[str, Optional[str]]:
    """Always the same answer, whether or not the account exists (no account enumeration)."""
    msg = "If an account exists for this email, we sent a reset code."
    row = db.one("SELECT id FROM users WHERE email=?", ((email or "").strip().lower(),))
    if row and not rate_limited(db, f"reset:{email}", limit=3, window_s=900):
        return msg, _dev(_send_code(db, row["id"], "reset"))
    return msg, None


def reset_password(db, email: str, code: str, new_password: str) -> tuple[bool, str]:
    row = db.one("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),))
    if not row or rate_limited(db, f"resetcode:{email}", limit=10, window_s=900):
        return False, "Invalid or expired code."
    if not (row["reset_code_hash"] and row["reset_expires"] and row["reset_expires"] > time.time()
            and hash_token((code or "").strip()) == row["reset_code_hash"]):
        return False, "Invalid or expired code."
    problems = password_problems(new_password, row["email"])
    if problems:
        return False, "Password needs: " + ", ".join(problems) + "."
    db.execute("UPDATE users SET pw_hash=?, reset_code_hash=NULL, reset_expires=NULL, failed_logins=0, "
               "locked_until=NULL, email_verified=1 WHERE id=?", (hash_password(new_password), row["id"]))
    logout_everywhere(db, row["id"])
    db.audit(row["id"], "password_reset")
    return True, "Password changed – log in with your new password."


# --------------------------------------------------------------------- 2FA --
def start_totp(db, user: User) -> tuple[str, str]:
    secret = totp_new_secret()
    db.execute("UPDATE users SET totp_secret=?, totp_enabled=0 WHERE id=?", (secret, user.id))
    return secret, totp_uri(secret, user.email)


def confirm_totp(db, user: User, code: str) -> bool:
    row = db.one("SELECT totp_secret FROM users WHERE id=?", (user.id,))
    if row and row["totp_secret"] and totp_verify(row["totp_secret"], code):
        db.execute("UPDATE users SET totp_enabled=1 WHERE id=?", (user.id,))
        db.audit(user.id, "totp_enabled")
        return True
    return False


def disable_totp(db, user: User, password: str) -> bool:
    row = db.one("SELECT pw_hash FROM users WHERE id=?", (user.id,))
    if row and verify_password(password, row["pw_hash"]):
        db.execute("UPDATE users SET totp_enabled=0, totp_secret=NULL WHERE id=?", (user.id,))
        db.audit(user.id, "totp_disabled")
        return True
    return False


# -------------------------------------------------------------------- RBAC --
class PermissionDenied(Exception):
    pass


def require_role(user: Optional[User], *roles: str) -> User:
    if user is None or user.role not in roles:
        raise PermissionDenied(f"Requires role: {', '.join(roles)}")
    return user


def require_listing_owner(db, user: User, listing_id: str) -> None:
    row = db.one("SELECT owner_id FROM listings WHERE id=?", (listing_id,))
    if not row or (row["owner_id"] != user.id and user.role != "admin"):
        raise PermissionDenied("You can only change your own listings.")


# -------------------------------------------------------------------- GDPR --
def export_user_data(db, user_id: int) -> str:
    def rows(sql, *p):
        return [dict(r) for r in db.query(sql, p)]
    user = dict(db.one("SELECT id, email, name, role, email_verified, totp_enabled, email_opt_out, created_at, "
                       "last_login_at FROM users WHERE id=?", (user_id,)))
    data = {
        "user": user,
        "tenant_profile": rows("SELECT criteria, published, updated_at FROM tenant_profiles WHERE user_id=?", user_id),
        "saved_searches": rows("SELECT * FROM saved_searches WHERE user_id=?", user_id),
        "listings": rows("SELECT id, status, payload, created_at FROM listings WHERE owner_id=?", user_id),
        "favorites": rows("SELECT listing_id, created_at FROM favorites WHERE user_id=?", user_id),
        "feedback": rows("SELECT listing_id, verdict, reason, created_at FROM feedback WHERE user_id=?", user_id),
        "messages_sent": rows("SELECT conversation_id, body, created_at FROM messages WHERE sender_id=?", user_id),
    }
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def delete_account(db, user: User, password: str) -> bool:
    row = db.one("SELECT pw_hash FROM users WHERE id=?", (user.id,))
    if not row or not verify_password(password, row["pw_hash"]):
        return False
    for lst in db.query("SELECT id FROM listings WHERE owner_id=?", (user.id,)):
        shutil.rmtree(STATIC_DIR / "photos" / lst["id"], ignore_errors=True)
        db.execute("DELETE FROM listings WHERE id=?", (lst["id"],))
    db.execute("DELETE FROM users WHERE id=?", (user.id,))           # cascades to sessions, searches, messages…
    db.audit(None, "account_deleted", f"user {user.id}")
    return True
