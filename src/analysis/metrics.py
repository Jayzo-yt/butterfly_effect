"""Impact metrics from ARCHITECTURE.md §6.1.

Access is measured to the *nearest operational* hospital, and weighted by that
hospital's remaining status. See related-work.md §2 for the accessibility
literature this follows.
"""
import networkx as nx

from ..graph.multiplex import CityGraph

# Isochrone cutoff for "timely access". 8 minutes is the common EMS response
# benchmark. It is genuinely city-scale dependent — on this 1.8km Manipal
# extract a 15-minute cutoff covers everything and the metric tells you
# nothing, so treat it as a parameter to set per city, not a constant.
COVERAGE_THRESHOLD_MIN = 8.0


def nearest_hospital_time(city: CityGraph, junction: str, hospitals) -> float:
    """Effective travel time from `junction` to its nearest working hospital.

    Two deliberate choices:

    * **Nearest, not the mean over all hospitals.** An ambulance goes to the
      closest one. Averaging across every hospital lets the destruction of a
      distant hospital *lower* the citywide mean — on the real Manipal data,
      destroying KMC scored -0.16 min, i.e. the model called a hospital loss
      an improvement.
    * **Divided by status**, so a hospital degraded to 0.6 by Mechanism A
      reads as proportionally slower service rather than being treated as
      fully healthy. Crude stand-in for a service-rate model, but without it
      partial degradation would be invisible to every metric.
    """
    routable = city.routable_road()
    best = None
    for h in hospitals:
        if h not in routable or junction not in routable:
            continue
        status = city.node_attrs(h)["status"]
        if status <= 0:
            continue
        try:
            travel = nx.shortest_path_length(routable, junction, h, weight="current_weight")
        except nx.NetworkXNoPath:
            continue
        effective = travel / status
        best = effective if best is None else min(best, effective)
    return best if best is not None else float("inf")


def _demand(city: CityGraph, junction: str) -> float:
    """Relative demand a junction represents. 1.0 when unweighted; on the real
    extract it is building density around that junction (see ingest script)."""
    return city.node_attrs(junction).get("population_weight", 1.0)


def nearest_assignment(city: CityGraph, junctions, hospitals,
                        require_in_service: bool = True) -> dict:
    """One pass: {junction: (nearest hospital, travel time in minutes)}.

    `require_in_service=False` answers the *road* question — how long to drive
    there — ignoring whether the hospital can currently treat anyone.
    `True` answers the *service* question — how long to reach somewhere that
    can actually help.

    Keeping these apart matters. A water-supply failure takes hospitals out of
    service without touching a single road, and reporting that as "travel time
    added" produced a dashboard claiming a water tower added 53 minutes of
    driving while also saying it is not on the road network. Both figures were
    computable; merging them into one labelled "minutes" was the error.

    Searched *outward from the hospitals* with a multi-source Dijkstra rather
    than once per junction-hospital pair. On the 2,600-node city extract the
    pairwise form is ~50,000 shortest-path runs and takes over five minutes
    per request; this is a handful of graph traversals and takes about a
    second, for identical results.

    The graph is undirected, so hospital->junction distance is the junction's
    travel time. Times are real minutes on the road: no division by status.
    Dividing travel time by a facility's health was a fudge that produced
    minute-denominated numbers which were not minutes of travel.
    """
    candidates = [h for h in hospitals if h in city.G_road]
    if require_in_service:
        candidates = [h for h in candidates if city.node_attrs(h)["status"] > 0]
    if not candidates:
        return {}

    routable = city.routable_road()
    sources = {h for h in candidates if h in routable}
    if not sources:
        return {}
    wanted = {j for j in junctions if j in routable}

    distances, paths = nx.multi_source_dijkstra(routable, sources, weight="current_weight")
    return {node: (paths[node][0], dist)
            for node, dist in distances.items() if node in wanted}


def service_availability(city: CityGraph, hospitals) -> dict:
    """How much hospital *service* exists, independent of roads.

    A utility failure degrades what a hospital can do without changing how
    long it takes to drive there. That belongs in its own metric with its own
    units — facilities and capacity — so it can never be presented as travel
    minutes.
    """
    in_service, degraded, out = [], [], []
    capacity_total = capacity_live = 0.0
    for h in hospitals:
        if h not in city.G_road:
            continue
        attrs = city.node_attrs(h)
        status, capacity = attrs["status"], attrs.get("capacity", 0.0) or 0.0
        capacity_total += capacity
        capacity_live += capacity * max(0.0, status)
        if status <= 0:
            out.append(h)
        elif status < 1:
            degraded.append(h)
        else:
            in_service.append(h)
    return {
        "total": len(in_service) + len(degraded) + len(out),
        "in_service": len(in_service),
        "degraded": len(degraded),
        "out_of_service": len(out),
        "out_ids": out,
        "capacity_share": (capacity_live / capacity_total) if capacity_total else 1.0,
    }


