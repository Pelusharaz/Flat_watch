"""Explainable, configurable match scoring (weights and curves in data/scoring.json).

Hard constraints (exclude a place):
  - type of place the tenant doesn't want
  - total cost above budget x budget.hard_limit_factor
  - commute above max x commute.hard_limit_factor
  - SHARED rooms/beds only: the poster's gender requirement, or the tenant's own
    flatmate-gender preference vs the current occupants (flatmate privacy)
Everything else is a soft, weighted criterion -> overall 0-100 score with a breakdown.
Nationality is never an input.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Optional

from .config import DATA_DIR
from .models import CriterionScore, Criteria, ExtractedDetails, RawListing

CONFIG_FILE = DATA_DIR / "scoring.json"

NAMES = {
    "budget": "Budget", "commute": "Commute", "move_in": "Move-in date", "size_rooms": "Size / rooms",
    "furnishing": "Furnishing", "pets": "Pets", "smoking": "Smoking", "language": "Common language",
    "amenities": "Amenities",
}


@lru_cache(maxsize=1)
def config() -> dict:
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def reload_config() -> None:
    config.cache_clear()


def curve(points: list[list[float]], x: float) -> float:
    """Piecewise-linear interpolation, clamped at both ends."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y1
    return points[-1][1]


def commute_category(minutes: Optional[int], max_min: int) -> str:
    if minutes is None:
        return "Unknown"
    ratio = minutes / max(max_min, 1)
    for limit, name in config()["commute"]["categories"]:
        if ratio <= limit:
            return name
    return "Too far"


def effective_weights(criteria: Criteria) -> dict[str, float]:
    w = dict(config()["weights"])
    for k, mult in criteria.weights_override.items():
        if k in w:
            w[k] = w[k] * mult
    return w


