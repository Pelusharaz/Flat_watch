"""First-run setup: sample listings + sample photos + demo accounts (only when INCLUDE_SAMPLES=1).

Demo accounts (password: DEMO_PASSWORD, default below) – for demos only, disable in production:
  tenant    anna@demo.flatwatch.hu
  landlords yasmin@ (room F01), gabor@ (F02, L07, L12), rahul@ (bed F03), eva@ (L02, L06, L10),
            peter@ (L13, L15)  – all @demo.flatwatch.hu
  admin     admin@demo.flatwatch.hu (moderation: reports, review queue, bans)
Sample tenant profiles (for landlord-side matching) use @sample.flatwatch.hu emails and random
passwords nobody knows.
"""
from __future__ import annotations

import json
import os
import secrets
import time

from . import listings, photos, searches
from .config import DATA_DIR, settings
from .models import Criteria
from .security import hash_password, new_token

DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "Debrecen-demo-2026")
SAMPLE_DOMAIN = "@sample.flatwatch.hu"
OWNERS = {"F01": ("yasmin@demo.flatwatch.hu", "Yasmin Khalil"),
          "F02": ("gabor@demo.flatwatch.hu", "Gábor Nagy"),
          "F03": ("rahul@demo.flatwatch.hu", "Rahul Sharma"),
          "L07": ("gabor@demo.flatwatch.hu", "Gábor Nagy"),
          "L12": ("gabor@demo.flatwatch.hu", "Gábor Nagy"),
          "L02": ("eva@demo.flatwatch.hu", "Éva Tóth"),
          "L06": ("eva@demo.flatwatch.hu", "Éva Tóth"),
          "L10": ("eva@demo.flatwatch.hu", "Éva Tóth"),
          "L13": ("peter@demo.flatwatch.hu", "Péter Szabó"),
          "L15": ("peter@demo.flatwatch.hu", "Péter Szabó")}


def _user(db, email: str, name: str, role: str, password: str) -> int:
    r = db.one("SELECT id FROM users WHERE email=?", (email,))
    if r:
        return r["id"]
    cur = db.execute("INSERT INTO users(email, name, role, pw_hash, email_verified, unsub_token, created_at) "
                     "VALUES(?,?,?,?,1,?,?)", (email, name, role, hash_password(password), new_token(), time.time()))
    return cur.lastrowid


def bootstrap(db) -> None:
    if db.kv_get("bootstrapped") or not settings.include_samples:
        listings.seed_samples(db)
        return
    listings.seed_samples(db)

    # Sample photos (illustrations, clearly labelled) for every non-scam sample listing
    for r in db.query("SELECT id, payload FROM listings WHERE sample=1"):
        offer = json.loads(r["payload"]).get("offer_type") or "apartment"
        cats = (["Living room", "Bedroom", "Kitchen", "Bathroom", "Exterior"] if offer == "apartment"
                else ["Bedroom", "Kitchen", "Bathroom", "Exterior"])
        if r["id"] not in ("L04", "L09", "L14"):              # scams get no photos
            photos.make_sample_photos(db, r["id"], cats)

    # Demo landlords own the in-app sample posts (so chat works)
    for lid, (email, name) in OWNERS.items():
        uid = _user(db, email, name, "landlord", DEMO_PASSWORD)
        db.execute("UPDATE listings SET owner_id=? WHERE id=?", (uid, lid))

    # Demo tenant with a profile and an active saved search
    anna = _user(db, "anna@demo.flatwatch.hu", "Anna Kovács", "tenant", DEMO_PASSWORD)
    c = Criteria(user_name="Anna Kovács", age=22, gender="Female", languages=["Hungarian", "English"],
                 max_total_huf=170_000, max_commute_min=35, campus="main", wanted_types=["apartment", "room"],
                 about="2nd-year economics student, tidy and quiet.", documents_ready=True)
    searches.save_profile(db, anna, c)
    searches.create_search(db, anna, "Near the main campus", c, frequency="instant")

    # Sample tenants for landlord-side matching and flatmates
    for p in json.loads((DATA_DIR / "sample_profiles.json").read_text(encoding="utf-8"))["profiles"]:
        email = p["name"].split()[0].lower() + SAMPLE_DOMAIN
        uid = _user(db, email, p["name"], "tenant", secrets.token_urlsafe(16))
        crit = Criteria(
            user_name=p["name"], age=p["age"], gender=p["gender"], languages=p.get("languages", ["English"]),
            smoker=p.get("smoker", False), has_pet=p.get("has_pet", False), lifestyle=p.get("lifestyle", "balanced"),
            about=p.get("about", ""), documents_ready=p.get("documents_ready", False),
            wanted_types=p.get("wanted_types", ["apartment", "room", "bed"]), max_total_huf=p["budget_huf"],
            campus=p.get("campus", "main"), move_in=p.get("move_in", "2027-02-01"),
            pref_genders=p.get("pref_genders", []), need_furnished=False)
        searches.save_profile(db, uid, crit)
    _user(db, "admin@demo.flatwatch.hu", "Moderator", "admin", DEMO_PASSWORD)
    _seed_conversations(db, anna)
    db.kv_set("bootstrapped", True)


def _seed_conversations(db, anna: int) -> None:
    """A few realistic chats so the Messages page isn't empty in the demo."""
    from .auth import get_user
    from . import messaging
    a = get_user(db, anna)
    yas = get_user(db, db.one("SELECT id FROM users WHERE email='yasmin@demo.flatwatch.hu'")["id"])
    gab = get_user(db, db.one("SELECT id FROM users WHERE email='gabor@demo.flatwatch.hu'")["id"])
    cid, _ = messaging.start_conversation(db, a, "F01")
    if cid:
        messaging.send(db, a, cid, "Hi Yasmin! I'm Anna, 2nd-year economics student. Is the room still free from February?")
        messaging.send(db, yas, cid, "Hi Anna! Yes it is 🙂 The girls are really nice and quiet. Could you come on Thursday at 5pm?")
    cid, _ = messaging.start_conversation(db, a, "L07")
    if cid:
        messaging.send(db, a, cid, "Hello! Is the studio near Bem tér available from 1 February? Are utilities really included?")
        messaging.send(db, gab, cid, "Good afternoon! Yes, utilities and internet are included. Viewing is possible on weekdays after 4pm.")
    dora = db.one("SELECT id FROM users WHERE email='dóra@sample.flatwatch.hu' OR name='Dóra Kiss'")
    if dora:
        cid, _ = messaging.start_flatmate_chat(db, a, dora["id"])
        if cid:
            messaging.send(db, a, cid, "Hi Dóra! We're both on the main campus with a similar budget – want to look for a 2-room flat together?")
    db.execute("DELETE FROM rate_events")      # seeding shouldn't count against anyone's limits
    db.execute("DELETE FROM email_queue")
    for s in searches.user_searches(db, anna):    # Anna's "first matches" email is waiting in the test inbox
        searches.run_initial(db, s["id"])