def hospital_strain(city: CityGraph, junctions, hospitals, assignment=None) -> dict:
    """How over-subscribed each hospital is once patients route to the nearest
    operational one — the "KMC fails, so everyone floods Kasturba" effect.
    1.0 is a proportionate load, 2.0 is twice what its capacity implies.

    The reference point is deliberately **nominal capacity across all
    hospitals**, including failed ones, not just the survivors. Normalising
    against survivors is self-cancelling: with one hospital left its share is
    always exactly 1.0, so destroying a hospital would *lower* the strain
    penalty and score as an improvement. (A test pins this.)

    Single pass: catchments are assigned by travel time, not re-solved against
    the resulting strain. Real patients do divert away from a swamped
    hospital; modelling that needs the iterate-to-convergence treatment
    Mechanism B gets.
    """
    if assignment is None:
        assignment = nearest_assignment(city, junctions, hospitals)
    live = [h for h in hospitals if h in city.G_road and city.node_attrs(h)["status"] > 0]
    if not live:
        return {}

    load = {h: 0.0 for h in live}
    for j, (h, _) in assignment.items():
        load[h] += _demand(city, j)

    total_demand = sum(_demand(city, j) for j in junctions if j in city.G_road)
    nominal_capacity = sum(city.node_attrs(h)["capacity"] for h in hospitals if h in city.G_road)
    if total_demand <= 0 or nominal_capacity <= 0:
        return {h: 1.0 for h in live}

    reference = total_demand / nominal_capacity  # demand units per capacity unit
    strain = {}
    for h in live:
        effective_capacity = city.node_attrs(h)["capacity"] * city.node_attrs(h)["status"]
        strain[h] = (load[h] / effective_capacity) / reference if effective_capacity > 0 else float("inf")
    return strain


def avg_hospital_delay(city: CityGraph, junctions, hospitals, use_strain: bool = True) -> float:
    """Demand-weighted mean effective time to the nearest operational hospital.

    Weighted so a dense junction counts for more than an empty one, and scaled
    by its hospital's over-subscription so absorbing a failed hospital's
    catchment shows up as degraded service rather than being free.
    """
    assignment = nearest_assignment(city, junctions, hospitals)
    strain = hospital_strain(city, junctions, hospitals, assignment) if use_strain else {}

    total_time, total_weight = 0.0, 0.0
    for j, (h, t) in assignment.items():
        penalty = max(1.0, strain.get(h, 1.0))
        w = _demand(city, j)
        total_time += t * penalty * w
        total_weight += w
    return total_time / total_weight if total_weight else float("inf")


def coverage_ratio(city: CityGraph, junctions, hospitals,
                    threshold_min: float = COVERAGE_THRESHOLD_MIN) -> float:
    """Share of *demand* within `threshold_min` of a working hospital — the
    isochrone coverage metric of ARCHITECTURE.md §6.1, weighted by population
    proxy so it reads as "how many people lost timely access", not "how many
    junctions". Distinct from disconnection: a junction can still have a path
    and yet be too far for the delay to be survivable."""
    assignment = nearest_assignment(city, junctions, hospitals)
    covered = total = 0.0
    for j in junctions:
        if j not in city.G_road:
            continue
        w = _demand(city, j)
        total += w
        reached = assignment.get(j)
        if reached and reached[1] <= threshold_min:
            covered += w
    return covered / total if total else 0.0


def disconnected_count(city: CityGraph, junctions, hospitals, assignment=None) -> int:
    """Junctions with no route to any operational hospital.

    Falls out of the same multi-source search the other metrics use: a
    junction the search never reached has no path to any live hospital. The
    previous form asked `has_path` for every junction-hospital pair, ~50,000
    traversals on the city extract, and was the single slowest thing in a
    request at 1s a call.
    """
    if assignment is None:
        assignment = nearest_assignment(city, junctions, hospitals)
    return sum(1 for j in junctions if j in city.G_road and j not in assignment)


def max_flow_value(city: CityGraph, source: str, sink: str) -> float:
    if source not in city.G_road or sink not in city.G_road:
        return 0.0
    G = city.G_road.copy()
    for u, v in G.edges:
        G.edges[u, v]["capacity"] = G.edges[u, v]["max_capacity"]
    try:
        return nx.maximum_flow_value(G, source, sink, capacity="capacity")
    except nx.NetworkXError:
        return 0.0