def score_listing(
    listing: RawListing, d: ExtractedDetails, criteria: Criteria, total: int,
    minutes: Optional[int], utilities_estimated: bool,
) -> tuple[list[CriterionScore], list[str], int]:
    """Returns (criterion scores, hard fails, overall 0-100)."""
    cfg = config()
    soft = cfg["soft"]
    w = effective_weights(criteria)
    offer = d.offer_type or "apartment"
    out: list[CriterionScore] = []
    hard: list[str] = []

    def add(key: str, s: float, detail: str, missing: bool = False, applicable: bool = True) -> None:
        out.append(CriterionScore(key=key, name=NAMES[key], score=round(100 * max(0.0, min(1.0, s))),
                                  weight=w.get(key, 0) if applicable else 0, detail=detail,
                                  missing=missing, applicable=applicable))

    # Type (hard)
    if offer not in criteria.wanted_types:
        hard.append(f"{offer.capitalize()} – you're not looking for this type")

    # Budget
    ratio = total / max(criteria.max_total_huf, 1)
    if ratio > cfg["budget"]["hard_limit_factor"]:
        hard.append(f"Over budget: {total:,} Ft vs your max {criteria.max_total_huf:,} Ft".replace(",", " "))
    note = " (utilities estimated)" if utilities_estimated else ""
    add("budget", curve(cfg["budget"]["curve"], ratio),
        f"{total:,} Ft/month = {ratio:.0%} of your budget{note}".replace(",", " "), missing=utilities_estimated)

    # Commute
    if minutes is None:
        add("commute", 0.5, "Location unknown", missing=True)
    else:
        r = minutes / max(criteria.max_commute_min, 1)
        if r > cfg["commute"]["hard_limit_factor"]:
            hard.append(f"Too far: {minutes} min (your max {criteria.max_commute_min})")
        add("commute", curve(cfg["commute"]["curve"], r),
            f"{minutes} min – {commute_category(minutes, criteria.max_commute_min)} (max {criteria.max_commute_min})")

    # Move-in
    if d.available_from is None:
        add("move_in", cfg["move_in"]["missing"], "Availability date not stated", missing=True)
    else:
        late = (d.available_from - criteria.move_in).days
        add("move_in", curve(cfg["move_in"]["curve"], max(0, late)),
            "Available in time" if late <= 0 else f"Available {late} days after your move-in date")

    # Size / rooms (whole apartments only)
    if offer == "apartment" and listing.rooms is not None and listing.rooms < criteria.min_rooms:
        add("size_rooms", soft["too_few_rooms"], f"{listing.rooms:g} room(s), you want {criteria.min_rooms:g}+")
    elif offer == "apartment" and listing.rooms is None:
        add("size_rooms", 0.7, "Number of rooms not stated", missing=True)
    else:
        add("size_rooms", 1.0, f"{listing.size_m2 or '?'} m²", applicable=offer == "apartment")

    # Furnishing
    if not criteria.need_furnished:
        add("furnishing", 1.0, "No preference", applicable=False)
    elif d.furnished is True:
        add("furnishing", 1.0, "Furnished")
    elif d.furnished is False:
        add("furnishing", soft["unfurnished"], "Unfurnished")
    else:
        add("furnishing", soft["furnishing_unknown"], "Furnishing not stated", missing=True)

    # Pets (soft)
    if not criteria.has_pet:
        add("pets", 1.0, "You have no pet", applicable=False)
    elif d.pets_allowed is True:
        add("pets", 1.0, "Pets allowed")
    elif d.pets_allowed is False:
        add("pets", soft["pets_not_allowed"], "Pets not allowed – ask the landlord")
    else:
        add("pets", soft["pets_unknown"], "Pet policy not stated", missing=True)

    # Smoking (soft)
    if not criteria.smoker:
        add("smoking", 1.0, "You don't smoke", applicable=False)
    elif listing.smoking_ok is True:
        add("smoking", 1.0, "Smoking allowed")
    elif listing.smoking_ok is False:
        add("smoking", soft["smoking_not_allowed"], "No smoking in this place")
    else:
        add("smoking", soft["smoking_unknown"], "Smoking policy not stated", missing=True)

    # Language (soft): flatmates/landlord ask for a common language
    if listing.pref_languages and not set(listing.pref_languages) & set(criteria.languages):
        add("language", soft["no_common_language"], f"They ask for: {', '.join(listing.pref_languages)}")
    else:
        add("language", 1.0, "You share a language they ask for" if listing.pref_languages else "No language requirement",
            applicable=bool(listing.pref_languages))

    # Amenities
    if criteria.wanted_amenities:
        have = set(listing.amenities) & set(criteria.wanted_amenities)
        known = bool(listing.amenities)
        add("amenities", len(have) / len(criteria.wanted_amenities) if known else 0.5,
            (f"{len(have)}/{len(criteria.wanted_amenities)} wanted amenities" + (f": {', '.join(sorted(have))}" if have else ""))
            if known else "Amenities not listed", missing=not known)
    else:
        add("amenities", 1.0, "No amenity preferences", applicable=False)

    # Shared living: who you'd share a room/bathroom with (hard, by design)
    if offer in ("room", "bed"):
        if d.pref_genders and criteria.gender not in d.pref_genders:
            hard.append(f"Shared {offer}: flatmates are looking for a {'/'.join(d.pref_genders).lower()} tenant")
        occ = {o.get("gender") for o in listing.occupants if o.get("gender")}
        if criteria.pref_genders and occ - set(criteria.pref_genders):
            hard.append("Current flatmates don't match your flatmate-gender preference")

    total_w = sum(c.weight for c in out) or 1
    overall = round(sum(c.score * c.weight for c in out) / total_w)
    return out, hard, overall


def explain(scores: list[CriterionScore]) -> tuple[list[str], list[str], list[str]]:
    """(reasons it's a good match, factors reducing it, missing information)"""
    pos = [f"{c.name}: {c.detail}" for c in scores if c.applicable and c.score >= 85 and not c.missing]
    neg = [f"{c.name}: {c.detail}" for c in scores if c.applicable and c.score < 70 and not c.missing]
    miss = [c.detail for c in scores if c.missing]
    return pos, neg, miss


def apply_feedback(criteria: Criteria, criterion_key: str) -> Criteria:
    """A user disliked a match because of `criterion_key` -> weight it more for them."""
    fb = config()["feedback"]
    if criterion_key not in config()["weights"]:
        return criteria
    cur = criteria.weights_override.get(criterion_key, 1.0)
    new = dict(criteria.weights_override)
    new[criterion_key] = round(min(fb["max_multiplier"], cur * fb["step"]), 3)
    return criteria.model_copy(update={"weights_override": new})
