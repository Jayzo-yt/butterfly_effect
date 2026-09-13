"""The simulation engine.

One pipeline, whatever the incident:

    incident -> affected assets -> network effects -> dependencies -> cascade
             -> impact metrics -> recommended actions

The incident type decides how the initial failure behaves; the city model
decides everything that happens afterwards. Nothing here asks "is this a
school fire" — it asks what the struck asset holds, what it supplies, whether
anything can substitute for it, and who has to reach it. That is why a
warehouse fire, a hospital fire and a substation fire all work without a line
of code each.
"""
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import networkx as nx

from ..graph.areas import AreaIndex
from ..graph.dependencies import NETWORKS, DependencyModel
from ..graph.infrastructure import CATEGORIES, SAFE_HAVEN_KEYS, group_of, label_of
from . import actions as actions_module
from . import cascade as cascade_module
from . import comparison as comparison_module
from . import occupancy as occupancy_module
from . import provenance as prov
from . import recovery as recovery_module
from . import response as response_module
from .impact import (
    Disruption,
    Facility,
    analyze,
    meters_between,
    nearest_reachable,
    road_label,
    travel_times_from,
)
from .incidents import INCIDENT_TYPES, SEVERITY_WEIGHT, incident_label, networks_stopped

EVACUATION_SEARCH_M = 3000    # how far to look for an assembly point
ALTERNATIVES_SHOWN = 4


@dataclass
class IncidentRequest:
    """What the operator asked for. Either a road segment or an asset."""
    type: str
    edge: Optional[Tuple[str, str]] = None
    facility_id: Optional[str] = None
    severity: str = "high"
    duration_hours: float = 2.0
    radius_m: Optional[float] = None

    def spec(self):
        return INCIDENT_TYPES[self.type]


class ScenarioError(ValueError):
    """A request that cannot be simulated — reported to the operator, not raised
    as a 500."""


# --- setting up ---------------------------------------------------------------

# A scenario is operator input, so it is checked once at the door rather than
# trusted downstream. An unknown severity used to reach the action writer and
# raise a ValueError there — a 500 on a typo.
MAX_DURATION_H = 168.0     # a week; beyond this the timeline is fiction
MIN_RADIUS_M, MAX_RADIUS_M = 50.0, 20_000.0


def _validate(request: IncidentRequest) -> None:
    if request.type not in INCIDENT_TYPES:
        raise ScenarioError(f"unknown incident type {request.type!r}")
    if request.severity not in SEVERITY_WEIGHT:
        raise ScenarioError(
            f"unknown severity {request.severity!r} — use one of "
            f"{', '.join(SEVERITY_WEIGHT)}")
    if not 0 < request.duration_hours <= MAX_DURATION_H:
        raise ScenarioError(
            f"duration must be between 0 and {MAX_DURATION_H:g} hours, "
            f"got {request.duration_hours:g}")
    if request.radius_m is not None and not MIN_RADIUS_M <= request.radius_m <= MAX_RADIUS_M:
        raise ScenarioError(
            f"impact zone must be between {MIN_RADIUS_M:g} m and {MAX_RADIUS_M:g} m, "
            f"got {request.radius_m:g} m")


def _resolve(city, facilities: List[Facility], request: IncidentRequest) -> dict:
    """Turn a request into a target the engine can act on, or explain why not."""
    _validate(request)
    spec = request.spec()

    if request.facility_id:
        if "asset" not in spec.targets:
            raise ScenarioError(f"{spec.label} applies to a road segment, not an asset")
        facility = next((f for f in facilities if f.id == request.facility_id), None)
        if facility is None:
            raise ScenarioError(f"no asset with id {request.facility_id!r}")
        if spec.asset_categories and facility.category not in spec.asset_categories:
            allowed = ", ".join(label_of(c).lower() for c in spec.asset_categories)
            raise ScenarioError(
                f"{spec.label} applies to {allowed}, not to a {label_of(facility.category).lower()}")
        if not facility.node:
            raise ScenarioError(f"{facility.display_name} is not connected to the road network")
        return {"kind": "asset", "facility": facility}

    if request.edge:
        if "road" not in spec.targets:
            raise ScenarioError(f"{spec.label} applies to an asset, not a road segment")
        u, v = request.edge
        if u == v:
            raise ScenarioError("that is a single point, not a road segment")
        if not city.G_road.has_edge(u, v):
            raise ScenarioError(f"no road segment between {u!r} and {v!r}")
        return {"kind": "road", "edge": (u, v)}

    raise ScenarioError("select a road segment or an asset for this incident")


def _disruption_for(request: IncidentRequest, target: dict) -> Disruption:
    spec = request.spec()
    if target["kind"] == "asset":
        f = target["facility"]
        return Disruption(kind=request.type, severity=request.severity,
                          duration_hours=request.duration_hours, radius_m=request.radius_m,
                          facility_id=f.id, node=f.node, point=(f.lat, f.lon),
                          cordon_m=spec.road_cordon_m)
    return Disruption(kind=request.type, severity=request.severity,
                      duration_hours=request.duration_hours, radius_m=request.radius_m,
                      edge=target["edge"])


