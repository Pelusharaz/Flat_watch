"""Accounts, security, chat, photos, saved searches, notifications, freshness."""
import io
import time

import pytest
from PIL import Image

from flatwatch import auth, listings, mailer, messaging, photos, searches, security, worker
from flatwatch.bootstrap import DEMO_PASSWORD, bootstrap
from flatwatch.commute import destination_maps_url, maps_url
from flatwatch.db import Database
from flatwatch.models import Criteria, RawListing


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from flatwatch import config
    monkeypatch.setattr(photos, "STATIC_DIR", tmp_path / "static")
    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "outbox")
    d = Database(":memory:")
    bootstrap(d)
    return d


def _user(db, email):
    return auth.get_user(db, db.one("SELECT id FROM users WHERE email=?", (email,))["id"])


# ---------------------------------------------------------------- security --
def test_password_hashing():
    h = security.hash_password("Correct-horse-42")
    assert h.startswith("scrypt$") and "Correct" not in h
    assert security.verify_password("Correct-horse-42", h) and not security.verify_password("wrong", h)


def test_real_location_links():
    district_url = maps_url("Debrecen, Belváros, Hatvan utca", "Belváros")
    campus_url = destination_maps_url("main")
    assert district_url and district_url.startswith("https://www.google.com/maps/search/")
    assert campus_url and "47.5536" in campus_url


def test_totp_roundtrip():
    s = security.totp_new_secret()
    assert security.totp_verify(s, security.totp_at(s)) and not security.totp_verify(s, "000000")


def test_md_escape_and_links():
    assert "<" not in security.md_escape("<script>alert(1)</script>")
    assert security.md_escape("[click](http://evil)").startswith("\\[")
    assert "risky_link" in security.message_flags("pay here bit.ly/abc")
    assert "payment_request" in security.message_flags("please send the deposit first via Western Union")
    assert security.message_flags("see https://ingatlan.com/123") == []


# --------------------------------------------------------------------- auth --
def test_register_verify_login_lockout_reset(db):
    assert not auth.register(db, "a@b.hu", "A", "short", "tenant")[0]
    ok, _, code = auth.register(db, "a@b.hu", "Ann", "Strong-pass-123", "tenant")
    assert ok and code
    assert not auth.register(db, "a@b.hu", "Ann", "Strong-pass-123", "tenant")[0]      # duplicate
    assert "verify" in auth.login(db, "a@b.hu", "Strong-pass-123")[1]
    assert auth.verify_email(db, "a@b.hu", code)[0]
    ok, _, token, _ = auth.login(db, "a@b.hu", "Strong-pass-123")
    assert ok and auth.session_user(db, token).name == "Ann"
    for _ in range(5):
        assert auth.login(db, "a@b.hu", "wrong-pass-123")[1] == auth.GENERIC_LOGIN_ERROR
    assert "locked" in auth.login(db, "a@b.hu", "Strong-pass-123")[1]
    msg, rcode = auth.request_reset(db, "a@b.hu")
    assert msg == auth.request_reset(db, "nobody@b.hu")[0]                                 # no enumeration
    assert auth.reset_password(db, "a@b.hu", rcode, "Another-pass-456")[0]
    assert auth.session_user(db, token) is None                                            # sessions revoked
    assert auth.login(db, "a@b.hu", "Another-pass-456")[0]


def test_two_factor_login(db):
    u = _user(db, "anna@demo.flatwatch.hu")
    secret, uri = auth.start_totp(db, u)
    assert uri.startswith("otpauth://") and auth.confirm_totp(db, u, security.totp_at(secret))
    ok, _, _, needs = auth.login(db, u.email, DEMO_PASSWORD)
    assert not ok and needs
    assert auth.login(db, u.email, DEMO_PASSWORD, security.totp_at(secret))[0]


