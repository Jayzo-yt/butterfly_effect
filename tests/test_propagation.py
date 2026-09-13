from src.disruption.propagation import (
    EMERGENCY_SERVICE_FLOOR,
    mechanism_a_dependency_cascade,
    mechanism_b_load_redistribution,
)
from src.graph.builder import build_toy_city

JUNCTIONS = [f"J{i}" for i in range(1, 12)]
HOSPITALS = ["H1", "H2"]
OD_PAIRS = [(j, h) for j in JUNCTIONS for h in HOSPITALS]


def test_hospital_losing_its_supply_degrades_but_keeps_emergency_function():
    """A hospital cut off from mains water runs on tankers and generators; it
    does not vanish. Modelling it as status 0 deleted 16 of 19 hospitals from
    a single water tower failure, and the resulting 'nearest working hospital'
    distance was then reported as added driving time."""
    city = build_toy_city()
    city.G_utility.nodes["W1"]["status"] = 0.0
    mechanism_a_dependency_cascade(city)
    status = city.node_attrs("H2")["status"]  # no redundancy
    assert 0 < status < 1, "degraded, not erased"
    assert status == EMERGENCY_SERVICE_FLOOR


def test_dependency_cascade_degrades_with_redundancy():
    city = build_toy_city()
    city.G_utility.nodes["W1"]["status"] = 0.0
    mechanism_a_dependency_cascade(city)
    assert city.node_attrs("H1")["status"] == 0.6  # backup generator -> degrades, doesn't die


def test_load_redistribution_converges():
    city = build_toy_city()
    u, v = "J2", "J3"
    city.G_road.edges[u, v]["max_capacity"] = 1.0  # force overload with few OD pairs
    iterations = mechanism_b_load_redistribution(city, OD_PAIRS, max_iter=20)
    assert iterations <= 20
    # congested edge's weight should have increased from the baseline
    assert city.G_road.edges[u, v]["current_weight"] > 5.0


if __name__ == "__main__":
    test_hospital_losing_its_supply_degrades_but_keeps_emergency_function()
    test_dependency_cascade_degrades_with_redundancy()
    test_load_redistribution_converges()
    print("ok")
