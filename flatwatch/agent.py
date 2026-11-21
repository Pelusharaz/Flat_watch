"""The agent: runs its tools on one place for one tenant and explains the result.

  1. extract_details  - read the listing text (LLM or rules)
  2. monthly_cost     - rent + utilities (estimated if missing)
  3. commute          - walk / bike / bus+tram to every campus (line + stops)
  4. score            - configurable weighted criteria + hard constraints (scoring.py)
  5. scam_check       - rule flags + LLM judgement
  6. decide           - ALERT (>= alert threshold) / MAYBE / SKIP / BLOCKED
  7. draft_message    - first message to the landlord / subletting tenant
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .commute import commute_table, destinations, locate
from .extract import extract_details
from .message import draft_message, message_language
from .models import MODE_LABEL, Criteria, Evaluation, RawListing, TraceStep
from .scam import assess
from .scoring import commute_category, config, explain, score_listing

UTILITIES_PER_M2_ESTIMATE = 550
UTILITIES_ESTIMATE = {"room": 20_000, "bed": 12_000}


def _fmt(n: Optional[int]) -> str:
    return "?" if n is None else f"{n:,}".replace(",", " ") + " Ft"


def monthly_total(listing: RawListing, d) -> tuple[int, int, bool]:
    """(total, utilities, utilities_estimated)"""
    offer = d.offer_type or "apartment"
    if d.utilities_included:
        return listing.price_huf, 0, False
    if d.utilities_huf is not None:
        return listing.price_huf + d.utilities_huf, d.utilities_huf, False
    est = UTILITIES_ESTIMATE.get(offer) or (listing.size_m2 or 0) * UTILITIES_PER_M2_ESTIMATE or 30_000
    return listing.price_huf + est, est, True


def evaluate(
    listing: RawListing,
    criteria: Criteria,
    history: list[RawListing],
    median_per_m2: Optional[float],
    today: Optional[date] = None,
    with_message: bool = True,
) -> Evaluation:
    trace: list[TraceStep] = []

    d, method = extract_details(listing, today)
    trace.append(TraceStep(tool=f"extract_details ({method})", result=d.summary_en or "done"))

    total, utilities, estimated = monthly_total(listing, d)
    trace.append(TraceStep(tool="monthly_cost", result=f"{_fmt(listing.price_huf)} rent + {_fmt(utilities)} utilities"
                           + (" (estimated)" if estimated else "") + f" = {_fmt(total)}"))

    commutes, basis, near = commute_table(listing.location, d.neighborhood)
    point, _ = locate(listing.location, d.neighborhood)
    mine = commutes.get(criteria.campus)
    minutes = mine.get(criteria.commute_mode) if mine else None
    campus = destinations().get(criteria.campus, {}).get("label", criteria.campus).split(" (")[0]
    category = commute_category(minutes, criteria.max_commute_min)
    if mine:
        how = f" – {mine['how']}" if criteria.commute_mode == "transit" else ""
        trace.append(TraceStep(tool="commute", result=f"from {basis}: {campus} {minutes} min "
                               f"{MODE_LABEL[criteria.commute_mode]} ({category}){how}"))
    else:
        trace.append(TraceStep(tool="commute", result=f"location unknown ({listing.location})"))

    scores, hard, overall = score_listing(listing, d, criteria, total, minutes, estimated)
    positives, negatives, missing = explain(scores)
    trace.append(TraceStep(tool="score", result=f"{overall}/100 – " + ", ".join(f"{c.name} {c.score}" for c in scores)
                           + (f"; HARD: {'; '.join(hard)}" if hard else "")))

    scam, scam_method = assess(listing, d, history, median_per_m2)
    trace.append(TraceStep(tool=f"scam_check ({scam_method})",
                           result=f"risk {scam.level.upper()} (score {scam.score}). {scam.explanation}"))

    th = config()["thresholds"]
    if scam.level == "high":
        decision = "blocked"
        reasons = [f"Likely scam ({len(scam.flags)} red flags) – " + "; ".join(f.detail for f in scam.flags[:3])]
        overall = 0
    elif hard:
        decision, reasons = "skip", hard
    elif scam.level == "medium":
        decision, reasons = "maybe", [f"Scam risk medium: {scam.explanation}"] + positives[:3]
    elif overall >= th["alert"]:
        decision, reasons = "alert", positives[:4] or [f"{overall}% match"]
    elif overall >= th["maybe"]:
        decision, reasons = "maybe", negatives[:3] + positives[:2]
    else:
        decision, reasons = "skip", [f"Low match ({overall}%)"] + negatives[:3]
    trace.append(TraceStep(tool="decide", result=f"{decision.upper()} – " + "; ".join(reasons)))

    message = ""
    if with_message and decision in ("alert", "maybe"):
        message, msg_method = draft_message(listing, d, criteria)
        trace.append(TraceStep(tool=f"draft_message ({msg_method})", result="message ready"))

    return Evaluation(
        listing=listing, details=d, total_monthly_huf=total, commute_min=minutes, commute_category=category,
        commutes=commutes, nearest_stops=near, commute_basis=basis,
        lat=point[0] if point else None, lon=point[1] if point else None,
        scam=scam, decision=decision, reasons=reasons, match_score=overall if decision != "blocked" else 0,
        criteria_scores=scores, positives=positives, negatives=negatives, missing=missing, hard_fails=hard,
        message_hu=message, message_lang=message_language(listing), trace=trace,
    )
