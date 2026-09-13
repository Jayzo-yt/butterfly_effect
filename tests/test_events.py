import pytest

from src.disruption.events import apply_event
from src.graph.builder import build_toy_city


def test_accident_rejects_bare_node_id():
    city = build_toy_city()
    with pytest.raises(ValueError):
        apply_event(city, "accident", "J1", severity=1.0)


def test_accident_rejects_non_adjacent_pair():
    city = build_toy_city()  # J1 and J4 exist but aren't directly connected
    with pytest.raises(ValueError):
        apply_event(city, "accident", ("J1", "J4"), severity=1.0)


def test_accident_applies_on_real_edge():
    city = build_toy_city()
    apply_event(city, "accident", ("J2", "J3"), severity=1.0)
    assert city.G_road.edges["J2", "J3"]["current_weight"] > 5.0


def test_flood_accepts_bare_node_id():
    city = build_toy_city()
    apply_event(city, "flood", "H1", severity=1.0)
    assert city.node_attrs("H1")["status"] == 0.0


if __name__ == "__main__":
    test_accident_rejects_bare_node_id()
    test_accident_rejects_non_adjacent_pair()
    test_accident_applies_on_real_edge()
    test_flood_accepts_bare_node_id()
    print("ok")
