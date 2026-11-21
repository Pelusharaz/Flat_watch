"""Demo helpers: create a new listing that fits a tenant, to show the alert flow end to end."""
from __future__ import annotations

import random
import time
from datetime import timedelta

from . import listings, mailer, searches
from .commute import destinations
from .models import Criteria, RawListing

AREAS = {"main": ("Nagyerdő", "Bem tér"), "medical": ("Nagyerdő", "Nagyerdei körút"),
         "kassai": ("Kassai út", "Kassai út"), "engineering": ("Belváros", "Hatvan utca"),
         "agri": ("Libakert", "Böszörményi út"), "center": ("Belváros", "Piac utca")}


def simulate_listing_for(db, c: Criteria) -> RawListing:
    """Publish a realistic new listing that matches criteria `c`, owned by a demo landlord."""
    from .photos import make_sample_photos
    owner = db.one("SELECT id, name FROM users WHERE email='gabor@demo.flatwatch.hu'") or \
        db.one("SELECT id, name FROM users WHERE role='landlord' ORDER BY id LIMIT 1")
    offer = c.wanted_types[0] if c.wanted_types else "apartment"
    district, street = AREAS.get(c.campus, ("Belváros", "Piac utca"))
    rng = random.Random(time.time())
    budget = c.max_total_huf
    util = {"apartment": 25_000, "room": 15_000, "bed": 0}[offer]
    price = int(round(budget * rng.uniform(0.62, 0.78) - util, -3))
    title = {"apartment": f"Bright furnished flat in {district}",
             "room": f"Private room in a friendly flat, {district}",
             "bed": f"Bed space near the university, {district}"}[offer]
    l = RawListing(
        id=f"D{int(time.time() * 1000) % 10_000_000}", source="Flat-Watch", title=title, price_huf=max(price, 30_000),
        location=f"Debrecen, {district}, {street}", size_m2={"apartment": 38, "room": 14, "bed": 22}[offer],
        rooms=max(1.0, c.min_rooms) if offer == "apartment" else 1, offer_type=offer, poster_role="owner",
        poster_name=owner["name"], furnished=True, utilities_huf=util or None, utilities_included=offer == "bed",
        available_from=c.move_in - timedelta(days=7), pets_ok=c.has_pet or None, smoking_ok=c.smoker or None,
        amenities=["Wi-Fi", "Washing machine", "Desk"],
        description=(f"Newly listed! Furnished {offer} in {district}, close to "
                     f"{destinations().get(c.campus, {}).get('label', 'the university')}. Quiet building, "
                     f"good public transport. Available from {c.move_in - timedelta(days=7):%d %B}."))
    listings.upsert(db, l, owner_id=owner["id"])
    make_sample_photos(db, l.id, ["Living room", "Bedroom", "Kitchen", "Exterior"] if offer == "apartment"
                       else ["Bedroom", "Kitchen", "Exterior"])
    return l


def run_alerts_now(db) -> dict:
    stats = searches.run_new_listings(db)
    stats["emails_sent"] = mailer.process_queue(db)
    return stats
