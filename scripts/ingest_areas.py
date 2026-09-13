"""Pull administrative boundaries (wards, municipalities, taluks, districts)
from OpenStreetMap into data/city/manipal.areas.json.

    python scripts/ingest_areas.py --radius 8000

OSM stores a boundary as a relation whose member ways are unordered fragments,
so the outer ways are stitched end-to-end into closed rings here. Everything
downstream is plain point-in-polygon with no geospatial dependency.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.graph.areas import LEVEL_NAMES  # noqa: E402

CENTER = (13.3467, 74.7855)
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OUT = Path(__file__).resolve().parents[1] / "data" / "city" / "manipal.areas.json"
SNAP = 1e-6  # coordinates this close are the same point


def _overpass(query: str, rounds: int = 2) -> dict:
    last = None
    for attempt in range(rounds):
        for url in OVERPASS_MIRRORS:
            try:
                r = requests.post(url, data=query.encode(), timeout=240,
                                  headers={"User-Agent": "cascading-failure-hackathon/1.0"})
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                print(f"    {url.split('/')[2]} unavailable ({type(e).__name__})")
        if attempt + 1 < rounds:
            print("    all mirrors busy; waiting 45s")
            time.sleep(45)
    raise RuntimeError(f"all Overpass mirrors failed: {last}")


def _key(point):
    return (round(point[0] / SNAP), round(point[1] / SNAP))


def stitch(ways) -> list:
    """Join unordered way fragments into closed rings.

    Each way is [(lat, lon), ...]. Fragments that never close are still kept
    if they are long enough to be a usable boundary — a ward whose relation is
    missing one segment is better shown approximately than dropped, and the
    ring is closed by joining its ends.
    """
    remaining = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while remaining:
        ring = remaining.pop(0)
        extended = True
        while extended and _key(ring[0]) != _key(ring[-1]):
            extended = False
            for i, candidate in enumerate(remaining):
                if _key(candidate[0]) == _key(ring[-1]):
                    ring += candidate[1:]
                elif _key(candidate[-1]) == _key(ring[-1]):
                    ring += list(reversed(candidate))[1:]
                elif _key(candidate[-1]) == _key(ring[0]):
                    ring = candidate[:-1] + ring
                elif _key(candidate[0]) == _key(ring[0]):
                    ring = list(reversed(candidate))[:-1] + ring
                else:
                    continue
                remaining.pop(i)
                extended = True
                break
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def fetch(radius: int) -> list:
    lat, lon = CENTER
    levels = "|".join(str(l) for l in sorted(LEVEL_NAMES))
    query = f"""
    [out:json][timeout:240];
    (
      relation["boundary"="administrative"]["admin_level"~"^({levels})$"](around:{radius},{lat},{lon});
      way["boundary"="administrative"]["admin_level"~"^({levels})$"](around:{radius},{lat},{lon});
    );
    out geom;
    """
    elements = _overpass(query).get("elements", [])
    print(f"  {len(elements)} boundary elements")

    areas = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("official_name")
        try:
            level = int(tags.get("admin_level", 0))
        except ValueError:
            continue
        if not name or level not in LEVEL_NAMES:
            continue

        if el["type"] == "way":
            ways = [[(p["lat"], p["lon"]) for p in el.get("geometry", []) if p]]
        else:
            ways = [[(p["lat"], p["lon"]) for p in m.get("geometry", []) if p]
                    for m in el.get("members", [])
                    if m.get("type") == "way" and m.get("role") in ("outer", "")]
        rings = stitch([w for w in ways if w])
        if not rings:
            continue
        areas.append({"id": f"{el['type']}/{el['id']}", "name": name, "level": level,
                      "rings": rings})
        pts = sum(len(r) for r in rings)
        print(f"    {LEVEL_NAMES[level]:14s} {name}  ({len(rings)} ring(s), {pts} points)")
    return areas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=int, default=8000)
    args = ap.parse_args()

    print(f"Fetching administrative boundaries within {args.radius}m...")
    areas = fetch(args.radius)
    if not areas:
        raise RuntimeError("no usable boundaries found — widen --radius")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"center": CENTER, "radius_m": args.radius, "areas": areas}),
                   encoding="utf-8")
    by_level = {}
    for a in areas:
        by_level[a["level"]] = by_level.get(a["level"], 0) + 1
    print(f"\n{len(areas)} boundaries saved to {OUT}")
    for level in sorted(by_level):
        print(f"  {LEVEL_NAMES[level]:14s} {by_level[level]}")


if __name__ == "__main__":
    main()
