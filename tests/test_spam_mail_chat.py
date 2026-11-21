"""Anti-spam layers, the test mail server, flatmate chats and DB migration."""
import smtplib
import sqlite3
import time

import pytest

from flatwatch import antispam, auth, listings, mailer, mailserver, messaging, photos, searches
from flatwatch.bootstrap import bootstrap
from flatwatch.db import Database
from flatwatch.models import Criteria, RawListing


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "STATIC_DIR", tmp_path / "static")
    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "outbox")
    d = Database(":memory:")
    bootstrap(d)
    return d


def _u(db, email):
    return auth.get_user(db, db.one("SELECT id FROM users WHERE email=?", (email,))["id"])


# ------------------------------------------------------------- sign-up bots --
def test_signup_bot_defences(db):
    assert not auth.register(db, "bot@x.hu", "Bot", "Strong-pass-123", "tenant", honeypot="http://spam")[0]
    assert not auth.register(db, "fast@x.hu", "Fast", "Strong-pass-123", "tenant", form_seconds=0.4)[0]
    ok, msg, _ = auth.register(db, "x@mailinator.com", "Temp", "Strong-pass-123", "tenant", form_seconds=9)
    assert not ok and "permanent email" in msg
    assert auth.register(db, "real@x.hu", "Real", "Strong-pass-123", "tenant", form_seconds=9)[0]


def test_banned_user_cannot_log_in(db):
    from flatwatch.bootstrap import DEMO_PASSWORD
    gabor = _u(db, "gabor@demo.flatwatch.hu")
    ok, _, token, _ = auth.login(db, gabor.email, DEMO_PASSWORD)
    antispam.ban_user(db, gabor.id)
    assert auth.session_user(db, token) is None
    assert "suspended" in auth.login(db, gabor.email, DEMO_PASSWORD)[1]
    assert listings.row(db, "F02")["status"] == "removed"


# ------------------------------------------------------------- search spam --
def test_saved_search_limits(db):
    anna = _u(db, "anna@demo.flatwatch.hu")
    c = searches.get_profile(db, anna.id)
    sid, err = searches.create_search_checked(db, anna.id, "dup", c)          # same criteria as her existing one
    assert sid is None and "already" in err
    made = [searches.create_search_checked(db, anna.id, f"s{i}", c.model_copy(update={"max_total_huf": 100_000 + i}))
            for i in range(6)]
    assert [bool(m[0]) for m in made][:3] == [True, True, True]                 # 3 new searches per hour
    assert made[3][0] is None and "hour" in made[3][1]


def test_instant_email_daily_cap(db):
    anna = _u(db, "anna@demo.flatwatch.hu")
    for _ in range(antispam.LIMITS["instant_emails_per_day"]):
        assert antispam.instant_email_allowed(db, anna.id)
    assert not antispam.instant_email_allowed(db, anna.id)


# ------------------------------------------------------------ listing spam --
def test_listing_review_and_limits(db):
    gabor = _u(db, "gabor@demo.flatwatch.hu")
    spam = RawListing(id="SP1", source="Flat-Watch", title="CHEAP ROOM CALL NOW BEST DEAL", price_huf=70000,
                      location="Debrecen, Belváros", description="WhatsApp +36 30 123 4567, see bit.ly/x",
                      offer_type="room", poster_role="owner", poster_name="Gábor")
    status, msg = listings.publish(db, spam, gabor.id)
    assert status == "review" and "links" in msg
    assert "SP1" not in {l.id for l in listings.active(db)}
    ok = RawListing(id="OK1", source="Flat-Watch", title="Quiet room near Kálvin tér", price_huf=80000,
                    location="Debrecen, Belváros", description="Furnished room, shared kitchen.", offer_type="room",
                    poster_role="owner", poster_name="Gábor")
    assert listings.publish(db, ok, gabor.id)[0] == "active"
    again = ok.model_copy(update={"id": "OK2"})
    assert listings.publish(db, again, gabor.id)[0] is None                     # same place twice


def test_reports_auto_hide_listing(db):
    for email in ("anna@demo.flatwatch.hu", "yasmin@demo.flatwatch.hu", "rahul@demo.flatwatch.hu"):
        messaging.report(db, _u(db, email), "fake", listing_id="L02")
    assert listings.row(db, "L02")["status"] == "review"


