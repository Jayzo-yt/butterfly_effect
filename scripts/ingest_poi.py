"""Pull every infrastructure category in the registry from OpenStreetMap.

Driven entirely by src/graph/infrastructure.py — adding a category there adds
it here, with no change to this file.

Output is data/city/<city>.poi.json: a flat list of facilities with real
names, coordinates, category and OSM id. Kept separate from the road graph
because it is *static reference data* about the city, whereas the graph is
the routable network; the impact engine joins them spatially at query time
rather than through pre-baked id relationships.

    python scripts/ingest_poi.py --radius 5000
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.graph.infrastructure import CATEGORIES, overpass_filters  # noqa: E402
from src.graph.validate import dedupe_facilities  # noqa: E402

CENTER = (13.3467, 74.7855)
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OUT = Path(__file__).resolve().parents[1] / "data" / "city" / "manipal.poi.json"


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


def _clause(tag: str, radius: int, lat: float, lon: float) -> str:
    """One OSM tag filter as node/way/relation clauses."""
    key, _, value = tag.partition("=")
    selector = f'["{key}"]' if value == "*" else f'["{key}"="{value}"]'
    return "".join(
        f'{kind}{selector}(around:{radius},{lat},{lon});' for kind in ("node", "way", "relation")
    )


def fetch(radius: int) -> list:
    lat, lon = CENTER
    filters = overpass_filters()
    facilities = []
    seen = set()

    # Batched rather than one query per category: ~25 categories would be 25
    # round trips against a rate-limited public API.
    keys = list(filters)
    batch_size = 6
    for i in range(0, len(keys), batch_size):
        batch = keys[i:i + batch_size]
        clauses = "".join(_clause(t, radius, lat, lon) for k in batch for t in filters[k])
        # `out geom` — body plus geometry, which includes tags. A trailing
        # `tags` modifier means "tags only" and silently strips the geometry
        # (and, as it turned out, half the results), which is the same trap
        # that made the boundary import return nothing.
        query = f"[out:json][timeout:240];({clauses});out geom;"
        print(f"  fetching {', '.join(batch)}")
        elements = _overpass(query).get("elements", [])

        for el in elements:
            tags = el.get("tags", {})
            geo_lat = el.get("lat") or el.get("center", {}).get("lat")
            geo_lon = el.get("lon") or el.get("center", {}).get("lon")
            if geo_lat is None or geo_lon is None:
                # `out geom` gives ways their outline but no centre point.
                geo_lat, geo_lon = _centroid(el.get("geometry"))
            if geo_lat is None or geo_lon is None:
                continue
            category = _classify(tags, batch, filters)
            if category is None:
                continue
            osm_id = f"{el.get('type', 'node')}/{el.get('id')}"
            if osm_id in seen:
                continue
            seen.add(osm_id)
            facilities.append({
                "id": osm_id,
                "name": tags.get("name") or tags.get("operator") or "",
                "category": category,
                "lat": geo_lat,
                "lon": geo_lon,
                "address": _address(tags),
                "capacity": _capacity(tags),
                "area_m2": _area_m2(el.get("geometry")),
                "source": "osm",
            })
    return facilities


def _classify(tags: dict, batch: list, filters: dict) -> str | None:
    """First category in this batch whose tag filter the element satisfies."""
    for key in batch:
        for tag in filters[key]:
            k, _, v = tag.partition("=")
            if k in tags and (v == "*" or tags[k] == v):
                return key
    return None


def _centroid(geometry):
    """Mean of a way's outline — good enough to snap an asset to a road."""
    points = [(p["lat"], p["lon"]) for p in (geometry or []) if "lat" in p and "lon" in p]
    if not points:
        return None, None
    return (sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points))


def _area_m2(geometry) -> float:
    """Ground area of a mapped way, by the shoelace formula on a local
    equirectangular projection. Accurate enough at the size of a playing field,
    and it is the difference between "capacity unknown" and a real comparison
    against the number of people being evacuated."""
    if not geometry or len(geometry) < 4:
        return 0.0
    points = [(p["lat"], p["lon"]) for p in geometry if "lat" in p and "lon" in p]
    if len(points) < 4:
        return 0.0
    mean_lat = math.radians(sum(p[0] for p in points) / len(points))
    xs = [p[1] * 111_320 * math.cos(mean_lat) for p in points]
    ys = [p[0] * 110_540 for p in points]
    area = 0.0
    for i in range(len(points)):
        j = (i + 1) % len(points)
        area += xs[i] * ys[j] - xs[j] * ys[i]
    return round(abs(area) / 2.0, 1)


def _address(tags: dict) -> str:
    parts = [tags.get("addr:housenumber"), tags.get("addr:street"),
             tags.get("addr:suburb"), tags.get("addr:city")]
    return ", ".join(p for p in parts if p)


def _capacity(tags: dict):
    """Real capacity where OSM states it. Left null rather than guessed — the
    impact engine treats null as unknown instead of substituting a number."""
    for key in ("beds", "capacity", "capacity:persons"):
        if key in tags:
            try:
                return float(tags[key])
            except ValueError:
                pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=int, default=5000)
    args = ap.parse_args()

    print(f"Fetching {len(CATEGORIES)} infrastructure categories within {args.radius}m...")
    facilities = fetch(args.radius)

    # OSM maps one asset as both a node and a building way; without this the
    # same hospital appears twice in every list the operator reads.
    facilities, duplicates = dedupe_facilities(facilities)
    if duplicates:
        print(f"  collapsed {len(duplicates)} duplicate assets")

    by_cat = {}
    for f in facilities:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
    named = sum(1 for f in facilities if f["name"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"center": CENTER, "radius_m": args.radius,
                               "facilities": facilities}), encoding="utf-8")

    print(f"\n{len(facilities)} facilities ({named} named) across {len(by_cat)} categories")
    for key, count in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"  {CATEGORIES[key].label:28s} {count}")
    print(f"\nSaved to {OUT}")


if __name__ == "__main__":
    main()
