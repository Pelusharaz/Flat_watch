"""SQLite database (single file). The schema is written to port 1:1 to PostgreSQL + PostGIS
(see docs/schema_postgres.sql). All queries are parameterised – never string-formatted."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

from .config import settings

_lock = threading.RLock()

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('tenant', 'landlord', 'admin')),
    banned INTEGER NOT NULL DEFAULT 0,
    pw_hash TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    verify_code_hash TEXT, verify_expires REAL,
    reset_code_hash TEXT, reset_expires REAL,
    totp_secret TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0, locked_until REAL,
    unsub_token TEXT, email_opt_out INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, last_login_at REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, last_seen_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    criteria TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    criteria TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    frequency TEXT NOT NULL DEFAULT 'instant' CHECK (frequency IN ('instant', 'daily', 'weekly', 'off')),
    instant_threshold INTEGER NOT NULL DEFAULT 85,
    min_score INTEGER NOT NULL DEFAULT 65,
    created_at REAL NOT NULL, last_run_at REAL, last_digest_at REAL
);

CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'removed', 'queued', 'review')),
    review_reason TEXT,
    sample INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    price_huf INTEGER NOT NULL,
    offer_type TEXT,
    fingerprint TEXT,
    duplicate_of TEXT,
    lat REAL, lon REAL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    last_verified_at REAL NOT NULL, reminder_sent_at REAL, reconfirm_token TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price_huf INTEGER NOT NULL, changed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    path TEXT NOT NULL, thumb_path TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    width INTEGER, height INTEGER, bytes INTEGER,
    sort INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, listing_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL CHECK (verdict IN ('like', 'dislike')),
    reason TEXT, created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'listing' CHECK (kind IN ('listing', 'flatmate')),
    listing_id TEXT REFERENCES listings(id) ON DELETE CASCADE,          -- NULL for flatmate chats
    tenant_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- tenant / flatmate who started it
    landlord_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, -- landlord / the other flatmate
    created_at REAL NOT NULL, last_message_at REAL NOT NULL,
    UNIQUE (listing_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    flags TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, read_at REAL
);

CREATE TABLE IF NOT EXISTS blocks (
    blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    conversation_id INTEGER, listing_id TEXT,
    reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notified (
    search_id INTEGER NOT NULL REFERENCES saved_searches(id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    score INTEGER NOT NULL, channel TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (search_id, listing_id)
);

CREATE TABLE IF NOT EXISTS email_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_email TEXT NOT NULL, subject TEXT NOT NULL, html TEXT NOT NULL, text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0, dedupe_key TEXT UNIQUE,
    created_at REAL NOT NULL, sent_at REAL, error TEXT
);

CREATE TABLE IF NOT EXISTS rate_events (key TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT NOT NULL,
    detail TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS ix_listings_status ON listings(status, offer_type, price_huf);
CREATE INDEX IF NOT EXISTS ix_listings_owner ON listings(owner_id);
CREATE INDEX IF NOT EXISTS ix_listings_fp ON listings(fingerprint);
CREATE INDEX IF NOT EXISTS ix_photos_listing ON photos(listing_id, sort);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS ix_conv_tenant ON conversations(tenant_id);
CREATE INDEX IF NOT EXISTS ix_conv_landlord ON conversations(landlord_id);
CREATE INDEX IF NOT EXISTS ix_searches_active ON saved_searches(active, frequency);
CREATE INDEX IF NOT EXISTS ix_email_status ON email_queue(status);
CREATE INDEX IF NOT EXISTS ix_rate ON rate_events(key, created_at);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
"""


SCHEMA_VERSION = 3


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade databases created by older versions (SQLite can't ALTER constraints -> rebuild)."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "users" not in tables:
        return
    if "evaluations" in tables:                      # hackathon v1 database: not compatible at all
        raise RuntimeError("This flatwatch.db is from the first hackathon version. Delete it and restart.")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "banned" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")
    conv_cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    lst_cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    if "kind" in conv_cols and "review_reason" in lst_cols:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    if "review_reason" not in lst_cols:
        conn.executescript("""
            ALTER TABLE listings RENAME TO listings_old;
        """)
    if "kind" not in conv_cols:
        conn.executescript("ALTER TABLE conversations RENAME TO conversations_old;")
    conn.executescript(SCHEMA.replace("PRAGMA foreign_keys = ON;", ""))     # keep FK checks off while copying
    if "review_reason" not in lst_cols:
        conn.executescript("""
            INSERT INTO listings(id, owner_id, source, status, sample, payload, price_huf, offer_type, fingerprint,
                duplicate_of, lat, lon, created_at, updated_at, last_verified_at, reminder_sent_at, reconfirm_token)
            SELECT id, owner_id, source, status, sample, payload, price_huf, offer_type, fingerprint,
                duplicate_of, lat, lon, created_at, updated_at, last_verified_at, reminder_sent_at, reconfirm_token
            FROM listings_old;
            DROP TABLE listings_old;""")
    if "kind" not in conv_cols:
        conn.executescript("""
            INSERT INTO conversations(id, listing_id, tenant_id, landlord_id, created_at, last_message_at)
            SELECT id, listing_id, tenant_id, landlord_id, created_at, last_message_at FROM conversations_old;
            DROP TABLE conversations_old;""")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


class Database:
    def __init__(self, path: Optional[str] = None):
        self.path = path or settings.db_path
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with _lock:
            _migrate(self.conn)
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA journal_mode = WAL") if self.path != ":memory:" else None
            self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with _lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with _lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        with _lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    # ---- small helpers -------------------------------------------------------
    def kv_get(self, key: str, default=None):
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value) -> None:
        self.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value)))

    def audit(self, user_id: Optional[int], action: str, detail: str = "") -> None:
        self.execute("INSERT INTO audit_log(user_id, action, detail, created_at) VALUES(?,?,?,?)",
                     (user_id, action, detail[:500], time.time()))


_default: Optional[Database] = None


def get_db() -> Database:
    """Process-wide database (Streamlit reruns and the worker thread share it)."""
    global _default
    if _default is None:
        _default = Database()
    return _default