# --- the run ------------------------------------------------------------------

def simulate(city, facilities: List[Facility], requests: List[IncidentRequest],
             dependencies: DependencyModel, areas: Optional[AreaIndex] = None,
             resources: Optional[dict] = None, observed: Optional[dict] = None) -> dict:
    """Run one or more incidents against a copy of the city. `city` is mutated.

    `resources` is an operator feed of station unit counts and duty state, and
    `observed` a feed of real occupancy — both absent from OpenStreetMap, both
    honoured when supplied.
    """
    if not requests:
        raise ScenarioError("no incidents to simulate")

    targets = [_resolve(city, facilities, r) for r in requests]
    disruptions = [_disruption_for(r, t) for r, t in zip(requests, targets)]
    by_id = {f.id: f for f in facilities}

    struck = {t["facility"].id: (r, t["facility"])
              for r, t in zip(requests, targets) if t["kind"] == "asset"}

    # One pool for the whole scenario: a station committed to the first
    # incident is not available to the second.
    pool = response_module.ResourcePool(facilities, resources)

    # Captured before anything is applied.
    usable_before = comparison_module._usable_segments(city)
    responder_cats = sorted({c for r in requests for c in r.spec().responders})
    before_dispatch = {c: travel_times_from(city, _category_nodes(facilities, c, exclude=struck))
                       for c in responder_cats}

    report = analyze(city, facilities, disruptions)
    routing = report.pop("_routing")
    segments_by_incident = report.pop("_segments")
    after_dispatch = {c: travel_times_from(city, _category_nodes(facilities, c, exclude=struck))
                      for c in responder_cats}

    incidents = [_incident_dict(city, facilities, r, t, d, i)
                 for i, (r, t, d) in enumerate(zip(requests, targets, disruptions))]
    cordons = _cordon_zones(disruptions)
    _mark_cordoned(report["facilities"], cordons, struck)

    direct = [_direct_impact(r, f, observed) for r, f in struck.values()]

    # Dependency propagation runs from the assets that actually stopped
    # supplying something, which is derived from the asset's own category.
    seeds = {}
    for fid, (request, facility) in struck.items():
        nets = networks_stopped(request.type, facility.category)
        if not nets:
            continue
        seeds[fid] = {
            "impact": request.spec().asset_loss * SEVERITY_WEIGHT.get(request.severity, 0.75),
            "reason": (f"{facility.display_name} is out of service — "
                       f"{incident_label(request.type, facility.category).lower()}"),
            "networks": nets,
            "confidence": "high",
        }
    cascade = (cascade_module.propagate(dependencies, facilities, seeds) if seeds
               else _no_cascade(dependencies, struck, by_id))

    # One shortest-path tree per struck asset answers both "where can these
    # people go" and "which peer facility absorbs this one's demand".
    #
    # Routed with this incident's own cordon lifted. The cordon exists to
    # protect the evacuation; treating it as a wall reported every assembly
    # point as unreachable and every alternative hospital as unroutable, which
    # is the opposite of what closing the street achieves. Every other
    # incident's closures still apply.
    own_segments = {}
    for index, (request, target) in enumerate(zip(requests, targets)):
        if target["kind"] == "asset":
            own_segments[target["facility"].id] = segments_by_incident[index]
    from_asset = {fid: _tree_from(city, f.node, own_segments.get(fid, []))
                  for fid, (_, f) in struck.items()}

    evacuation = _evacuation(city, facilities, struck, from_asset, cordons, observed)
    alternatives = _alternatives(facilities, struck, from_asset, cordons)
    dispatch = _dispatch(requests, targets, city, facilities, before_dispatch,
                         after_dispatch, struck, pool)

    comparison = comparison_module.build(
        usable_before, city, routing, report["facilities"],
        has_hospitals=bool(_category_nodes(facilities, "hospital")),
        has_responders=bool(responder_cats or _category_nodes(facilities, "fire_station")),
        cascade=cascade,
    )

    area_report = (areas or AreaIndex([])).describe(
        *(incidents[0]["target"]["lat"], incidents[0]["target"]["lon"])
        if incidents[0]["target"].get("lat") else (None, None))

    score = _score(report, cascade, direct, dispatch, evacuation, requests)
    by_id_all = {f.id: f for f in facilities}
    result = {
        "scenario_id": uuid.uuid4().hex[:12],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "incidents": incidents,
        "summary": report["summary"],
        "score": score,
        "direct": direct,
        "network": report["network"],
        "networks": report["networks"],
        "facilities": report["facilities"],
        "affected_segments": report["affected_segments"],
        "population": report["population"],
        "emergency": {**report["emergency"], "dispatch": dispatch},
        "evacuation": evacuation,
        "alternatives": alternatives,
        "cascade": cascade,
        "comparison": comparison,
        "areas": area_report,
        "disruption": report["disruption"],
        "resources": pool.to_dict(by_id_all),
    }
    result["recovery"] = recovery_module.plan(requests, result)
    result["timeline"] = recovery_module.timeline(requests, result, result["recovery"])
    result["confidence"] = _confidence(result, requests, dependencies)
    result["actions"] = actions_module.build(result)
    return result


