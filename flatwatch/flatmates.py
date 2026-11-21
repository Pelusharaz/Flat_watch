"""Flatmate matching: mutual preferences + compatibility score + intro message.

A match needs BOTH people's must-haves to be satisfied (gender for shared living,
language, age range, smoking, pets). Then a score ranks compatible pairs:
budget, campus distance, move-in date, shared languages, lifestyle.
"""
from __future__ import annotations

import json
from typing import Optional

from . import llm
from .commute import destinations
from .config import DATA_DIR
from .models import Evaluation, FlatmateMatch, Profile
from .prompts import INTRO_SYSTEM, INTRO_USER
from .transit import km

SAMPLE_PROFILES_FILE = DATA_DIR / "sample_profiles.json"

LANGUAGES = ["English", "Hungarian", "German", "Arabic", "Persian", "Turkish", "Urdu", "Hindi",
             "French", "Spanish", "Portuguese", "Russian", "Chinese", "Japanese", "Korean",
             "Vietnamese", "Norwegian", "Swedish", "Hebrew", "Other"]
GENDERS = ["Female", "Male", "Non-binary"]


def load_sample_profiles() -> list[Profile]:
    if not SAMPLE_PROFILES_FILE.exists():
        return []
    data = json.loads(SAMPLE_PROFILES_FILE.read_text(encoding="utf-8"))
    # (wanted_types etc. default sensibly if a sample doesn't specify them)
    return [Profile.model_validate({**p, "sample": True}) for p in data["profiles"]]


def _accepts(x: Profile, y: Profile) -> list[str]:
    """Reasons why x would NOT accept y as a flatmate (empty list = accepts)."""
    no = []
    if x.pref_genders and y.gender not in x.pref_genders:
        no.append(f"{x.name} prefers {'/'.join(x.pref_genders)} flatmates")
    if x.pref_languages and not set(x.pref_languages) & set(y.languages):
        no.append(f"{x.name} needs a shared language ({', '.join(x.pref_languages)})")
    if not (x.pref_age_min <= y.age <= x.pref_age_max):
        no.append(f"{x.name} prefers age {x.pref_age_min}–{x.pref_age_max}")
    if y.smoker and not x.ok_with_smoker:
        no.append(f"{x.name} doesn't want a smoker")
    if y.has_pet and not x.ok_with_pets:
        no.append(f"{x.name} doesn't want pets")
    return no


def _campus_km(a: str, b: str) -> Optional[float]:
    d = destinations()
    if a in d and b in d:
        return km((d[a]["lat"], d[a]["lon"]), (d[b]["lat"], d[b]["lon"]))
    return None


def compatibility(a: Profile, b: Profile) -> Optional[FlatmateMatch]:
    """None if either person's must-haves rule the other out."""
    if a.id == b.id or _accepts(a, b) or _accepts(b, a):
        return None

    score, reasons = 40, []
    shared = sorted(set(a.languages) & set(b.languages))
    if shared:
        score += 15
        reasons.append(f"Both speak {', '.join(shared)}")
    else:
        score -= 20
        reasons.append("⚠️ No common language listed")

    gap = abs(a.budget_huf - b.budget_huf) / max(a.budget_huf, b.budget_huf)
    if gap <= 0.15:
        score += 15
        reasons.append("Similar budget")
    elif gap > 0.4:
        score -= 10
        reasons.append("⚠️ Quite different budgets")

    dist = _campus_km(a.campus, b.campus)
    if a.campus == b.campus:
        score += 15
        reasons.append(f"Same campus ({destinations()[a.campus]['label'].split(' (')[0]})")
    elif dist is not None and dist < 1.5:
        score += 8
        reasons.append("Campuses close to each other")

    days = abs((a.move_in - b.move_in).days)
    if days <= 14:
        score += 10
        reasons.append("Same move-in time")
    elif days > 60:
        score -= 10
        reasons.append(f"⚠️ Move-in dates {days} days apart")

    if a.lifestyle == b.lifestyle:
        score += 5
        reasons.append(f"Both {a.lifestyle}")
    elif {a.lifestyle, b.lifestyle} == {"quiet", "social"}:
        score -= 10
        reasons.append("⚠️ Quiet vs social lifestyle")

    return FlatmateMatch(a=a, b=b, score=max(0, min(100, score)), reasons=reasons, shared_languages=shared)


def matches_for(me: Profile, others: list[Profile], limit: int = 10) -> list[FlatmateMatch]:
    found = [m for o in others if (m := compatibility(me, o))]
    return sorted(found, key=lambda m: m.score, reverse=True)[:limit]


def all_pairs(profiles: list[Profile], min_score: int = 60) -> list[FlatmateMatch]:
    """Every compatible pair in the community (each pair once)."""
    out = []
    for i, a in enumerate(profiles):
        for b in profiles[i + 1:]:
            m = compatibility(a, b)
            if m and m.score >= min_score:
                out.append(m)
    return sorted(out, key=lambda m: m.score, reverse=True)


def why_not(a: Profile, b: Profile) -> list[str]:
    return _accepts(a, b) + _accepts(b, a)


def intro_message(m: FlatmateMatch) -> str:
    common = "; ".join(r for r in m.reasons if not r.startswith("⚠️")) or "looking for a flat in Debrecen"
    if llm.is_enabled():
        try:
            text = llm.complete(INTRO_SYSTEM, INTRO_USER.format(
                from_name=m.a.name, to_name=m.b.name, common=common), max_tokens=250, temperature=0.6).strip()
            if text:
                return text
        except Exception as exc:  # noqa: BLE001
            print(f"[flatmates] LLM failed, using template: {exc}")
    phrases = []
    for r in m.reasons:
        if r.startswith("Both speak"):
            phrases.append("we both speak " + r.removeprefix("Both speak "))
        elif r == "Similar budget":
            phrases.append("our budgets are similar")
        elif r.startswith("Same campus"):
            phrases.append("we study on the same campus")
        elif r == "Campuses close to each other":
            phrases.append("our campuses are close")
        elif r == "Same move-in time":
            phrases.append("we want to move in around the same time")
        elif r.startswith("Both "):
            phrases.append(f"we both like a {r.removeprefix('Both ')} home")
    common = " and ".join(phrases[:2]) or "we're both looking for a place"
    return (f"Hi {m.b.name.split()[0]}! I'm {m.a.name.split()[0]}. Flat-Watch suggested us as flatmates – "
            f"{common}. Would you like to look for a 2-room flat in Debrecen together? "
            f"Happy to have a quick chat first.\n{m.a.name.split()[0]}")


def flats_for_pair(m: FlatmateMatch, evaluations: list[Evaluation], max_transit: int = 30) -> list[tuple[Evaluation, int]]:
    """Rentals with 2+ rooms the two can afford together, reachable from both campuses.
    Returns (evaluation, total per person)."""
    out = []
    budget = m.a.budget_huf + m.b.budget_huf
    for ev in evaluations:
        l = ev.listing
        if ev.decision == "blocked" or ev.details.offer_type not in (None, "apartment") or (l.rooms or 0) < 2:
            continue
        if ev.total_monthly_huf > budget:
            continue
        ok = all(ev.commutes.get(c, {}).get("transit", 999) <= max_transit for c in (m.a.campus, m.b.campus))
        if ok:
            out.append((ev, round(ev.total_monthly_huf / 2)))
    return sorted(out, key=lambda t: t[1])
