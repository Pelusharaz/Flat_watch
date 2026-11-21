"""Tool: walking and public-transport minutes from a listing to every campus + the city centre.

How it works:
  1. Locate the listing: geocode its street address with OpenStreetMap Nominatim
     (cached in data/geocache.json); if that fails, use its neighbourhood centre.
  2. For each destination: walking time, and the fastest DIRECT bus/tram line
     between stops near the flat and stops near the destination (see transit.py),
     e.g. "🚋 Tram 1: walk 4 min to Kálvin tér → ride to Egyetem → walk 2 min".

These are ESTIMATES. Swap `estimate()` for Google Directions / a GTFS router later.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from functools import lru_cache
from typing import Optional
from urllib.parse import urlencode

import requests

from . import transit
from .config import DATA_DIR, settings

TRANSIT_KMH = 18.0       # Debrecen tram/bus average incl. stops
TRANSIT_OVERHEAD_MIN = 10  # walk to stop (~4) + average wait (~4) + walk from stop (~2)

GEOCACHE_FILE = DATA_DIR / "geocache.json"
_geo_lock = threading.Lock()
_last_geocode = 0.0
_failed: set[str] = set()   # network failures this session - don't retry every listing


def _plain(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn"
    )


@lru_cache(maxsize=1)
def _districts() -> dict:
    with open(settings.districts_file, encoding="utf-8") as f:
        return json.load(f)["districts"]


MY_DESTINATIONS_FILE = DATA_DIR / "my_destinations.json"


@lru_cache(maxsize=1)
def destinations() -> dict:
    """Built-in campuses + the ones users added in the app (data/my_destinations.json)."""
    with open(DATA_DIR / "destinations.json", encoding="utf-8") as f:
        out = dict(json.load(f)["destinations"])
    if MY_DESTINATIONS_FILE.exists():
        out.update(json.loads(MY_DESTINATIONS_FILE.read_text(encoding="utf-8")).get("destinations", {}))
    return out


def add_destination(label: str, address: str = "", lat: Optional[float] = None,
                    lon: Optional[float] = None) -> tuple[bool, str]:
    """Add a campus/place. Uses lat/lon if given, otherwise geocodes the address."""
    if lat is None or lon is None:
        point = geocode(address if _has_street(address) else f"{address} utca")
        if not point:
            return False, "Could not find that address – enter the coordinates instead (right-click in Google Maps)."
        lat, lon = point
    key = re.sub(r"[^a-z0-9]+", "_", _plain(label)).strip("_")[:30] or f"place_{int(time.time())}"
    data = {"destinations": {}}
    if MY_DESTINATIONS_FILE.exists():
        data = json.loads(MY_DESTINATIONS_FILE.read_text(encoding="utf-8"))
    data["destinations"][key] = {"label": label, "lat": float(lat), "lon": float(lon)}
    MY_DESTINATIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    destinations.cache_clear()
    commute_table.cache_clear()
    return True, key


def district_names() -> list[str]:
    return list(_districts().keys())


def match_district(location: str) -> Optional[str]:
    loc = _plain(location)
    if not loc:
        return None
    for name, info in _districts().items():
        if _plain(name) in loc or any(alias in loc for alias in info.get("aliases", [])):
            return name
    return None


# ------------------------------------------------------------- geocoding --
def _load_cache() -> dict:
    try:
        return json.loads(GEOCACHE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _has_street(location: str) -> bool:
    return bool(re.search(r"\b(utca|u\.|út|ut|tér|ter|körút|krt|sor|köz|sugárút|street)\b", location or "", re.I))


def geocode(location: str) -> Optional[tuple[float, float]]:
    """Street address -> (lat, lon) via Nominatim. Cached; max 1 request/second; None on failure."""
    global _last_geocode
    if not settings.geocode or not _has_street(location):
        return None
    key = _plain(location)
    with _geo_lock:
        cache = _load_cache()
        if key in cache:
            return tuple(cache[key]) if cache[key] else None
        if len(_failed) >= 3:   # offline: stop trying, use district centres
            return None
        wait = 1.1 - (time.time() - _last_geocode)
        if wait > 0:
            time.sleep(wait)
        _last_geocode = time.time()
        query = location if "debrecen" in key else f"Debrecen, {location}"
        result = None
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "limit": 1, "countrycodes": "hu"},
                headers={"User-Agent": "flat-watch-debrecen-hackathon/1.0"},
                timeout=5,
            )
            r.raise_for_status()
            hits = r.json()
            if hits:
                lat, lon = float(hits[0]["lat"]), float(hits[0]["lon"])
                if 47.45 < lat < 47.65 and 21.50 < lon < 21.75:  # inside Debrecen
                    result = (lat, lon)
        except Exception as exc:  # noqa: BLE001
            _failed.add(key)
            print(f"[geocode] failed for '{location}' ({type(exc).__name__}); using the district centre")
            return None  # don't cache network errors
        cache[key] = list(result) if result else None
        try:
            GEOCACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return result


def locate(location: str, district: Optional[str]) -> tuple[Optional[tuple[float, float]], str]:
    """Returns ((lat, lon) or None, basis) where basis says how precise it is."""
    point = geocode(location)
    if point:
        return point, "street address"
    if district and district in _districts():
        d = _districts()[district]
        return (d["lat"], d["lon"]), f"{district} centre (approx.)"
    return None, "unknown location"


def maps_url(location: str, district: Optional[str] = None) -> Optional[str]:
    """Return a real Google Maps link for a known location or approximate district centre."""
    point, _ = locate(location, district)
    if point:
        lat, lon = point
        return f"https://www.google.com/maps/search/?api=1&{urlencode({'query': f'{lat},{lon}'})}"
    return None


def destination_maps_url(destination_key: str) -> Optional[str]:
    destination = destinations().get(destination_key)
    if not destination:
        return None
    if destination.get("url"):
        return str(destination["url"])
    lat, lon = destination["lat"], destination["lon"]
    return f"https://www.google.com/maps/search/?api=1&{urlencode({'query': f'{lat},{lon}'})}"


# --------------------------------------------------------------- minutes --
def estimate(origin: tuple[float, float], dest: tuple[float, float]) -> dict:
    """Walk, bike and public transport minutes, and HOW (which line, which stops)."""
    from .scoring import config
    travel = config()["travel"]
    road_km = transit.km(origin, dest) * travel["detour"]
    walk = max(1, round(road_km / travel["walk_kmh"] * 60))
    bike = max(1, round(road_km / travel["bike_kmh"] * 60 + 2))   # +2 min to lock/unlock
    route = transit.best_route(origin, dest)
    if route and route["minutes"] < walk:
        return {"walk": walk, "bike": bike, "transit": route["minutes"], "how": route["how"]}
    if route is None and walk > 15:
        ride_km = transit.km(origin, dest) * transit.RIDE_DETOUR
        t = round(TRANSIT_OVERHEAD_MIN + 5 + ride_km / TRANSIT_KMH * 60)
        if t < walk:
            return {"walk": walk, "bike": bike, "transit": t, "how": "🔁 No direct line found – estimate with 1 change"}
    return {"walk": walk, "bike": bike, "transit": walk, "how": "🚶 Walking is fastest"}


@lru_cache(maxsize=4096)
def commute_table(location: str, district: Optional[str]) -> tuple[dict[str, dict], str, list[dict]]:
    """({destination_key: {"walk", "transit", "how"}}, basis, nearest stops)"""
    origin, basis = locate(location, district)
    if origin is None:
        return {}, basis, []
    table = {key: estimate(origin, (d["lat"], d["lon"])) for key, d in destinations().items()}
    return table, basis, transit.nearest_stops(origin)