def _cordon_zones(disruptions) -> List[tuple]:
    """(centre, radius) of every closed area in this scenario.

    One definition, used by the access list, the evacuation destinations and
    the substitution list alike — otherwise the panel can recommend diverting
    patients to a hospital the same cordon has just closed, which is exactly
    what it did before this existed.
    """
    return [(d.point, d.cordon_m * (0.5 + SEVERITY_WEIGHT.get(d.severity, 0.75)))
            for d in disruptions if d.cordon_m and d.point]


def _inside_cordon(lat, lon, cordons) -> bool:
    return any(meters_between((lat, lon), point) <= radius for point, radius in cordons)


def _mark_cordoned(facility_rows: List[dict], cordons, struck) -> None:
    """Distinguish "inside the closed area" from "route severed".

    Both come back from routing as unreachable, but they are different findings:
    one lifts when the cordon does, the other needs an engineering solution. The
    struck asset itself is flagged too, so its own closure is not reported a
    second time as an access problem.
    """
    for row in facility_rows:
        row["is_incident_site"] = row["id"] in struck
        inside = _inside_cordon(row["lat"], row["lon"], cordons)
        row["in_cordon"] = inside and not row["is_incident_site"]
        if row["is_incident_site"]:
            row["reason"] = "This is the incident site — see direct impact"
        elif inside and row["cut_off"]:
            row["reason"] = ("Inside the closed area around the incident — no vehicle access "
                             "until the cordon lifts")


def _path_metres(city, path) -> Optional[float]:
    """Length of a routed path along the mapped geometry.

    The graph used to store only minutes per segment, so nothing downstream
    could state a distance or time a walk. length_m now travels with the edge.
    """
    if not path or len(path) < 2:
        return None
    total = 0.0
    for u, v in zip(path, path[1:]):
        if not city.G_road.has_edge(u, v):
            return None
        total += float(city.G_road.edges[u, v].get("length_m") or 0.0)
    return total or None


def _category_nodes(facilities, category: str, exclude=()) -> List[str]:
    return [f.node for f in facilities
            if f.category == category and f.node and f.id not in exclude]


def _tree_from(city, node: str, lift=()) -> tuple:
    """Distances and paths from one node across the disrupted network.

    `lift` names segments to restore to their free-flow weight for this
    calculation — the cordon around the incident, which does not stop the
    people being evacuated out of it.
    """
    saved = []
    for u, v in lift:
        if city.G_road.has_edge(u, v):
            edge = city.G_road.edges[u, v]
            saved.append((u, v, edge["current_weight"]))
            edge["current_weight"] = edge.get("base_weight", edge["current_weight"])
    try:
        routable = city.routable_road()
        if node not in routable:
            return {}, {}
        return nx.single_source_dijkstra(routable, node, weight="current_weight")
    finally:
        for u, v, weight in saved:
            city.G_road.edges[u, v]["current_weight"] = weight


# --- per-incident description --------------------------------------------------

def _incident_dict(city, facilities, request, target, disruption, index) -> dict:
    spec = request.spec()
    if target["kind"] == "asset":
        f = target["facility"]
        cat = CATEGORIES.get(f.category)
        target_dict = {
            "kind": "asset", "id": f.id, "name": f.display_name,
            "category": f.category, "category_label": label_of(f.category),
            "group": group_of(f.category), "lat": f.lat, "lon": f.lon,
            "address": f.address,
            "service": cat.service if cat else "",
        }
        label = incident_label(request.type, f.category)
    else:
        road = road_label(city, *target["edge"], landmarks=facilities)
        centre = disruption.point or _midpoint(city, *target["edge"])
        target_dict = {
            "kind": "road", "id": road["id"], "name": road["name"],
            "category_label": road["road_class"], "group": "Road network",
            "lat": centre[0] if centre else None, "lon": centre[1] if centre else None,
            "named_in_osm": road.get("named_in_osm", False),
        }
        label = spec.label

    radius = disruption.affected_radius()
    return {
        "index": index,
        "type": request.type,
        "label": label,
        "type_label": spec.label,
        "group": spec.group,
        "severity": request.severity,
        "duration_hours": request.duration_hours,
        "target": target_dict,
        "radius_m": round(radius),
        "radius_basis": ("set by the operator" if request.radius_m else
                         f"{spec.base_radius_m} m for {spec.label.lower()}, scaled by "
                         f"{request.severity} severity and {request.duration_hours} h duration"),
        "note": spec.note,
        "responders": [label_of(c) for c in spec.responders],
        "evacuation": spec.evacuation,
        "onset_min": spec.onset_min,
    }


