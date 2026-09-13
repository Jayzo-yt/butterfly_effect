"""Criticality ranking — ARCHITECTURE.md §6.2, the core deliverable.

Deliberately a full leave-one-out over every node, no topological pre-filter:
the whole point is that outcome-based ranking can surface a node (e.g. a
shared water pump) that plain betweenness centrality would rank as
unimportant. Pre-filtering by centrality would throw away exactly the nodes
this is meant to catch. At toy/city-subgraph scale the full simulation is
cheap enough that there is nothing to optimize yet.
"""
import networkx as nx

from ..disruption.propagation import run_propagation
from ..graph.multiplex import CityGraph
from .metrics import avg_hospital_delay, coverage_ratio, disconnected_count

# Tunable weights combining the score components into one ranking number.
W_DELAY = 1.0
W_DISCONNECTION = 10.0
W_CASCADE_DEPTH = 1.0
W_COVERAGE = 50.0  # coverage is a 0-1 ratio, so it needs a larger multiplier


def representative_junctions(city: CityGraph, max_origins: int = 20):
    """A representative sample of junctions to use as routing origins.

    This is the "Targeted OD Pairs" mitigation from ARCHITECTURE.md §5.2 —
    NOT the rejected topological pre-filter from §6.2. It only thins out how
    many origins get a shortest-path computed per iteration; every single
    node (junction, hospital, substation, pump) is still individually
    disabled and scored below. On a small graph (max_origins >= node count)
    this is a no-op and every junction is used.
    """
    junctions = city.nodes_of_type("road_junction")
    step = max(1, len(junctions) // max_origins)
    return junctions[::step][:max_origins]


def _disable(city: CityGraph, node_id: str):
    if node_id in city.G_road:
        city.G_road.remove_node(node_id)
    elif node_id in city.G_utility:
        city.G_utility.nodes[node_id]["status"] = 0.0


def leave_one_out_ranking(city: CityGraph, od_pairs, junctions, hospitals,
                           candidates=None) -> dict:
    """Returns {node_id: score}, higher score = more critical. Score combines
    delay increase, newly-disconnected junctions, and cascade depth.

    `junctions` is the demand sample used to *measure* impact (pass a
    representative_junctions() sample on a large city to keep this fast) —
    unrelated to which nodes get tested.

    `candidates` restricts *which* nodes get disabled. Leave it None for the
    real ranking, which must test every node — that is the whole method. It
    exists for the sensitivity analysis, which asks the narrower question
    "do the already-top assets stay on top under a different wiring?" and so
    does not need to re-score four thousand nodes to answer it.
    """
    baseline = city.copy()
    run_propagation(baseline, od_pairs)
    # Computed once — re-deriving these per iteration was the actual cost
    # blowup on the real city graph, not the leave-one-out loop itself.
    baseline_delay = avg_hospital_delay(baseline, junctions, hospitals)
    baseline_disc = disconnected_count(baseline, junctions, hospitals)
    baseline_coverage = coverage_ratio(baseline, junctions, hospitals)

    scores = {}
    all_nodes = list(city.G_road.nodes) + list(city.G_utility.nodes)
    if candidates is not None:
        allowed = set(candidates)
        all_nodes = [n for n in all_nodes if n in allowed]
    for node_id in all_nodes:
        snapshot = city.copy()
        _disable(snapshot, node_id)
        cascade_depth = run_propagation(snapshot, od_pairs)

        snapshot_delay = avg_hospital_delay(snapshot, junctions, hospitals)
        delay_delta = 1e6 if snapshot_delay == float("inf") else snapshot_delay - baseline_delay
        disc_delta = disconnected_count(snapshot, junctions, hospitals) - baseline_disc
        coverage_loss = baseline_coverage - coverage_ratio(snapshot, junctions, hospitals)

        scores[node_id] = (
            W_DELAY * delay_delta
            + W_DISCONNECTION * disc_delta
            + W_COVERAGE * coverage_loss
            + W_CASCADE_DEPTH * cascade_depth
        )

    return scores


def betweenness_baseline(city: CityGraph) -> dict:
    """Topological comparison per §6.2 — computed to show it diverges from
    the outcome-based ranking above, not used to filter anything."""
    return nx.betweenness_centrality(city.G_road, weight="current_weight")
