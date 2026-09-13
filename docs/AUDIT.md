# Provenance audit

Every figure the interface shows, traced to where it actually comes from.

State meanings are defined in `src/analysis/provenance.py`:

| State | Meaning |
|---|---|
| `measured` | observed directly in the source data |
| `calculated` | computed from measured inputs only |
| `estimated` | computed, but using a documented assumption |
| `assumed` | a judgement value, not derived from anything |
| `unknown` | the data cannot answer this |
| `missing_data` | the input that would answer it is absent |
| `not_applicable` | the question does not arise for this incident |

## What the audit found

Four things were being presented more confidently than the data supports. All
four are fixed; they are recorded here because the fixes matter less than the
habit of checking.

**"~600 occupants" was one constant for every school.** It came from
`CATEGORIES["school"].occupancy`, a hardcoded planning figure, and appeared in
the same typeface as a routed travel time. It now arrives through a priority
chain — operator feed, then published capacity, then the category figure — and
is labelled `estimated` with the sentence "the same value for every asset of
this category". No asset in this extract publishes a capacity, so every school
still lands on the constant. It now says so.

**"13 min to the fire station" was a drive time presented as a response time.**
It was a shortest path over the road network containing no call handling, no
turnout and no traffic. Travel and response are now separate figures, and the
response is a range.

**45% of road segments were timed at a flat 6 seconds.** Ingestion applied a
`max(0.1, …)` floor to per-segment travel time. In a town whose median segment
is 81 m, that floor bound on 1,588 of 3,513 segments, padding every route that
crossed many junctions. The floor is gone; time is length ÷ speed.

**Only 2.4% of segments carry a real speed limit.** osmnx imputed the other
97% from the mean of those 84 — which are mostly highway — and assigned
46 km/h to residential streets. Speeds are now set explicitly by road class
(20 km/h residential, 60 trunk, and so on): still an assumption, but a stated
one that matches the streets rather than an artefact of a biased sample.
Travel times across the network roughly doubled as a result, which is the
honest direction.

## Figure by figure

| Shown as | Source | State |
|---|---|---|
| Road geometry, junctions, asset positions | OpenStreetMap | `measured` |
| Road segment length | OSM way geometry | `measured` |
| Road speed | class table in `ingest_manipal.py`; 2.4% from OSM `maxspeed` | `estimated` |
| Travel time to a facility | multi-source Dijkstra over the above | `calculated` |
| Response time | travel + dispatch + turnout, ×1.0–1.3 for congestion | `estimated` |
| Station availability | nothing published; only this scenario's commitments | `unknown` |
| Appliances per station | `response.PROFILES` planning figures | `assumed` |
| Occupancy | priority chain; currently always the category figure | `estimated` |
| Assembly point capacity | mapped ground area ÷ 1 m² per person | `estimated` |
| Walking time | route length ÷ 4 km/h | `estimated` |
| People in the zone | building density × 4.5 per building | `estimated` |
| Facility access change | routing before vs after, same algorithm | `calculated` |
| Dependency links | nearest supplier within service range | `estimated` |
| Cascade impact | propagation over those links | `estimated` |
| Containment time | the duration the operator entered | input |
| Clearance and restoration | per-incident-type planning figures | `assumed` |
| Administrative area | point-in-polygon against OSM boundaries | `calculated` |
| Flood / storm / earthquake extent | uniform radius; no hazard layer exists | `assumed` |

## What the data cannot answer

Reported as `unknown` or `missing_data`, never as zero.

- **Ambulance cover.** No ambulance station is mapped in the extract.
- **Telecom dependencies.** No telecom infrastructure is mapped, so the
  communications chain cannot be modelled at all.
- **The upstream ends of the power and water chains.** No power plant, pumping
  station or treatment works is mapped; propagation starts at the substation
  and water-tower tiers.
- **Hospital capacity.** No bed count is published for any hospital here, so
  displaced demand can be located but not quantified.
- **Duty state of any emergency service.**
- **Ward boundaries.** OSM has state, district and taluk for this area and
  nothing below.
