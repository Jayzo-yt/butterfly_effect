"""Data validation for a city extract.

Runs on the plain-dict form (CityGraph.to_dict / the ingested JSON file) so
the same checks cover a file on disk and a live graph, and so duplicate edges
are still visible — networkx silently collapses them on load, which is exactly
how four self-loops survived in the dataset while the graph API reported
nothing wrong.

Every ingestion calls report() and refuses to save a graph that fails.
"""
import math
from typing import Optional

import networkx as nx

# A junction with no coordinates cannot be routed to, drawn, or snapped to;
# one outside these bounds is a data error, not a distant suburb.
LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)

FATAL = {"self_loops", "missing_coordinates", "invalid_geometry", "missing_asset_types",
         "dangling_edges"}


def report(data: dict, facilities: Optional[list] = None) -> dict:
    """Count everything that can be wrong with a city extract.

    Returns counts plus `problems` (fatal) and `warnings` (worth knowing but
    not disqualifying — a disconnected component is usually a real island of
    road, not a bug).
    """
    checks = {}
    samples = {}
    for layer in ("road", "utility"):
        nodes = data.get(layer, {}).get("nodes", [])
        edges = data.get(layer, {}).get("edges", [])
        ids = {n.get("id") for n in nodes}

        self_loops = [e for e in edges if e.get("source") == e.get("target")]
        seen, duplicates = set(), []
        for e in edges:
            key = tuple(sorted((str(e.get("source")), str(e.get("target")))))
            if key in seen:
                duplicates.append(e)
            seen.add(key)
        missing_coords = [n for n in nodes if not _has_geo(n)]
        invalid_geom = [n for n in nodes if _has_geo(n) and not _geo_in_range(n["geo"])]
        missing_types = [n for n in nodes if not n.get("type")]
        dangling = [e for e in edges
                    if e.get("source") not in ids or e.get("target") not in ids]

        checks[layer] = {
            "assets": len(nodes),
            "edges": len(edges),
            "self_loops": len(self_loops),
            "duplicate_edges": len(duplicates),
            "missing_coordinates": len(missing_coords),
            "invalid_geometry": len(invalid_geom),
            "missing_asset_types": len(missing_types),
            "dangling_edges": len(dangling),
            "disconnected_components": _components(nodes, edges),
        }
        samples[layer] = {
            "self_loops": [e.get("source") for e in self_loops[:5]],
            "missing_coordinates": [n.get("id") for n in missing_coords[:5]],
            "invalid_geometry": [n.get("id") for n in invalid_geom[:5]],
            "dangling_edges": [f"{e.get('source')}-{e.get('target')}" for e in dangling[:5]],
        }

    out = {"layers": checks, "samples": samples, "problems": [], "warnings": []}

    if facilities is not None:
        unnamed = sum(1 for f in facilities if not (f.get("name") or "").strip())
        no_geo = sum(1 for f in facilities if f.get("lat") is None or f.get("lon") is None)
        located = [f for f in facilities if f.get("lat") is not None and f.get("lon") is not None]
        _, duplicates = dedupe_facilities(located)
        out["facilities"] = {"total": len(facilities), "unnamed": unnamed,
                             "missing_coordinates": no_geo,
                             "duplicate_assets": len(duplicates)}
        if duplicates:
            out["warnings"].append(
                f"{len(duplicates)} assets are the same thing mapped twice "
                f"(e.g. {duplicates[0][0]!r} and {duplicates[0][1]!r})")
        if no_geo:
            out["problems"].append(f"{no_geo} facilities have no coordinates")
        if unnamed:
            out["warnings"].append(
                f"{unnamed} facilities are unnamed in OSM — shown by category and nearest landmark")

    for layer, c in checks.items():
        for key in FATAL:
            if c[key]:
                out["problems"].append(f"{layer}: {c[key]} {key.replace('_', ' ')}")
        if c["duplicate_edges"]:
            out["warnings"].append(f"{layer}: {c['duplicate_edges']} duplicate edges (collapsed on load)")
        if c["disconnected_components"] > 1:
            out["warnings"].append(
                f"{layer}: {c['disconnected_components']} disconnected components — "
                "routes cannot cross between them")

    out["ok"] = not out["problems"]
    return out


