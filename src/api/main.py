"""FastAPI layer per ARCHITECTURE.md §7/§12. Thin wrapper over the engine in
src/graph, src/disruption, src/analysis — no simulation logic lives here.

CITY_DATA_PATH picks the data source: "toy" (default) or a path to a file
produced by scripts/ingest_manipal.py, e.g. data/city/manipal.json. The
facility inventory, dependency model and administrative boundaries are loaded
from files beside it (.poi.json, .areas.json, .dependencies.json).
"""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional, Tuple, Union

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..analysis.criticality import (
    betweenness_baseline,
    leave_one_out_ranking,
    representative_junctions,
)
from ..analysis.impact import (
    DISRUPTION_TYPES,
    SEVERITY_WEIGHT,
    Disruption,
    analyze,
    meters_between,
    snap_facilities,
)
from ..analysis.incidents import INCIDENT_TYPES, catalogue
from ..analysis.metrics import avg_hospital_delay, coverage_ratio, disconnected_count
from ..analysis.scenario import IncidentRequest, ScenarioError, simulate
from ..disruption.events import apply_event
from ..disruption.propagation import run_propagation
from ..graph import areas as areas_module
from collections import Counter

from ..graph import dependencies as dependencies_module
from ..graph.builder import build_city
from ..graph.infrastructure import CATEGORIES
from ..graph.validate import report as validate_report

CITY_SOURCE = os.environ.get("CITY_DATA_PATH", "toy")
_criticality_cache = None
_criticality_stale = False
_city_template = None
_facility_cache = None
_dependency_cache = None
_area_cache = None
_search_index = None


def _sidecar(suffix: str) -> Optional[Path]:
    """A file that lives beside the city extract, e.g. manipal.poi.json."""
    if CITY_SOURCE == "toy":
        return None
    return Path(CITY_SOURCE).with_suffix(suffix)


def _fresh_city():
    """A clean city for this request.

    The city is parsed from disk once and copied thereafter: on the 2,625-node
    extract, re-reading and re-parsing the JSON costs ~2s and every request
    needs two cities (baseline and after), which dominated request time.
    Copying the parsed graph is ~60ms.
    """
    global _city_template
    if _city_template is None:
        _city_template = build_city(CITY_SOURCE)
    return _city_template.copy()


def _facilities():
    """Infrastructure snapped to the road network, once.

    Kept separate from the graph on purpose: facilities are static reference
    data about the city, the graph is the routable network, and the impact
    engine joins them spatially at query time. Nothing here encodes a
    road->facility relationship by id.
    """
    global _facility_cache
    if _facility_cache is None:
        path = _sidecar(".poi.json")
        raw = json.loads(path.read_text(encoding="utf-8"))["facilities"] if path and path.exists() else []
        _facility_cache = snap_facilities(_fresh_city(), raw)
    return _facility_cache


def _dependencies():
    """Derived once at boot — it is a function of the facility inventory, which
    does not change between requests."""
    global _dependency_cache
    if _dependency_cache is None:
        overrides = dependencies_module.load_overrides(_sidecar(".dependencies.json"))
        _dependency_cache = dependencies_module.build(_facilities(), overrides)
    return _dependency_cache


def _areas():
    global _area_cache
    if _area_cache is None:
        path = _sidecar(".areas.json")
        _area_cache = areas_module.load(path) if path else areas_module.AreaIndex([])
    return _area_cache


