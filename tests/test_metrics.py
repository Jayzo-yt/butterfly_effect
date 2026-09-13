"""Metric semantics — see related-work.md §2 for the literature these follow."""
from src.analysis.metrics import (
    avg_hospital_delay,
    coverage_ratio,
    hospital_strain,
    nearest_hospital_time,
)
from src.disruption.events import apply_event
from src.disruption.propagation import mechanism_a_dependency_cascade
from src.graph.builder import build_toy_city

JUNCTIONS = [f"J{i}" for i in range(1, 12)]
HOSPITALS = ["H1", "H2"]


def test_destroying_a_hospital_never_improves_delay():
    """The metric must not reward losing a hospital. Averaging over all
    hospitals instead of the nearest one used to do exactly that."""
    base = build_toy_city()
    before = avg_hospital_delay(base, JUNCTIONS, HOSPITALS)
    for victim in HOSPITALS:
        after = build_toy_city()
        apply_event(after, "fire", victim, 1.0)
        assert avg_hospital_delay(after, JUNCTIONS, HOSPITALS) >= before


def test_nearest_hospital_time_picks_the_closer_one():
    city = build_toy_city()
    # H1 hangs off J3, so from J3 the nearest hospital is H1 at its edge weight
    assert nearest_hospital_time(city, "J3", HOSPITALS) == 5.0


def test_degraded_hospital_reads_as_slower_not_healthy():
    city = build_toy_city()
    healthy = nearest_hospital_time(city, "J3", ["H1"])
    city.node_attrs("H1")["status"] = 0.5
    assert nearest_hospital_time(city, "J3", ["H1"]) == healthy * 2


def test_partial_upstream_failure_degrades_dependent():
    """A pump at 50% used to have zero downstream effect."""
    city = build_toy_city()
    city.G_utility.nodes["W1"]["status"] = 0.5
    mechanism_a_dependency_cascade(city)
    assert city.node_attrs("H2")["status"] == 0.5          # no redundancy, inherits support
    assert city.node_attrs("H1")["status"] == 0.6          # backup floor holds it up


def test_surviving_hospital_gets_more_strained_when_the_other_dies():
    """Normalising strain over survivors instead of nominal capacity makes the
    lone survivor's share exactly 1.0, so losing a hospital would look like an
    improvement. Pin the absolute reference."""
    base = build_toy_city()
    before = hospital_strain(base, JUNCTIONS, HOSPITALS)["H1"]

    after = build_toy_city()
    apply_event(after, "fire", "H2", 1.0)
    assert hospital_strain(after, JUNCTIONS, HOSPITALS)["H1"] > before


def test_population_weight_shifts_the_delay_average():
    """A dense junction must count for more than an empty one."""
    flat = build_toy_city()
    unweighted = avg_hospital_delay(flat, JUNCTIONS, HOSPITALS)

    weighted = build_toy_city()
    far = max(JUNCTIONS, key=lambda j: nearest_hospital_time(weighted, j, HOSPITALS))
    weighted.node_attrs(far)["population_weight"] = 50.0
    assert avg_hospital_delay(weighted, JUNCTIONS, HOSPITALS) > unweighted


def test_destroyed_junction_stops_carrying_traffic():
    """A junction set to status 0 must not route traffic. Routing used to read
    only graph structure, so a fire on a junction changed nothing at all and
    only leave-one-out's node removal ever blocked a path."""
    city = build_toy_city()
    before = nearest_hospital_time(city, "J1", HOSPITALS)

    # J2 and J6 are J1's only links to the rest of the network.
    blocked = build_toy_city()
    apply_event(blocked, "fire", "J2", 1.0)
    apply_event(blocked, "fire", "J6", 1.0)
    assert nearest_hospital_time(blocked, "J1", HOSPITALS) > before


def test_coverage_drops_when_hospitals_fail():
    base = build_toy_city()
    before = coverage_ratio(base, JUNCTIONS, HOSPITALS, threshold_min=15.0)
    after = build_toy_city()
    for h in HOSPITALS:
        apply_event(after, "fire", h, 1.0)
    assert coverage_ratio(after, JUNCTIONS, HOSPITALS, threshold_min=15.0) < before


if __name__ == "__main__":
    test_destroying_a_hospital_never_improves_delay()
    test_nearest_hospital_time_picks_the_closer_one()
    test_degraded_hospital_reads_as_slower_not_healthy()
    test_partial_upstream_failure_degrades_dependent()
    test_surviving_hospital_gets_more_strained_when_the_other_dies()
    test_population_weight_shifts_the_delay_average()
    test_coverage_drops_when_hospitals_fail()
    print("ok")
