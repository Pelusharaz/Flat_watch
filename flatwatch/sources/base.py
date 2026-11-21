"""A listing source. The watcher only knows this interface.

To go live after the hackathon, add e.g. `IngatlanSource(Source)` that fetches
a saved-search page/RSS and returns RawListing objects. Check each site's
terms of use before scraping.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import RawListing


class Source(ABC):
    name: str = "source"

    @abstractmethod
    def fetch_new(self) -> list[RawListing]:
        """Return listings that appeared since the last call (duplicates are filtered later)."""

    def reset(self) -> None:  # optional
        pass