def _cache_path() -> Optional[Path]:
    """Where the precomputed ranking for this city lives. None for the toy
    graph, which ranks in well under a second and needs no cache."""
    return _sidecar(".criticality.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Leave-one-out over every node of the real extract takes minutes, so it
    # is precomputed to disk by scripts/rank_city.py and only loaded here.
    # Nothing at boot may block on it: a stale ranking is flagged and served,
    # and a missing one is computed on first request, because a server that
    # takes four minutes to answer /city is useless in an operations room.
    global _criticality_cache, _criticality_stale
    path = _cache_path()
    if path and path.exists():
        _criticality_cache = json.loads(path.read_text())
        _criticality_stale = path.stat().st_mtime < Path(CITY_SOURCE).stat().st_mtime
    _dependencies()
    _areas()
    yield


app = FastAPI(title="City Disruption & Resilience Simulator", lifespan=lifespan)


class EventSpec(BaseModel):
    event: str
    target: Union[str, Tuple[str, str]]
    severity: float = 1.0


class SimulateRequest(BaseModel):
    events: List[EventSpec] = []


def _all_junctions_hospitals(city):
    return city.nodes_of_type("road_junction"), city.nodes_of_type("hospital")


def _propagation_od_pairs(city):
    """Sampled origins for Mechanism B's iterative loop (ARCHITECTURE.md
    §5.2's "Targeted OD Pairs" mitigation) — the expensive part. Reported
    metrics below still use every junction, so accuracy of what's *shown*
    is unaffected; only the internal routing workload is thinned."""
    hospitals = city.nodes_of_type("hospital")
    origins = representative_junctions(city)
    return [(j, h) for j in origins for h in hospitals]


def _run_scenario(events: List[EventSpec]) -> dict:
    baseline = _fresh_city()
    run_propagation(baseline, _propagation_od_pairs(baseline))

    after = _fresh_city()
    for spec in events:
        try:
            apply_event(after, spec.event, spec.target, spec.severity)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=f"{spec.event} on {spec.target!r}: {e}")
    cascade_depth = run_propagation(after, _propagation_od_pairs(after))

    junctions, hospitals = _all_junctions_hospitals(after)
    delay_delta = avg_hospital_delay(after, junctions, hospitals) - avg_hospital_delay(
        baseline, junctions, hospitals
    )
    disc_delta = disconnected_count(after, junctions, hospitals) - disconnected_count(
        baseline, junctions, hospitals
    )
    coverage_loss = coverage_ratio(baseline, junctions, hospitals) - coverage_ratio(
        after, junctions, hospitals
    )
    return {
        "cascade_depth": cascade_depth,
        "delay_delta": delay_delta,
        "disconnected_delta": disc_delta,
        "coverage_loss": coverage_loss,
        "after_city": after.to_dict(),
    }


def _compute_criticality() -> dict:
    city = _fresh_city()
    hospitals = city.nodes_of_type("hospital")
    # Sampled demand junctions, same principle as _propagation_od_pairs:
    # every node is still individually tested below, this only keeps the
    # per-iteration delay/disconnection *measurement* affordable at city scale.
    sample_junctions = representative_junctions(city)
    return {
        "outcome_scores": leave_one_out_ranking(
            city, _propagation_od_pairs(city), sample_junctions, hospitals
        ),
        "betweenness_baseline": betweenness_baseline(city),
    }


@app.get("/city")
def get_city():
    return _fresh_city().to_dict()


@app.post("/simulate")
def simulate_events(request: SimulateRequest):
    return _run_scenario(request.events)


class AnalyzeRequest(BaseModel):
    kind: str = "accident"
    severity: str = "high"
    duration_hours: float = 2.0
    edge: Optional[Tuple[str, str]] = None
    node: Optional[str] = None
    radius_m: Optional[float] = None


@app.get("/disruption-types")
def disruption_types():
    """Drives the operator's dropdowns from the model, so the UI cannot drift
    out of sync with what the engine actually supports."""
    return {
        "types": catalogue(),
        "severities": list(SEVERITY_WEIGHT),
        "severity_help": {
            "low": "Minor obstruction; traffic still flows",
            "moderate": "Significant obstruction or partial loss of service",
            "high": "Route or service effectively unusable",
            "critical": "Complete loss, with no local alternative",
        },
        "durations_hours": [0.25, 0.5, 1, 2, 6, 12, 24],
        "categories": [{"key": k, "label": c.label, "group": c.group,
                        "criticality": c.criticality, "needs": list(c.needs),
                        "substitutable": c.substitutable, "occupancy": c.occupancy,
                        "safe_haven": c.safe_haven, "service": c.service}
                       for k, c in CATEGORIES.items()],
        "networks": [{"key": n.key, "label": n.label,
                      "chain": [CATEGORIES[t].label for t in n.tiers if t in CATEGORIES]}
                     for n in dependencies_module.NETWORKS.values()],
    }


