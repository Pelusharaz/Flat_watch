"""Data models shared across the app."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

OfferType = Literal["apartment", "room", "bed"]
CommuteMode = Literal["transit", "walk", "bike"]
OFFER_LABEL = {"apartment": "Whole apartment", "room": "Private room", "bed": "Bed in a shared room"}
OFFER_ICON = {"apartment": "🏢", "room": "🚪", "bed": "🛏️"}
ROLE_LABEL = {"site": "Listing site", "owner": "Owner / landlord", "tenant": "Current tenant (sublet)"}
MODE_LABEL = {"transit": "🚋 Bus / tram", "walk": "🚶 Walking", "bike": "🚲 Cycling"}
AMENITIES = ["Wi-Fi", "Washing machine", "Dishwasher", "Air conditioning", "Balcony", "Elevator",
             "Parking", "Bike storage", "Desk", "Oven"]
PHOTO_CATEGORIES = ["Living room", "Bedroom", "Kitchen", "Bathroom", "Exterior", "Floor plan", "Other"]


class RawListing(BaseModel):
    """A place offered for rent: from a listing site, or posted in the app by an owner or tenant.

    Nationality is deliberately NOT a field: it must never be used to rank or exclude tenants.
    """
    id: str
    source: str                      # "ingatlan.com", "Jófogás", "Facebook", "Flat-Watch" (posted in app)
    title: str
    price_huf: int                   # monthly rent for the whole offer (apartment / room / one bed)
    location: str
    size_m2: Optional[int] = None
    rooms: Optional[float] = None
    description: str
    contact: str = ""                # kept private: tenants use the in-app chat
    url: str = ""
    photo_hashes: list[str] = Field(default_factory=list)
    sample: bool = False

    offer_type: Optional[OfferType] = None
    poster_role: Literal["site", "owner", "tenant"] = "site"
    poster_name: str = ""
    # Who can live there. Gender only for SHARED rooms/beds (flatmates' privacy); ignored for apartments.
    pref_genders: list[str] = Field(default_factory=list)
    pref_languages: list[str] = Field(default_factory=list)   # e.g. flatmates need a common language
    occupants: list[dict[str, str]] = Field(default_factory=list)   # [{"gender": ..}]
    beds_in_room: Optional[int] = None
    utilities_huf: Optional[int] = None
    utilities_included: Optional[bool] = None
    deposit_huf: Optional[int] = None
    furnished: Optional[bool] = None
    available_from: Optional[date] = None
    smoking_ok: Optional[bool] = None
    pets_ok: Optional[bool] = None
    amenities: list[str] = Field(default_factory=list)


class ExtractedDetails(BaseModel):
    offer_type: Optional[OfferType] = None
    pref_genders: list[str] = Field(default_factory=list)
    utilities_huf: Optional[int] = None
    utilities_included: bool = False
    deposit_huf: Optional[int] = None
    furnished: Optional[bool] = None
    pets_allowed: Optional[bool] = None
    available_from: Optional[date] = None
    neighborhood: Optional[str] = None
    contact_channel: Optional[str] = None
    summary_en: str = ""


class Criteria(BaseModel):
    """A tenant's profile + search preferences (one saved search)."""
    profile_id: str = ""
    user_name: str = "Student"
    age: int = 22
    gender: str = "Female"
    languages: list[str] = Field(default_factory=lambda: ["English"])
    smoker: bool = False
    has_pet: bool = False
    lifestyle: Literal["quiet", "balanced", "social"] = "balanced"
    about: str = ""
    documents_ready: bool = False      # can provide the ID/residence papers a lease needs (shown, never scored)
    publish: bool = True               # landlords / flatmates can find my profile (contact stays private)

    wanted_types: list[OfferType] = Field(default_factory=lambda: ["apartment", "room", "bed"])
    max_total_huf: int = 180_000
    max_commute_min: int = 30
    min_rooms: float = 1
    move_in: date = date(2027, 2, 1)
    need_furnished: bool = True
    campus: str = "main"
    commute_mode: CommuteMode = "transit"
    wanted_amenities: list[str] = Field(default_factory=list)
    pref_genders: list[str] = Field(default_factory=list)          # flatmates (rooms / beds only)
    weights_override: dict[str, float] = Field(default_factory=dict)   # learned from feedback


class ScamFlag(BaseModel):
    code: str
    detail: str
    weight: int


class ScamReport(BaseModel):
    flags: list[ScamFlag] = Field(default_factory=list)
    score: int = 0
    level: Literal["low", "medium", "high"] = "low"
    explanation: str = ""


Decision = Literal["alert", "maybe", "skip", "blocked"]


class TraceStep(BaseModel):
    tool: str
    result: str


class CriterionScore(BaseModel):
    key: str
    name: str
    score: int                 # 0-100
    weight: float
    detail: str
    missing: bool = False
    applicable: bool = True    # False = doesn't apply to this tenant (left out of the average)


class Evaluation(BaseModel):
    listing: RawListing
    details: ExtractedDetails
    total_monthly_huf: int
    commute_min: Optional[int]
    commute_category: str = ""          # Near / Average / Far / Too far
    commutes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    nearest_stops: list[dict[str, Any]] = Field(default_factory=list)
    commute_basis: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    scam: ScamReport
    decision: Decision
    reasons: list[str]
    match_score: int
    criteria_scores: list[CriterionScore] = Field(default_factory=list)
    positives: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    hard_fails: list[str] = Field(default_factory=list)
    message_hu: str = ""
    message_lang: str = "hu"
    trace: list[TraceStep] = Field(default_factory=list)


class Profile(BaseModel):
    """A person looking for a place (used for landlord-side and flatmate matching)."""
    id: str
    name: str
    age: int
    gender: str
    languages: list[str] = Field(default_factory=list)
    campus: str = "main"
    budget_huf: int = 120_000
    move_in: date = date(2027, 2, 1)
    smoker: bool = False
    has_pet: bool = False
    lifestyle: Literal["quiet", "balanced", "social"] = "balanced"
    about: str = ""
    documents_ready: bool = False
    sample: bool = False
    user_id: Optional[int] = None
    wanted_types: list[OfferType] = Field(default_factory=lambda: ["apartment", "room", "bed"])
    pref_genders: list[str] = Field(default_factory=list)
    pref_languages: list[str] = Field(default_factory=list)
    pref_age_min: int = 18
    pref_age_max: int = 99
    ok_with_smoker: bool = False
    ok_with_pets: bool = True


def profile_from_criteria(c: Criteria, user_id: Optional[int] = None) -> Profile:
    return Profile(
        id=c.profile_id or (f"user{user_id}" if user_id else "me"), name=c.user_name, age=c.age,
        gender=c.gender, languages=c.languages, campus=c.campus, budget_huf=c.max_total_huf,
        move_in=c.move_in, smoker=c.smoker, has_pet=c.has_pet, lifestyle=c.lifestyle, about=c.about,
        documents_ready=c.documents_ready, wanted_types=c.wanted_types, pref_genders=c.pref_genders,
        ok_with_smoker=c.smoker, user_id=user_id,
    )


class FlatmateMatch(BaseModel):
    a: Profile
    b: Profile
    score: int
    reasons: list[str]
    shared_languages: list[str]
    intro_message: str = ""


class TenantMatch(BaseModel):
    profile: Profile
    score: int
    reasons: list[str]
    total_monthly_huf: int
    commute_min: Optional[int] = None
