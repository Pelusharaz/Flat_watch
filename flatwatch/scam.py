"""Tool: scam detection = deterministic rule flags + LLM judgement/explanation."""
from __future__ import annotations

import re
from statistics import median
from typing import Iterable, Optional

from . import llm
from .config import settings
from .extract import strip_accents
from .models import ExtractedDetails, RawListing, ScamFlag, ScamReport
from .prompts import SCAM_SYSTEM, SCAM_USER

# (code, regex on accent-free lowercase text, weight, human explanation)
TEXT_RULES = [
    ("owner_abroad", r"kulfoldon (el|tartozkod|dolgoz|vagyok)|kulfoldre koltoz|currently abroad|i live abroad|nem vagyok magyarorszagon",
     3, "Owner says they are abroad and can't show the flat in person"),
    ("keys_by_post", r"kulcs\w* (postan|futarral|postai)|postan kuldom|send (you )?the keys",
     3, "Keys would be sent by post/courier instead of a viewing"),
    ("pay_before_viewing", r"(megtekintes|nezes|lakas megnezese) elott\w* (utal|fizet|kaucio)|(elore|azonnal) (at)?utal|foglalot elore|elore (utalja|utald)|(utald|utalja) at .{0,30}(foglal|kaucio)|western union|moneygram|revolut(on)? keresztul",
     4, "Asks for money/deposit before you have seen the flat"),
    ("platform_payment", r"airbnb(-n|-en)? keresztul|booking(\.com)? (rendszeren|keresztul)",
     2, "Wants payment through a 'booking platform' outside the normal process"),
    ("urgency", r"sok (az )?erdeklodo|meg ma|ma (kell|dont)|siess|azonnal dont|csak ma|first come",
     1, "Pressure to decide or pay quickly"),
    ("email_only", r"csak (e-?mail|emailben)|kizarolag (e-?mail|emailben)|only (by )?email",
     2, "Contact only by email (no phone, no viewing)"),
]

FOREIGN_PHONE = re.compile(r"\+(?!36)\d{2}")


def _is_whole_apartment(l: RawListing) -> bool:
    if l.offer_type:
        return l.offer_type == "apartment"
    t = strip_accents(f"{l.title} {l.description}".lower())
    return not re.search(r"szoba kiado|agy kiado|ferohely|kiado szoba", t)


def market_median_per_m2(listings: Iterable[RawListing]) -> Optional[float]:
    """Median rent per m² of WHOLE apartments (rooms/beds are priced differently)."""
    values = [l.price_huf / l.size_m2 for l in listings if l.size_m2 and _is_whole_apartment(l)]
    return median(values) if len(values) >= 5 else None


def _similar(a: str, b: str) -> float:
    """Jaccard similarity of word 3-grams (good enough for reposted texts)."""
    def grams(t: str) -> set:
        w = re.findall(r"\w+", strip_accents(t.lower()))
        return {" ".join(w[i:i + 3]) for i in range(max(len(w) - 2, 0))}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def rule_flags(
    listing: RawListing,
    details: ExtractedDetails,
    history: list[RawListing],
    median_per_m2: Optional[float],
) -> list[ScamFlag]:
    text = strip_accents(f"{listing.title}\n{listing.description}".lower())
    flags = [ScamFlag(code=c, detail=d, weight=w) for c, rx, w, d in TEXT_RULES if re.search(rx, text)]

    if details.contact_channel == "whatsapp" or FOREIGN_PHONE.search(listing.contact or ""):
        flags.append(ScamFlag(code="foreign_or_whatsapp", weight=2,
                              detail=f"Contact via WhatsApp / foreign number ({listing.contact})"))

    if median_per_m2 and listing.size_m2 and details.offer_type in (None, "apartment"):
        ratio = (listing.price_huf / listing.size_m2) / median_per_m2
        if ratio < settings.underprice_ratio:
            flags.append(ScamFlag(code="too_cheap", weight=3,
                                  detail=f"Price per m² is {ratio:.0%} of the Debrecen median"))

    for old in history:
        if old.id == listing.id:
            continue
        same_photo = set(old.photo_hashes) & set(listing.photo_hashes)
        sim = _similar(old.description, listing.description)
        if same_photo or sim > 0.6:
            if old.price_huf != listing.price_huf or old.contact != listing.contact:
                why = "same photos" if same_photo else f"{sim:.0%} identical text"
                flags.append(ScamFlag(code="reposted", weight=3,
                                      detail=f"Looks like a repost of '{old.title}' ({why}) with a different price/contact"))
                break
    return flags


def _level(score: int) -> str:
    return "high" if score >= 5 else "medium" if score >= 2 else "low"


def assess(
    listing: RawListing,
    details: ExtractedDetails,
    history: list[RawListing],
    median_per_m2: Optional[float],
) -> tuple[ScamReport, str]:
    flags = rule_flags(listing, details, history, median_per_m2)
    score = sum(f.weight for f in flags)
    report = ScamReport(flags=flags, score=score, level=_level(score))

    if not flags:
        report.explanation = "No red flags found."
        return report, "rules"

    if llm.is_enabled():
        try:
            data = llm.complete_json(
                SCAM_SYSTEM,
                SCAM_USER.format(
                    price=listing.price_huf, size=listing.size_m2, location=listing.location,
                    description=listing.description, contact=listing.contact,
                    flags="; ".join(f.detail for f in flags),
                ),
                max_tokens=300,
            )
            llm_level = str(data.get("level", report.level)).lower()
            # The LLM may raise the level but never lower a rule-based "high"
            order = ["low", "medium", "high"]
            if llm_level in order and order.index(llm_level) > order.index(report.level):
                report.level = llm_level
            report.explanation = data.get("explanation") or report.explanation
            return report, "llm"
        except Exception as exc:  # noqa: BLE001
            print(f"[scam] LLM failed, using rules: {exc}")

    report.explanation = "Red flags: " + "; ".join(f.detail for f in flags) + "."
    return report, "rules"
