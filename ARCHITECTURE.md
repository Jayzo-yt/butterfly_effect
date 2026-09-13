# Cascading Failure — Infrastructure Resilience Simulator

**Manipal Hackathon 2026 (M#26) — "The Butterfly Effect"**
Problem Statement: *Cascading Failure: When One Failure Becomes Many*
Domain: Infrastructure Resilience | SDG 11: Sustainable Cities and Communities

---

## 1. Problem Framing

Infrastructure is monitored asset-by-asset. A road is inspected as a road, a hospital as a hospital. But they are not independent — a hospital is only as reachable as the roads that feed it, and only as functional as the power and water lines that supply it.

When one asset fails, the consequences are rarely local. Traffic reroutes onto alternate roads that were never sized for that load. Ambulance response times increase across an entire district. A substation outage silently degrades three hospitals at once.

**This project models a city's infrastructure as an interconnected network, simulates disruptions, and quantifies how failure propagates — with the explicit goal of identifying which assets, if protected, would prevent the most downstream damage.**

### What this is NOT

- Not a binary "affected / not affected" graph traversal. Impact is continuous and distance-decaying.
- Not generic graph centrality. Criticality here is defined by *service outcome* (hospital reachability, supply delivery), not by topological position alone.
- Not a live monitoring dashboard. This is a planning and scenario-analysis tool.

---

## 2. System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        DATA LAYER                                │
│  OSM road network  │  Hospital registry  │  Utility node set     │
│  (osmnx)           │  (manual, bed count)│  (manual/estimated)   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                     GRAPH CONSTRUCTION LAYER                      │
│  Typed multi-layer graph: nodes + attributes, weighted edges      │
│  Baseline state snapshot (pre-disruption reference)               │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                     DISRUPTION ENGINE                             │
│  Event library → weight/status mutation → propagation rules       │
│  (capacity overflow, redundancy check, severity decay)            │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                     IMPACT ANALYSIS LAYER                         │
│  Shortest-path delta  │  Max-flow delta  │  Service coverage loss │
│  Criticality ranking (leave-one-out simulation)                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                     PRESENTATION LAYER                            │
│  Interactive map │ Before/after comparison │ Ranked asset table   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Model

### 3.1 Node Schema

```python
Node = {
    "id":                  str,     # unique identifier
    "type":                str,     # road_junction | hospital | substation |
                                    # water_pump | fire_station | depot
    "geo":                 (lat, lon),
    "capacity":            float,   # type-dependent unit (see 3.3)
    "status":              float,   # 0.0 (down) - 1.0 (fully operational)
    "redundancy":          int,     # count of independent backup supplies
    "depends_on":          [str],   # node ids this node requires to function
    "source":              str,     # osm | estimated | assumed — provenance,
                                    # surfaced in the API and UI (see 3.3)
    "population_weight":   float,   # relative demand; 1.0 = unweighted
}
```

**Two fields were deliberately removed rather than left unimplemented.**
`restore_time_hrs` implied a recovery/restoration model this project does not
have — that's a feature to add, not a field to leave dangling.
`criticality_weight` (an assigned 1–10 importance score) was removed because
using it in the ranking would contradict §6.2's entire thesis: criticality here
is *measured from outcome*, never assigned as an attribute. `capacity` already
carries service scale.

```python
```

### 3.2 Edge Schema

```python
Edge = {
    "source":         str,
    "target":         str,
    "layer":          str,    # road | power | water | supply_route
    "base_weight":    float,  # baseline travel time (min) or flow capacity
    "current_weight": float,  # mutable — disruptions modify this
    "max_capacity":   float,  # throughput ceiling (vehicles/hr, MW, m³/hr)
    "current_load":   float,  # present utilization
    "directed":       bool,
}
```

### 3.3 Capacity Units by Type

| Node type      | Capacity unit          | Typical assumed value      |
|----------------|------------------------|----------------------------|
| Road junction  | vehicles/hour          | 800/lane (standard HCM est.)|
| Hospital       | patients/day           | derived from bed count × 1.5|
| Substation     | MW                     | 10–50 depending on tier     |
| Water pump     | m³/hour                | estimated by service area   |
| Fire station   | simultaneous responses | 2–4 per station             |

**Assumption policy:** where real values are unavailable (which is most of them — road capacity and hospital throughput are not published open data), values are estimated from standard traffic-engineering and healthcare-planning references. Every assumed value is documented in `assumptions.md` and surfaced in the UI. **The model never presents an estimate as a measurement.**

---

## 4. Multi-Layer Graph Structure (Multiplex Pattern)

The network is not one flat graph. It is three coupled layers:

| Layer  | Nodes                       | Edges                          |
|--------|-----------------------------|--------------------------------|
| Physical transport | junctions, roads     | road segments (weighted by travel time) |
| Utility supply     | substations, pumps   | power/water distribution lines |
| Service demand     | hospitals, fire stations, shelters | dependency edges to both layers above |

**Implementation decision:** a single graph with a `layer` edge attribute lets a routing call accidentally traverse a utility edge while computing road travel time. To rule that out structurally, the layers are kept as **separate graph objects** rather than one graph with type tags:

| Object | Type | Purpose |
|--------|------|---------|
| `G_road` | `nx.Graph()` | Physical transport only. All Mechanism B routing (Dijkstra, max-flow) runs exclusively on this graph. |
| `G_utility` | `nx.Graph()` | Power/water distribution only. |
| `dependencies` | `dict[node_id, list[node_id]]` | Cross-layer coupling — which `G_utility` nodes a `G_road` node depends on. Consulted only by Mechanism A. |

**Inter-layer coupling** is where cascades actually happen. A hospital node exists in `G_road` (for ambulance routing) and has an entry in `dependencies` pointing at a substation and a water pump in `G_utility`. A substation failure degrades the hospital *without touching any road edge*, and because the coupling lives in a separate dict rather than mixed-in graph edges, a routing call on `G_road` cannot cross into it by accident. This coupling is the core of the model — single-layer graphs cannot represent it, and this is the primary differentiator from a naive approach.

---

## 5. Disruption Engine

### 5.1 Event Library

| Event            | Target       | Effect on `current_weight` / `status`         | Duration |
|------------------|--------------|-----------------------------------------------|----------|
| Accident         | road edge    | weight × 1.5, capacity × 0.5                   | 1–3 hrs  |
| Flood            | road edge / node | weight = ∞ (impassable), status = 0        | 12–72 hrs|
| Protest/blockade | road edge    | weight = ∞                                     | 2–12 hrs |
| Fire             | any node     | status = 0                                     | 4–24 hrs |
| Power outage     | substation   | status = 0 → cascades to dependents            | 2–48 hrs |
| Bridge collapse  | road edge    | edge removed from graph entirely               | weeks    |

Severity is a parameter (0.0–1.0), not a fixed constant — a "partial flood" degrades an edge rather than removing it. Users can compose multiple simultaneous events to test compound scenarios.

### 5.2 Propagation Rules

Propagation is **not** breadth-first flood-fill. It runs in three explicit mechanisms:

**Mechanism A — Dependency cascade (graded)**
Node X inherits the *worst* status among its `depends_on` nodes — a hospital with power but no water is limited by the water — floored at 0.6 when `X.redundancy > 0` (running on backup generator), and a `restore_time` countdown begins.

Deliberately graded rather than binary: a pump at 50% degrades its dependents to 50% rather than having no effect at all, which keeps the continuous severity parameter of §5.1 meaningful all the way down the chain. Binary "any upstream loss kills the dependent" is the *strong* interdependence assumption that the multilayer-network literature identifies as unrealistic (see `related-work.md` §2).

**Mechanism B — Load redistribution (the real cascade)**
When an edge's capacity drops, its traffic must go somewhere. The engine:
1. Recomputes shortest paths for all affected origin-destination pairs.
2. Adds displaced load to the new routes.
3. Checks whether any receiving edge now exceeds `max_capacity`.
4. If it does, that edge's weight increases via a **damped BPR (Bureau of Public Roads) congestion function**, not a linear bump — this is the standard traffic-engineering formula for how travel time grows with overload, and the damping factor keeps the redistribution loop from oscillating instead of converging:

```python
overload_ratio = load / capacity
congestion_factor = 1 + 0.15 * (overload_ratio ** 4)
new_weight = old_weight + ALPHA * (old_weight * congestion_factor - old_weight)   # ALPHA = 0.3 damping
```

5. Repeat until convergence (total network delay changes < 1% between iterations) or a fixed iteration cap.

**This iterative overload loop is what produces genuine cascading failure** — the same mechanism that causes real power-grid blackouts, applied to transport.

**Mechanism C — Severity decay**
For soft impact scoring (used in visualization), impact at distance *d* hops from the origin decays as `impact = initial_severity × λ^d`, with λ ≈ 0.6, floored at a cutoff threshold. This prevents an infinite ripple across the whole city from a single pothole.

---

## 6. Impact Analysis

### 6.1 Metrics Computed

| Metric | Method | Answers |
|--------|--------|---------|
| **Service delay delta** | Dijkstra before vs after, to the **nearest operational** hospital, divided by that hospital's status | "How much longer does an ambulance now take?" |
| **Supply flow loss** | `max_flow` before vs after, source→hospital | "How much capacity was lost?" |
| **Coverage loss** | % of junctions within an 8-minute isochrone (common EMS benchmark) of a functioning hospital | "How many people lost timely access?" |
| **Disconnection events** | Count of nodes with no path to any service node | "Who got completely cut off?" |
| **Cascade depth** | Number of redistribution iterations before convergence | "How far did it spread?" |

### 6.2 Criticality Ranking — the core deliverable

Generic betweenness centrality answers "which node is topologically central." That is the wrong question. The right question is **"which node's failure causes the most service damage."**

Method — leave-one-out simulation:

```
for each node n in graph:
    snapshot = copy(baseline_graph)
    disable(snapshot, n)
    run_propagation(snapshot)
    score[n] = weighted_sum(
        Δ average hospital delay,
        Δ population coverage,
        count of newly disconnected nodes,
        cascade depth
    )
rank nodes by score, descending
```

The top-ranked nodes are the ones a city should hardened first. This produces an **actionable output** ("reinforce these five assets") rather than a descriptive one ("here is a graph"). It is also directly what the problem statement asks for: *"identify disproportionately important assets or connections."*

Betweenness centrality and articulation-point detection are still computed — but as a **baseline comparison** to show that outcome-based ranking differs from topological ranking. Demonstrating that difference is itself a strong result for the judges.

### 6.3 Scenario Comparison

Users can define and store multiple scenarios (e.g., "flood on Bridge A" vs "substation B outage") and compare them side-by-side on the same metrics. This satisfies the "compare alternative scenarios" requirement and makes the demo far more compelling than a single canned failure.

---

## 7. Technology Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Graph engine | `networkx` | Dijkstra, max-flow, centrality, articulation points all built in. No need to implement graph algorithms from scratch. |
| Map data ingestion | `osmnx` | Pulls real routable road networks from OpenStreetMap in one call, with geometry and travel-time estimation included. |
| Data ingestion fallback | Heuristic tagging | Manually tagging every hospital/substation with real attributes doesn't scale to hackathon time. Where OSM tags are missing: infer hospital scale from building footprint area, generate a synthetic grid for unmapped utility lines. Still governed by the assumption policy in §3.3 — inferred values are labeled as estimates, never shown as measurements. |
| Numerics | `numpy`, `pandas` | Load redistribution matrices, metric tables. |
| Backend API | `FastAPI` | Lightweight, async, auto-generated docs. Keeps simulation logic separate from UI. |
| Frontend | `React` + `Leaflet` (or `deck.gl`) | Real geographic map rendering with color-coded overlays. |
| Visualization | `folium` (prototype) → Leaflet (final) | Folium for fast iteration during development; proper frontend for the demo. |
| Storage | JSON / SQLite | Scenarios and graph snapshots. No need for a heavy DB at this scale. |

**Deliberately excluded:** Neo4j (adds setup overhead without benefit at this graph size), any ML model (the problem is deterministic simulation, not prediction — bolting on ML for its own sake would be noise, not innovation).

---

## 8. Build Sequence

**Phase 1 — Synthetic graph and core model**
Hand-build a 15–20 node toy network. Implement node/edge schema, baseline snapshot, and Dijkstra-based delay computation. Verify by hand that results are correct on a graph small enough to check manually. *Do not touch real data yet — debugging propagation logic on 2000 nodes is a waste of time.*

**Phase 2 — Disruption engine**
Implement the event library and all three propagation mechanisms. Test the overload redistribution loop specifically — that it converges and does not oscillate infinitely.

**Phase 3 — Impact metrics and criticality ranking**
Implement leave-one-out simulation. Compare its output against betweenness centrality; if the two rankings are identical, the outcome-weighting is not doing anything and needs adjustment.

**Phase 4 — Real city ingestion**
Pull a small city's road network via `osmnx`. Prune to a manageable subgraph if needed (simplify, remove residential dead-ends). Manually tag real hospitals, substations, and fire stations with estimated attributes.

**Phase 5 — Frontend and visualization**
Map rendering, disruption controls, before/after toggle, ranked criticality table.

**Phase 6 — Submission assets**
PPT (mandatory template), ≤3 min video with prototype demo, public GitHub repo.

---

## 9. Validation Strategy

A simulator that cannot be checked is a simulator nobody trusts. Three validation approaches:

1. **Manual verification on the toy graph** — compute expected shortest-path deltas by hand for 3–4 disruption scenarios and assert the engine matches.
2. **Sanity invariants** — removing a leaf node should not increase citywide delay. Removing a bridge node should. Total flow after disruption must never exceed baseline. Assert these as tests.
3. **Known-case comparison** — pick a real historical disruption in the chosen city (a documented flood or road closure), run it through the model, and check whether the assets the model flags as critical match what actually caused problems. Qualitative, but far more convincing than nothing.

---

## 10. Known Limitations

Stated explicitly, because judges will find them anyway and pre-empting them reads as rigor rather than oversight:

- Capacity and demand values are estimated, not measured. Absolute numbers are indicative; **relative rankings are the trustworthy output.**
- The traffic model is static equilibrium, not time-dependent simulation. Rush-hour dynamics are not modeled.
- Human behavior (drivers avoiding an area entirely, informal rerouting) is not captured.
- Repair and restoration scheduling is simplified to a fixed duration per event type.
- The model assumes complete network topology; unmapped informal roads are invisible to it.

---

## 11. Deployment and Who Pays

| Buyer | Use case | Model |
|-------|----------|-------|
| Municipal planning departments | Capital allocation — which assets to reinforce first | Annual license per city |
| Disaster management authorities | Pre-event scenario planning, response prioritization | Government contract / tender |
| Infrastructure insurers | Risk pricing for infrastructure portfolios | Per-analysis or API pricing |
| Utility operators | Maintenance scheduling by downstream criticality | SaaS subscription |

**Scalability path:** the same engine works for any city; ingestion is automated via OSM, so onboarding a new city is a configuration task rather than a rebuild. Marginal cost per additional city is low, which is what makes the model viable.

---

## 12. Repository Layout

```
cascading-failure/
├── README.md
├── ARCHITECTURE.md
├── assumptions.md              # every estimated value, documented
├── data/
│   ├── synthetic/              # toy graphs for testing
│   └── city/                   # cached OSM extracts, tagged nodes
├── src/
│   ├── graph/
│   │   ├── schema.py           # node/edge definitions
│   │   ├── builder.py          # synthetic + OSM graph construction
│   │   ├── multiplex.py        # G_road / G_utility / dependencies management
│   │   └── snapshot.py         # baseline state management
│   ├── disruption/
│   │   ├── events.py           # event library
│   │   ├── propagation.py      # mechanisms A, B, C
│   │   └── convergence.py      # overload redistribution loop
│   ├── analysis/
│   │   ├── metrics.py          # delay, flow, coverage, disconnection
│   │   ├── criticality.py      # leave-one-out ranking
│   │   └── comparison.py       # scenario diffing
│   ├── api/
│   │   └── main.py             # FastAPI endpoints
│   └── viz/
│       └── mapper.py           # geographic rendering helpers
├── frontend/                   # React + Leaflet
├── tests/
│   ├── test_propagation.py
│   ├── test_invariants.py
│   └── test_criticality.py
└── requirements.txt
```

---

## 13. Alignment with Judging Criteria

| Criterion | How this design addresses it |
|-----------|------------------------------|
| **8.1 Innovation** | Multi-layer coupled graph and outcome-based criticality ranking, not generic centrality. Iterative overload redistribution rather than flood-fill propagation. |
| **8.2 Feasibility** | Built on mature libraries; phased build with a working core before real-data ingestion. Limitations stated openly. |
| **8.3 Marketing** | Clear target users (municipal planners, disaster authorities); demo is visual and immediately legible to non-technical decision-makers. |
| **8.4 Monetisation** | Four defined buyer segments with distinct pricing models; low marginal cost per additional city. |
| **8.6 Prototype bonus** | Phased build guarantees a demonstrable working core even if later phases are cut. |
