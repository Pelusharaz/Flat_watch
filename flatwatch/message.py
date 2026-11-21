"""Tool: draft the first Hungarian message to the landlord."""
from __future__ import annotations

from . import llm
from .models import Criteria, ExtractedDetails, RawListing
from .prompts import MESSAGE_SYSTEM, MESSAGE_USER

HU_MONTHS = ["január", "február", "március", "április", "május", "június", "július",
             "augusztus", "szeptember", "október", "november", "december"]


def open_questions(details: ExtractedDetails, criteria: Criteria) -> list[str]:
    q = []
    if details.utilities_huf is None and not details.utilities_included:
        q.append("utilities")
    if criteria.has_pet and details.pets_allowed is None:
        q.append("pets")
    if details.deposit_huf is None:
        q.append("deposit")
    return q


def _article(word: str) -> str:
    """Hungarian definite article: 'az' before a vowel sound, else 'a'."""
    return "az" if word[:1].lower() in "aáeéiíoóöőuúüű" else "a"


HU_OFFER = {"apartment": "lakás", "room": "szoba", "bed": "férőhely (ágy)"}
EN_OFFER = {"apartment": "apartment", "room": "room", "bed": "bed space"}


def _template_en(listing: RawListing, details: ExtractedDetails, criteria: Criteria) -> str:
    area = details.neighborhood or listing.location
    price = f"{listing.price_huf:,}".replace(",", " ")
    offer = EN_OFFER[details.offer_type or "apartment"]
    hi = f"Hi {listing.poster_name.split()[0]}!" if listing.poster_name else "Hi!"
    me = f"I'm {criteria.user_name}"
    return (f"{hi}\n\nI saw your {offer} in {area} for {price} Ft/month on Flat-Watch. {me}, studying at the "
            f"University of Debrecen, and I'd like to move in from {criteria.move_in.strftime('%d %B %Y')}. "
            f"{'I am a non-smoker. ' if not criteria.smoker else ''}"
            f"When could I come and see it?\n\nThanks,\n{criteria.user_name}")


def _template(listing: RawListing, details: ExtractedDetails, criteria: Criteria) -> str:
    area = details.neighborhood or listing.location
    price = f"{listing.price_huf:,}".replace(",", " ")
    offer = HU_OFFER[details.offer_type or "apartment"]
    move = f"{criteria.move_in.year}. {HU_MONTHS[criteria.move_in.month - 1]} {criteria.move_in.day}-"
    questions = {
        "utilities": "Körülbelül mennyi a havi rezsi?",
        "pets": "Egy kisállattal költöznék – ez megengedett?",
        "deposit": "Mekkora kauciót kér?",
    }
    oq = open_questions(details, criteria)
    extra = f" {questions[oq[0]]}" if oq else ""
    return (
        "Jó napot kívánok!\n\n"
        f"Érdeklődnék {_article(listing.source)} {listing.source} oldalon hirdetett, "
        f"{area} környéki, {price} Ft/hó bérleti díjú {offer} iránt."
        f" A Debreceni Egyetem hallgatója vagyok, {move}től szeretnék beköltözni."
        f"{extra} Mikor lenne lehetőség megtekinteni?\n\n"
        f"Köszönöm a válaszát!\n{criteria.user_name}"
    )


def message_language(listing: RawListing) -> str:
    """Hungarian for listing-site ads; English for places posted in the app."""
    return "en" if listing.source == "Flat-Watch" else "hu"


def draft_message(listing: RawListing, details: ExtractedDetails, criteria: Criteria) -> tuple[str, str]:
    lang = message_language(listing)
    if llm.is_enabled():
        try:
            text = llm.complete(
                MESSAGE_SYSTEM.format(language="Hungarian" if lang == "hu" else "English",
                                      offer=EN_OFFER[details.offer_type or "apartment"]),
                MESSAGE_USER.format(
                    name=criteria.user_name,
                    move_in=criteria.move_in.isoformat(),
                    has_pet="yes" if criteria.has_pet else "no",
                    source=listing.source,
                    area=details.neighborhood or listing.location,
                    price=listing.price_huf,
                    open_questions=", ".join(open_questions(details, criteria)) or "none",
                ),
                max_tokens=400,
                temperature=0.4,
            ).strip()
            if text:
                return text, "llm"
        except Exception as exc:  # noqa: BLE001
            print(f"[message] LLM failed, using template: {exc}")
    tpl = _template_en if lang == "en" else _template
    return tpl(listing, details, criteria), "template"

