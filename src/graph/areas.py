"""Administrative areas — which ward, town or district an incident is in.

Boundaries are OSM relations, fetched offline by scripts/ingest_areas.py into
data/city/<city>.areas.json and tested here with plain ray casting. No
geospatial dependency at runtime, and no network call while an operator is
waiting.

When the file is absent the API says so instead of guessing: "administrative
boundary data not loaded" is a true statement an operator can act on;
inventing a ward name is not.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# OSM admin_level as Karnataka uses it: the extract returns "Udupi" at level 5
# and "Udupi taluku" at level 6, so 5 is the district and 6 the taluk. Anything
# outside this range keeps a generic label rather than being dropped.
LEVEL_NAMES = {
    4: "State", 5: "District", 6: "Taluk", 7: "Sub-district",
    8: "Municipality", 9: "Zone", 10: "Ward",
}


@dataclass
class Area:
    id: str
    name: str
    level: int
    level_label: str
    rings: List[List[tuple]]
    bbox: tuple  # (min_lat, min_lon, max_lat, max_lon)

    def contains(self, lat: float, lon: float) -> bool:
        min_lat, min_lon, max_lat, max_lon = self.bbox
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            return False
        return any(_in_ring(lat, lon, ring) for ring in self.rings)


def _in_ring(lat: float, lon: float, ring) -> bool:
    """Ray casting. Ring is [(lat, lon), ...]."""
    inside = False
    n = len(ring)
    for i in range(n):
        y1, x1 = ring[i]
        y2, x2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if x_cross > lon:
                inside = not inside
    return inside


class AreaIndex:
    """Every loaded boundary, smallest first so the most specific one leads."""

    def __init__(self, areas: List[Area] = ()):
        self.areas = sorted(areas, key=lambda a: -a.level)

    def __bool__(self):
        return bool(self.areas)

    def at(self, lat: float, lon: float) -> List[Area]:
        return [a for a in self.areas if a.contains(lat, lon)]

    def describe(self, lat: Optional[float], lon: Optional[float]) -> dict:
        if not self.areas:
            return {"areas": [], "available": False,
                    "note": ("Administrative boundary data is not loaded for this city. "
                             "Run scripts/ingest_areas.py to add wards and municipalities.")}
        if lat is None or lon is None:
            return {"areas": [], "available": True, "note": "No incident location."}
        hits = self.at(lat, lon)
        return {
            "areas": [{"id": a.id, "name": a.name, "level": a.level,
                       "level_label": a.level_label} for a in hits],
            "available": True,
            "note": "" if hits else "The incident falls outside every mapped boundary.",
        }

    def names(self) -> List[dict]:
        return [{"id": a.id, "name": a.name, "level": a.level, "level_label": a.level_label,
                 "lat": (a.bbox[0] + a.bbox[2]) / 2, "lon": (a.bbox[1] + a.bbox[3]) / 2}
                for a in self.areas]


def load(path) -> AreaIndex:
    path = Path(path)
    if not path.exists():
        return AreaIndex([])
    raw = json.loads(path.read_text(encoding="utf-8"))
    areas = []
    for a in raw.get("areas", []):
        rings = [[tuple(p) for p in ring] for ring in a["rings"] if len(ring) >= 3]
        if not rings:
            continue
        lats = [p[0] for ring in rings for p in ring]
        lons = [p[1] for ring in rings for p in ring]
        areas.append(Area(
            id=a["id"], name=a["name"], level=a["level"],
            level_label=LEVEL_NAMES.get(a["level"], f"Level {a['level']}"),
            rings=rings, bbox=(min(lats), min(lons), max(lats), max(lons)),
        ))
    return AreaIndex(areas)


def demo():
    square = Area(id="w1", name="Ward 1", level=10, level_label="Ward",
                  rings=[[(0, 0), (0, 2), (2, 2), (2, 0)]], bbox=(0, 0, 2, 2))
    big = Area(id="c1", name="Town", level=8, level_label="Municipality",
               rings=[[(-1, -1), (-1, 5), (5, 5), (5, -1)]], bbox=(-1, -1, 5, 5))
    idx = AreaIndex([square, big])
    assert [a.name for a in idx.at(1, 1)] == ["Ward 1", "Town"], "smallest area first"
    assert idx.at(4, 4) == [big]
    assert idx.at(9, 9) == []
    assert AreaIndex([]).describe(1, 1)["available"] is False
    print("ok")


if __name__ == "__main__":
    demo()