def test_rbac(db):
    tenant = _user(db, "anna@demo.flatwatch.hu")
    landlord = _user(db, "gabor@demo.flatwatch.hu")
    with pytest.raises(auth.PermissionDenied):
        auth.require_role(tenant, "landlord")
    with pytest.raises(auth.PermissionDenied):
        auth.require_listing_owner(db, landlord, "F01")         # Yasmin's listing
    auth.require_listing_owner(db, landlord, "F02")


def test_gdpr_export_and_delete(db):
    u = _user(db, "anna@demo.flatwatch.hu")
    assert '"saved_searches"' in auth.export_user_data(db, u.id)
    assert not auth.delete_account(db, u, "wrong")
    assert auth.delete_account(db, u, DEMO_PASSWORD)
    assert db.one("SELECT COUNT(*) n FROM saved_searches WHERE user_id=?", (u.id,))["n"] == 0


# ---------------------------------------------------------------- messaging --
def test_chat_privacy_unread_block(db):
    anna = _user(db, "anna@demo.flatwatch.hu")
    yasmin = _user(db, "yasmin@demo.flatwatch.hu")
    gabor = _user(db, "gabor@demo.flatwatch.hu")
    cid, err = messaging.start_conversation(db, anna, "F01")
    assert cid and not err
    assert messaging.start_conversation(db, anna, "L03")[0] is None          # site listing: no owner
    before = messaging.unread_count(db, yasmin)
    assert messaging.send(db, anna, cid, "Hi! Is the room still free?")[0]
    assert messaging.unread_count(db, yasmin) == before + 1
    messaging.messages(db, yasmin, cid)                                       # reading marks the whole chat read
    assert messaging.unread_count(db, yasmin) == 0
    with pytest.raises(auth.PermissionDenied):
        messaging.messages(db, gabor, cid)
    messaging.block(db, yasmin, anna.id)
    assert not messaging.send(db, anna, cid, "hello?")[0]
    q = db.query("SELECT text FROM email_queue WHERE to_email=?", (yasmin.email,))
    assert q and "Is the room still free" not in q[0]["text"]                # no content in emails


def test_message_rate_limit(db):
    anna = _user(db, "anna@demo.flatwatch.hu")
    cid, _ = messaging.start_conversation(db, anna, "F02")
    results = [messaging.send(db, anna, cid, f"message number {i}")[0] for i in range(32)]
    assert results[:30] == [True] * 30 and results[-1] is False


# ------------------------------------------------------------------- photos --
def _jpeg(w=2400, h=1800, exif=True):
    img = Image.new("RGB", (w, h), (120, 160, 200))
    buf = io.BytesIO()
    ex = Image.Exif()
    ex[0x010F] = "SecretCam"            # camera make – must be stripped
    img.save(buf, "JPEG", exif=ex.tobytes() if exif else b"")
    return buf.getvalue()


def test_photo_pipeline(db):
    pid = photos.process_upload(db, "F02", _jpeg(), "Kitchen")
    r = db.one("SELECT * FROM photos WHERE id=?", (pid,))
    full = Image.open(photos.STATIC_DIR / r["path"])
    assert max(full.size) == photos.FULL_SIZE and not full.getexif()       # resized + metadata stripped
    assert max(Image.open(photos.STATIC_DIR / r["thumb_path"]).size) == photos.THUMB_SIZE
    with pytest.raises(photos.PhotoError):
        photos.process_upload(db, "F02", b"<?php evil ?>", "Other")
    with pytest.raises(photos.PhotoError):
        photos.process_upload(db, "F02", b"x" * (photos.MAX_BYTES + 1), "Other")


def test_sample_photos_exist(db):
    assert photos.first_thumb(db, "L02") and not photos.first_thumb(db, "L04")    # scams get none