def test_duplicate_message_spam(db):
    anna = _u(db, "anna@demo.flatwatch.hu")
    owned = [r["id"] for r in db.query("SELECT id FROM listings WHERE owner_id IS NOT NULL AND status='active'")]
    convs = [messaging.start_conversation(db, anna, lid)[0] for lid in owned[:7]]
    assert len(convs) >= 6 and all(convs)
    results = [messaging.send(db, anna, c, "Hi, is it available? Reply fast")[0] for c in convs]
    assert results[:5] == [True] * 5 and results[-1] is False


# --------------------------------------------------------------- flatmates --
def test_flatmate_chat(db):
    anna = _u(db, "anna@demo.flatwatch.hu")
    dora = db.one("SELECT id FROM users WHERE name='Dóra Kiss'")["id"]
    cid, err = messaging.start_flatmate_chat(db, anna, dora)
    assert cid and not err
    assert messaging.start_flatmate_chat(db, anna, dora)[0] == cid               # reused, not duplicated
    gabor = _u(db, "gabor@demo.flatwatch.hu")
    assert messaging.start_flatmate_chat(db, anna, gabor.id)[0] is None         # landlords aren't flatmates
    titles = [c["listing_title"] for c in messaging.conversations(db, anna)]
    assert "👥 Flatmate chat" in titles


# -------------------------------------------------------------- mail server --
def test_test_mailserver_receives_alerts(tmp_path, monkeypatch, db):
    monkeypatch.setattr(mailserver, "MAILBOX", tmp_path / "mbx")
    assert mailserver.start("127.0.0.1", 2599)
    try:
        with smtplib.SMTP("127.0.0.1", 2599, timeout=5) as s:
            s.sendmail("a@x.hu", ["anna@demo.flatwatch.hu"],
                       "Subject: hello\r\nTo: anna@demo.flatwatch.hu\r\nContent-Type: text/plain\r\n\r\n.dot line\r\n")
        m = mailserver.inbox("anna@demo.flatwatch.hu")
        assert m and m[0]["subject"] == "hello" and ".dot line" in m[0]["text"]
        assert mailserver.inbox("nobody@x.hu") == []
        assert mailserver.clear() == 1
    finally:
        mailserver.stop()


# ---------------------------------------------------------------- migration --
def test_migrates_previous_version_db(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          role TEXT NOT NULL, pw_hash TEXT NOT NULL, email_verified INTEGER NOT NULL DEFAULT 0,
          verify_code_hash TEXT, verify_expires REAL, reset_code_hash TEXT, reset_expires REAL, totp_secret TEXT,
          totp_enabled INTEGER NOT NULL DEFAULT 0, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until REAL,
          unsub_token TEXT, email_opt_out INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, last_login_at REAL);
        CREATE TABLE listings (id TEXT PRIMARY KEY, owner_id INTEGER, source TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','removed','queued')),
          sample INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL, price_huf INTEGER NOT NULL, offer_type TEXT,
          fingerprint TEXT, duplicate_of TEXT, lat REAL, lon REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
          last_verified_at REAL NOT NULL, reminder_sent_at REAL, reconfirm_token TEXT);
        CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id TEXT NOT NULL,
          tenant_id INTEGER NOT NULL, landlord_id INTEGER NOT NULL, created_at REAL NOT NULL,
          last_message_at REAL NOT NULL, UNIQUE (listing_id, tenant_id));
        INSERT INTO listings VALUES ('X1', NULL, 'site', 'active', 0, '{}', 1, NULL, NULL, NULL, NULL, NULL, 1, 1, 1, NULL, NULL);
        INSERT INTO conversations VALUES (1, 'X1', 1, 2, 1, 1);
    """)
    con.commit()
    con.close()
    d = Database(str(path))
    assert {r[1] for r in d.conn.execute("PRAGMA table_info(conversations)")} >= {"kind"}
    assert d.one("SELECT kind FROM conversations WHERE id=1")["kind"] == "listing"
    d.execute("UPDATE listings SET status='review' WHERE id='X1'")              # new status accepted
    assert d.one("SELECT status FROM listings WHERE id='X1'")["status"] == "review"
