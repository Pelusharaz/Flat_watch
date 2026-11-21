"""Tool: turn the Hungarian free text of a listing into structured details.

Uses the LLM when one is configured; otherwise (or if the LLM fails) a
regex-based extractor, so the demo never dies because of an API problem.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Optional

from . import llm
from .commute import district_names, match_district
from .models import ExtractedDetails, RawListing
from .prompts import EXTRACT_SYSTEM, EXTRACT_USER

HU_MONTHS = {
    "januar": 1, "februar": 2, "marcius": 3, "aprilis": 4, "majus": 5, "junius": 6,
    "julius": 7, "augusztus": 8, "szeptember": 9, "oktober": 10, "november": 11, "december": 12,
}


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def parse_amount(text: str) -> Optional[int]:
    """'25-30e Ft' -> 30000, '25 000 Ft' -> 25000, '45.000' -> 45000, '40 ezer' -> 40000."""
    t = text.lower().replace(" ", " ")
    m = re.search(r"(\d[\d .]*?)\s*(?:-|–)\s*(\d[\d .]*)\s*(e\b|ezer|k\b)?", t)
    if m:
        num, unit = m.group(2), m.group(3)
    else:
        m = re.search(r"(\d[\d .]*)\s*(e\b|ezer|k\b)?", t)
        if not m:
            return None
        num, unit = m.group(1), m.group(2)
    digits = re.sub(r"[ .]", "", num)
    if not digits.isdigit():
        return None
    value = int(digits)
    if unit:
        value *= 1000
    return value


def _rule_extract(listing: RawListing, today: date) -> ExtractedDetails:
    text = f"{listing.title}\n{listing.description}"
    plain = strip_accents(text.lower())

    included = bool(re.search(r"rezsivel egyutt|rezsit tartalmaz|rezsi(?:vel)? benne|rezsivel", plain))
    utilities = None
    m = re.search(r"rezsi[^\d\n]{0,30}(\d[\d .\-–]*\s*(?:e\b|ezer|k\b)?)", plain)
    if m and not included:
        utilities = parse_amount(m.group(1))

    deposit = None
    m = re.search(r"(\d)\s*havi\s*kaucio", plain)
    if m:
        deposit = int(m.group(1)) * listing.price_huf
    else:
        m = re.search(r"kaucio[^\d\n]{0,20}(\d[\d .]*\s*(?:e\b|ezer|k\b)?)", plain)
        if m:
            deposit = parse_amount(m.group(1))

    furnished = None
    if re.search(r"butorozatlan|ures lakas|butor nelkul", plain):
        furnished = False
    elif re.search(r"butorozott|berendezett|gepesitett es butorozott", plain):
        furnished = True

    pets = None
    if re.search(r"(haziallat|kisallat|allat)\w*\s*(nem|nelkul|kizart)|nem\s*(hozhato|lehet)\s*(hazi|kis)?allat", plain):
        pets = False
    elif re.search(r"(haziallat|kisallat|allat)\w*\s*(hozhato|megengedett|engedelyezett|ok|lehet|johet)", plain):
        pets = True

    available = None
    if "azonnal" in plain:
        available = today
    else:
        m = re.search(r"(20\d\d)\.\s*(\d{1,2})\.\s*(\d{1,2})", plain)
        if m:
            available = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            for name, month in HU_MONTHS.items():
                if re.search(rf"\b{name}", plain):
                    year = today.year if month >= today.month else today.year + 1
                    available = date(year, month, 1)
                    break

    contact = (listing.contact or "").lower()
    if "whatsapp" in plain or "whatsapp" in contact:
        channel = "whatsapp"
    elif "@" in contact:
        channel = "email"
    elif "messenger" in contact:
        channel = "messenger"
    elif re.search(r"\d{6,}", re.sub(r"\D", "", contact) or ""):
        channel = "phone"
    else:
        channel = "unknown"

    # What is offered
    if re.search(r"\bagy(at)? kiado|agyat|ferohely|\bagy egy|megosztott szob|ketagyas szob|haromagyas", plain):
        offer = "bed"
    elif re.search(r"szoba kiado|szobat|szoba berlet|albérlet szoba|szoba egy|kiado szoba|szobaba", plain):
        offer = "room"
    else:
        offer = "apartment"
    # Who the landlord / flatmates want ("lánynak" = for a girl, "fiúnak" = for a boy)
    genders = []
    if re.search(r"lany(nak|oknak|ok)\b|nonek|holgynek|noi (berlo|lakotars)|csak lany|female only", plain):
        genders.append("Female")
    if re.search(r"fiu(nak|knak|k)\b|ferfinak|ferfi (berlo|lakotars)|csak fiu|male only", plain):
        genders.append("Male")

    return ExtractedDetails(
        offer_type=offer,
        pref_genders=genders,
        utilities_huf=utilities,
        utilities_included=included,
        deposit_huf=deposit,
        furnished=furnished,
        pets_allowed=pets,
        available_from=available,
        neighborhood=match_district(listing.location),
        contact_channel=channel,
        summary_en=(
            {"bed": "Bed in a shared room", "room": "Private room"}.get(
                offer, f"{format(listing.rooms, 'g') if listing.rooms else '?'}-room apartment")
            + f" ({listing.size_m2 or '?'} m²) in {match_district(listing.location) or listing.location} for "
            + f"{listing.price_huf:,}".replace(",", " ") + " Ft/month."
        ),
    )


def _merge_structured(d: ExtractedDetails, l: RawListing) -> ExtractedDetails:
    """Facts the poster entered in the app beat what we guessed from the text."""
    if l.offer_type:
        d.offer_type = l.offer_type
    if l.pref_genders:
        d.pref_genders = l.pref_genders
    for field in ("utilities_huf", "deposit_huf", "furnished", "available_from"):
        if getattr(l, field) is not None:
            setattr(d, field, getattr(l, field))
    if l.utilities_included is not None:
        d.utilities_included = l.utilities_included
    if l.pets_ok is not None:
        d.pets_allowed = l.pets_ok
    d.offer_type = d.offer_type or "apartment"
    return d


_CACHE: dict[str, tuple[ExtractedDetails, str]] = {}


def extract_details(listing: RawListing, today: Optional[date] = None) -> tuple[ExtractedDetails, str]:
    """Returns (details, method) where method is 'llm' or 'rules'. Cached per listing content,
    so the (possibly paid) LLM reads each listing once, not once per tenant."""
    import hashlib
    key = hashlib.sha1((listing.model_dump_json() + str(today)).encode()).hexdigest()
    if key not in _CACHE:
        if len(_CACHE) > 5000:
            _CACHE.clear()
        details, method = _extract(listing, today)
        _CACHE[key] = (_merge_structured(details, listing), method)
    d, m = _CACHE[key]
    return d.model_copy(deep=True), m


def _extract(listing: RawListing, today: Optional[date] = None) -> tuple[ExtractedDetails, str]:
    today = today or date.today()
    if llm.is_enabled():
        try:
            data = llm.complete_json(
                EXTRACT_SYSTEM,
                EXTRACT_USER.format(
                    today=today.isoformat(),
                    neighborhoods=", ".join(district_names()),
                    price=f"{listing.price_huf} Ft/month",
                    title=listing.title,
                    location=listing.location,
                    description=listing.description,
                    contact=listing.contact,
                ),
            )
            details = ExtractedDetails.model_validate(data)
            # Normalise the neighborhood to our table; fall back to rule matching
            details.neighborhood = match_district(details.neighborhood or "") or match_district(listing.location)
            return details, "llm"
        except Exception as exc:  # noqa: BLE001 - never crash the watcher
            print(f"[extract] LLM failed, using rules: {exc}")
    return _rule_extract(listing, today), "rules"
