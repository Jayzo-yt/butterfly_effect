"""How many people, and where they can go — with the source of each number.

The engine used to answer "how many people are in this school" with a single
constant: 600, for every school in the city, presented in the same typeface as
a routed travel time. This replaces that with a priority chain that prefers
real data wherever it exists and states which rung it landed on:

    1. observed occupancy         an operator feed, if one is supplied
    2. published capacity         an OSM capacity/beds tag on the asset
    3. dataset estimate           a sized estimate from mapped floor area
    4. category planning figure   the constant — the last resort, not the first

With the current extract every school falls through to rung 4, because OSM
publishes no capacity for any of the 166 assets here. That is worth knowing,
and the result says so rather than hiding it behind a confident number.

Assembly capacity works the same way: a mapped open space with an area can
carry an estimate at a stated crowd density; one without can only say unknown.
"""
import math
from typing import Optional

from ..graph.infrastructure import CATEGORIES, label_of
from . import provenance as prov

# Crowd density for an outdoor assembly area. 1 m² per person is the usual
# planning figure for a standing crowd that still has to be marshalled; denser
# than that is a crush, not an assembly point.
ASSEMBLY_M2_PER_PERSON = 1.0

# Evacuating a school is a marshalled walk with children in it, not an adult
# striding out. Slower than the 5 km/h usually quoted for pedestrians.
WALK_SPEED_KMH = 4.0


def occupancy(facility, observed: Optional[dict] = None) -> prov.Finding:
    """People typically present at this asset, by the best available source."""
    observed = observed or {}
    stated = observed.get(facility.id)
    if stated is not None:
        return prov.measured(int(stated), "occupancy supplied by the operator", "people")

    if getattr(facility, "capacity", None):
        return prov.measured(
            int(facility.capacity),
            f"capacity published in OpenStreetMap for {facility.display_name}", "people")

    cat = CATEGORIES.get(facility.category)
    if not cat or not cat.occupancy:
        return prov.unknown(
            f"no occupancy figure is defined for {label_of(facility.category).lower()}, and "
            f"none is published for this asset", "people")

    return prov.estimated(
        cat.occupancy,
        f"planning figure for {label_of(facility.category).lower()} — the same value for every "
        f"asset of this category. OpenStreetMap publishes no capacity for this one, so this "
        f"is an order-of-magnitude estimate, not a headcount", "people")


def assembly_capacity(facility) -> prov.Finding:
    """How many people an assembly point can hold."""
    area = getattr(facility, "area_m2", 0) or 0
    if area > 0:
        return prov.estimated(
            int(area / ASSEMBLY_M2_PER_PERSON),
            f"{int(area):,} m² of mapped ground at {ASSEMBLY_M2_PER_PERSON:g} m² per person",
            "people")
    return prov.unknown(
        "OpenStreetMap maps this space as a point with no area, so its capacity cannot be "
        "estimated", "people")


def sufficiency(people: prov.Finding, capacity: prov.Finding) -> dict:
    """Whether the destination can take the occupants — or whether that is
    simply not knowable, which is a different answer from "no"."""
    if not people.known or not capacity.known:
        return {
            "state": "unknown",
            "message": ("Capacity cannot be compared: "
                        + ("the number of occupants is unknown. " if not people.known else "")
                        + ("this assembly point has no mapped area. " if not capacity.known else "")
                        ).strip(),
        }
    enough = capacity.value >= people.value
    return {
        "state": "sufficient" if enough else "insufficient",
        "headroom": int(capacity.value - people.value),
        "message": (f"Holds about {capacity.value:,}; {people.value:,} to move."
                    if enough else
                    f"About {capacity.value:,} capacity for {people.value:,} occupants — "
                    f"roughly {people.value - capacity.value:,} more than it can take. "
                    f"A second assembly point is needed."),
    }


def walk_minutes(metres: Optional[float]) -> prov.Finding:
    """Time on foot. Evacuating occupants do not drive."""
    if metres is None or not math.isfinite(metres) or metres <= 0:
        return prov.unknown("no walkable route was found on the mapped network", "min")
    return prov.estimated(
        round(metres / 1000.0 / WALK_SPEED_KMH * 60.0, 1),
        f"{int(metres):,} m along the road network at {WALK_SPEED_KMH:g} km/h — a marshalled "
        f"walking pace, not a drive time",
        "min")


def demo():
    from .impact import Facility

    school = Facility(id="s", name="Toy School", category="school", lat=0, lon=0)
    found = occupancy(school)
    assert found.state == prov.ESTIMATED and found.value == 600
    assert "every asset of this category" in found.basis

    # A published capacity beats the constant.
    sized = Facility(id="s2", name="Real School", category="school", lat=0, lon=0, capacity=340)
    assert occupancy(sized).state == prov.MEASURED and occupancy(sized).value == 340

    # An operator feed beats both.
    assert occupancy(school, {"s": 512}).value == 512

    ground = Facility(id="g", name="Toy Ground", category="open_space", lat=0, lon=0)
    assert assembly_capacity(ground).state == prov.UNKNOWN
    ground.area_m2 = 900
    cap = assembly_capacity(ground)
    assert cap.value == 900

    # 600 into 900 fits; 600 into 400 does not; unknown is neither.
    assert sufficiency(found, cap)["state"] == "sufficient"
    small = prov.estimated(400, "x", "people")
    assert sufficiency(found, small)["state"] == "insufficient"
    assert "second assembly point" in sufficiency(found, small)["message"]
    assert sufficiency(found, prov.unknown("no area"))["state"] == "unknown"

    walk = walk_minutes(1748)
    assert walk.value == 26.2, walk.value
    assert "not a drive time" in walk.basis
    print(f"ok — occupancy chain, capacity check, 1,748 m walk = {walk.value} min")


if __name__ == "__main__":
    demo()
