"""Event library per ARCHITECTURE.md §5.1. Severity (0.0-1.0) is continuous —
a "partial flood" degrades an edge rather than removing it outright.
"""
from ..graph.multiplex import CityGraph


def _edge_target(city: CityGraph, event: str, target):
    """Validates target is a real (source, target) pair naming an existing
    road edge, and returns it. Guards against two failure modes that both
    used to crash as an unhandled 500: a bare node id string (e.g. the
    frontend left "second node" unset) silently unpacking into characters
    via `u, v = target`, and a syntactically valid pair that just isn't an
    edge in this city (real OSM data has 635 nodes — most pairs aren't
    neighbors)."""
    if isinstance(target, str) or len(target) != 2:
        raise ValueError(f"{event} needs an (source, target) edge, got {target!r}")
    u, v = target
    if not city.G_road.has_edge(u, v):
        raise ValueError(f"no road edge between {u!r} and {v!r}")
    return u, v


def apply_event(city: CityGraph, event: str, target, severity: float = 1.0):
    """target is a node id (str) for node-targeted events, or an
    (source, target) tuple for edge-targeted events."""
    if event == "accident":
        u, v = _edge_target(city, event, target)
        e = city.G_road.edges[u, v]
        e["current_weight"] *= 1 + 0.5 * severity
        e["max_capacity"] *= 1 - 0.5 * severity
    elif event == "flood":
        if isinstance(target, str):
            city.node_attrs(target)["status"] = max(0.0, 1 - severity)
        else:
            u, v = _edge_target(city, event, target)
            city.G_road.edges[u, v]["current_weight"] *= 1 + 50 * severity
    elif event == "protest":
        u, v = _edge_target(city, event, target)
        city.G_road.edges[u, v]["current_weight"] *= 1 + 50 * severity
    elif event == "fire":
        city.node_attrs(target)["status"] = max(0.0, 1 - severity)
    elif event == "power_outage":
        city.node_attrs(target)["status"] = max(0.0, 1 - severity)
    elif event == "bridge_collapse":
        u, v = _edge_target(city, event, target)
        city.G_road.remove_edge(u, v)
    else:
        raise ValueError(f"unknown event type: {event}")
