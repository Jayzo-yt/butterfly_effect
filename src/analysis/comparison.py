"""Baseline versus disrupted.

"+6.5 min" only means something next to the 8.2 min it started from. Every row
here is the same quantity measured twice on the same routing — once on the
network as it normally is, once after the incidents are applied — so the
change is a difference, not an estimate of a difference.

A metric that cannot be computed from the available data is left out rather
than filled with a plausible number.
"""
import math
from typing import Dict, List, Optional

from ..graph.multiplex import CityGraph

# Unreachable nodes are excluded from travel-time averages and counted in the
# coverage row instead: averaging an infinity produces an infinity, and
# substituting a large number invents a journey nobody can make.


def _weighted_mean(city: CityGraph, times: Dict[str, float]) -> tuple:
    """Population-weighted mean travel time, and how many junctions it covers.

    Weighted because a minute lost at a junction serving 400 buildings is not
    the same finding as a minute lost at one serving four.
    """
    total_w = total_wt = 0.0
    reachable = counted = 0
    for n, data in city.G_road.nodes(data=True):
        if data.get("type") != "road_junction":
            continue
        counted += 1
        t = times.get(n)
        if t is None or not math.isfinite(t):
            continue
        w = max(float(data.get("population_weight", 1.0)), 0.01)
        total_w += w
        total_wt += w * t
        reachable += 1
    mean = (total_wt / total_w) if total_w else None
    return mean, reachable, counted


def _usable_segments(city: CityGraph) -> tuple:
    total = usable = 0
    for _, _, data in city.G_road.edges(data=True):
        total += 1
        if math.isfinite(data.get("current_weight", 1.0)):
            usable += 1
    return usable, total


def row(metric: str, baseline, disrupted, unit: str, better: str = "lower",
        confidence: str = "medium", basis: str = "") -> dict:
    """One comparison row. `change` is computed, never passed in."""
    change = None
    if isinstance(baseline, (int, float)) and isinstance(disrupted, (int, float)):
        change = disrupted - baseline
    direction = "none"
    if change:
        worse = change > 0 if better == "lower" else change < 0
        direction = "worse" if worse else "better"
    return {
        "metric": metric, "baseline": baseline, "disrupted": disrupted,
        "change": change, "unit": unit, "direction": direction,
        "confidence": confidence, "basis": basis,
    }


def build(city_before_edges: tuple, city_after: CityGraph, routing: dict,
          facilities_impacted: List[dict], has_hospitals: bool, has_responders: bool,
          cascade: Optional[dict] = None) -> dict:
    """Rows the available data can actually support.

    city_before_edges is (usable, total) captured before the incidents were
    applied; city_after is the mutated graph.
    """
    rows = []

    if has_hospitals:
        base_mean, base_reach, junctions = _weighted_mean(city_after, routing["before_health"])
        dis_mean, dis_reach, _ = _weighted_mean(city_after, routing["after_health"])
        if base_mean is not None and dis_mean is not None:
            rows.append(row("Average travel time to nearest hospital",
                            round(base_mean, 1), round(dis_mean, 1), "min",
                            confidence="medium",
                            basis=f"population-weighted over {junctions} junctions, "
                                  f"OSM drive network speeds"))
        if junctions:
            rows.append(row("Junctions with hospital access",
                            round(100 * base_reach / junctions, 1),
                            round(100 * dis_reach / junctions, 1), "%",
                            better="higher", confidence="high",
                            basis="a route exists on the road network"))

    if has_responders:
        base_mean, base_reach, junctions = _weighted_mean(city_after, routing["before_reach"])
        dis_mean, dis_reach, _ = _weighted_mean(city_after, routing["after_reach"])
        if base_mean is not None and dis_mean is not None:
            rows.append(row("Average emergency response time",
                            round(base_mean, 1), round(dis_mean, 1), "min",
                            confidence="medium",
                            basis="drive time from the nearest fire, police or "
                                  "ambulance station; no dispatch or turnout delay"))

    usable_before, total = city_before_edges
    usable_after, _ = _usable_segments(city_after)
    if total:
        rows.append(row("Road segments open",
                        round(100 * usable_before / total, 1),
                        round(100 * usable_after / total, 1), "%",
                        better="higher", confidence="high",
                        basis=f"{total} segments in the extract"))

    serious = [f for f in facilities_impacted if f["level"] in ("critical", "high")]
    rows.append(row("Facilities with critical or high impact", 0, len(serious), "",
                    confidence="high",
                    basis="facilities whose routed access changed materially"))

    cut_off = [f for f in facilities_impacted if f.get("cut_off")]
    if cut_off:
        rows.append(row("Facilities with no remaining route", 0, len(cut_off), "",
                        confidence="high", basis="no path exists on the disrupted network"))

    # A substation failure closes no roads, so without this the table read
    # "nothing changed" while twenty-three assets lost supply. The dependency
    # layer is part of the comparison, not a separate story.
    if cascade and cascade.get("indirect_total"):
        essential = [n for n in cascade["nodes"]
                     if n["round"] > 0 and n["group"] in ("Healthcare", "Emergency")]
        rows.append(row("Assets without normal utility supply", 0,
                        cascade["indirect_total"], "", confidence="medium",
                        basis=f"through {cascade['rounds']} round(s) of dependency "
                              f"propagation; links are service-area inferences"))
        if essential:
            rows.append(row("Healthcare or emergency assets on backup", 0, len(essential), "",
                            confidence="medium",
                            basis="downstream of the failed supply"))

    return {"rows": rows,
            "note": ("Both columns come from the same routing on the same network, so each "
                     "change is a measured difference. Rows that the available data cannot "
                     "support are omitted rather than estimated.")}


def demo():
    from ..graph.builder import build_toy_city
    from .impact import Disruption, analyze, snap_facilities

    city = build_toy_city()
    facs = snap_facilities(city, [
        {"id": "h", "name": "Toy hospital", "category": "hospital",
         "lat": city.node_attrs("H1")["geo"][0], "lon": city.node_attrs("H1")["geo"][1],
         "address": "", "capacity": None},
        {"id": "f", "name": "Toy fire station", "category": "fire_station",
         "lat": city.node_attrs("F1")["geo"][0], "lon": city.node_attrs("F1")["geo"][1],
         "address": "", "capacity": None},
    ])
    before = _usable_segments(city)
    result = analyze(city, facs, Disruption(kind="closure", severity="critical",
                                            duration_hours=6, edge=("H1", "J3")))
    table = build(before, city, result["_routing"], result["facilities"], True, True)
    assert table["rows"], "a closure must produce comparable rows"
    open_row = [r for r in table["rows"] if r["metric"] == "Road segments open"][0]
    assert open_row["baseline"] == 100.0 and open_row["disrupted"] < 100.0
    assert open_row["direction"] == "worse"
    for r in table["rows"]:
        assert r["change"] is None or math.isfinite(r["change"])
    print(f"ok — {len(table['rows'])} rows")


if __name__ == "__main__":
    demo()
