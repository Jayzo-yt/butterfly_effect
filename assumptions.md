# Assumptions

Per ARCHITECTURE.md §3.3: real capacity and demand data for infrastructure is
mostly not published open data. Everything estimated or assumed by
`scripts/ingest_manipal.py` and `src/graph/builder.py` is listed here.

**None of these are measurements.** Absolute numbers are indicative; relative
rankings are the trustworthy output (ARCHITECTURE.md §10).

## Provenance is tracked in the data itself

Every node carries a `source` field, surfaced in the API and shown as a badge
in the UI, so the distinction can't quietly disappear between this file and
what a viewer sees:

| `source` | Meaning |
|---|---|
| `osm` | The asset's existence and location came from OpenStreetMap |
| `estimated` | Real asset, but this particular value is a documented estimate |
| `assumed` | The asset or the link itself is our assumption, not observed |

## Real data (OpenStreetMap, not estimated)

- **Road network** — geometry, `highway` class, `lanes`, and derived speed/travel
  time for the ~1.8 km radius around Manipal centre (13.3467, 74.7855).
- **Hospitals** (`amenity=hospital`) — KMC Hospital and Kasturba Hospital Udupi.
- **Substations** (`power=substation`) — 3 distinct assets after de-duplication,
  one named "Manipal". Searched to 8 km, since utility assets live in
  `G_utility` and don't need to sit inside the road extract.
- **Water assets** (`man_made=water_works|water_tower|pumping_station`) — 2 distinct.
- **Buildings** — ~3,300 within 2 km, used for the demand proxy below.

## Estimated / assumed

- **Road capacity**: 800 vehicles/hour per lane (standard HCM estimate). `lanes`
  used when OSM has it, default 1 lane when missing or ambiguous.
- **Hospital capacity**: flat 120 patients/day. OSM has no bed count or building
  footprint for these two (they're tagged as points, not building ways), so the
  footprint-inference heuristic doesn't apply.
- **Substation capacity** 20 MW, **water capacity** 500 m³/hr — mid-range of the
  §3.3 tiers. OSM rarely tags throughput.
- **Which substation feeds which hospital — ASSUMED, and permanently so.**
  OSM has only 6 power-line ways within 8 km, versus the hundreds a town this
  size actually has, so the electricity distribution network is not mappable
  from open data. Hospitals are wired to their *nearest* substation and water
  asset as a geographic proxy. This is the single biggest assumption in the
  model, and it directly shapes the cascade results.
- **Water→substation links**: same nearest-asset proxy, same caveat.
- **Hospital redundancy**: KMC assumed to have backup generator capacity
  (`redundancy=1`), Kasturba assumed not (`redundancy=0`). A demo assumption to
  exercise Mechanism A's degrade-vs-fail branch, not a verified fact about
  either hospital's actual backup power.
- **Population weighting**: junction demand comes from OSM building count within
  200 m. Buildings are observed, but "one building = one unit of demand" ignores
  building size, height, and occupancy — a *proxy*, not a population count.
  WorldPop or GHSL rasters would be the honest upgrade.
- **Coverage threshold**: 8 minutes, the common EMS response benchmark. Genuinely
  city-scale dependent — on this 1.8 km extract a 15-minute cutoff covers
  everything and tells you nothing.
- **Hospital strain**: catchments assigned in one pass by travel time, not
  re-solved against the resulting strain. Real patients divert away from a
  swamped hospital; modelling that needs Mechanism B's iterate-to-convergence
  treatment.
- **Fire station**: omitted from the real extract — no `amenity=fire_station`
  exists in OSM within the ingestion radius. Left out rather than invented.
  (The toy graph still has `F1` for exercising the mechanism in tests.)
- **Multi-edge collapse**: OSM's road network is a directed multigraph; ingestion
  collapses it to the simple undirected graph the schema uses, so a divided road
  becomes one edge. A known simplification, not a data error.
- **Toy city** (`src/graph/builder.py`): entirely synthetic, every node
  `source="assumed"`. It exists for hand-verifiable tests, not for results.
