"""Security primitives (standard library only):
password hashing (scrypt), tokens, one-time codes, TOTP 2FA (RFC 6238), rate limiting,
and text sanitising for chat / user content."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
import unicodedata
from typing import Optional
from urllib.parse import quote, urlparse

# ------------------------------------------------------------------ passwords --
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
COMMON_PASSWORDS = {"password", "password123", "123456789", "1234567890", "qwertyuiop", "letmein123",
                    "iloveyou12", "debrecen123", "jelszo1234", "admin12345"}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt${n}${r}${p}${s}${h}".format(
        s=base64.b64encode(salt).decode(), h=base64.b64encode(dk).decode(), **_SCRYPT)


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, s, h = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(s), n=int(n), r=int(r), p=int(p),
                            dklen=len(base64.b64decode(h)))
        return hmac.compare_digest(dk, base64.b64decode(h))
    except Exception:  # noqa: BLE001
        return False


def password_problems(password: str, email: str = "") -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("at least 10 characters")
    local = (email or "").split("@")[0].lower()
    if password.lower() in COMMON_PASSWORDS or (len(local) >= 4 and local in password.lower()):
        problems.append("not a common password or your email name")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        problems.append("letters and numbers")
    return problems


# --------------------------------------------------------------- tokens/codes --
def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


# ------------------------------------------------------------------------ TOTP --
def totp_new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret: str, t: Optional[float] = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((t if t is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code:0{digits}d}"


def totp_verify(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    now = time.time()
    return any(hmac.compare_digest(totp_at(secret, now + i * 30), code) for i in range(-window, window + 1))


def totp_uri(secret: str, email: str, issuer: str = "Flat-Watch") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}"


# --------------------------------------------------------------- rate limiting --
def rate_limited(db, key: str, limit: int, window_s: int, record: bool = True) -> bool:
    """True if `key` already had `limit` events in the last `window_s` seconds."""
    now = time.time()
    db.execute("DELETE FROM rate_events WHERE created_at < ?", (now - 86_400,))
    n = db.one("SELECT COUNT(*) AS n FROM rate_events WHERE key=? AND created_at>=?", (key, now - window_s))["n"]
    if n >= limit:
        return True
    if record:
        db.execute("INSERT INTO rate_events(key, created_at) VALUES(?, ?)", (key, now))
    return False


# ------------------------------------------------------------ text sanitising --
URL_RE = re.compile(
    r"((?:https?://|www\.)[^\s<>\"']+"                                  # explicit links
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|hu|ly|io|co|me|info|biz|xyz|top|ru|link|app|site|gd|gl)"
    r"(?:/[^\s<>\"']*)?)",                                                # bare domains: bit.ly/x, evil.xyz
    re.I)
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "cutt.ly", "is.gd", "ow.ly", "rb.gy"}
TRUSTED_DOMAINS = {"ingatlan.com", "jofogas.hu", "alberlet.hu", "unideb.hu", "dkv.hu", "google.com",
                   "maps.google.com", "openstreetmap.org"}
PAYMENT_RE = re.compile(r"western union|moneygram|revolut|crypto|bitcoin|gift ?card|wire (the )?money|"
                        r"pay (the )?deposit (before|first)|send (the )?deposit|előre utal|utald át|"
                        r"kaució(t)? (előre|azonnal)", re.I)


def sanitize_text(text: str, max_len: int = 2000) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:max_len]


def md_escape(text: str) -> str:
    """Escape user text before putting it into Streamlit markdown (no links, no HTML, no formatting)."""
    text = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~$])", r"\\\1", text)


def link_report(text: str) -> list[dict]:
    """Links in a message with a risk label."""
    out = []
    for raw in URL_RE.findall(text or ""):
        url = raw if raw.lower().startswith("http") else "http://" + raw
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        trusted = any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS)
        risky = host in SHORTENERS or host.replace(".", "").isdigit() or url.lower().startswith("http://")
        out.append({"url": raw, "host": host, "trusted": trusted, "risky": risky and not trusted})
    return out


def message_flags(text: str) -> list[str]:
    flags = []
    links = link_report(text)
    if any(l["risky"] for l in links):
        flags.append("risky_link")
    elif any(not l["trusted"] for l in links):
        flags.append("external_link")
    if PAYMENT_RE.search(text or ""):
        flags.append("payment_request")
    return flags