def _midpoint(city, u, v):
    a, b = city.node_attrs(u)["geo"], city.node_attrs(v)["geo"]
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _direct_impact(request: IncidentRequest, facility: Facility, observed=None) -> dict:
    """What happens to the struck asset itself, from its own traits."""
    spec = request.spec()
    cat = CATEGORIES.get(facility.category)
    weight = SEVERITY_WEIGHT.get(request.severity, 0.75)
    loss = min(1.0, spec.asset_loss * weight)
    status = ("Out of service" if loss >= 0.9 else
              "Severely degraded" if loss >= 0.5 else
              "Partially degraded" if loss > 0 else "Operating")
    people = occupancy_module.occupancy(facility, observed)
    return {
        "id": facility.id,
        "name": facility.display_name,
        "category": facility.category,
        "category_label": label_of(facility.category),
        "group": group_of(facility.category),
        "lat": facility.lat, "lon": facility.lon,
        "status": status,
        "function_lost_pct": round(100 * loss),
        "service_lost": cat.service if cat else "",
        "occupancy": people.value,
        "occupancy_finding": people.to_dict(),
        "occupancy_basis": people.basis,
        "capacity": facility.capacity,
        "reason": (f"{incident_label(request.type, facility.category)} at {facility.display_name}, "
                   f"{request.severity} severity for {request.duration_hours} h"),
    }


def _no_cascade(dependencies, struck, by_id) -> dict:
    """A cascade result for incidents that stop nothing downstream — still
    reporting what was checked, so the empty answer is legible."""
    names = ", ".join(by_id[fid].display_name for fid in struck) or "the affected road"
    supplies = [NETWORKS[n].label.lower() for fid in struck
                for n in (cascade_module.supplied_network(by_id[fid].category),) if n]
    if supplies:
        message = (f"{names} supplies {', '.join(sorted(set(supplies)))}, but this incident "
                   f"does not take it out of service, so nothing propagates.")
    else:
        message = (f"{names} supplies no utility network in the model, so there is no "
                   f"dependency cascade. Impact is through the road network only.")
    return {"nodes": [], "edges": [], "rounds": 0, "indirect_total": 0,
            "summary": message, "gaps": dependencies.gaps, "coverage": dependencies.coverage}


# --- emergency response --------------------------------------------------------

def _dispatch(requests, targets, city, facilities, before, after, struck, pool) -> List[dict]:
    """Who responds, from where, how long it takes, and whether they are free.

    Three corrections over the old version. The nearest station is chosen from
    those with a unit left, so two incidents cannot both have the same
    appliance. Availability is reported as unknown when nothing is published,
    rather than assumed. And the drive time is no longer presented as the
    response time — dispatch and turnout are added, and the result is a range.
    """
    out = []
    seen = set()
    for request, target in zip(requests, targets):
        site = target["facility"].node if target["kind"] == "asset" else target["edge"][0]
        # Only an asset incident applies a cordon; reading it off the incident
        # *type* let a flooded road claim one it never set up.
        cordon = (request.spec().road_cordon_m * (0.5 + SEVERITY_WEIGHT[request.severity])
                  if target["kind"] == "asset" else 0.0)

        for category in request.spec().responders:
            key = (site, category)
            if key in seen:
                continue
            seen.add(key)

            b, a = before.get(category, {}), after.get(category, {})
            station, _, skipped, options = response_module.nearest_available(
                pool, facilities, category, b, exclude=set(struck))

            if station is None and not options:
                out.append(response_module.Dispatch(
                    category=category,
                    note=(f"No {label_of(category).lower()} is mapped in this extract, so its "
                          f"response cannot be measured."),
                ).to_dict())
                continue
            if station is None:
                # Every station of this type is already committed in this scenario.
                out.append(response_module.Dispatch(
                    category=category, availability=response_module.BUSY,
                    note=(f"Every mapped {label_of(category).lower()} is already committed to "
                          f"another incident in this scenario. Mutual aid from outside the "
                          f"area would be required."),
                ).to_dict())
                continue

            travel_after, travel_before = a.get(site), b.get(site)
            approach = None
            if (travel_after is None or not math.isfinite(travel_after)) and cordon > 0:
                approach = nearest_reachable(city, a, site, max_m=cordon * 1.5)
                travel_after = approach[1] if approach else None
            unreachable = travel_after is None or not math.isfinite(travel_after)

            note = ""
            if skipped:
                note = (f"{skipped[0].display_name} is nearer but its units are committed to "
                        f"another incident in this scenario.")
            elif approach:
                note = (f"Timed to the edge of the closed area, {round(approach[2])} m from the "
                        f"incident; the last stretch is inside the cordon.")
            elif unreachable:
                note = "No route from any station on the disrupted network."

            out.append(response_module.Dispatch(
                category=category,
                station=station,
                travel_min=None if unreachable else travel_after,
                baseline_travel_min=travel_before,
                unreachable=unreachable,
                to_cordon_edge=bool(approach),
                availability=pool.state(station.id),
                displaced_from=skipped[0].display_name if skipped else None,
                note=note,
                candidates=[{"name": f.display_name, "travel_min": round(t, 1),
                             "availability": pool.state(f.id)}
                            for t, f in options[:4]],
            ).to_dict())
    return out


