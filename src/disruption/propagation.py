"""The three propagation mechanisms from ARCHITECTURE.md §5.2.

The damped-BPR redistribution loop (Mechanism B) is small enough to live
here rather than in a separate convergence.py — one function, one loop.
"""
from collections import deque

import networkx as nx

from ..graph.multiplex import CityGraph

ALPHA = 0.3       # damping factor, prevents oscillation
MAX_ITER = 20
CONVERGENCE_TOL = 0.01
LAMBDA = 0.6      # severity decay per hop
DECAY_CUTOFF = 0.05
BACKUP_LEVEL = 0.6  # status a redundant node holds on backup supply

# A hospital cut off from mains water or power does not cease to exist: it
# runs degraded on tankers, stored water and generators. Modelling that as
# status 0 removed 16 of 19 hospitals from the map when a single water tower
# failed, and the resulting "nearest working hospital" figure was then
# reported as though it were added driving time.
#
# This is an assumption, not a measurement, and it is deliberately explicit:
# it is the floor a staffed emergency facility retains when its utilities
# fail. Non-staffed assets (pumps, substations) have no such floor and do
# stop when their supply stops.
EMERGENCY_SERVICE_FLOOR = 0.25
FLOORED_TYPES = {"hospital", "fire_station", "police"}


def mechanism_a_dependency_cascade(city: CityGraph):
    """Dependency cascade, graded by how much upstream support survives.

    The dependent node inherits its *worst* upstream status (a hospital with
    power but no water is limited by the water), floored at BACKUP_LEVEL when
    it has redundancy.

    Deliberately not binary. The previous `status <= 0` test meant a pump at
    50% had no downstream effect whatsoever, silently discarding the
    continuous severity parameter ARCHITECTURE.md §5.1 makes a point of. It
    also assumed "strong" interdependence — any upstream loss kills the
    dependent outright — which the multilayer-network literature identifies
    as unrealistic; see related-work.md §2.
    """
    for node_id, deps in city.dependencies.items():
        if node_id not in city.G_road and node_id not in city.G_utility:
            continue  # leave-one-out removed this node entirely
        node = city.node_attrs(node_id)
        support = min((city.node_attrs(d)["status"] for d in deps), default=1.0)
        if support >= 1.0:
            continue
        degraded = max(support, BACKUP_LEVEL) if node["redundancy"] > 0 else support
        if node.get("type") in FLOORED_TYPES:
            degraded = max(degraded, EMERGENCY_SERVICE_FLOOR)
        node["status"] = min(node["status"], degraded)


def mechanism_b_load_redistribution(city: CityGraph, od_pairs, alpha=ALPHA,
                                     max_iter=MAX_ITER, tol=CONVERGENCE_TOL) -> int:
    """Routes each origin to its nearest operational destination, accumulates
    load onto the edges used, and pushes overloaded edges' travel time up via
    a damped BPR congestion function until total network delay stops changing
    (or max_iter is hit). Returns the number of iterations run — the "cascade
    depth" metric.

    Each origin is routed to *one* destination, the nearest. Loading every
    origin onto a path to every hospital would count the same trip 19 times on
    the real city extract and inflate congestion accordingly; an ambulance
    goes to one hospital. With two hospitals this barely showed, which is why
    it survived until the extract grew.

    Paths come from a single multi-source Dijkstra outward from the
    destinations per iteration, rather than one run per origin-destination
    pair — the graph is undirected, so a path found from a hospital is the
    origin's route to it reversed.
    """
    G = city.G_road
    prev_total_delay = None
    iterations = 0

    origins = {o for o, _ in od_pairs}
    destinations = {d for _, d in od_pairs}

    for iterations in range(1, max_iter + 1):
        for u, v in G.edges:
            G.edges[u, v]["current_load"] = 0.0

        live_dests = {d for d in destinations
                      if d in G and G.nodes[d].get("status", 1.0) > 0}
        if not live_dests:
            break
        routable = city.routable_road()
        live_dests = {d for d in live_dests if d in routable}
        if not live_dests:
            break
        _, paths = nx.multi_source_dijkstra(routable, live_dests, weight="current_weight")

        total_delay = 0.0
        for origin in origins:
            path = paths.get(origin)
            if not path or len(path) < 2:
                continue
            for u, v in zip(path[:-1], path[1:]):
                total_delay += G.edges[u, v]["current_weight"]
                G.edges[u, v]["current_load"] += 1

        for u, v in G.edges:
            e = G.edges[u, v]
            capacity = e["max_capacity"]
            if capacity <= 0:
                continue
            overload_ratio = e["current_load"] / capacity
            congestion_factor = 1 + 0.15 * (overload_ratio ** 4)
            old_weight = e["current_weight"]
            e["current_weight"] = old_weight + alpha * (old_weight * congestion_factor - old_weight)

        if prev_total_delay is not None and prev_total_delay > 0:
            if abs(total_delay - prev_total_delay) / prev_total_delay < tol:
                break
        prev_total_delay = total_delay

    return iterations


def mechanism_c_severity_decay(city: CityGraph, origin: str, initial_severity: float) -> dict:
    """Soft impact score for visualization: impact = initial_severity * LAMBDA^hops,
    floored at DECAY_CUTOFF, BFS-expanded over the road graph."""
    if origin not in city.G_road:
        return {origin: initial_severity}

    impact = {origin: initial_severity}
    frontier = deque([(origin, 0)])
    seen = {origin}
    while frontier:
        node, hops = frontier.popleft()
        value = initial_severity * (LAMBDA ** hops)
        if value < DECAY_CUTOFF:
            continue
        impact[node] = value
        for neighbor in city.G_road.neighbors(node):
            if neighbor not in seen:
                seen.add(neighbor)
                frontier.append((neighbor, hops + 1))
    return impact


def run_propagation(city: CityGraph, od_pairs) -> int:
    """Orchestrates Mechanism A then B. Returns cascade depth (iteration count)."""
    mechanism_a_dependency_cascade(city)
    return mechanism_b_load_redistribution(city, od_pairs)
