"""ARCHITECTURE.md §6.2 / Phase 3: if leave-one-out ranking matches betweenness
centrality, the outcome-weighting isn't doing anything and needs adjustment."""
from src.analysis.criticality import betweenness_baseline, leave_one_out_ranking
from src.graph.builder import build_toy_city

JUNCTIONS = [f"J{i}" for i in range(1, 12)]
HOSPITALS = ["H1", "H2"]
OD_PAIRS = [(j, h) for j in JUNCTIONS for h in HOSPITALS]


def test_shared_utility_node_ranks_above_leaf_junction():
    city = build_toy_city()
    scores = leave_one_out_ranking(city, OD_PAIRS, JUNCTIONS, HOSPITALS)
    assert scores["W1"] > scores["J11"]


def test_outcome_ranking_diverges_from_betweenness():
    city = build_toy_city()
    scores = leave_one_out_ranking(city, OD_PAIRS, JUNCTIONS, HOSPITALS)
    betweenness = betweenness_baseline(city)

    # W1 (shared water pump, single point of failure for both hospitals) isn't
    # even in the road graph, so betweenness can't rank it at all — but it's
    # exactly the kind of hidden critical asset outcome-based ranking exists to catch.
    assert "W1" not in betweenness
    assert scores["W1"] > 0


if __name__ == "__main__":
    test_shared_utility_node_ranks_above_leaf_junction()
    test_outcome_ranking_diverges_from_betweenness()
    print("ok")
