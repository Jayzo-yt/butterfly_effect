"""Phase 4 ingestion (ARCHITECTURE.md §7/§8): builds a real city extract for
Manipal from OpenStreetMap.

What is real and what is assumed is tracked per-node in `Node.source`:

    osm       - the asset's existence and location came from OpenStreetMap
    estimated - real asset, but this value is a documented estimate
    assumed   - the asset or the link itself is our assumption

OSM has substations and water towers for this area, so those are now real
assets rather than invented ones. It does NOT have a usable electricity
distribution network (6 power-line ways within 8km, versus the hundreds a
town this size actually has), so *which* substation feeds a given hospital
is still a nearest-asset assumption and is tagged accordingly. See
assumptions.md.

Dev-time only — needs osmnx + network access, which the deployed API does
not. Run once; it writes data/city/manipal.json.

    python scripts/ingest_manipal.py
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import networkx as nx
import osmnx as ox
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.graph.multiplex import CityGraph  # noqa: E402
from src.graph.schema import Edge, Node  # noqa: E402
from src.graph.validate import clean, format_report, report  # noqa: E402

CENTER = (13.3467, 74.7855)  # Manipal, Karnataka
RADIUS_M = 5000           # Manipal + Udupi town, not one neighbourhood (--radius to change)
UTILITY_MARGIN_M = 3000   # utilities may sit outside the road extract; they live in G_utility
POP_RADIUS_M = 200        # buildings within this of a junction count toward its demand
DEDUP_M = 60              # assets closer than this are the same thing mapped twice
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "city" / "manipal.json"

# --- documented estimates (ARCHITECTURE.md §3.3) -----------------------------
LANE_CAPACITY_VEH_HR = 800.0        # HCM standard estimate, per lane

# Free-flow speed by road class, km/h.
#
# Only 84 of 3,507 segments here carry an OSM maxspeed tag. Left to itself
# osmnx imputes the rest from the mean of those 84 — which are mostly highway
# — and assigned 46 km/h to residential streets, making every route through
# the town far too fast. These are explicit planning speeds for Indian urban
# roads: an assumption, but a stated one that matches the streets rather than
# an artefact of a biased sample.
HIGHWAY_SPEED_KPH = {
    "motorway": 80, "motorway_link": 50,
    "trunk": 60, "trunk_link": 40,
    "primary": 45, "primary_link": 35,
    "secondary": 40, "secondary_link": 30,
    "tertiary": 30, "tertiary_link": 25,
    "unclassified": 25,
    "residential": 20,
    "living_street": 10,
    "service": 15,
    "road": 25,
}
DEFAULT_HOSPITAL_CAPACITY = 120.0   # patients/day — no bed count published for these OSM entries
SUBSTATION_CAPACITY_MW = 20.0       # mid of the 10-50 MW tier in §3.3
WATER_CAPACITY_M3HR = 500.0


def _configure_osmnx():
    """osmnx has its own Overpass client, and it defaults to the same busy
    main endpoint our direct queries rotate away from. A 5km extract is a
    heavy query, so point it at a mirror and give it room."""
    ox.settings.overpass_url = "https://overpass.kumi.systems/api"
    ox.settings.requests_timeout = 300
    ox.settings.overpass_rate_limit = False


def UTILITY_SEARCH_M() -> int:
    """Utilities feeding the extract can sit outside it: they live in
    G_utility, not the road graph, so they need no road connection."""
    return RADIUS_M + UTILITY_MARGIN_M


def BUILDING_SEARCH_M() -> int:
    return RADIUS_M + 200


def _overpass(query: str, rounds: int = 2) -> dict:
    """Overpass rate-limits per IP by slot, and osmnx has already spent some
    on the road/hospital queries above — so rotate mirrors and back off rather
    than hammering one host."""
    last = None
    for round_no in range(rounds):
        for url in OVERPASS_MIRRORS:
            try:
                r = requests.post(
                    url, data=query.encode(), timeout=180,
                    headers={"User-Agent": "cascading-failure-hackathon/1.0"},
                )
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                print(f"    {url.split('/')[2]} unavailable ({type(e).__name__})")
        if round_no + 1 < rounds:
            print("    all mirrors busy; waiting 45s")
            time.sleep(45)
    raise RuntimeError(f"all Overpass mirrors failed: {last}")


def _meters(a, b) -> float:
    """Equirectangular approximation — fine at city scale."""
    (lat1, lon1), (lat2, lon2) = a, b
    mean_lat = (lat1 + lat2) / 2
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians(mean_lat))
    dy = (lat2 - lat1) * 110_540
    return math.hypot(dx, dy)


def _parse_lanes(raw) -> float:
    if raw is None:
        return 1.0
    if isinstance(raw, list):
        raw = raw[0]
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 1.0


def _element_latlon(el):
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    return (lat, lon) if lat is not None and lon is not None else None


def _dedup(points):
    """OSM maps the same physical asset as both a node and a way; collapse
    anything within DEDUP_M into one asset."""
    kept = []
    for geo, name in points:
        if not any(_meters(geo, k[0]) < DEDUP_M for k in kept):
            kept.append((geo, name))
    return kept


# --- road layer --------------------------------------------------------------

def build_road_layer(city: CityGraph):
    G = ox.graph_from_point(CENTER, dist=RADIUS_M, network_type="drive", simplify=True)
    # hwy_speeds overrides the imputation; fallback only where a class is unlisted.
    G = ox.routing.add_edge_speeds(G, hwy_speeds=HIGHWAY_SPEED_KPH, fallback=25)
    G = ox.routing.add_edge_travel_times(G)

    # Collapse the directed multigraph osmnx returns into the simple undirected
    # graph our schema uses — a divided road's two carriageways become one edge.
    Gs = nx.Graph(G)

    for node_id, data in Gs.nodes(data=True):
        city.add_node(
            Node(id=str(node_id), type="road_junction", geo=(data["y"], data["x"]),
                 capacity=LANE_CAPACITY_VEH_HR, source="osm"),
            layer="road",
        )
    for u, v, data in Gs.edges(data=True):
        # Collapsing the OSM multigraph can leave a way that starts and ends
        # at the same node as a self-loop. It is not a road segment anyone can
        # drive along or block, and it crashed the impact analysis.
        if u == v:
            continue
        lanes = _parse_lanes(data.get("lanes"))
        length_m = float(data.get("length", 0.0) or 0.0)
        speed_kph = float(data.get("speed_kph", 0.0) or 0.0)

        # The old floor of 0.1 min bound on 45% of segments: in a dense town
        # most links are short, so nearly half the network was timed at a flat
        # six seconds regardless of length, padding every route that crossed
        # many junctions. Time is now length/speed, floored only against
        # division by zero.
        if length_m > 0 and speed_kph > 0:
            minutes = (length_m / 1000.0) / speed_kph * 60.0
        else:
            minutes = float(data.get("travel_time", 60.0)) / 60.0
        city.add_edge(
            Edge(source=str(u), target=str(v), layer="road",
                 base_weight=max(0.005, minutes),
                 max_capacity=lanes * LANE_CAPACITY_VEH_HR,
                 name=_first(data.get("name")),
                 road_class=_first(data.get("highway")),
                 length_m=round(length_m, 1),
                 speed_kph=round(speed_kph, 1),
                 # osmnx imputes a speed by highway class wherever OSM has no
                 # maxspeed tag. Which of the two produced this number matters
                 # to anyone reading the travel time.
                 speed_source="osm" if data.get("maxspeed") else "estimated"),
            layer="road",
        )
    return Gs


def _first(value) -> str:
    """OSM returns a list where a merged way had several values."""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value else ""


def _nearest_node(Gs, lat, lon):
    return min(Gs.nodes, key=lambda n: (Gs.nodes[n]["y"] - lat) ** 2 + (Gs.nodes[n]["x"] - lon) ** 2)


def attach_real_hospitals(city: CityGraph, Gs):
    hospitals = ox.features_from_point(CENTER, tags={"amenity": "hospital"}, dist=RADIUS_M)
    hospital_ids = []
    for i, (_, row) in enumerate(hospitals.iterrows(), start=1):
        hid = f"H{i}"
        geom = row.geometry if row.geometry.geom_type == "Point" else row.geometry.centroid
        lat, lon = geom.y, geom.x
        nearest = str(_nearest_node(Gs, lat, lon))

        city.add_node(
            Node(id=hid, type="hospital", geo=(lat, lon), capacity=DEFAULT_HOSPITAL_CAPACITY,
                 redundancy=1 if i == 1 else 0, source="osm",
                 name=str(row.get("name") or f"Hospital {i}")),
            layer="road",
        )
        city.add_edge(
            Edge(source=hid, target=nearest, layer="road", base_weight=1.0,
                 max_capacity=LANE_CAPACITY_VEH_HR),
            layer="road",
        )
        hospital_ids.append(hid)
        print(f"  {hid} = {row.get('name', '(unnamed)')!r} -> junction {nearest}")
    return hospital_ids


def prune_dead_end_junctions(city: CityGraph):
    """Remove degree<=1 road_junction nodes (residential dead-ends) per
    ARCHITECTURE.md Phase 4. Hospitals are real endpoints, never candidates."""
    while True:
        leaves = [n for n, d in city.G_road.degree()
                  if d <= 1 and city.G_road.nodes[n]["type"] == "road_junction"]
        if not leaves:
            break
        city.G_road.remove_nodes_from(leaves)


# --- utility layer (now real) ------------------------------------------------

def add_real_utility_layer(city: CityGraph, hospital_ids):
    """Real substations and water assets from OSM. Their *locations* are
    observed; their capacities are estimates, and the hospital->utility
    wiring is an assumption (OSM has no usable distribution network)."""
    lat, lon = CENTER
    query = f"""
    [out:json][timeout:120];
    (
      node["power"="substation"](around:{UTILITY_SEARCH_M()},{lat},{lon});
      way["power"="substation"](around:{UTILITY_SEARCH_M()},{lat},{lon});
      node["man_made"~"water_works|water_tower|pumping_station"](around:{UTILITY_SEARCH_M()},{lat},{lon});
      way["man_made"~"water_works|water_tower|pumping_station"](around:{UTILITY_SEARCH_M()},{lat},{lon});
    );
    out center tags;
    """
    elements = _overpass(query).get("elements", [])

    subs, waters = [], []
    for el in elements:
        geo = _element_latlon(el)
        if geo is None:
            continue
        tags = el.get("tags", {})
        name = tags.get("name", "(unnamed)")
        (subs if tags.get("power") == "substation" else waters).append((geo, name))

    subs, waters = _dedup(subs), _dedup(waters)
    print(f"  real assets from OSM: {len(subs)} substation(s), {len(waters)} water asset(s)")

    sub_ids, water_ids = [], []
    for i, (geo, name) in enumerate(subs, start=1):
        sid = f"S{i}"
        city.add_node(Node(id=sid, type="substation", geo=geo,
                            capacity=SUBSTATION_CAPACITY_MW, source="osm",
                            name=(name if name != "(unnamed)" else f"Substation {i}")),
                      layer="utility")
        sub_ids.append(sid)
        print(f"    {sid} = {name} @ {geo[0]:.4f},{geo[1]:.4f}")
    for i, (geo, name) in enumerate(waters, start=1):
        wid = f"W{i}"
        city.add_node(Node(id=wid, type="water_pump", geo=geo,
                            capacity=WATER_CAPACITY_M3HR, source="osm",
                            name=(name if name != "(unnamed)" else f"Water tower {i}")),
                      layer="utility")
        water_ids.append(wid)
        print(f"    {wid} = {name} @ {geo[0]:.4f},{geo[1]:.4f}")

    if not sub_ids or not water_ids:
        raise RuntimeError("no real utility assets found — widen UTILITY_SEARCH_M")

    # Distribution lines are not mapped in OSM here, so connect each water
    # asset to its nearest substation purely as a plausible topology.
    for wid in water_ids:
        w_geo = city.node_attrs(wid)["geo"]
        nearest_sub = min(sub_ids, key=lambda s: _meters(w_geo, city.node_attrs(s)["geo"]))
        city.add_edge(Edge(source=nearest_sub, target=wid, layer="power",
                            base_weight=1.0, max_capacity=SUBSTATION_CAPACITY_MW),
                      layer="utility")

    # ASSUMED: each hospital draws from its nearest substation and nearest
    # water asset. Geographic proxy, not a verified electrical/water connection.
    for hid in hospital_ids:
        h_geo = city.node_attrs(hid)["geo"]
        sub = min(sub_ids, key=lambda s: _meters(h_geo, city.node_attrs(s)["geo"]))
        wat = min(water_ids, key=lambda w: _meters(h_geo, city.node_attrs(w)["geo"]))
        city.set_dependency(hid, [sub, wat])
        redundancy = city.node_attrs(hid)["redundancy"]
        print(f"    {hid} depends_on [{sub}, {wat}]  (assumed; redundancy={redundancy})")


# --- population weighting ----------------------------------------------------

def apply_population_weights(city: CityGraph):
    """Relative demand per junction from OSM building density.

    A proxy, not a population count — tagged `estimated`. Buildings are
    observed, but 'one building = one unit of demand' ignores building size,
    height and occupancy. WorldPop/GHSL rasters would be the honest upgrade.
    """
    lat, lon = CENTER
    query = f"""
    [out:json][timeout:180];
    ( way["building"](around:{BUILDING_SEARCH_M()},{lat},{lon});
      node["building"](around:{BUILDING_SEARCH_M()},{lat},{lon}); );
    out center ids;
    """
    points = [g for g in (_element_latlon(el) for el in _overpass(query).get("elements", []))
              if g is not None]
    print(f"  {len(points)} buildings -> demand weights")

    junctions = city.nodes_of_type("road_junction")
    # Bucket buildings into a coarse grid so this stays O(buildings), not
    # O(buildings x junctions) — ~3k x ~600 would be 2M distance checks.
    cell = POP_RADIUS_M / 111_320 * 2
    grid = {}
    for p in points:
        grid.setdefault((int(p[0] / cell), int(p[1] / cell)), []).append(p)

    counts = {}
    for j in junctions:
        geo = city.node_attrs(j)["geo"]
        gy, gx = int(geo[0] / cell), int(geo[1] / cell)
        nearby = (p for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                  for p in grid.get((gy + dy, gx + dx), []))
        counts[j] = sum(1 for p in nearby if _meters(geo, p) <= POP_RADIUS_M)

    mean = (sum(counts.values()) / len(counts)) if counts else 0
    for j, c in counts.items():
        # normalise around 1.0 so an average junction keeps its old weight
        node = city.node_attrs(j)
        node["population_weight"] = (c / mean) if mean > 0 else 1.0
        node["source"] = "estimated" if c else "osm"
    if mean:
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        print(f"  mean {mean:.1f} buildings/junction; busiest: "
              + ", ".join(f"{j}={c}" for j, c in top))


def main():
    global RADIUS_M
    ap = argparse.ArgumentParser(description="Ingest a real city extract from OSM.")
    ap.add_argument("--radius", type=int, default=RADIUS_M,
                    help="road network radius in metres around the city centre")
    RADIUS_M = ap.parse_args().radius
    _configure_osmnx()
    print(f"Pulling drive network within {RADIUS_M}m of Manipal center...")
    city = CityGraph()
    Gs = build_road_layer(city)
    print(f"  raw: {city.G_road.number_of_nodes()} nodes, {city.G_road.number_of_edges()} edges")

    print("Attaching real hospitals from OSM...")
    hospital_ids = attach_real_hospitals(city, Gs)
    if not hospital_ids:
        raise RuntimeError("no hospitals found in range — widen RADIUS_M")

    print("Pruning residential dead-ends...")
    prune_dead_end_junctions(city)
    print(f"  pruned: {city.G_road.number_of_nodes()} nodes, {city.G_road.number_of_edges()} edges")

    print("Adding real utility layer from OSM...")
    add_real_utility_layer(city, hospital_ids)

    print("Weighting junctions by building density...")
    apply_population_weights(city)

    # Every ingestion validates before it writes. The four self-loops that
    # crashed the impact analysis were in the dataset for days because nothing
    # ever looked; a graph that fails here is not saved.
    print("Validating extract...")
    data = city.to_dict()
    rep = report(data)
    print(format_report(rep))
    if not rep["ok"]:
        data, removed = clean(data)
        print(f"  removed {len(removed)} invalid elements")
        rep = report(data)
        if not rep["ok"]:
            raise RuntimeError("extract still invalid after cleaning:\n" + format_report(rep))
        print("  re-validated: passed")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(data), encoding="utf-8")
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
