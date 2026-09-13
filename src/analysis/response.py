"""Emergency response: who goes, from where, and how long it really takes.

Three things this fixes.

**A drive time is not a response time.** The engine used to report "13 min" as
the fire service's answer to a school fire. Thirteen minutes was the shortest
path over the road network — it contains no call handling, no turnout, and no
traffic. The real figure is later, and the two are now reported separately: a
measured-network travel time, and an estimated response time built from it by
adding named, configurable overheads.

**The nearest station is not always available.** OSM publishes no appliance
counts or duty state, so availability is `unknown` unless an operator supplies
it. Unknown is reported as unknown — never as "available".

**One station cannot attend two fires.** In a multi-incident scenario the same
appliance was being dispatched to every incident at once. A station's units are
committed as they are assigned, and the next incident gets the next station,
with the substitution stated.

Every overhead below is an assumption, not a local measurement. They live here
so they can be argued with, and replaced with a service's own published times.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..graph.infrastructure import label_of
from . import provenance as prov


@dataclass(frozen=True)
class ResponseProfile:
    """What happens between the call and the wheels turning, per service.

    dispatch_min  call handling and the decision to mobilise
    turnout_min   crew to appliance, appliance rolling
    units         response units a station is assumed to hold
    """
    dispatch_min: float
    turnout_min: float
    units: int
    note: str


PROFILES: Dict[str, ResponseProfile] = {
    "fire_station": ResponseProfile(
        1.5, 1.5, 2,
        "Planning assumption: 1.5 min call handling, 1.5 min turnout, 2 appliances. "
        "Not a measured figure for this service."),
    "ambulance_station": ResponseProfile(
        1.0, 1.0, 2,
        "Planning assumption: 1.0 min call handling, 1.0 min turnout, 2 vehicles."),
    "police": ResponseProfile(
        1.0, 2.0, 2,
        "Planning assumption: 1.0 min call handling, 2.0 min to mobilise a unit."),
}

DEFAULT_PROFILE = ResponseProfile(1.5, 1.5, 1, "Planning assumption for an unlisted service.")

# Drive times come from OSM speed limits, which describe an empty road. This is
# the allowance for everything a real journey meets. A single factor, applied
# as a range rather than a point, because the model has no time-of-day data.
TRAFFIC_FACTOR = 1.3
TRAFFIC_NOTE = ("Travel times are free-flow: OSM speed limits with no traffic model. The "
                "upper bound allows 30% for congestion; no time-of-day data is available "
                "for this city.")

# Availability states an operator would recognise.
AVAILABLE, BUSY, UNAVAILABLE, UNKNOWN = "available", "busy", "unavailable", "unknown"


class ResourcePool:
    """Response units, and what has already been committed to an incident.

    Without this the same fire station answered three simultaneous incidents
    at once, which flatters every multi-incident scenario.
    """

    def __init__(self, facilities, declared: Optional[dict] = None):
        # declared: {facility_id: {"units": n, "available": n}} from an operator
        # feed, when one exists. Nothing in OSM provides it.
        self.declared = declared or {}
        self.total: Dict[str, int] = {}
        self.committed: Dict[str, int] = {}
        for f in facilities:
            profile = PROFILES.get(f.category)
            if not profile:
                continue
            stated = self.declared.get(f.id, {})
            self.total[f.id] = int(stated.get("units", profile.units))
            self.committed[f.id] = 0

    def state(self, facility_id: str) -> str:
        """What is known about this station's availability."""
        if facility_id not in self.total:
            return UNKNOWN
        free = self.free(facility_id)
        if facility_id in self.declared:
            return AVAILABLE if free > 0 else BUSY
        # Nothing observed: only what this scenario has itself committed is
        # known, so an untouched station is honestly "unknown", not "available".
        return BUSY if free <= 0 else UNKNOWN

    def free(self, facility_id: str) -> int:
        return self.total.get(facility_id, 0) - self.committed.get(facility_id, 0)

    def commit(self, facility_id: str) -> bool:
        if self.free(facility_id) <= 0:
            return False
        self.committed[facility_id] += 1
        return True

    def to_dict(self, index) -> List[dict]:
        return [
            {"id": fid, "name": getattr(index.get(fid), "display_name", fid),
             "category": getattr(index.get(fid), "category", ""),
             "units_assumed": total, "committed": self.committed[fid],
             "state": self.state(fid),
             "basis": ("declared by the operator" if fid in self.declared
                       else "unit count is a planning assumption; no duty state is published")}
            for fid, total in self.total.items() if self.committed[fid] > 0
        ]


def _round(value):
    return None if value is None or not math.isfinite(value) else round(value, 1)


