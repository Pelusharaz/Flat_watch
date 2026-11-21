"""Landlord / subletting-tenant side: which tenants fit a posted place?

Uses the SAME scoring engine as the tenant side (scoring.py), with each tenant's own
preferences, so both sides see consistent, explainable scores. Nationality is never used.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from . import llm
from .agent import evaluate
from .extract import extract_details
from .models import OFFER_LABEL, Criteria, Profile, RawListing, TenantMatch
from .prompts import TENANT_INVITE_SYSTEM, TENANT_INVITE_USER


def criteria_for(p: Profile) -> Criteria:
    return Criteria(
        profile_id=p.id, user_name=p.name, age=p.age, gender=p.gender, languages=p.languages,
        smoker=p.smoker, has_pet=p.has_pet, wanted_types=p.wanted_types, max_total_huf=p.budget_huf,
        max_commute_min=35, campus=p.campus, move_in=p.move_in, need_furnished=False,
        pref_genders=p.pref_genders,
    )


def tenants_for_offer(listing: RawListing, profiles: list[Profile],
                      median_per_m2: Optional[float] = None) -> tuple[list[TenantMatch], Counter]:
    """(fitting tenants best first, Counter of why the others were excluded)"""
    matches, excluded = [], Counter()
    for p in profiles:
        ev = evaluate(listing, criteria_for(p), [], median_per_m2, with_message=False)
        if ev.hard_fails or ev.decision in ("skip", "blocked"):
            for h in ev.hard_fails or [f"low match"]:
                key = ("not looking for this type" if "not looking" in h else
                       "over their budget" if "budget" in h.lower() else
                       "too far from their campus" if "Too far" in h else
                       "your gender requirement (shared)" if "looking for a" in h else
                       "their flatmate preference" if "flatmate" in h else "low match")
                excluded[key] += 1
            continue
        reasons = [c.name + ": " + c.detail for c in ev.criteria_scores if c.score >= 85 and c.key in ("budget", "commute", "move_in")]
        matches.append(TenantMatch(profile=p, score=ev.match_score, reasons=reasons,
                                   total_monthly_huf=ev.total_monthly_huf, commute_min=ev.commute_min))
    return sorted(matches, key=lambda m: m.score, reverse=True), excluded


def invite_message(listing: RawListing, m: TenantMatch) -> str:
    d, _ = extract_details(listing)
    offer = OFFER_LABEL[d.offer_type or "apartment"].lower()
    area = d.neighborhood or listing.location
    poster = (listing.poster_name or "the owner").split()[0]
    why = "; ".join(m.reasons[:2]) or f"{m.score}% match"
    if llm.is_enabled():
        try:
            text = llm.complete(TENANT_INVITE_SYSTEM, TENANT_INVITE_USER.format(
                poster=poster, tenant=m.profile.name, offer=offer, area=area, price=listing.price_huf, why=why),
                max_tokens=250, temperature=0.5).strip()
            if text:
                return text
        except Exception as exc:  # noqa: BLE001
            print(f"[tenants] LLM failed, using template: {exc}")
    price = f"{listing.price_huf:,}".replace(",", " ")
    return (f"Hi {m.profile.name.split()[0]}! I'm {poster}. I'm renting out a {offer} in {area} for {price} Ft/month "
            f"and it looks like a {m.score}% match for what you're looking for. "
            f"Would you like to come and see it this week?\n{poster}")
