# City Disruption & Resilience

**What happens to a city when one thing fails?** An operator picks a road or an
asset, says what happened to it, and gets the answer an incident room needs:
what is affected, why, what follows, and what to do first — every figure traced
to real data, a documented calculation, or a labelled estimate.

Built for Manipal Hackathon 2026 (M#26), "The Butterfly Effect", on a real
OpenStreetMap extract of Udupi and Manipal, Karnataka: 2,619 junctions, 3,507
road segments, 166 mapped assets.

---

## What it answers

- **What is affected, and why?** Not by distance — by routing. A facility
  appears because a path it depends on got longer or disappeared, and the
  reason is printed beside it.
- **What happens next?** Dependency propagation across power, water and
  communications — substation → water tower → hospital — each step naming its
  source, its reason and how much to trust it.
- **Who responds, and how much slower?** Nearest fire, police and ambulance
  station, with travel time and response time kept apart.
- **Where does the demand go?** Named alternative hospitals and named assembly
  points, with routes and capacity.
- **How does it compare with normal?** A baseline-versus-disrupted table from
  the same routing, so each change is a measured difference.
- **What should be done now?** Actions generated from the result, each naming
  the asset it came from.
- **How does the city recover?** Containment, clearance, reopening and service
  restoration — and an explicit refusal to invent a reopening date for a
  building that burnt down.

Every figure carries its provenance — `measured`, `calculated`, `estimated`,
`assumed` — and the model keeps four kinds of nothing apart: a real zero,
`unknown`, `not_applicable` and `missing_data`. Where the data cannot support
an answer (no ambulance station mapped, no distribution network published, no
flood-hazard layer) it says so instead of filling the gap with a number.

The full provenance trace is in **[docs/AUDIT.md](docs/AUDIT.md)**; the design
rationale is in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Quick start

```bash
python -m pip install -r requirements.txt
python -m pytest tests/ -q
```

```bash
cd frontend && npm install && npm run build && cd ..
```

```bash
CITY_DATA_PATH=data/city/manipal.json python -m uvicorn src.api.main:app --port 8000
```

On PowerShell the environment variable is set separately:

```powershell
$env:CITY_DATA_PATH = "data/city/manipal.json"; python -m uvicorn src.api.main:app --port 8000
```

Open **http://127.0.0.1:8000/app/** — search for a place or click the map,
choose what happened, run it. Leaving `CITY_DATA_PATH` unset uses the
hand-built toy network the tests run against.

The city data is committed, so this works without an OpenStreetMap download.

**Frontend dev mode** (hot reload instead of build-and-serve):

```bash
cd frontend && npm run dev
```

---

## See the whole engine in one command

```bash
python scripts/run_scenarios.py
```

Runs six reference scenarios against the real extract — road accident, school
fire, hospital fire, substation failure, flooding, and all three at once — and
prints the full reasoning for each: response times, evacuation, cascade,
baseline comparison, timeline, recovery and recommended actions. This is the
fastest way to see what the engine does without driving the interface.

---

## How an incident is modelled

An incident type describes only *how the initial failure behaves* — whether it
blocks a road, how far it reaches, how much of the struck asset's function it
destroys, who responds, how long clearance takes. What that failure *means*
comes from the asset's own traits in `src/graph/infrastructure.py`: how many
people are typically inside, whether a peer facility absorbs its demand, which
utility networks it needs, and whether it rides out a supply failure on standby
power or stored water.

So **"school fire" is not a feature.** It is `fire` applied to an asset whose
traits say it holds people and has no substitute — which is why the same
incident type diverts patients at a hospital, cuts power downstream at a
substation, and would handle a warehouse without a line of new code.

```
incident → direct impact → network effects → resource response
        → dependencies → cascade → impact metrics → actions → recovery
```

There is no `if incident == "school_fire"` anywhere in the codebase.

---

## Dependency data

Which substation feeds which hospital is not published anywhere. Rather than
inventing it, `src/graph/dependencies.py` derives links from service-area
proximity and grades every one:

| Basis | Confidence | Meaning |
|---|---|---|
| `explicit` | high | An operator-supplied dependency file said so |
| `service_area` | medium | Nearest supplier, inside its plausible service radius |
| `distant` | low | Nearest supplier, but beyond that radius |

Supply real data as `data/city/manipal.dependencies.json` and it wins:

```json
{"node/672742480": [{"source": "way/256638639", "network": "power"}]}
```

Manipal has no mapped power plant, pumping station, treatment works or telecom
mast, so those parts of each chain are reported as gaps in every result rather
than silently skipped. A correct "no cascade" beats a fabricated one.

---

## API

| Endpoint | What it does |
|---|---|
| `POST /simulate-incidents` | The engine. One or more incidents → direct impact, network effects, dependency cascade, emergency response, evacuation, alternatives, baseline comparison, timeline, recovery, score, confidence and actions |
| `GET /incident-types` | The incident catalogue, severities, durations and asset taxonomy — the interface's dropdowns are built from this, so they cannot drift from the engine |
| `GET /facilities` | Every mapped asset, named, with category, service, area and provenance |
| `GET /dependencies` | The derived dependency model, with the basis and confidence of every link |
| `GET /search?q=` | Roads, assets and areas by name |
| `GET /areas` | Administrative boundaries |
| `GET /validation` | Data-quality report for the loaded extract |
| `GET /city` | The baseline graph, for rendering |
| `POST /analyze` | The road-network slice on its own |
| `GET /criticality` | Leave-one-out outcome ranking, with a betweenness baseline to compare against |

---

## Repository map

```
src/
  graph/         the city model
    schema.py           node and edge records, with provenance
    multiplex.py        road and utility layers, kept separate
    infrastructure.py   28-category asset taxonomy and its traits
    dependencies.py     derived power / water / telecom links
    areas.py            administrative boundaries, point-in-polygon
    validate.py         data-quality checks used by ingestion and the API
  analysis/      the simulation engine
    scenario.py         the pipeline that ties everything together
    incidents.py        23 incident types — behaviour only, no consequences
    impact.py           routing, disruption application, facility impact
    cascade.py          dependency propagation in rounds
    response.py         dispatch, turnout, availability, resource contention
    occupancy.py        occupancy priority chain, walking, assembly capacity
    recovery.py         containment → clearance → reopening, and the timeline
    comparison.py       baseline versus disrupted
    actions.py          recommendations derived from the result
    provenance.py       measured / estimated / unknown, and confidence grading
    criticality.py      leave-one-out ranking against betweenness
  api/main.py    FastAPI layer — no simulation logic lives here

frontend/src/    React + Vite + react-leaflet
scripts/         data ingestion, validation, ranking, scenario runner
tests/           85 tests
docs/AUDIT.md    every figure, traced to its source
```

---

## Tests

```bash
python -m pytest tests/ -q
```

85 tests. They assert the *reasoning*, not just that a number appeared: a
school fire must report who is inside and where they go; a substation failure
must reach a hospital through the water chain; an incident with an equal
detour must return zero **with an explanation**. Several are named after bugs
that reached the screen — a structure fire that closed a kilometre of road,
responders unable to reach the site their own cordon closed, a drive time
presented as a response time.

Most modules also carry a runnable self-check:

```bash
python -m src.analysis.cascade
python -m src.analysis.response
python -m src.graph.dependencies
```

---

## Data quality

```bash
python scripts/validate_city.py data/city/manipal.json
python scripts/validate_city.py data/city/manipal.json --fix
```

Reports assets, edges, self-loops, duplicate edges, missing coordinates,
invalid geometry, disconnected components, missing asset types and duplicate
assets. `--fix` repairs what is repairable and re-validates. Ingestion runs the
same check and refuses to save a graph that fails it; `GET /validation` exposes
it at runtime.

---

## Regenerating the city data

Only needed to change the city, the radius, or to pull fresher OpenStreetMap
data. The committed extract works as-is.

```bash
python -m pip install -r requirements-dev.txt
python scripts/ingest_manipal.py --radius 5000    # road network + population weights
python scripts/ingest_poi.py --radius 6000        # every asset category in the taxonomy
python scripts/ingest_areas.py --radius 8000      # administrative boundaries
python scripts/rank_city.py data/city/manipal.json
```

The ranking is precomputed because leave-one-out over the whole extract takes
minutes, and a server that cannot answer for four minutes after boot is no use
in an operations room. A stale ranking is flagged and served rather than
recomputed on the critical path.

**Sensitivity — the analysis that defends the result:**

```bash
python scripts/rank_city.py data/city/manipal.json --sensitivity 25
```

Re-ranks under randomised hospital→utility wirings and reports how often each
asset still reaches the top. Assets that persist regardless of the wiring are
the defensible finding; assets that appear only under one particular guess are
not.

---

## Docker

```bash
docker compose up --build
```

Multi-stage: Node builds the frontend, and the Python runtime image gets only
the built static files — no Node, no osmnx, no live OpenStreetMap calls.

> Docker was not available in the environment this was written in, so the image
> follows a standard multi-stage pattern but has not been built end to end.
> `docker compose up --build` is the real check.

---

## Known limits

Stated in the interface as well as here.

- No ambulance station is mapped in this extract, so ambulance response is
  reported as unmapped rather than estimated.
- No telecom infrastructure is mapped, so the communications dependency chain
  cannot be modelled at all.
- No power plant, pumping station or treatment works is mapped; propagation
  starts at the substation and water-tower tiers.
- No hospital publishes a bed count here, so displaced demand can be located
  but not quantified.
- OpenStreetMap has no ward boundaries for this area — taluk is the finest
  level available.
- Flood, storm and earthquake extents are uniform radii. There is no hazard or
  elevation layer, and the model says so rather than implying hydrology.
- Only 2.4% of road segments carry a real speed limit; the rest use documented
  per-class planning speeds.

---

## Licence

Source code: [MIT](LICENSE).

City data under `data/` is derived from OpenStreetMap — © OpenStreetMap
contributors, [ODbL v1.0](https://www.openstreetmap.org/copyright). Keep the
attribution if you redistribute it.