@dataclass
class Dispatch:
    """One service's answer to one incident."""
    category: str
    station: Optional[object] = None
    travel_min: Optional[float] = None
    baseline_travel_min: Optional[float] = None
    unreachable: bool = False
    to_cordon_edge: bool = False
    availability: str = UNKNOWN
    displaced_from: Optional[str] = None
    note: str = ""
    candidates: List[dict] = field(default_factory=list)

    def response_range(self) -> tuple:
        """Travel time plus the overheads, as a range."""
        if self.travel_min is None:
            return (None, None)
        p = PROFILES.get(self.category, DEFAULT_PROFILE)
        fixed = p.dispatch_min + p.turnout_min
        return (fixed + self.travel_min, fixed + self.travel_min * TRAFFIC_FACTOR)

    def to_dict(self) -> dict:
        p = PROFILES.get(self.category, DEFAULT_PROFILE)
        low, high = self.response_range()
        travel = (prov.calculated(_round(self.travel_min),
                                  "shortest path over the mapped road network at OSM speed "
                                  "limits, on the disrupted network", "min")
                  if self.travel_min is not None
                  else prov.unknown("no route exists on the disrupted network", "min"))
        added = (None if self.baseline_travel_min is None or self.travel_min is None
                 or self.to_cordon_edge
                 else round(max(0.0, self.travel_min - self.baseline_travel_min), 1))
        return {
            "category": self.category,
            "category_label": label_of(self.category),
            "station": getattr(self.station, "display_name", None) if self.station else None,
            "available": self.station is not None,
            "availability": self.availability,
            "availability_basis": (
                "no duty state or appliance count is published for this service; "
                "the model tracks only what this scenario has committed"),
            "travel": travel.to_dict(),
            "travel_min": _round(self.travel_min),
            "baseline_min": _round(self.baseline_travel_min),
            "added_min": added,
            "unreachable": self.unreachable,
            "to_cordon_edge": self.to_cordon_edge,
            # The figure an operator should actually plan against.
            "response": prov.estimated(
                None if low is None else round(low, 1),
                f"{p.note} {TRAFFIC_NOTE}", "min").to_dict(),
            "response_low_min": None if low is None else round(low, 1),
            "response_high_min": None if high is None else round(high, 1),
            "overhead_min": round(p.dispatch_min + p.turnout_min, 1),
            "displaced_from": self.displaced_from,
            "note": self.note,
            "candidates": self.candidates[:4],
        }


def nearest_available(pool: ResourcePool, facilities, category: str, times: Dict[str, float],
                      exclude=()) -> tuple:
    """The closest station of this category with a unit left, by travel time.

    Returns (chosen, skipped) — `skipped` names stations passed over because
    this scenario has already committed their units, so the substitution can be
    explained rather than silently applied.
    """
    options = []
    for f in facilities:
        if f.category != category or not f.node or f.id in exclude:
            continue
        t = times.get(f.node)
        if t is None or not math.isfinite(t):
            continue
        options.append((t, f))
    options.sort(key=lambda pair: pair[0])

    skipped = []
    for t, f in options:
        if pool.commit(f.id):
            return f, t, skipped, options
        skipped.append(f)
    return None, None, skipped, options


def demo():
    from .impact import Facility

    stations = [
        Facility(id="fs1", name="Udupi Fire Station", category="fire_station",
                 lat=13.34, lon=74.75, node="a"),
        Facility(id="fs2", name="Manipal Fire Station", category="fire_station",
                 lat=13.36, lon=74.79, node="b"),
    ]
    pool = ResourcePool(stations)
    times = {"a": 4.0, "b": 9.0}

    # Two appliances at the nearest station: it answers the first two calls.
    first, t1, skipped, _ = nearest_available(pool, stations, "fire_station", times)
    second, _, _, _ = nearest_available(pool, stations, "fire_station", times)
    assert first.id == "fs1" and second.id == "fs1" and not skipped

    # The third finds it committed and moves on, and says so.
    third, t3, skipped, _ = nearest_available(pool, stations, "fire_station", times)
    assert third.id == "fs2", third
    assert skipped and skipped[0].id == "fs1", "the substitution has to be explainable"
    assert pool.state("fs1") == BUSY

    d = Dispatch(category="fire_station", station=first, travel_min=t1,
                 baseline_travel_min=3.0)
    low, high = d.response_range()
    assert low == 7.0 and round(high, 1) == 8.2, (low, high)
    out = d.to_dict()
    assert out["travel"]["state"] == prov.CALCULATED
    assert out["response"]["state"] == prov.ESTIMATED
    assert out["response_low_min"] < out["response_high_min"]
    assert out["availability"] in (AVAILABLE, BUSY, UNAVAILABLE, UNKNOWN)
    print(f"ok — travel {t1} min, response {low}-{high:.1f} min, contention honoured")


if __name__ == "__main__":
    demo()