def _round(value):
    return None if value is None or not math.isfinite(value) else round(value, 1)


# --- evacuation ----------------------------------------------------------------

def _evacuation(city, facilities, struck, trees, cordons, observed=None) -> dict:
    """Where the occupants of an evacuated asset can go, and how they get there.

    Destinations are mapped open ground and assembly points. When none are
    mapped, that is what it says — an invented assembly point would send people
    to a field that may not exist.
    """
    evacuating = [(fid, request, facility) for fid, (request, facility) in struck.items()
                  if request.spec().evacuation]
    if not evacuating:
        return {"required": False, "sites": []}

    havens = [f for f in facilities if f.category in SAFE_HAVEN_KEYS and f.node]
    sites = []
    for fid, request, facility in evacuating:
        cat = CATEGORIES.get(facility.category)
        people = occupancy_module.occupancy(facility, observed)
        distances, paths = trees.get(fid, ({}, {}))
        options = []
        for h in havens:
            if h.id == fid:
                continue
            straight = meters_between((facility.lat, facility.lon), (h.lat, h.lon))
            if straight > EVACUATION_SEARCH_M:
                continue
            t = distances.get(h.node)
            path = paths.get(h.node, [])
            # Occupants walk. Routing them at driving speed made a 1.7 km
            # evacuation look like a five-minute trip.
            route_m = _path_metres(city, path) if path else None
            walk = occupancy_module.walk_minutes(route_m)
            capacity = occupancy_module.assembly_capacity(h)
            options.append({
                "id": h.id, "name": h.display_name,
                "category_label": label_of(h.category),
                "lat": h.lat, "lon": h.lon,
                "distance_m": round(straight),
                "route_m": None if route_m is None else round(route_m),
                "walk_min": walk.value,
                "walk": walk.to_dict(),
                "capacity": capacity.value,
                "capacity_finding": capacity.to_dict(),
                "sufficiency": occupancy_module.sufficiency(people, capacity),
                # A mapped open space is ground, not a designated centre.
                "designated": h.category == "shelter",
                "travel_min": _round(t),
                "reachable": t is not None and math.isfinite(t),
                # Sending people to an assembly point inside the closed area
                # moves them from one part of the hazard zone to another.
                "in_cordon": _inside_cordon(h.lat, h.lon, cordons),
                "crossings": max(0, len(path) - 2),
                "route": [list(city.node_attrs(n)["geo"]) for n in path][:400],
            })
        # Nearest first, but a point that cannot hold the occupants is not the
        # primary destination however close it is.
        options.sort(key=lambda o: (not o["reachable"], o["in_cordon"],
                                    o["sufficiency"]["state"] == "insufficient",
                                    o["walk_min"] if o["walk_min"] is not None else 1e9))

        chosen = next((o for o in options if o["reachable"] and not o["in_cordon"]), None)
        sites.append({
            "id": fid,
            "name": facility.display_name,
            "lat": facility.lat, "lon": facility.lon,
            "people": people.value,
            "people_finding": people.to_dict(),
            "people_basis": people.basis,
            "start_min": 10,
            "walk_speed_kmh": occupancy_module.WALK_SPEED_KMH,
            "capacity_state": (chosen["sufficiency"] if chosen else
                               {"state": "unknown",
                                "message": "No reachable assembly point outside the cordon."}),
            "zone_m": round(request.spec().road_cordon_m * 1.5) or 300,
            "destinations": options[:ALTERNATIVES_SHOWN],
            "note": ("" if options else
                     f"No open space, playing field or assembly point is mapped within "
                     f"{EVACUATION_SEARCH_M / 1000:.0f} km. Evacuation destinations cannot be "
                     f"proposed from this dataset — run scripts/ingest_poi.py to add them, or "
                     f"supply the municipality's own assembly-point list."),
            "caveat": ("Destinations are public open ground mapped in OpenStreetMap. A site's "
                       "own muster point — a school playground, a hospital car park — is "
                       "normally the first assembly area and is not mapped, so treat these as "
                       "secondary destinations for a full evacuation of the area."),
        })
    return {"required": True, "sites": sites}


# --- substitution --------------------------------------------------------------

