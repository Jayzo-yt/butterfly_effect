# Session Report — Cascading Failure Simulator

What got built, fixed, and decided in this session. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the design rationale and
[assumptions.md](assumptions.md) for every estimated value.

## 1. Architecture review and merge

Started from two versions of `ARCHITECTURE.md` (an original and a "v2.0
hardened" rewrite). Reviewed both and merged selectively rather than taking
either wholesale:

- **Adopted**: multiplex graph pattern (`G_road`/`G_utility`/`dependencies`
  as separate objects, not one graph with a `layer` tag), damped BPR
  congestion function for load redistribution, heuristic data-ingestion
  fallbacks.
- **Rejected**: the "two-tier" criticality ranking (topological pre-filter
  that discards 80% of nodes before running leave-one-out). It would have
  thrown away exactly the kind of node — low-centrality, high-impact — the
  whole project exists to surface. Kept full leave-one-out instead.
- **Rejected**: adding Framer Motion/GSAP for UI animation. A CSS
  transition does the same job with no new dependency.

## 2. Core simulation engine (Phases 1–3)

Built from scratch: `Node`/`Edge` schema, the `CityGraph` multiplex
container, a hand-built 17-node toy city, the three propagation mechanisms
(dependency cascade, damped load redistribution, severity decay), impact
metrics (delay, disconnection, max-flow), and full leave-one-out criticality
ranking with a betweenness-centrality baseline for comparison.

**Bug caught during this build**: leave-one-out crashed when disabling a
hospital node, because `dependencies` still referenced it after removal.
Fixed with an existence guard in `mechanism_a_dependency_cascade`.

Verified with a toy-graph test proving the core thesis: a shared water pump
(`W1`, single point of failure for two hospitals) scores higher in the
outcome-based ranking than a genuinely unimportant leaf junction — and
betweenness centrality can't rank `W1` at all, since it isn't part of the
road graph.

## 3. API and frontend, round 1

FastAPI backend (`/city`, `/simulate`, `/criticality`, `/compare`) over the
engine, plus a first frontend pass in vanilla JS + Leaflet-via-CDN
(deliberately not React yet — no build step needed at that scale). Verified
live in-browser: triggering a power outage correctly cascaded to both
hospitals, one failing fully and one degrading via its backup generator.

## 4. Real Manipal data (Phase 4)

Ingested a real OSM extract via `osmnx` (`scripts/ingest_manipal.py`):
635 real road junctions and 2 real hospitals (KMC, Kasturba) within 1.8km
of Manipal center, with a synthetic utility layer for the infrastructure
that has no public data (documented in `assumptions.md`).

**Performance bug caught here**: `leave_one_out_ranking` was recomputing
the *baseline* delay/disconnection metrics from scratch on every one of 638
iterations — a ~600s runtime on the real graph. Fixed by hoisting the
baseline computation out of the loop and sampling the demand-junction set
used for *measurement* (not for which nodes get tested — every node is
still individually disabled and scored). Brought real-city criticality
computation down to a ~40s one-time cost, cached at server startup instead
of recomputed per request.

## 5. Deployment packaging

Dockerfile + docker-compose (Python-only runtime — no `osmnx`/`geopandas`
in the deployed image; real-city data is pre-baked at build time), `.gitignore`/`.dockerignore`,
`README.md`, `assumptions.md`. Docker itself wasn't available in this
environment to verify the build — flagged as an open item rather than
claimed as tested.

## 6. React frontend rewrite

Replaced the vanilla JS page with Vite + React + `react-leaflet` on
explicit request. Built `MapView`, `ControlPanel`, `StatsPanel`,
`CriticalityPanel` components; dark-sidebar/light-map layout; log-scaled
bars on the criticality panel so the disconnection-sentinel score
(1,000,220) doesn't crush the rest of the chart. Added click-to-select on
the map as a second way to pick a disruption target, alongside the
dropdowns. Dockerfile updated to a Node-builds/Python-serves multi-stage
build.

## 7. Simulate crash on real data — root cause and fix

**Symptom**: clicking Simulate against the real Manipal city returned a 500.

**Root cause**: an edge event (accident/protest/bridge collapse) submitted
with no second node passed a bare string into `u, v = target` — Python
unpacks a string into its characters instead of raising a clear error. On
the real 635-node graph, even a *chosen* pair of nodes usually isn't an
actual road edge, since most node pairs aren't neighbors.

**Fix, at the root rather than patched around**:
- `apply_event` now validates the edge target's shape and that the edge
  actually exists, raising `ValueError` with a clear message.
- The API translates that into a `400` response instead of an unhandled `500`.
- The frontend restricts the "second node" dropdown to the target's actual
  road neighbors, and disables Simulate (labeled "Pick second node") until
  a valid pair is chosen — so the bad request can no longer be sent.
- 6 new regression tests added (unit-level in `test_events.py`, API-level
  in `test_api.py`) covering both failure modes.

Verified live against the real data after the fix: `/simulate` returns
`200 OK` and the map updates correctly.

## 8. Research-driven engine improvements

Surveyed comparable projects and the literature (see
[related-work.md](related-work.md)), which surfaced three concrete gaps:

**Fixed a perverse incentive in the core metric.** `avg_hospital_delay`
averaged travel time to *all* hospitals rather than the nearest one. Because
of that, destroying a distant hospital could *lower* the citywide average and
score as an improvement — measured on the real Manipal data, destroying KMC
Hospital scored **−0.16 min**, i.e. the model called a hospital loss a
benefit. Now measures time to the **nearest operational** hospital, which is
what the accessibility literature uses throughout. Destroying a hospital can
no longer score negative (there's a test pinning this).

**Made the dependency cascade graded instead of binary.** Mechanism A only
fired on `status <= 0`, so a pump at 50% severity had *zero* downstream
effect — silently discarding the continuous severity parameter the event
library is built around. Dependents now inherit their worst upstream status,
floored at 0.6 with redundancy. Backed by the multilayer-network work on
"weak" interdependence (PMC5794991).

**Implemented the coverage metric that was documented but missing.**
ARCHITECTURE.md §6.1 specified an isochrone coverage metric and §6.2 listed
coverage in the criticality score; neither existed in code. Added
`coverage_ratio` at an 8-minute EMS threshold, wired into both the API
response and the criticality score.

**Effect on results:** these compound. The shared water pump `W1` now shows
**+3.54 min delay and 50 percentage points of lost 8-minute hospital
coverage** — roughly 10× the impact of losing either hospital outright, and
by far the largest of any single node. Previously its cascade was mostly
invisible, because degrading KMC to 0.6 registered as "still fine." This
considerably strengthens the project's headline finding, since `W1` is
exactly the node betweenness centrality cannot rank at all.

## 9. Honesty pass — replacing invented data with real data

The model's biggest credibility problem was that its headline result was about
a water pump **we made up**. Checked whether OSM could fix that rather than
just documenting it — it can, partially:

**Real utility assets now replace the synthetic ones.** OSM has 3 distinct
substations within 8 km of Manipal centre (one actually named "Manipal") and 2
water assets. These are now ingested at their true coordinates. They live in
`G_utility`, so they don't need to sit inside the 1.8 km road extract.

**But the wiring stays an assumption, permanently.** OSM has only 6 power-line
ways within 8 km — versus the hundreds a town this size really has — so the
electricity distribution network is not mappable from open data. Which
substation feeds which hospital remains a nearest-asset geographic proxy. The
project went from *"invented assets, assumed connections"* to *"real assets,
assumed connections"*, and must not claim more than that.

**Provenance is now tracked in the data, not just in a doc.** Every node
carries `source` (`osm` / `estimated` / `assumed`), surfaced through the API
and shown as a coloured badge in the ranking UI — so the real-vs-assumed
distinction can't quietly vanish between `assumptions.md` and what a viewer
actually sees. This is the systematic fix for the whole class of problem.

**Two dead fields resolved by deletion, not implementation.**
`restore_time_hrs` implied a recovery model that doesn't exist.
`criticality_weight` was worse than dead — using an *assigned* importance score
in the ranking would contradict the project's whole thesis that criticality is
*measured from outcome*. Both removed; `capacity` already carries service scale.

**Hospital capacity strain implemented.** When KMC fails, its catchment floods
Kasturba — previously modelled as free. Now over-subscription degrades
effective service. A first attempt at this **reintroduced the perverse
incentive**: normalising strain across *surviving* hospitals is self-cancelling
(with one hospital left its share is always exactly 1.0), so destroying a
hospital *lowered* the penalty. Caught by the existing test; fixed by
referencing nominal capacity across all hospitals. Now pinned by its own test.

**Population weighting added.** Junctions no longer count equally — demand comes
from OSM building density within 200 m (~3,300 buildings). An observed proxy,
not a population count; WorldPop/GHSL would be the honest upgrade.

**Result on real data:** `S1` (the real "Manipal" substation) and `W2` (a real
water tower) now rank **#3 and #4 citywide**, and neither appears in
betweenness centrality at all because neither is in the road graph. The
outcome-vs-topology finding now rests on real infrastructure.

## 10. Frontend rebuilt to instrument grade

Redesigned around what this tool actually is: an instrument a municipal
engineer uses to defend a capital-allocation decision, not a dashboard.

- **The map is the product** — full-bleed, with control and readout panels
  floating over it: inputs left, results right.
- **Cross-layer dependencies are now drawn.** They were the core of the model
  and previously invisible in the UI. You can watch a hospital's lifeline run
  off the road network to a substation kilometres away — which is the whole
  argument for outcome-based ranking, made visual.
- **Facilities have real iconography** instead of slightly-larger dots, so
  hospitals are findable among 635 junctions. This was a fair complaint about
  the old map.
- **Typography**: IBM Plex Sans, drawn for industrial and engineering
  contexts; IBM Plex Mono reserved strictly for figures, where tabular
  alignment is functional rather than decorative. Self-hosted, no CDN.
- **Palette**: blue-slate ground with one accent — periwinkle, deliberately
  the same violet as the utility layer, so "the thing you interact with" and
  "the hidden layer this tool reveals" are the same colour. Status colours
  stay semantic and never compete with chrome.
- Opening view frames every asset including the distant substations; legend,
  loading and empty states are deliberate; keyboard focus visible; reduced
  motion respected.

**Three real bugs surfaced during this work**, all found by inspecting the DOM
rather than guessing:
1. CARTO's dark tiles now require an API key — a deployment liability, so the
   basemap is standard OSM darkened with a CSS filter instead.
2. Leaflet was creating the map before its container had been laid out, so the
   tile grid and canvas covered only part of the viewport until something
   forced a resize. Fixed by mounting the sized box first and creating the map
   only once it measures non-zero.
3. `fitBounds` on that mis-measured container silently collapsed to max zoom —
   the opening view landed on individual buildings. The initial view is now
   computed deterministically from the data extent instead.

## Current state

- **25/25 tests passing** (`python -m pytest tests/ -q`).
- Toy city (17 nodes, instant) and real Manipal extract (638 nodes, ~40s
  startup) both work end-to-end through the API and the React frontend.
- Not done: PPT and demo video (explicitly out of scope for this repo —
  handled separately), and an actual `docker build` verification (no Docker
  in this environment).