def _facility_row(f) -> dict:
    cat = CATEGORIES.get(f.category)
    area = _search_areas(f.lat, f.lon)
    return {
        "id": f.id, "name": f.display_name, "raw_name": f.name,
        "category": f.category,
        "category_label": cat.label if cat else f.category,
        "group": cat.group if cat else "Other",
        "criticality": cat.criticality if cat else None,
        "service": cat.service if cat else "",
        "substitutable": bool(cat and cat.substitutable),
        "needs": list(cat.needs) if cat else [],
        "lat": f.lat, "lon": f.lon, "address": f.address, "capacity": f.capacity,
        "snap_m": round(f.snap_m), "node": f.node,
        "area": area,
    }


def _search_areas(lat, lon) -> str:
    hits = _areas().at(lat, lon) if _areas() else []
    return hits[0].name if hits else ""


@app.get("/facilities")
def facilities():
    """Every known facility, named. Ids are metadata, not the headline."""
    return {"facilities": [_facility_row(f) for f in _facilities()]}


@app.get("/dependencies")
def dependencies():
    """The derived dependency model, with the basis and confidence of every
    link and an explicit list of what could not be modelled."""
    model = _dependencies()
    names = {f.id: f.display_name for f in _facilities()}
    data = model.to_dict()
    for link in data["links"]:
        link["source_name"] = names.get(link["source"], link["source"])
        link["target_name"] = names.get(link["target"], link["target"])
    return data


@app.get("/areas")
def areas():
    index = _areas()
    return {"available": bool(index), "areas": index.names(),
            "note": "" if index else "No administrative boundaries loaded. "
                                     "Run scripts/ingest_areas.py."}


@app.get("/validation")
def validation():
    """What the loaded dataset actually contains, and anything wrong with it.
    An operator should be able to see the quality of the data they are
    reasoning about without reading the ingestion logs."""
    poi = _sidecar(".poi.json")
    raw = json.loads(poi.read_text(encoding="utf-8"))["facilities"] if poi and poi.exists() else None
    rep = validate_report(_fresh_city().to_dict(), raw)
    model = _dependencies()
    rep["dependencies"] = {"links": len(model.links), "coverage": model.coverage,
                           "gaps": model.gaps}
    rep["areas_loaded"] = bool(_areas())
    rep["source"] = CITY_SOURCE
    return rep


@app.get("/search")
def search(q: str = Query(..., min_length=2), limit: int = 12):
    """Find a road, facility or area by name — never by id.

    The index is names, categories and areas, because that is what an operator
    knows. OSM ids appear only in the technical detail of a result.
    """
    global _search_index
    if _search_index is None:
        _search_index = _build_search_index()
    needle = q.strip().lower()
    scored = []
    for entry in _search_index:
        hay = entry["_haystack"]
        if needle not in hay:
            continue
        # Prefix matches first, then whole-word, then anything.
        rank = 0 if hay.startswith(needle) else (1 if f" {needle}" in hay else 2)
        scored.append((rank, len(entry["name"]), entry))
    scored.sort(key=lambda s: (s[0], s[1]))
    return {"query": q, "results": [
        {k: v for k, v in entry.items() if not k.startswith("_")}
        for _, _, entry in scored[:limit]
    ], "total": len(scored)}


def _build_search_index() -> List[dict]:
    # Two substations in this extract carry the same name, and one feeds fifteen
    # assets while the other feeds two. "Electrical substation, Udupi taluku"
    # twice is not a choice an operator can make, so say what each one supplies.
    supplies = Counter(link.source for link in _dependencies().links)

    entries = []
    for f in _facilities():
        row = _facility_row(f)
        entries.append({
            "kind": "asset", "id": row["id"], "name": row["name"],
            "type": row["category_label"], "group": row["group"],
            "category": row["category"], "supplies": supplies.get(row["id"], 0),
            "area": row["area"], "lat": row["lat"], "lon": row["lon"],
            "status": "As mapped — no live status feed for this asset",
            "_haystack": " ".join([row["name"], row["category_label"], row["group"],
                                   row["area"]]).lower(),
        })

    city = _fresh_city()
    roads = {}
    for u, v, data in city.G_road.edges(data=True):
        name = (data.get("name") or "").strip()
        if not name:
            continue
        entry = roads.setdefault(name, {"segments": 0, "edge": (u, v), "lat": 0.0, "lon": 0.0})
        entry["segments"] += 1
        a, b = city.node_attrs(u)["geo"], city.node_attrs(v)["geo"]
        entry["lat"], entry["lon"] = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    for name, entry in roads.items():
        entries.append({
            "kind": "road", "id": f"{entry['edge'][0]}-{entry['edge'][1]}", "name": name,
            "type": "Road", "group": "Road network",
            "edge": list(entry["edge"]), "segments": entry["segments"],
            "area": _search_areas(entry["lat"], entry["lon"]),
            "lat": entry["lat"], "lon": entry["lon"],
            "status": f"{entry['segments']} segment(s) in the extract",
            "_haystack": name.lower(),
        })

    for area in _areas().names():
        entries.append({
            "kind": "area", "id": area["id"], "name": area["name"],
            "type": area["level_label"], "group": "Administrative",
            "area": "", "lat": area["lat"], "lon": area["lon"],
            "status": "Administrative boundary",
            "_haystack": f"{area['name']} {area['level_label']}".lower(),
        })
    return entries