def _alternatives(facilities, struck, trees, cordons) -> dict:
    """Peer facilities that absorb a struck asset's demand.

    Only for categories the model marks substitutable: a hospital's patients go
    to another hospital, a water tower's customers go nowhere.
    """
    out = []
    for fid, (request, facility) in struck.items():
        cat = CATEGORIES.get(facility.category)
        if not cat or not cat.substitutable:
            continue
        distances, _ = trees.get(fid, ({}, {}))
        peers = []
        for f in facilities:
            if f.id == fid or f.category != facility.category or not f.node:
                continue
            t = distances.get(f.node)
            peers.append({
                "id": f.id, "name": f.display_name, "lat": f.lat, "lon": f.lon,
                "travel_min": _round(t),
                "reachable": t is not None and math.isfinite(t),
                "capacity": f.capacity,
                # The nearest peer is often on the same campus, which means it
                # is inside the cordon this incident just closed. Still listed,
                # because it is where the demand naturally goes, but never
                # recommended ahead of one that is actually open.
                "in_cordon": _inside_cordon(f.lat, f.lon, cordons),
                "distance_m": round(meters_between((facility.lat, facility.lon), (f.lat, f.lon))),
            })
        peers.sort(key=lambda p: (not p["reachable"], p["in_cordon"],
                                  p["travel_min"] if p["reachable"] else 1e9))
        known = [p["capacity"] for p in peers[:ALTERNATIVES_SHOWN] if p["capacity"]]
        out.append({
            "id": fid,
            "name": facility.display_name,
            "category_label": label_of(facility.category),
            "service": cat.service,
            "displaced_capacity": facility.capacity,
            "capacity_note": ("" if facility.capacity else
                              f"OSM publishes no capacity for {facility.display_name}, so the "
                              f"volume of displaced demand is unknown — only where it would go."),
            "peers": peers[:ALTERNATIVES_SHOWN],
            "absorbing_capacity": sum(known) if known else None,
        })
    return {"available": bool(out), "items": out,
            "note": "" if out else "No struck asset has a peer that can absorb its function."}


# --- scoring -------------------------------------------------------------------

def _score(report, cascade, direct, dispatch, evacuation, requests) -> dict:
    """One 0-100 figure, with the arithmetic shown.

    Every factor is bounded and named, so "critical" is never a mood — it is a
    number an operator can take apart and disagree with.
    """
    factors = []

    def add(label, points, detail):
        if points > 0:
            factors.append({"label": label, "points": round(points, 1), "detail": detail})

    worst_sev = max(SEVERITY_WEIGHT.get(r.severity, 0.75) for r in requests)
    longest = max(r.duration_hours for r in requests)
    crit = max((CATEGORIES[d["category"]].criticality for d in direct
                if d["category"] in CATEGORIES), default=0.0)

    add("Incident severity", 15 * worst_sev,
        f"{max(requests, key=lambda r: SEVERITY_WEIGHT.get(r.severity, 0.75)).severity} severity")
    add("Duration", 8 * min(1.0, math.log2(longest + 1) / 4.6),
        f"{longest:g} hours")
    if crit:
        add("Criticality of the asset hit", 1.2 * crit,
            f"{direct[0]['category_label']} — criticality {crit:g}/10")

    serious = [f for f in report["facilities"] if f["level"] in ("critical", "high")]
    cut = [f for f in report["facilities"] if f.get("cut_off")]
    add("Accessibility loss", min(20, 4 * len(serious)),
        f"{len(serious)} facilities with materially worse access")
    add("Facilities cut off", min(15, 7.5 * len(cut)),
        f"{len(cut)} with no remaining route" if cut else "")

    delays = [d["added_min"] for d in dispatch if d.get("added_min")]
    # Only a service that exists can fail to reach the site. An unmapped one is
    # a data gap, reported as its own action, not a routing failure worth 12
    # points on every incident in the city.
    unreachable = [d for d in dispatch if d.get("available") and d.get("unreachable")]
    if delays:
        add("Emergency response degradation", min(12, 2.5 * max(delays)),
            f"+{max(delays):.1f} min to the slowest responding service")
    if unreachable:
        add("Responder cannot reach the site", 12,
            f"{len(unreachable)} service(s) have no route")

    people = report["population"].get("estimated_people") or 0
    add("Population exposure", min(12, people / 2500),
        f"~{people:,} people in the incident zone (estimate)" if people else "")

    if cascade["indirect_total"]:
        add("Dependency cascade", min(14, 3.5 * cascade["indirect_total"] + 2 * cascade["rounds"]),
            f"{cascade['indirect_total']} asset(s) over {cascade['rounds']} round(s)")

    if evacuation.get("required"):
        people_out = sum(s.get("people") or 0 for s in evacuation["sites"])
        stranded = [s for s in evacuation["sites"] if not s["destinations"]]
        add("Evacuation required", min(10, 4 + people_out / 400),
            f"~{people_out:,} occupants to move" if people_out else "occupants to move")
        if stranded:
            add("No mapped evacuation destination", 5,
                f"{len(stranded)} site(s) with nowhere identified")

    if len(requests) > 1:
        add("Simultaneous incidents", 4 * (len(requests) - 1),
            f"{len(requests)} incidents interacting on one network")

    value = min(100.0, sum(f["points"] for f in factors))
    level = ("critical" if value >= 60 else "high" if value >= 35 else
             "moderate" if value >= 15 else "low" if value > 0 else "none")
    factors.sort(key=lambda f: -f["points"])
    return {
        "value": round(value),
        "level": level,
        "factors": factors,
        "basis": ("Weighted sum of measured effects. Severity, duration and asset criticality "
                  "are inputs; everything else is computed from the routing, the population "
                  "estimate and the dependency model."),
    }


