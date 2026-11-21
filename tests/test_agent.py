"""Run with:  python -m pytest -q"""
from datetime import date

import pytest

from flatwatch.agent import evaluate
from flatwatch.extract import extract_details, parse_amount
from flatwatch.models import Criteria
from flatwatch.scam import market_median_per_m2
from flatwatch.scoring import apply_feedback, commute_category, curve
from flatwatch.sources.simulator import _read
from flatwatch.config import settings

TODAY = date(2026, 11, 21)
LISTINGS = {l.id: l for l in _read(settings.listings_file, sample=True)}
MEDIAN = market_median_per_m2(LISTINGS.values())
CRIT = Criteria(max_commute_min=40)


def ev(lid, crit=CRIT, history=()):
    return evaluate(LISTINGS[lid], crit, list(history), MEDIAN, TODAY)


# ------------------------------------------------------------------ extraction --
@pytest.mark.parametrize("text,expected", [
    ("30e Ft", 30_000), ("25-30e", 30_000), ("25 000 Ft", 25_000), ("45.000", 45_000), ("40 ezer", 40_000)])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_extract_room_bed_gender():
    d, _ = extract_details(LISTINGS["L16"], TODAY)
    assert d.offer_type == "room" and d.pref_genders == ["Female"] and d.utilities_huf == 20_000
    d, _ = extract_details(LISTINGS["L17"], TODAY)
    assert d.offer_type == "bed" and d.pref_genders == ["Male"] and d.utilities_included


# ------------------------------------------------------------- 1A commute curve --
def test_commute_curve_matches_spec():
    pts = [[0.0, 1.0], [0.375, 1.0], [0.625, 0.85], [0.875, 0.6], [1.0, 0.45], [1.25, 0.0]]
    s = {m: curve(pts, m / 40) for m in (10, 15, 20, 25, 30, 35, 40)}
    assert s[10] == s[15] == 1.0                    # excellent
    assert 0.85 <= s[20] <= 0.95 and s[25] == 0.85  # good
    assert 0.6 <= s[30] <= 0.75 and s[35] == 0.6    # average
    assert s[40] == 0.45                            # lower – but NOT excluded
    assert [commute_category(m, 40) for m in (12, 25, 38, 45)] == ["Near", "Average", "Far", "Too far"]


def test_commute_is_soft_until_hard_limit():
    near = ev("L07", Criteria(max_commute_min=40))
    far = ev("L08", Criteria(max_commute_min=30))           # 34 min vs max 30: over, but within 125%
    assert far.decision != "skip" or "Too far" not in " ".join(far.hard_fails)
    assert near.match_score > far.match_score
    too_far = ev("L08", Criteria(max_commute_min=20))       # 34 > 25 -> hard
    assert any("Too far" in h for h in too_far.hard_fails)


def test_bike_and_walk_modes():
    e = ev("L02")
    t = e.commutes["main"]
    assert t["bike"] < t["walk"]
    assert ev("L02", Criteria(commute_mode="bike", max_commute_min=40)).commute_min == t["bike"]


# ----------------------------------------------------------- 1B soft penalties --
def test_pets_and_smoking_are_moderate_not_exclusions():
    base = ev("F01")
    pet_smoker = ev("F01", Criteria(max_commute_min=40, has_pet=True, smoker=True))
    assert not pet_smoker.hard_fails                       # F01 says no pets, no smoking – still not excluded
    assert 5 <= base.match_score - pet_smoker.match_score <= 15
    assert pet_smoker.decision in ("alert", "maybe")


def test_weights_are_configurable(monkeypatch):
    from flatwatch import scoring
    cfg = dict(scoring.config())
    monkeypatch.setattr(scoring, "config", lambda: {**cfg, "weights": {**cfg["weights"], "commute": 0}})
    a = ev("L03")                                          # far but cheap
    monkeypatch.setattr(scoring, "config", lambda: cfg)
    b = ev("L03")
    assert a.match_score > b.match_score


def test_feedback_personalises_weights():
    c = apply_feedback(apply_feedback(CRIT, "commute"), "commute")
    assert c.weights_override["commute"] == pytest.approx(1.15 * 1.15, rel=1e-3)
    assert ev("L03", c).match_score < ev("L03").match_score    # commute matters more now


# -------------------------------------------------------------- 1C nationality --
def test_no_nationality_anywhere():
    from flatwatch.models import Criteria as C, Profile, RawListing
    for model in (C, Profile, RawListing):
        assert not any("national" in f for f in model.model_fields), model
    import json
    raw = (settings.listings_file and open(settings.listings_file, encoding="utf-8").read()).lower()
    assert "nationalit" not in raw


def test_gender_only_for_shared_living():
    male = Criteria(gender="Male", max_commute_min=40)
    assert any("looking for a female" in h for h in ev("L16", male).hard_fails)     # room in a girls' flat
    apt = LISTINGS["F02"].model_copy(update={"pref_genders": ["Female"]})          # gender on a WHOLE apartment
    e = evaluate(apt, male, [], MEDIAN, TODAY)
    assert not e.hard_fails                                                         # ignored


# ------------------------------------------------------------- 1D explanations --
def test_explainable_breakdown():
    e = ev("L06")                       # utilities + furnishing not stated
    keys = {c.key for c in e.criteria_scores}
    assert {"budget", "commute", "move_in", "furnishing", "pets", "smoking"} <= keys
    assert e.missing and any("Furnishing" in m for m in e.missing)
    assert all(0 <= c.score <= 100 for c in e.criteria_scores)
    good = ev("L07")
    assert good.decision == "alert" and good.positives and good.match_score >= 85


@pytest.mark.parametrize("lid,decision", [
    ("L01", "skip"), ("L02", "alert"), ("L04", "blocked"), ("L09", "skip"), ("L14", "blocked"),
    ("L16", "alert"), ("L17", "skip"), ("F01", "alert")])
def test_decisions(lid, decision):
    hist = [LISTINGS["L02"]] if lid == "L09" else []
    e = ev(lid, history=hist)
    if lid == "L09":                    # repost of L02 with UK WhatsApp number -> scam
        assert e.decision == "blocked"
    else:
        assert e.decision == decision, (e.reasons, e.hard_fails)


def test_in_app_post_english_message_and_hungarian_for_sites():
    assert ev("F01").message_lang == "en" and ev("F01").message_hu.startswith("Hi Yasmin")
    assert ev("L03").message_hu.startswith("Jó napot")          # Hungarian ad from a listing site
