"""Sanity invariants from ARCHITECTURE.md §9.2."""
from src.analysis.metrics import avg_hospital_delay, max_flow_value
from src.disruption.events import apply_event
from src.disruption.propagation import run_propagation
from src.graph.builder import build_toy_city

JUNCTIONS = [f"J{i}" for i in range(1, 11)]  # excludes the leaf, J11
HOSPITALS = ["H1", "H2"]
OD_PAIRS = [(j, h) for j in JUNCTIONS for h in HOSPITALS]


def test_removing_leaf_node_does_not_increase_delay():
    before = build_toy_city()
    run_propagation(before, OD_PAIRS)
    before_delay = avg_hospital_delay(before, JUNCTIONS, HOSPITALS)

    after = build_toy_city()
    after.G_road.remove_node("J11")  # dead-end residential leaf
    run_propagation(after, OD_PAIRS)
    after_delay = avg_hospital_delay(after, JUNCTIONS, HOSPITALS)

    assert after_delay == before_delay


def test_flow_never_exceeds_baseline_after_disruption():
    before = build_toy_city()
    before_flow = max_flow_value(before, "J1", "H2")

    after = build_toy_city()
    apply_event(after, "accident", ("H2", "J8"), severity=0.8)  # H2's only road link
    after_flow = max_flow_value(after, "J1", "H2")

    assert after_flow <= before_flow


if __name__ == "__main__":
    test_removing_leaf_node_does_not_increase_delay()
    test_flow_never_exceeds_baseline_after_disruption()
    print("ok")