# --- timeline ------------------------------------------------------------------

def _timeline(requests, result) -> List[dict]:
    """How the consequences arrive over time.

    Times come from the incident's own onset, the measured response times and
    the propagation delay of each network — not from a fixed script.
    """
    events = [{"minute": 0, "label": "Incident begins",
               "detail": "; ".join(i["label"] + " at " + i["target"]["name"]
                                   for i in result["incidents"])}]

    arrivals = [d for d in result["emergency"].get("dispatch", [])
                if d.get("disrupted_min") is not None]
    if arrivals:
        first = min(arrivals, key=lambda d: d["disrupted_min"])
        events.append({
            "minute": round(first["disrupted_min"]),
            "label": f"First {first['category_label'].lower()} unit on scene",
            "detail": (f"{first['station']} — {first['disrupted_min']:.0f} min drive on the "
                       f"disrupted network"
                       + (f", {first['added_min']:.0f} min more than normal"
                          if first.get("added_min") else "")),
        })
    for d in result["emergency"].get("dispatch", []):
        if d.get("unreachable"):
            events.append({"minute": 0, "label": f"{d['category_label']} has no route",
                           "detail": d["note"]})

    onset = max((i["onset_min"] for i in result["incidents"]), default=0)
    net = result.get("network") or {}
    if net.get("added_travel_min"):
        events.append({
            "minute": max(onset, 10),
            "label": "Traffic disruption measurable",
            "detail": (f"Through traffic takes the detour: +{net['added_travel_min']:.1f} min "
                       f"between the segment's own endpoints, across "
                       f"{net.get('diverted_count', 0)} adjacent roads"),
        })

    # One line per wave of the cascade, not one per asset: a substation that
    # feeds fourteen consumers produced fourteen identical timeline rows, which
    # buries the two that matter.
    waves: Dict[tuple, list] = {}
    for node in result["cascade"]["nodes"]:
        if node["round"] == 0:
            continue
        network = node.get("via_network")
        delay = NETWORKS[network].propagation_delay_min if network in NETWORKS else 15
        minute = max(onset, delay * node["round"])
        waves.setdefault((minute, network, node["level"]), []).append(node)

    for (minute, network, level), group in waves.items():
        net = NETWORKS[network].label.lower() if network in NETWORKS else "service"
        # Name the ones an operator cares about first.
        group.sort(key=lambda n: -CATEGORIES[n["category"]].criticality
                   if n["category"] in CATEGORIES else 0)
        names = ", ".join(n["name"] for n in group[:3])
        more = f" and {len(group) - 3} more" if len(group) > 3 else ""
        events.append({
            "minute": minute,
            "label": (f"{len(group)} asset{'s' if len(group) > 1 else ''} lose {net} supply"
                      f" ({level})"),
            "detail": f"{names}{more}. {group[0]['reason']}",
        })

    if result["evacuation"].get("required"):
        for site in result["evacuation"]["sites"]:
            best = next((d for d in site["destinations"] if d["reachable"]), None)
            events.append({
                "minute": 15,
                "label": f"Evacuation of {site['name']}",
                "detail": (f"~{site['people']:,} occupants to {best['name']} "
                           f"({best['travel_min']:.0f} min)" if best and site.get("people")
                           else site["note"] or "Occupants to be moved clear of the cordon"),
            })

    longest = max(r.duration_hours for r in requests)
    events.append({
        "minute": round(longest * 60),
        "label": "Incident expected to clear",
        "detail": (f"Stated duration of {longest:g} h. Recovery of access is immediate on "
                   f"reopening; dependency effects lag by the storage or restart time of "
                   f"each network."),
    })

    events.sort(key=lambda e: e["minute"])
    return events


# --- confidence ----------------------------------------------------------------