# ------------------------------------------------- searches & notifications --
def test_continuous_discovery_dedupe_and_unsubscribe(db):
    anna = _user(db, "anna@demo.flatwatch.hu")
    s = searches.user_searches(db, anna.id)[0]
    assert db.one("SELECT COUNT(*) n FROM notified WHERE search_id=?", (s["id"],))["n"] > 0   # first email at setup
    assert searches.run_initial(db, s["id"]) == 0                               # no duplicates
    released = 0
    for _ in range(8):
        rep = worker.run_cycle(db)
        released += bool(rep["released"])
    assert released == listings.DEMO_HELD_BACK
    sent = db.query("SELECT listing_id, channel FROM notified WHERE search_id=?", (s["id"],))
    assert len({r["listing_id"] for r in sent}) == len(sent)                    # each listing once
    assert db.one("SELECT COUNT(*) n FROM email_queue WHERE status='sent'")["n"] >= 2
    html = db.one("SELECT html FROM email_queue WHERE to_email=? AND subject LIKE '%match%'", (anna.email,))
    assert html and "unsubscribe=" in html["html"] and "% match" in html["html"]
    # digest after a day
    assert searches.send_digests(db, now=time.time() + 2 * 86400) >= 0
    assert searches.unsubscribe(db, anna.unsub_token)
    assert searches.user_searches(db, anna.id)[0]["frequency"] == "off"


def test_new_listing_triggers_instant_alert(db):
    anna = _user(db, "anna@demo.flatwatch.hu")
    s = searches.user_searches(db, anna.id)[0]
    searches.run_initial(db, s["id"])
    gabor = _user(db, "gabor@demo.flatwatch.hu")
    l = RawListing(id="NEW1", source="Flat-Watch", title="Bright studio near Egyetem", price_huf=125000,
                   location="Debrecen, Nagyerdő, Bem tér", size_m2=32, rooms=1, offer_type="apartment",
                   description="Furnished studio.", furnished=True, utilities_huf=20000,
                   available_from=__import__("datetime").date(2027, 1, 20), poster_role="owner", poster_name="Gábor")
    listings.upsert(db, l, owner_id=gabor.id)
    rep = searches.run_new_listings(db)
    assert rep["instant"] == 1
    assert db.one("SELECT 1 FROM notified WHERE search_id=? AND listing_id='NEW1' AND channel='instant'", (s["id"],))


def test_freshness_reminder_and_expiry(db):
    old = time.time() - 20 * 86400
    db.execute("UPDATE listings SET last_verified_at=? WHERE id='F02'", (old,))
    out = worker.freshness(db)
    assert out["reminded"] == 1 and listings.row(db, "F02")["reminder_sent_at"]
    token = listings.row(db, "F02")["reconfirm_token"]
    assert listings.reconfirm(db, token=token)
    db.execute("UPDATE listings SET last_verified_at=? WHERE id='F02'", (time.time() - 40 * 86400,))
    worker.freshness(db)
    assert listings.row(db, "F02")["status"] == "expired"


def test_price_history_and_duplicates(db):
    l = listings.get(db, "F02")
    listings.upsert(db, l.model_copy(update={"price_huf": 125000}), owner_id=listings.row(db, "F02")["owner_id"])
    assert [h["price_huf"] for h in listings.price_history(db, "F02")] == [130000, 125000]
    dup = l.model_copy(update={"id": "DUP1", "source": "ingatlan.com", "price_huf": 125000})
    listings.upsert(db, dup)
    assert listings.row(db, "DUP1")["duplicate_of"] == "F02"
    assert "DUP1" not in {x.id for x in listings.active(db)}


# ------------------------------------------------------------ landlord side --
def test_compatible_tenants_and_invite(db):
    from flatwatch.tenants import tenants_for_offer
    l = listings.get(db, "F03")                  # bed space for a male tenant
    matches, excluded = tenants_for_offer(l, searches.published_profiles(db), listings.median_per_m2(db))
    assert matches and all(m.profile.gender == "Male" for m in matches)
    assert excluded
    rahul = _user(db, "rahul@demo.flatwatch.hu")
    cid, err = messaging.landlord_invite(db, rahul, "F03", matches[0].profile.user_id, "Hi! Want to see it?")
    assert cid and not err