def clean(data: dict) -> tuple:
    """Drop everything report() calls fatal. Returns (data, list of removals).

    Used by the ingestion pipeline and by scripts/validate_city.py --fix; the
    loader also refuses self-loops, so a stale file cannot reintroduce one.
    """
    removed = []
    for layer in ("road", "utility"):
        section = data.get(layer)
        if not section:
            continue
        nodes = [n for n in section["nodes"] if _has_geo(n) and _geo_in_range(n["geo"]) and n.get("type")]
        dropped_nodes = {n["id"] for n in section["nodes"]} - {n["id"] for n in nodes}
        ids = {n["id"] for n in nodes}

        edges, seen = [], set()
        for e in section["edges"]:
            u, v = e.get("source"), e.get("target")
            key = tuple(sorted((str(u), str(v))))
            if u == v:
                removed.append(f"{layer}: self-loop at {u}")
                continue
            if u not in ids or v not in ids:
                removed.append(f"{layer}: edge {u}-{v} references a missing node")
                continue
            if key in seen:
                removed.append(f"{layer}: duplicate edge {u}-{v}")
                continue
            seen.add(key)
            edges.append(e)

        for node_id in sorted(dropped_nodes):
            removed.append(f"{layer}: node {node_id} has no usable coordinates or type")
        section["nodes"], section["edges"] = nodes, edges

    known = {n["id"] for layer in ("road", "utility") for n in data.get(layer, {}).get("nodes", [])}
    data["dependencies"] = {k: [d for d in v if d in known]
                            for k, v in data.get("dependencies", {}).items() if k in known}
    return data, removed


DEDUPE_M = 60.0  # assets closer than this, in the same category, may be one thing


def _normalised(name: str) -> str:
    """Name with spacing and punctuation removed, so "TMA Pai Hospital" and
    "T M A Pai Hospital" compare equal."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def dedupe_facilities(facilities: list) -> tuple:
    """Collapse the same physical asset mapped twice.

    OSM records one hospital as a node *and* as a building way, so "TMA Pai
    Hospital" and "T M A Pai Hospital" both appear, and an operator reading
    "warn MAHE sewage treatment" twice in one action list rightly stops
    trusting the list.

    Proximity alone is not enough to merge: an earlier version of this used
    "same category within 60 m" and quietly deleted Acharya ENT Clinic because
    Anugraha Medical Centre shares its building. Two assets are merged only
    when the data says they are the same thing — matching names once spacing
    and punctuation are ignored, or one of them unnamed. Different names mean
    different assets, however close together they sit.
    """
    kept, removed = [], []
    for f in sorted(facilities, key=lambda x: (not (x.get("name") or "").strip(),
                                               -len(x.get("name") or ""))):
        name = _normalised(f.get("name", ""))
        twin = next((k for k in kept
                     if k["category"] == f["category"]
                     and _metres(k["lat"], k["lon"], f["lat"], f["lon"]) < DEDUPE_M
                     and (not name or not _normalised(k.get("name", ""))
                          or name == _normalised(k.get("name", "")))), None)
        if twin:
            removed.append((f.get("name") or f["id"], twin.get("name") or twin["id"]))
        else:
            kept.append(f)
    return kept, removed


def _metres(lat1, lon1, lat2, lon2) -> float:
    mean_lat = math.radians((lat1 + lat2) / 2)
    return math.hypot((lon2 - lon1) * 111_320 * math.cos(mean_lat),
                      (lat2 - lat1) * 110_540)


def format_report(rep: dict) -> str:
    lines = []
    for layer, c in rep["layers"].items():
        lines.append(f"  {layer} layer")
        for key, value in c.items():
            flag = "  <-- FAIL" if key in FATAL and value else ""
            lines.append(f"    {key.replace('_', ' '):24s} {value}{flag}")
    if "facilities" in rep:
        lines.append("  facilities")
        for key, value in rep["facilities"].items():
            lines.append(f"    {key.replace('_', ' '):24s} {value}")
    for w in rep["warnings"]:
        lines.append(f"  warning: {w}")
    for p in rep["problems"]:
        lines.append(f"  PROBLEM: {p}")
    lines.append("  validation: " + ("passed" if rep["ok"] else "FAILED"))
    return "\n".join(lines)


def _has_geo(node: dict) -> bool:
    geo = node.get("geo")
    return isinstance(geo, (list, tuple)) and len(geo) == 2 and all(
        isinstance(c, (int, float)) for c in geo)


def _geo_in_range(geo) -> bool:
    return LAT_RANGE[0] <= geo[0] <= LAT_RANGE[1] and LON_RANGE[0] <= geo[1] <= LON_RANGE[1]


def _components(nodes, edges) -> int:
    if not nodes:
        return 0
    g = nx.Graph()
    g.add_nodes_from(n["id"] for n in nodes)
    g.add_edges_from((e["source"], e["target"]) for e in edges
                     if e.get("source") != e.get("target"))
    return nx.number_connected_components(g)
