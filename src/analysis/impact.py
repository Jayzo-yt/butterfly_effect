"""Localised disruption impact analysis.

Why this module exists
----------------------
The original metrics were citywide averages, and a road accident is a local
event. Measured that way an accident on a real road segment scored +0.0000
min: the segment is ~0.3 min of a trip, the change was averaged across 2,600
junctions, and none of the sampled traffic used that road at all. The
arithmetic was right; the question was wrong.

This asks the local question instead: given a disruption at a place, what is
near it, what gets harder to reach, and by how much.

How impact is established
-------------------------
Not by distance alone, and never by pre-baked id relationships. For each
facility:

  1. snap it to the road network,
  2. route from every hospital / emergency station outward across the whole
     network, before and after the disruption (multi-source Dijkstra, a
     couple of passes for the entire city rather than one per facility),
  3. the change in that facility's travel time is its accessibility loss.

A facility is therefore "affected" because a route actually got longer or
disappeared — which is a statement that can be checked, and is reported
alongside the number.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx

from ..graph.infrastructure import CATEGORIES, criticality_of, group_of, label_of
from ..graph.multiplex import CityGraph

# --- disruption model --------------------------------------------------------
# The registry lives in incidents.py, which holds the full model (what an
# incident does to an asset, who responds, what it evacuates). These two names
# are the road-facing slice of it.
from .incidents import DISRUPTION_TYPES, SEVERITY_WEIGHT  # noqa: E402

# Impact bands. A facility's score is the accessibility loss weighted by how
# much that category's loss matters; the thresholds are where those two
# together stop being a nuisance and start being an operational problem.
IMPACT_BANDS = [(60.0, "critical"), (25.0, "high"), (8.0, "moderate"), (0.5, "low")]

# Labelled estimate, not a measurement: OSM building footprints carry no
# occupancy. Stated here so the one assumption behind every population
# figure is in a single place.
PEOPLE_PER_BUILDING = 4.5


@dataclass
class Disruption:
    kind: str
    severity: str = "high"
    duration_hours: float = 2.0
    edge: Optional[Tuple[str, str]] = None
    node: Optional[str] = None
    radius_m: Optional[float] = None  # operator override
    # Set when the incident is at an asset rather than on a road: the asset's
    # own coordinates (which are off the carriageway) and the cordon its type
    # closes around it.
    facility_id: Optional[str] = None
    point: Optional[Tuple[float, float]] = None
    cordon_m: float = 0.0

    @property
    def spec(self) -> dict:
        return DISRUPTION_TYPES.get(self.kind, DISRUPTION_TYPES["accident"])

    @property
    def severity_weight(self) -> float:
        return SEVERITY_WEIGHT.get(self.severity, 0.75)

    def affected_radius(self) -> float:
        """Radius the disruption is treated as reaching.

        Scales with severity and, more weakly, with duration — a six-hour
        closure spreads further than a fifteen-minute one because traffic has
        time to back up, but not proportionally. Operator override wins.
        """
        if self.radius_m:
            return float(self.radius_m)
        base = self.spec["base_radius_m"]
        duration_factor = min(2.0, 0.7 + 0.3 * math.log2(max(self.duration_hours, 0.25) + 1))
        return base * (0.5 + self.severity_weight) * duration_factor


# --- geometry ----------------------------------------------------------------

def meters_between(a, b) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = (lon2 - lon1) * 111_320 * math.cos(mean_lat)
    dy = (lat2 - lat1) * 110_540
    return math.hypot(dx, dy)


ROAD_CLASS_LABEL = {
    "motorway": "Highway", "trunk": "Major road", "primary": "Main road",
    "secondary": "Secondary road", "tertiary": "Local road",
    "residential": "Residential street", "unclassified": "Minor road",
    "service": "Service road", "living_street": "Residential street",
}


def road_label(city: CityGraph, u: str, v: str, landmarks: List["Facility"] = ()) -> dict:
    """A name an operator can act on, never a pair of node ids.

    Uses the street name when OSM has one. When it does not, it builds a
    fallback from the road's classification and the nearest named landmark —
    "Unnamed residential street near District Hospital" locates the incident
    for someone dispatching a crew; "2539402888 -- 1838490543" does not.
    """
    if not city.G_road.has_edge(u, v):
        return {"name": "Unknown road", "road_class": "", "id": f"{u}-{v}"}
    data = city.G_road.edges[u, v]
    klass = ROAD_CLASS_LABEL.get(data.get("road_class", ""), "Road")
    # OSM packs alternative names into one tag: "Malpe-Manipal Road;Malpe -
    # Manipal Road". An operator needs one name, not the tag.
    name = (data.get("name") or "").split(";")[0].strip()

    if not name:
        mid = _edge_midpoint(city, u, v)
        near = None
        if mid and landmarks:
            named = [f for f in landmarks if f.name]
            if named:
                near = min(named, key=lambda f: meters_between(mid, (f.lat, f.lon)))
        name = f"Unnamed {klass.lower()} near {near.name}" if near else f"Unnamed {klass.lower()}"

    return {
        "name": name,
        "road_class": klass,
        "named_in_osm": bool((data.get("name") or "").strip()),
        "id": f"{u}-{v}",
        "nodes": [u, v],
    }


def disruption_point(city: CityGraph, disruption: Disruption) -> Optional[tuple]:
    """Where the incident is, as a coordinate — the midpoint of the affected
    segment, or the node itself."""
    if disruption.point:
        return tuple(disruption.point)
    if disruption.edge:
        u, v = disruption.edge
        if u in city.G_road and v in city.G_road:
            a, b = city.node_attrs(u)["geo"], city.node_attrs(v)["geo"]
            return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    if disruption.node and disruption.node in city.G_road:
        return city.node_attrs(disruption.node)["geo"]
    return None


# --- applying the disruption to the network ----------------------------------

def apply_disruption(city: CityGraph, disruption: Disruption) -> List[Tuple[str, str]]:
    """Mutate the road network and return the segments actually affected.

    Blocking events remove the segment from routing; slowing events multiply
    its travel time. Spatial events (flooding, spill) also degrade other
    segments inside the radius, because water does not respect the fact that
    the operator clicked one road.
    """
    spec = disruption.spec
    weight = disruption.severity_weight
    affected: List[Tuple[str, str]] = []

    def hit(u, v, blocking: bool):
        if not city.G_road.has_edge(u, v):
            return
        edge = city.G_road.edges[u, v]
        if blocking:
            edge["current_weight"] = float("inf")
            edge["max_capacity"] = 0.0
        else:
            edge["current_weight"] *= 1 + (spec["slowdown"] - 1) * weight
            edge["max_capacity"] *= max(0.1, 1 - 0.6 * weight)
        affected.append((u, v))

    blocks = spec["blocks"] and weight >= 0.5  # a "low severity" closure still lets traffic trickle
    centre = disruption_point(city, disruption)

    if disruption.edge:
        hit(disruption.edge[0], disruption.edge[1], blocks)
    elif disruption.cordon_m and centre:
        # An incident at an asset closes the streets around it. A structure
        # fire shuts the road outside whether or not anyone selected that road,
        # which is how an asset incident reaches the road network at all.
        cordon = disruption.cordon_m * (0.5 + weight)
        for u, v in list(city.G_road.edges):
            mid = _edge_midpoint(city, u, v)
            if mid and meters_between(centre, mid) <= cordon:
                hit(u, v, blocks)
    elif disruption.node and not disruption.facility_id and disruption.node in city.G_road:
        # Legacy node-targeted disruption (/analyze?node=): the junction itself
        # is the event, so every road into it is affected.
        #
        # An *asset* incident never lands here. Its road effect is its cordon,
        # and an incident type with no cordon has none: a substation losing
        # power does not slow the street outside it, but this branch was
        # degrading three segments and reporting them as traffic impact.
        for neighbour in list(city.G_road.neighbors(disruption.node)):
            hit(disruption.node, neighbour, blocks)

    if spec["spatial"]:
        radius = disruption.affected_radius()
        if centre:
            for u, v in list(city.G_road.edges):
                if (u, v) in affected or (v, u) in affected:
                    continue
                mid = _edge_midpoint(city, u, v)
                if mid and meters_between(centre, mid) <= radius:
                    # Degrades with distance from the centre rather than a
                    # hard cut at the radius edge.
                    share = 1 - meters_between(centre, mid) / radius
                    edge = city.G_road.edges[u, v]
                    if blocks and share > 0.55:
                        edge["current_weight"] = float("inf")
                        edge["max_capacity"] = 0.0
                    else:
                        edge["current_weight"] *= 1 + 1.5 * weight * share
                        edge["max_capacity"] *= max(0.2, 1 - 0.5 * weight * share)
                    affected.append((u, v))
    return affected


def _edge_midpoint(city: CityGraph, u: str, v: str):
    try:
        a, b = city.node_attrs(u)["geo"], city.node_attrs(v)["geo"]
    except KeyError:
        return None
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


# --- facilities --------------------------------------------------------------

@dataclass
class Facility:
    id: str
    name: str
    category: str
    lat: float
    lon: float
    address: str = ""
    capacity: Optional[float] = None
    node: Optional[str] = None          # snapped road node
    snap_m: float = 0.0
    landmark: str = ""                  # nearest named thing, for unnamed assets
    # Mapped ground area, where OSM has a polygon. Decides whether an assembly
    # point can hold the people being sent to it.
    area_m2: float = 0.0

    @property
    def display_name(self) -> str:
        """A label that says what the thing is, and where.

        OSM names an electrical substation "Manipal", which in a sentence like
        "supply comes from Manipal" tells an operator nothing; and 47 of the
        assets here have no name at all. If the name does not already carry its
        own kind, the category is appended, and an unnamed asset is located by
        its nearest named neighbour — never by the OSM id, which is metadata
        and belongs in the technical detail.
        """
        label = label_of(self.category)
        if not self.name:
            return (f"Unnamed {label.lower()} near {self.landmark}" if self.landmark
                    else f"Unnamed {label.lower()}")
        words = [w.strip(".,").lower() for w in self.name.split()]
        if any(w.lower() in words for w in label.split() if len(w) > 3):
            return self.name
        # Only where OSM names are habitually terse: a substation called
        # "Manipal" needs saying what it is, "Cricket Stadium" does not.
        if len(words) == 1 or group_of(self.category) in ("Utilities", "Communications"):
            return f"{self.name} {label.lower()}"
        return self.name


def snap_facilities(city: CityGraph, facilities: List[dict], max_snap_m: float = 900) -> List[Facility]:
    """Attach each facility to its nearest road node.

    Bucketed into a coarse grid so this is linear in facilities rather than
    facilities x nodes — at city scale the naive form is millions of distance
    checks. Anything further than max_snap_m from any road is dropped: it
    cannot be reasoned about with a road model.
    """
    cell = 0.01  # ~1.1 km
    grid: Dict[tuple, list] = {}
    for n, data in city.G_road.nodes(data=True):
        lat, lon = data["geo"]
        grid.setdefault((int(lat / cell), int(lon / cell)), []).append((n, lat, lon))

    snapped = []
    for f in facilities:
        lat, lon = f["lat"], f["lon"]
        gy, gx = int(lat / cell), int(lon / cell)
        best, best_d = None, None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for n, nlat, nlon in grid.get((gy + dy, gx + dx), ()):
                    d = meters_between((lat, lon), (nlat, nlon))
                    if best_d is None or d < best_d:
                        best, best_d = n, d
        if best is None or best_d > max_snap_m:
            continue
        snapped.append(Facility(
            id=f["id"], name=f.get("name", ""), category=f["category"],
            lat=lat, lon=lon, address=f.get("address", ""),
            capacity=f.get("capacity"), node=best, snap_m=best_d,
            area_m2=float(f.get("area_m2") or 0.0),
        ))

    # Locate the unnamed ones by their nearest named neighbour, so an operator
    # reads "Unnamed playing field near Kasturba Hospital" rather than a label
    # that could be any of forty identical entries.
    named = [f for f in snapped if f.name]
    for f in snapped:
        if not f.name and named:
            f.landmark = min(named, key=lambda n: meters_between((f.lat, f.lon),
                                                                 (n.lat, n.lon))).name
    return snapped


# --- accessibility -----------------------------------------------------------

def travel_times_from(city: CityGraph, sources: List[str]) -> Dict[str, float]:
    """Travel time from the nearest of `sources` to every reachable node.

    One multi-source Dijkstra for the whole city, not one run per
    origin-destination pair. Unreachable nodes are simply absent, which is how
    "cut off" is detected rather than inferred.
    """
    routable = city.routable_road()
    live = {s for s in sources if s in routable}
    if not live:
        return {}
    distances, _ = nx.multi_source_dijkstra(routable, live, weight="current_weight")
    return distances


def _category_nodes(facilities: List[Facility], keys: List[str]) -> List[str]:
    return [f.node for f in facilities if f.category in keys and f.node]


def nearest_reachable(city: CityGraph, times: Dict[str, float], site: str,
                      max_m: float = 1500):
    """Closest still-drivable node to `site`, as (node, minutes, metres).

    An incident at an asset sits inside the cordon its own response closes, so
    routing to the exact node reports "no route" for the very crews setting up
    that cordon. This gives the point they can actually drive to, and the
    caller says so rather than reporting the site as unreachable.
    """
    if site not in city.G_road:
        return None
    origin = city.node_attrs(site)["geo"]
    best = None
    for node, t in times.items():
        if t is None or not math.isfinite(t) or node not in city.G_road:
            continue
        d = meters_between(origin, city.node_attrs(node)["geo"])
        if d <= max_m and (best is None or d < best[2]):
            best = (node, t, d)
    return best


@dataclass
class FacilityImpact:
    facility: Facility
    distance_m: float
    baseline_min: Optional[float]
    after_min: Optional[float]
    added_min: float
    cut_off: bool
    score: float
    level: str
    reason: str

    def to_dict(self) -> dict:
        # JSON has no infinity. An unreachable facility has an infinite travel
        # time internally, and letting that reach the encoder raised
        # "Out of range float values are not JSON compliant" — a 500 on an
        # otherwise valid analysis. Unreachable is expressed by `cut_off`,
        # so the numeric fields carry null instead.
        def finite(value):
            return None if value is None or not math.isfinite(value) else value

        return {
            "id": self.facility.id,
            "name": self.facility.display_name,
            "category": self.facility.category,
            "category_label": label_of(self.facility.category),
            "group": CATEGORIES[self.facility.category].group if self.facility.category in CATEGORIES else "Other",
            "address": self.facility.address,
            "lat": self.facility.lat,
            "lon": self.facility.lon,
            "distance_m": None if not math.isfinite(self.distance_m) else round(self.distance_m),
            "baseline_min": None if finite(self.baseline_min) is None else round(self.baseline_min, 1),
            "after_min": None if finite(self.after_min) is None else round(self.after_min, 1),
            "added_min": round(finite(self.added_min) or 0.0, 1),
            "cut_off": self.cut_off,
            "score": round(finite(self.score) or 0.0, 1),
            "level": self.level,
            "reason": self.reason,
        }


def _band(score: float) -> str:
    for threshold, level in IMPACT_BANDS:
        if score >= threshold:
            return level
    return "none"


def _score_facility(added_min: float, cut_off: bool, category: str,
                    distance_m: float, radius_m: float, disruption: Disruption) -> float:
    """Impact score for one facility.

    Accessibility loss is the driver — a facility whose access did not change
    is not affected, however close it sits to the incident. Proximity only
    modulates, and criticality says how much the loss matters. Duration and
    severity are already reflected in the routing change and the radius, so
    they are not multiplied in again.
    """
    if cut_off:
        access_term = 60.0
    elif added_min <= 0.01:
        access_term = 0.0
    else:
        access_term = added_min

    if access_term == 0.0:
        return 0.0

    proximity = 1.0 if distance_m <= radius_m else max(0.3, radius_m / max(distance_m, 1))
    criticality = criticality_of(category) / 10.0
    return access_term * proximity * (0.4 + criticality)


def _reason(added_min: float, cut_off: bool, distance_m: float, radius_m: float,
            in_zone: bool, category: str) -> str:
    if cut_off:
        return "No remaining route from emergency services — access severed"
    if added_min >= 5:
        return f"Primary access route affected, adding {added_min:.0f} min"
    if added_min >= 1:
        return f"Access route slower by {added_min:.1f} min; alternatives available"
    if added_min > 0.01:
        return f"Marginal delay of {added_min:.1f} min on the approach route"
    if in_zone:
        return "Inside the disruption zone but its access route is unaffected"
    return "Outside the impact zone"


# --- the analysis ------------------------------------------------------------

def analyze(city: CityGraph, facilities: List[Facility], disruption, top_n: int = 40) -> dict:
    """Run one or more disruptions and report what they do to the road network.

    `disruption` is a Disruption or a list of them. Several at once are applied
    to the same network before anything is measured, which is the point: a
    flood that closes the coast road and a power cut that stops a pumping
    station are not two independent problems, and evaluating them separately
    misses the combination that actually strands a hospital.

    `city` is mutated — pass a copy. Every figure traces to the routing it came
    from, and anything that is an estimate says so.
    """
    disruptions = [disruption] if isinstance(disruption, Disruption) else list(disruption)
    primary = disruptions[0]

    # Emergency services reach outward; people travel to healthcare. Both are
    # "time between this node and the nearest of a set", symmetric on an
    # undirected graph, so one multi-source pass answers each.
    responders = _category_nodes(facilities, ["fire_station", "police", "ambulance_station"])
    healthcare = _category_nodes(facilities, ["hospital", "clinic", "health_centre"])
    reach_sources = responders or healthcare

    before_reach = travel_times_from(city, reach_sources)
    before_health = travel_times_from(city, healthcare)

    zones, affected_segments, networks = [], [], []
    for d in disruptions:
        centre_d = disruption_point(city, d)
        radius_d = d.affected_radius()
        zones.append((centre_d, radius_d, d))

        # Captured before mutation: the segment's own normal travel time,
        # needed to price the detour the closure forces.
        direct_before = None
        if d.edge and city.G_road.has_edge(*d.edge):
            direct_before = city.G_road.edges[d.edge]["current_weight"]

        segments = apply_disruption(city, d)
        affected_segments.extend(segments)
        networks.append((d, direct_before, set(segments)))

    affected_segments = list(dict.fromkeys(affected_segments))
    after_reach = travel_times_from(city, reach_sources)
    after_health = travel_times_from(city, healthcare)

    centre = zones[0][0]
    radius = zones[0][1]

    impacts: List[FacilityImpact] = []
    for f in facilities:
        if not f.node:
            continue
        # With several incidents the relevant distance is to the nearest one,
        # and "in the zone" means inside any of them.
        distance, in_zone, near_radius = float("inf"), False, radius
        for centre_d, radius_d, _ in zones:
            if not centre_d:
                continue
            d_m = meters_between((f.lat, f.lon), centre_d)
            if d_m < distance:
                distance, near_radius = d_m, radius_d
            in_zone = in_zone or d_m <= radius_d
        before = before_reach.get(f.node)
        after = after_reach.get(f.node)

        # Blocking sets edge weight to infinity rather than removing the
        # segment, so a node reachable only through it comes back with an
        # infinite distance instead of being absent. Both mean "cut off".
        unreachable = after is None or not math.isfinite(after)
        cut_off = before is not None and math.isfinite(before) and unreachable
        added = 0.0 if before is None or unreachable else max(0.0, after - before)

        score = _score_facility(added, cut_off, f.category, distance, near_radius, primary)
        level = _band(score)
        if level == "none" and not in_zone:
            continue  # unaffected and not even nearby
        impacts.append(FacilityImpact(
            facility=f, distance_m=distance, baseline_min=before, after_min=after,
            added_min=added, cut_off=cut_off, score=score, level=level,
            reason=_reason(added, cut_off, distance, near_radius, in_zone, f.category),
        ))

    impacts.sort(key=lambda i: -i.score)
    affected = [i for i in impacts if i.level != "none"]
    network_reports = [_network_impact(city, direct, d, segs) for d, direct, segs in networks]

    return {
        "disruption": _disruption_dict(city, primary, facilities, zones[0][1], len(affected_segments)),
        "disruptions": [_disruption_dict(city, d, facilities, r, None) for _, r, d in zones],
        "summary": _summarise(affected, impacts, affected_segments, radius, len(zones)),
        "network": network_reports[0],
        "networks": network_reports,
        "population": _population(city, [(c, r) for c, r, _ in zones]),
        "emergency": _emergency(city, facilities, before_health, after_health,
                                before_reach, after_reach, centre,
                                cordon_m=max((d.cordon_m * (0.5 + d.severity_weight)
                                              for d in disruptions), default=0.0)),
        "facilities": [i.to_dict() for i in impacts[:top_n]],
        "affected_segments": [{"source": u, "target": v} for u, v in affected_segments[:300]],
        # Travel-time maps for the baseline-vs-disrupted comparison, which
        # needs the same routing rather than a second, differently-computed one.
        "_routing": {"before_health": before_health, "after_health": after_health,
                     "before_reach": before_reach, "after_reach": after_reach},
        # Which segments each individual incident touched, so a caller can ask
        # "what would this look like without this one incident's closure".
        "_segments": [list(segs) for _, _, segs in networks],
    }


def _disruption_dict(city, d: Disruption, facilities, radius, segments_affected) -> dict:
    out = {
        "kind": d.kind,
        "label": d.spec["label"],
        "road": road_label(city, *d.edge, landmarks=facilities) if d.edge else None,
        "facility_id": d.facility_id,
        "severity": d.severity,
        "duration_hours": d.duration_hours,
        "radius_m": round(radius),
        "radius_overridden": d.radius_m is not None,
        "centre": disruption_point(city, d),
    }
    if segments_affected is not None:
        out["segments_affected"] = segments_affected
    return out


def _summarise(affected, all_impacts, segments, radius, zone_count: int = 1) -> dict:
    by_level, by_group = {}, {}
    for i in affected:
        by_level[i.level] = by_level.get(i.level, 0) + 1
        group = CATEGORIES[i.facility.category].group if i.facility.category in CATEGORIES else "Other"
        by_group[group] = by_group.get(group, 0) + 1

    worst = max((i.score for i in affected), default=0.0)
    added = [i.added_min for i in affected if i.added_min > 0]

    # An honest empty state: say what was searched and what was found, rather
    # than a bare zero that reads like a broken screen.
    explanation = ""
    if not affected:
        nearby = len(all_impacts)
        where = (f"the {radius / 1000:.1f} km zone" if zone_count == 1
                 else f"the {zone_count} incident zones")
        explanation = (
            f"No facility's access route changed. {nearby} facilities lie within "
            f"{where} but all retain their existing routes"
            if nearby else
            f"No registered facilities were found within {where}"
        )
        explanation += (f"; {len(segments)} road segment(s) still carry secondary traffic impact."
                        if segments else ".")

    return {
        "affected_total": len(affected),
        "by_level": by_level,
        "by_group": by_group,
        "overall_level": _band(worst),
        "added_min_range": [round(min(added), 1), round(max(added), 1)] if added else [0, 0],
        "explanation": explanation,
    }


def _network_impact(after_city: CityGraph, direct_before: Optional[float],
                    disruption: Disruption, segments) -> dict:
    """What the disruption does to traffic, independent of facilities.

    A closed segment in a dense grid often changes nobody's hospital access —
    traffic simply goes round the block — and reporting only facility impact
    then reads as "no effect", which is useless to a traffic operator. The
    detour is real even when the access impact is nil: this measures how much
    longer the way round actually is, and which nearby roads absorb it.
    """
    if not disruption.edge:
        # An asset incident has no segment of its own; what it does to traffic
        # is the cordon it closes around itself.
        closed = [(u, v) for u, v in segments
                  if after_city.G_road.has_edge(u, v)
                  and not math.isfinite(after_city.G_road.edges[u, v]["current_weight"])]
        return {
            "segments_in_cordon": len(segments),
            "segments_closed": len(closed),
            "severed": False,
            "diverted_onto": [],
            "diverted_count": 0,
            "note": (f"{len(closed)} street(s) closed and {len(segments) - len(closed)} "
                     f"degraded within the cordon around the asset"
                     if segments else
                     "This incident type closes no roads; its effect is on the asset and "
                     "what depends on it"),
        }
    u, v = disruption.edge
    if u not in after_city.G_road or v not in after_city.G_road:
        return {}
    direct = direct_before

    # The detour: best remaining route between the segment's own endpoints.
    routable = after_city.routable_road()
    detour_min = None
    detour_hops = None
    if u in routable and v in routable:
        try:
            length, path = nx.single_source_dijkstra(routable, u, target=v, weight="current_weight")
            if math.isfinite(length):
                detour_min, detour_hops = length, max(0, len(path) - 1)
        except nx.NetworkXNoPath:
            detour_min = None

    severed = detour_min is None
    added = None if severed or direct is None else max(0.0, detour_min - direct)

    # Roads that will carry the diverted traffic: those adjacent to the
    # closure that remain open.
    diverted = []
    for endpoint in (u, v):
        for nb in after_city.G_road.neighbors(endpoint):
            if (endpoint, nb) in segments or (nb, endpoint) in segments:
                continue
            edge = after_city.G_road.edges[endpoint, nb]
            if math.isfinite(edge["current_weight"]):
                diverted.append({"source": endpoint, "target": nb})

    return {
        "segment_normal_min": None if direct is None or not math.isfinite(direct) else round(direct, 2),
        "detour_min": None if detour_min is None else round(detour_min, 2),
        "detour_hops": detour_hops,
        "added_travel_min": None if added is None else round(added, 2),
        "severed": severed,
        "diverted_onto": diverted[:12],
        "diverted_count": len(diverted),
        "note": ("No remaining route between the segment's own endpoints — this "
                 "closure splits the local network"
                 if severed else
                 "Traffic between the segment's endpoints must take this detour"),
    }


def _population(city: CityGraph, zones) -> dict:
    """People near the incident, from building density around each junction.

    An estimate and labelled as one: OSM footprints carry no occupancy, so
    this is a building count scaled by an assumed household size. Overlapping
    incident zones count each junction once.
    """
    live = [(c, r) for c, r in zones if c]
    if not live:
        return {"estimated_people": None, "basis": "no incident location"}
    buildings, junctions = 0.0, 0
    for _, data in city.G_road.nodes(data=True):
        if data.get("type") != "road_junction":
            continue
        if any(meters_between(c, data["geo"]) <= r for c, r in live):
            buildings += data.get("population_weight", 1.0)
            junctions += 1
    return {
        "estimated_people": round(buildings * PEOPLE_PER_BUILDING),
        "junctions_in_zone": junctions,
        "basis": f"OSM building density x {PEOPLE_PER_BUILDING} people per building",
        "is_estimate": True,
    }


def _emergency(city, facilities, before_health, after_health,
               before_reach, after_reach, centre, cordon_m: float = 0.0) -> dict:
    """What the incident does to emergency response at the incident itself."""
    if not centre:
        return {}
    site, best = None, None
    for n, data in city.G_road.nodes(data=True):
        d = meters_between(centre, data["geo"])
        if best is None or d < best:
            site, best = n, d
    if site is None:
        return {}

    def nearest(cat_keys, times):
        options = [(f, times.get(f.node)) for f in facilities
                   if f.category in cat_keys and f.node and times.get(f.node) is not None]
        return min(options, key=lambda kv: kv[1])[0] if options else None

    before_hosp, after_hosp = before_health.get(site), after_health.get(site)
    before_resp, after_resp = before_reach.get(site), after_reach.get(site)

    # Same cordon caveat as dispatch: if the site is inside a closed area,
    # measure to the nearest point still drivable rather than declaring the
    # whole site unreachable. Bounded by the cordon itself — without that
    # bound a flood that strands a whole district reported a proxy point a
    # kilometre away as if it were the response time, which read as the
    # response getting *faster* during the flood.
    cordoned = False
    if cordon_m > 0:
        for value, times, which in ((after_hosp, after_health, "hosp"),
                                    (after_resp, after_reach, "resp")):
            if value is None or not math.isfinite(value):
                fallback = nearest_reachable(city, times, site, max_m=cordon_m * 1.5)
                if fallback:
                    cordoned = True
                    if which == "hosp":
                        after_hosp = fallback[1]
                    else:
                        after_resp = fallback[1]

    def delta(b, a):
        if b is None:
            return None
        return float("inf") if a is None else max(0.0, a - b)

    hosp_delta, resp_delta = delta(before_hosp, after_hosp), delta(before_resp, after_resp)
    finite = [d for d in (hosp_delta, resp_delta) if d is not None]
    worst = max(finite) if finite else 0.0
    status = "high risk" if worst == float("inf") or worst >= 5 else (
        "elevated" if worst >= 1 else "normal")

    def shown(d):
        """JSON has no infinity, and an unreachable site legitimately produces
        one. Null here; the `unreachable` flags carry the meaning."""
        return None if d is None or not math.isfinite(d) else round(d, 1)

    hospital = nearest(["hospital", "clinic", "health_centre"], before_health)
    responder = nearest(["fire_station", "police", "ambulance_station"], before_reach)
    return {
        "nearest_hospital": hospital.display_name if hospital else None,
        "hospital_before_min": shown(before_hosp),
        "hospital_after_min": shown(after_hosp),
        "hospital_added_min": shown(hosp_delta),
        "hospital_unreachable": shown(after_hosp) is None and shown(before_hosp) is not None,
        "responder_unreachable": shown(after_resp) is None and shown(before_resp) is not None,
        "nearest_responder": responder.display_name if responder else None,
        "responder_before_min": shown(before_resp),
        "responder_after_min": shown(after_resp),
        "responder_added_min": shown(resp_delta),
        "status": status,
        "measured_to_cordon_edge": cordoned,
        "note": ("Measured to the nearest drivable point outside the closed area — the "
                 "incident site is inside a cordon." if cordoned else ""),
    }
