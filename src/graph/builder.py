"""Phase 1 toy network per ARCHITECTURE.md: hand-built, small enough to verify
shortest-path deltas by hand. Do not point this at real OSM data (Phase 4).
"""
import math
from pathlib import Path

from .multiplex import CityGraph
from .schema import Edge, Node

# Manipal-ish coordinates, spaced out arbitrarily — not real road geometry.
_BASE_LAT, _BASE_LON = 13.35, 74.79


def _geo(row: int, col: int):
    return (_BASE_LAT + row * 0.01, _BASE_LON + col * 0.01)


def _metres(a, b) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    mean = math.radians((lat1 + lat2) / 2)
    return math.hypot((lon2 - lon1) * 111_320 * math.cos(mean), (lat2 - lat1) * 110_540)


def build_toy_city() -> CityGraph:
    city = CityGraph()

    junctions = {
        "J1": _geo(0, 0), "J2": _geo(0, 1), "J3": _geo(0, 2), "J4": _geo(0, 3), "J5": _geo(0, 4),
        "J6": _geo(1, 0), "J7": _geo(1, 1), "J8": _geo(1, 2), "J9": _geo(1, 3), "J10": _geo(1, 4),
        "J11": _geo(2, 1),  # residential dead-end off J7
    }
    for jid, geo in junctions.items():
        city.add_node(Node(id=jid, type="road_junction", geo=geo, capacity=800.0), layer="road")

    city.add_node(Node(id="H1", type="hospital", geo=_geo(-1, 2), capacity=150.0, redundancy=1), layer="road")
    city.add_node(Node(id="H2", type="hospital", geo=_geo(2, 2), capacity=90.0, redundancy=0), layer="road")
    city.add_node(Node(id="F1", type="fire_station", geo=_geo(2, 0), capacity=3.0), layer="road")

    road_edges = [
        ("J1", "J2"), ("J2", "J3"), ("J3", "J4"), ("J4", "J5"),
        ("J1", "J6"), ("J2", "J7"), ("J3", "J8"), ("J4", "J9"), ("J5", "J10"),
        ("J6", "J7"), ("J7", "J8"), ("J8", "J9"), ("J9", "J10"),
        ("J7", "J11"),
        ("H1", "J3"), ("H2", "J8"), ("F1", "J6"),
    ]
    # Real metres, derived from the toy geometry, so anything that needs a
    # distance — a walking time, a route length — exercises the same code path
    # as the real extract instead of silently reporting "unknown".
    for u, v in road_edges:
        a = city.node_attrs(u)["geo"]
        b = city.node_attrs(v)["geo"]
        metres = _metres(a, b)
        minutes = 5.0
        city.add_edge(Edge(source=u, target=v, layer="road", base_weight=minutes,
                            max_capacity=800.0, length_m=round(metres, 1),
                            speed_kph=round(metres / 1000 / (minutes / 60), 1),
                            speed_source="assumed"), layer="road")

    city.add_node(Node(id="S1", type="substation", geo=_geo(-1, 0), capacity=20.0), layer="utility")
    city.add_node(Node(id="S2", type="substation", geo=_geo(2, 3), capacity=15.0), layer="utility")
    city.add_node(Node(id="W1", type="water_pump", geo=_geo(0, 2), capacity=500.0), layer="utility")
    city.add_edge(Edge(source="S1", target="W1", layer="power", base_weight=1.0,
                        max_capacity=20.0), layer="utility")
    city.add_edge(Edge(source="S2", target="W1", layer="power", base_weight=1.0,
                        max_capacity=15.0), layer="utility")

    # H1 has a backup generator (redundancy=1) so it degrades rather than fully
    # fails; H2 shares the same water pump with no backup — a single point of
    # failure across two hospitals, matching ARCHITECTURE.md §1's example.
    city.set_dependency("H1", ["S1", "W1"])
    city.set_dependency("H2", ["S2", "W1"])

    return city


def build_city(source: str = "toy") -> CityGraph:
    """source="toy" for the hand-built network; a file path loads a real
    city pre-ingested by scripts/ingest_manipal.py (see data/city/)."""
    if source == "toy":
        return build_toy_city()
    return CityGraph.load(source)