def _edge_target(edge) -> Tuple[str, str]:
    if not edge or len(edge) != 2:
        raise HTTPException(400, "a road segment is two node ids")
    return tuple(edge)


@app.post("/analyze")
def analyze_disruption(request: AnalyzeRequest):
    """Localised impact analysis — the road-network slice of a simulation.
    Kept for direct road queries; /simulate-incidents is the full engine."""
    if request.kind not in DISRUPTION_TYPES:
        raise HTTPException(400, f"unknown disruption type {request.kind!r}")
    if not request.edge and not request.node:
        raise HTTPException(400, "select a road segment (edge) or a node")

    city = _fresh_city()
    if request.edge:
        u, v = _edge_target(request.edge)
        if u == v:
            raise HTTPException(400, "that is a single point, not a road segment")
        if not city.G_road.has_edge(u, v):
            raise HTTPException(400, f"no road segment between {u!r} and {v!r}")

    return analyze(city, _facilities(), Disruption(
        kind=request.kind, severity=request.severity,
        duration_hours=request.duration_hours,
        edge=tuple(request.edge) if request.edge else None,
        node=request.node, radius_m=request.radius_m,
    ))


class IncidentSpec(BaseModel):
    type: str
    edge: Optional[Tuple[str, str]] = None
    facility_id: Optional[str] = None
    severity: str = "high"
    duration_hours: float = 2.0
    radius_m: Optional[float] = None


class ScenarioRequest(BaseModel):
    incidents: List[IncidentSpec] = []


@app.post("/simulate-incidents")
def simulate_incidents(request: ScenarioRequest):
    """The full engine: direct impact, network effects, dependency cascade,
    baseline-vs-disrupted comparison, timeline and recommended actions."""
    if not request.incidents:
        raise HTTPException(400, "add at least one incident")
    if len(request.incidents) > 5:
        raise HTTPException(400, "at most five simultaneous incidents")
    try:
        return simulate(
            _fresh_city(), _facilities(),
            [IncidentRequest(type=i.type, edge=tuple(i.edge) if i.edge else None,
                             facility_id=i.facility_id, severity=i.severity,
                             duration_hours=i.duration_hours, radius_m=i.radius_m)
             for i in request.incidents],
            _dependencies(), _areas(),
        )
    except ScenarioError as e:
        raise HTTPException(400, str(e))


@app.get("/incident-types")
def incident_types():
    """Alias of /disruption-types under the name the engine now uses."""
    return disruption_types()


@app.get("/criticality")
def criticality():
    global _criticality_cache, _criticality_stale
    if _criticality_cache is None:
        _criticality_cache = _compute_criticality()
        _criticality_stale = False
        path = _cache_path()
        if path:
            path.write_text(json.dumps(_criticality_cache))
    return {**_criticality_cache, "stale": _criticality_stale,
            "note": ("Computed before the current city extract — re-run "
                     "scripts/rank_city.py" if _criticality_stale else "")}


_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
# check_dir=False: don't fail app import (and every test that imports it) if
# `npm run build` hasn't been run yet — requests just 404 until it has.
app.mount("/app", StaticFiles(directory=_FRONTEND_DIST, html=True, check_dir=False), name="frontend")
