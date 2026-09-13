"""Dependency propagation: what fails *because* something else failed.

Walks the dependency model outward from the assets an incident hits directly,
one round at a time:

    Substation A  --power-->  Pumping station B  --water-->  Hospital C

Each step records where it came from, over which network, how bad it is, why,
and how much to trust it. A chain is only as trustworthy as its weakest link,
so confidence never improves as it propagates.

Two guards against inventing cascades:

  * propagation only follows links that the dependency model derived from
    data, so an asset with no mapped supplier produces no downstream effect
    (reported as a gap, not as zero impact);
  * an effect below `FLOOR` stops — a fourth-order tremor is not an
    operational finding, and reporting one would bury the real ones.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..graph.dependencies import CONFIDENCE_ORDER, NETWORK_OF_CATEGORY, NETWORKS, DependencyModel
from ..graph.infrastructure import CATEGORIES, group_of, label_of

FLOOR = 0.15          # below this, a knock-on effect is noise
MAX_ROUNDS = 6
BACKUP_RELIEF = 0.4   # on-site backup: degraded, not offline

LEVELS = [(0.75, "critical"), (0.5, "high"), (0.25, "moderate"), (0.0, "low")]


def level_of(impact: float) -> str:
    for threshold, level in LEVELS:
        if impact >= threshold:
            return level
    return "none"


@dataclass
class Node:
    id: str
    impact: float
    level: str
    round: int
    reason: str
    confidence: str
    via_network: Optional[str] = None
    via_source: Optional[str] = None
    supplies_down: List[str] = field(default_factory=list)


@dataclass
class Edge:
    source: str
    target: str
    network: str
    impact: float
    level: str
    reason: str
    confidence: str
    round: int


def supplied_network(category: str) -> Optional[str]:
    """The network an asset of this category feeds, if any."""
    return NETWORK_OF_CATEGORY.get(category)


def propagate(model: DependencyModel, facilities: List, seeds: Dict[str, dict],
              floor: float = FLOOR, max_rounds: int = MAX_ROUNDS) -> dict:
    """Spread the seeds through the dependency model.

    seeds: {facility_id: {"impact": 0..1, "reason": str,
                          "networks": [network keys this asset stops supplying]}}
    """
    index = {f.id: f for f in facilities}
    nodes: Dict[str, Node] = {}
    edges: List[Edge] = []

    frontier = {}
    for fid, seed in seeds.items():
        if fid not in index:
            continue
        impact = float(seed.get("impact", 1.0))
        nets = seed.get("networks")
        if nets is None:
            net = supplied_network(index[fid].category)
            nets = [net] if net else []
        nodes[fid] = Node(id=fid, impact=impact, level=level_of(impact), round=0,
                          reason=seed.get("reason", "Directly affected by the incident"),
                          confidence=seed.get("confidence", "high"),
                          supplies_down=list(nets))
        frontier[fid] = nodes[fid]

    for round_no in range(1, max_rounds + 1):
        next_frontier: Dict[str, Node] = {}
        for fid, node in frontier.items():
            if not node.supplies_down:
                continue
            for link in model.dependents_of(fid):
                if link.network not in node.supplies_down:
                    continue
                consumer = index.get(link.target)
                if consumer is None:
                    continue
                cat = CATEGORIES.get(consumer.category)
                has_backup = bool(cat and link.network in cat.backup)
                impact = node.impact * (BACKUP_RELIEF if has_backup else 1.0)
                if impact < floor:
                    continue

                confidence = _weakest(node.confidence, link.confidence)
                net = NETWORKS[link.network]
                reason = (f"{_name(index, link.target)} {net.failure_effect} because its "
                          f"{net.label.lower()} supply comes from {_name(index, fid)}")
                if has_backup:
                    reason += (" — an assumed local buffer (standby generation or stored "
                               "volume) keeps it partly running rather than stopping it")

                edges.append(Edge(source=fid, target=link.target, network=link.network,
                                  impact=impact, level=level_of(impact), reason=link.reason,
                                  confidence=confidence, round=round_no))

                existing = nodes.get(link.target)
                if existing and existing.impact >= impact:
                    continue  # already reached by a worse path
                downstream = supplied_network(consumer.category)
                new_node = Node(
                    id=link.target, impact=impact, level=level_of(impact), round=round_no,
                    reason=reason, confidence=confidence, via_network=link.network,
                    via_source=fid,
                    supplies_down=[downstream] if downstream else [],
                )
                nodes[link.target] = new_node
                next_frontier[link.target] = new_node
        if not next_frontier:
            break
        frontier = next_frontier

    return _render(model, index, nodes, edges, seeds)


def _weakest(a: str, b: str) -> str:
    return a if CONFIDENCE_ORDER.get(a, 1) <= CONFIDENCE_ORDER.get(b, 1) else b


def _name(index, fid: str) -> str:
    f = index.get(fid)
    return getattr(f, "display_name", None) or fid


def _render(model, index, nodes: Dict[str, Node], edges: List[Edge], seeds) -> dict:
    def node_dict(n: Node) -> dict:
        f = index[n.id]
        return {
            "id": n.id,
            "name": _name(index, n.id),
            "category": f.category,
            "category_label": label_of(f.category),
            "group": group_of(f.category),
            "lat": f.lat, "lon": f.lon,
            "impact": round(n.impact, 2),
            "level": n.level,
            "round": n.round,
            "reason": n.reason,
            "confidence": n.confidence,
            "via_network": n.via_network,
            "via_network_label": NETWORKS[n.via_network].label if n.via_network else None,
            "via_source": n.via_source,
            "via_source_name": _name(index, n.via_source) if n.via_source else None,
            "supplies": [NETWORKS[s].label for s in n.supplies_down],
        }

    ordered = sorted(nodes.values(), key=lambda n: (n.round, -n.impact))
    rounds = max((n.round for n in nodes.values()), default=0)
    indirect = [n for n in ordered if n.round > 0]

    if indirect:
        summary = (f"{len(indirect)} asset(s) are affected indirectly through "
                   f"{rounds} round(s) of dependency propagation.")
    elif any(model.dependents_of(fid) for fid in seeds):
        summary = ("The affected assets have dependents in the model, but the knock-on "
                   "effect stays below the reporting threshold.")
    else:
        supplied = [NETWORKS[s].label.lower() for fid in seeds
                    for s in (nodes[fid].supplies_down if fid in nodes else [])]
        summary = ("No asset in the model draws "
                   + (f"{', '.join(sorted(set(supplied)))} " if supplied else "a service ")
                   + "from the affected assets, so nothing propagates. "
                     "Dependency data for this area is limited — see the gaps below.")

    return {
        "nodes": [node_dict(n) for n in ordered],
        "edges": [{"source": e.source, "target": e.target, "network": e.network,
                   "network_label": NETWORKS[e.network].label, "impact": round(e.impact, 2),
                   "level": e.level, "reason": e.reason, "confidence": e.confidence,
                   "round": e.round} for e in edges],
        "rounds": rounds,
        "indirect_total": len(indirect),
        "summary": summary,
        "gaps": model.gaps,
        "coverage": model.coverage,
    }


def demo():
    """Self-check: power failure must reach a hospital through water, and must
    not reach anything the data does not connect."""
    from ..graph import dependencies as dep
    from .impact import Facility

    fac = [
        Facility(id="sub", name="Central substation", category="substation", lat=13.350, lon=74.790),
        Facility(id="pump", name="Eastern pumping station", category="pumping_station", lat=13.352, lon=74.791),
        Facility(id="tower", name="Manipal water tower", category="water_tower", lat=13.353, lon=74.792),
        Facility(id="hosp", name="District Hospital", category="hospital", lat=13.354, lon=74.793),
        Facility(id="school", name="Government School", category="school", lat=13.355, lon=74.794),
    ]
    model = dep.build(fac)
    out = propagate(model, fac, {"sub": {"impact": 1.0, "reason": "Substation offline"}})
    reached = {n["id"]: n for n in out["nodes"]}
    assert "pump" in reached, "the pump draws power from the substation"
    assert "hosp" in reached, "the hospital must be reached through the water chain"
    assert reached["hosp"]["round"] >= 2, reached["hosp"]
    assert out["rounds"] >= 2
    power_to_hospital = [e for e in out["edges"]
                         if e["source"] == "sub" and e["target"] == "hosp" and e["network"] == "power"]
    assert power_to_hospital and power_to_hospital[0]["impact"] < 1.0, \
        "a hospital with standby generation degrades on power loss, it does not stop"

    # Nothing supplies anything downstream of a school: the cascade must stop.
    quiet = propagate(model, fac, {"school": {"impact": 1.0, "reason": "Fire"}})
    assert quiet["indirect_total"] == 0
    assert "propagates" in quiet["summary"] or "threshold" in quiet["summary"]
    print(f"ok — {len(out['nodes'])} nodes over {out['rounds']} rounds")


if __name__ == "__main__":
    demo()
