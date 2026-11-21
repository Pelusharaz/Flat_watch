"""Demo source: replays data/listings.json one listing per call, in file order.

The order of the file IS the demo script - arrange it so the story flows
(good match -> over budget -> scam -> perfect match ...).
"""
from __future__ import annotations

import json
import os

from ..config import settings
from ..models import RawListing
from .base import Source


def _read(path: str, sample: bool) -> list[RawListing]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [RawListing.model_validate({**item, "sample": sample}) for item in data.get("listings", [])]


def load_listings(path: str | None = None) -> list[RawListing]:
    """Real listings (pasted or posted in the app, data/my_listings.json) first, then the samples."""
    if path:
        return _read(path, sample=False)
    real = _read(settings.my_listings_file, sample=False)
    samples = _read(settings.listings_file, sample=True) if settings.include_samples else []
    return real + samples


def posted_offers() -> list[RawListing]:
    """Places posted in the app by owners or subletting tenants."""
    return [l for l in load_listings() if l.source == "Flat-Watch"]


def save_my_listing(listing: RawListing) -> None:
    """Append a real listing (pasted in the UI) to data/my_listings.json."""
    path = settings.my_listings_file
    data = {"listings": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    data["listings"].append(listing.model_dump(exclude={"sample"}))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class SimulatedFeed(Source):
    name = "simulator"

    def __init__(self, store, per_tick: int = 1, path: str | None = None):
        self.store = store
        self.per_tick = per_tick
        # In-app posts go straight to the agent when posted; the feed replays the rest
        self.listings = [l for l in load_listings(path) if l.sample or l.source != "Flat-Watch"]

    def fetch_new(self) -> list[RawListing]:
        cursor = self.store.get("sim_cursor", 0)
        batch = self.listings[cursor:cursor + self.per_tick]
        self.store.set("sim_cursor", cursor + len(batch))
        return batch

    def remaining(self) -> int:
        return len(self.listings) - self.store.get("sim_cursor", 0)

    def reset(self) -> None:
        self.store.set("sim_cursor", 0)
