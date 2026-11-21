"""✨ Listing description helper for landlords (LLM if configured, otherwise a template)."""
from __future__ import annotations

from . import llm
from .models import OFFER_LABEL, RawListing

SYSTEM = """Write an honest, friendly rental listing description in English (4-6 sentences) for
students in Debrecen, using ONLY the facts given. No exaggeration, no invented features.
Do not mention nationality or any protected characteristic. Output the description only."""


def describe(l: RawListing, area: str, commute_line: str = "") -> str:
    facts = {
        "type": OFFER_LABEL[l.offer_type or "apartment"], "area": area, "size_m2": l.size_m2, "rooms": l.rooms,
        "rent_huf": l.price_huf, "utilities_included": l.utilities_included, "utilities_huf": l.utilities_huf,
        "deposit_huf": l.deposit_huf, "furnished": l.furnished, "available_from": str(l.available_from or ""),
        "amenities": l.amenities, "pets_ok": l.pets_ok, "smoking_ok": l.smoking_ok,
        "beds_in_room": l.beds_in_room, "getting_to_university": commute_line,
    }
    if llm.is_enabled():
        try:
            text = llm.complete(SYSTEM, str(facts), max_tokens=300, temperature=0.5).strip()
            if text:
                return text
        except Exception as exc:  # noqa: BLE001
            print(f"[describe] LLM failed: {exc}")
    kind = OFFER_LABEL[l.offer_type or "apartment"].lower()
    parts = [f"{'Furnished' if l.furnished else 'Bright'} {kind} in {area}"
             + (f", {l.size_m2} m²" if l.size_m2 else "") + "."]
    if l.offer_type == "bed" and l.beds_in_room:
        parts.append(f"One bed in a {l.beds_in_room}-bed room.")
    if l.amenities:
        parts.append("Includes " + ", ".join(a.lower() for a in l.amenities) + ".")
    money = f"Rent is {l.price_huf:,} Ft/month".replace(",", " ")
    money += " with utilities included." if l.utilities_included else (
        f" plus about {l.utilities_huf:,} Ft utilities.".replace(",", " ") if l.utilities_huf else ".")
    parts.append(money)
    if commute_line:
        parts.append(commute_line + ".")
    if l.available_from:
        parts.append(f"Available from {l.available_from:%d %B %Y}.")
    parts.append(("Pets are welcome. " if l.pets_ok else "") + ("Non-smoking." if l.smoking_ok is False else ""))
    return " ".join(p for p in parts if p.strip())