def _confidence(result, requests, dependencies) -> dict:
    """Graded per component, so one missing dataset does not discredit the rest.

    The old model took the weakest reason and stamped it on the whole
    simulation: a run whose routing was solid still read "low confidence"
    because no telecom mast is mapped in this extract. An operator who sees
    that on every result learns to ignore the grade.
    """
    c = prov.Confidence()

    c.note("road_impact", "high",
           "Road geometry, junction positions and asset locations are OpenStreetMap "
           "observations.")
    # Speeds are the weak link in every travel time, so they grade the routing.
    c.note("emergency_routing", "medium",
           "Travel times use documented free-flow speeds by road class, not observed "
           "traffic; only a small share of segments carry an OSM speed limit.")

    dispatch = result["emergency"].get("dispatch", [])
    if any(not d["available"] for d in dispatch):
        c.note("resources", "low",
               "A responding service has no mapped station in this extract.")
    elif dispatch:
        c.note("resources", "low",
               "No duty state or appliance count is published for any service; unit counts "
               "are planning assumptions.")

    for d in result["direct"]:
        finding = d.get("occupancy_finding") or {}
        if finding.get("state"):
            c.note("occupancy", finding["confidence"], finding["basis"])

    if result["population"].get("estimated_people") is not None:
        c.note("population", "low",
               "Population in the zone is a building-density estimate; no census or raster "
               "population layer is loaded.")

    if result["cascade"]["nodes"]:
        levels = {n["confidence"] for n in result["cascade"]["nodes"] if n["round"] > 0}
        if levels:
            c.note("dependencies", "medium" if "medium" in levels else "low",
                   "Dependency links are service-area inferences — no electricity or water "
                   "distribution network is mapped here.")
    for gap in dependencies.gaps[:2]:
        c.note("dependencies", "low", gap["message"])

    for request in requests:
        spec = request.spec()
        if spec.area_damage or spec.key in ("flooding", "storm", "earthquake"):
            c.note("hazard_extent", "low", f"{spec.label}: {spec.note}")
        else:
            c.note("hazard_extent", "high",
                   "The affected extent is the selected asset and its cordon, not a modelled "
                   "hazard footprint.")

    c.note("recovery", "low",
           "Clearance and restoration times are per-incident-type planning assumptions, not "
           "local service data.")

    out = c.to_dict()
    out["reasons"] = [{"level": comp["level"], "text": f"{comp['label']}: {comp['reason']}"}
                      for comp in out["components"]]
    return out


def demo():
    """End-to-end self-check on the toy city."""
    from ..graph import dependencies as dep
    from ..graph.builder import build_toy_city
    from .impact import snap_facilities

    city = build_toy_city()
    raw = [
        {"id": "hosp", "name": "Toy District Hospital", "category": "hospital",
         "lat": city.node_attrs("H1")["geo"][0], "lon": city.node_attrs("H1")["geo"][1],
         "address": "", "capacity": 120},
        {"id": "hosp2", "name": "Toy Community Hospital", "category": "hospital",
         "lat": city.node_attrs("H2")["geo"][0], "lon": city.node_attrs("H2")["geo"][1],
         "address": "", "capacity": 60},
        {"id": "fire", "name": "Toy Fire Station", "category": "fire_station",
         "lat": city.node_attrs("F1")["geo"][0], "lon": city.node_attrs("F1")["geo"][1],
         "address": "", "capacity": None},
        {"id": "school", "name": "Toy Government School", "category": "school",
         "lat": city.node_attrs("J7")["geo"][0], "lon": city.node_attrs("J7")["geo"][1],
         "address": "", "capacity": None},
        {"id": "sub", "name": "Toy Substation", "category": "substation",
         "lat": city.node_attrs("J2")["geo"][0], "lon": city.node_attrs("J2")["geo"][1],
         "address": "", "capacity": None},
    ]
    facilities = snap_facilities(city, raw)
    model = dep.build(facilities)

    fire = simulate(build_toy_city(), facilities,
                    [IncidentRequest("fire", facility_id="school", severity="critical",
                                     duration_hours=2)], model)
    assert fire["incidents"][0]["label"] == "School fire", fire["incidents"][0]["label"]
    assert fire["direct"][0]["status"] == "Out of service"
    assert fire["direct"][0]["occupancy"], "a school fire must report who is inside"
    assert fire["evacuation"]["required"]
    assert any(d["category"] == "fire_station" for d in fire["emergency"]["dispatch"])
    assert fire["actions"], "a critical fire must produce actions"
    assert fire["score"]["value"] > 0 and fire["score"]["factors"]
    assert fire["timeline"][0]["minute"] == 0

    hosp = simulate(build_toy_city(), facilities,
                    [IncidentRequest("fire", facility_id="hosp", severity="critical")], model)
    assert hosp["alternatives"]["available"], "a hospital fire must offer alternative hospitals"
    assert hosp["alternatives"]["items"][0]["peers"], "and name them"

    power = simulate(build_toy_city(), facilities,
                     [IncidentRequest("power_failure", facility_id="sub", severity="critical")],
                     model)
    assert power["cascade"]["summary"]
    assert not power["evacuation"]["required"], "a power cut evacuates nobody"

    multi = simulate(build_toy_city(), facilities, [
        IncidentRequest("closure", edge=("H1", "J3"), severity="critical", duration_hours=6),
        IncidentRequest("power_failure", facility_id="sub", severity="critical"),
    ], model)
    assert len(multi["incidents"]) == 2
    assert any(f["label"] == "Simultaneous incidents" for f in multi["score"]["factors"])
    import json
    json.dumps(multi)  # no infinities may reach the API
    print(f"ok — school fire scored {fire['score']['value']} ({fire['score']['level']}), "
          f"{len(fire['actions'])} actions; combined scenario scored {multi['score']['value']}")


if __name__ == "__main__":
    demo()
