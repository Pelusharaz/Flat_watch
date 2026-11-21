"""Public transport: nearest stops and the best direct line between two points.

Stops + lines come from data/transit_stops.json:
  - shipped: a small fallback (tram 1 and 2 only, approximate coordinates)
  - real:    run `python tools/fetch_osm_stops.py` to download every Debrecen
             bus / tram / trolleybus stop and its lines from OpenStreetMap.

Routing is deliberately simple (direct lines only, no timetable):
  time = walk to stop + average wait + ride + walk from stop
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Optional

from .config import DATA_DIR

STOPS_FILE = DATA_DIR / "transit_stops.json"

WALK_KMH = 4.8
WALK_DETOUR = 1.3        # streets vs straight line
RIDE_DETOUR = 1.25
MAX_WALK_TO_STOP_KM = 0.9
SPEED_KMH = {"Tram": 17.0, "Bus": 19.0, "Trolley": 17.0}
WAIT_MIN = {"Tram": 5, "Bus": 7, "Trolley": 6}   # ≈ half the usual headway
ICON = {"Tram": "🚋", "Bus": "🚌", "Trolley": "🚎"}

Point = tuple[float, float]


def km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def walk_min(a: Point, b: Point) -> float:
    return km(a, b) * WALK_DETOUR / WALK_KMH * 60


def kind(line: str) -> str:
    return line.split()[0] if line.split()[0] in SPEED_KMH else "Bus"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        with open(STOPS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"_source": "missing", "stops": []}


def stops() -> list[dict]:
    return _data()["stops"]


def source() -> str:
    return _data().get("_source", "unknown")


def reload() -> None:
    _data.cache_clear()


def nearest_stops(p: Point, n: int = 3, max_km: float = 1.2) -> list[dict]:
    """[{name, lines, walk_min, dist_m}] closest first, one entry per stop name."""
    out, seen = [], set()
    for s in sorted(stops(), key=lambda s: km(p, (s["lat"], s["lon"]))):
        d = km(p, (s["lat"], s["lon"]))
        if d > max_km or len(out) >= n:
            break
        if s["name"] in seen:
            continue
        seen.add(s["name"])
        out.append({"name": s["name"], "lines": s["lines"],
                    "walk_min": max(1, round(walk_min(p, (s["lat"], s["lon"])))), "dist_m": round(d * 1000)})
    return out


def best_route(origin: Point, dest: Point) -> Optional[dict]:
    """Fastest DIRECT line between stops near origin and stops near dest, or None."""
    near_o = [s for s in stops() if km(origin, (s["lat"], s["lon"])) <= MAX_WALK_TO_STOP_KM]
    near_d = [s for s in stops() if km(dest, (s["lat"], s["lon"])) <= MAX_WALK_TO_STOP_KM]
    best = None
    for so in near_o:
        po = (so["lat"], so["lon"])
        w1 = walk_min(origin, po)
        for sd in near_d:
            if sd["name"] == so["name"]:
                continue
            pd = (sd["lat"], sd["lon"])
            common = set(so["lines"]) & set(sd["lines"])
            if not common:
                continue
            w2 = walk_min(pd, dest)
            ride_km = km(po, pd) * RIDE_DETOUR
            for line in common:
                k = kind(line)
                total = w1 + WAIT_MIN[k] + ride_km / SPEED_KMH[k] * 60 + w2
                if best is None or total < best["minutes"]:
                    best = {"minutes": total, "line": line, "from": so["name"], "to": sd["name"],
                            "walk_to": round(w1), "walk_from": round(w2)}
    if best:
        best["minutes"] = max(1, round(best["minutes"]))
        best["how"] = (f"{ICON[kind(best['line'])]} {best['line']}: walk {best['walk_to']} min to "
                       f"{best['from']} → ride to {best['to']} → walk {best['walk_from']} min")
    return best


# ---------------------------------------------------- OpenStreetMap parsing --
STOP_ROLES = {"stop", "platform", "stop_entry_only", "stop_exit_only",
              "platform_entry_only", "platform_exit_only", ""}
ROUTE_KIND = {"tram": "Tram", "bus": "Bus", "trolleybus": "Trolley"}


def parse_overpass(data: dict, merge_m: float = 250) -> list[dict]:
    """Overpass JSON (route relations + their nodes) -> merged stop list with lines."""
    nodes = {e["id"]: e for e in data.get("elements", []) if e["type"] == "node"}
    lines_at: dict[int, set] = {}
    for rel in (e for e in data.get("elements", []) if e["type"] == "relation"):
        tags = rel.get("tags", {})
        ref = tags.get("ref")
        k = ROUTE_KIND.get(tags.get("route", ""))
        if not ref or not k:
            continue
        for m in rel.get("members", []):
            if m.get("type") == "node" and m.get("role", "") in STOP_ROLES:
                lines_at.setdefault(m["ref"], set()).add(f"{k} {ref}")

    merged: list[dict] = []
    for nid, lines in lines_at.items():
        n = nodes.get(nid)
        if not n or "lat" not in n:
            continue
        name = n.get("tags", {}).get("name")
        if not name:
            continue
        p = (n["lat"], n["lon"])
        for s in merged:  # same name within merge_m = same stop (both directions / platforms)
            if s["name"] == name and km(p, (s["lat"], s["lon"])) * 1000 <= merge_m:
                s["lines"] |= lines
                break
        else:
            merged.append({"name": name, "lat": p[0], "lon": p[1], "lines": set(lines)})

    def order(line: str):
        k, ref = line.split(" ", 1)
        digits = "".join(ch for ch in ref if ch.isdigit())
        return ({"Tram": 0, "Trolley": 1, "Bus": 2}[k], int(digits or 999), ref)

    return [{**s, "lines": sorted(s["lines"], key=order)} for s in merged]
