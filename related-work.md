# Related Work — Existing Projects and Research

Survey of prior art for the Cascading Failure simulator. Two purposes: knowing
what already exists (judges will ask), and citing the literature that backs
the specific design choices in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Closest existing open-source projects

### InfraRisk — the nearest match
**[github.com/srijithbalakrishnan/dreaminsg-integrated-model](https://github.com/srijithbalakrishnan/dreaminsg-integrated-model)** ·
paper: [arXiv:2205.04717](https://arxiv.org/abs/2205.04717)

Python simulation platform for **interdependent water–power–transport
networks** — disaster-induced failures, cascade propagation, and recovery
sequencing. This is the same problem statement as ours, done at research grade.

*How it differs from us:* it wires up real domain engines (WNTR for water
hydraulics, pandapower for power flow) and focuses on **restoration
scheduling** — what order to repair things in. We do outcome-based
**criticality ranking** (what to harden *before* anything breaks) and frame
impact specifically as hospital reachability.

### IN-CORE / pyincore — the heavyweight
**[github.com/IN-CORE](https://github.com/IN-CORE)** · NIST Center of
Excellence for Risk-Based Community Resilience Planning

Models an entire community — buildings, transport, water, power, *plus*
social and economic systems — with hazard fragility curves and probabilistic
risk. Production-scale, backed by NIST/NCSA, requires their web services.

*How it differs:* probabilistic risk-based with fragility curves, vastly
larger scope. Ours is deterministic scenario simulation you can run from a
single `uvicorn` command.

### Others worth knowing
| Project | What it does | Relevance |
|---|---|---|
| [GIScience/Jakarta_Thesis_Klipper](https://github.com/GIScience/Jakarta_Thesis_Klipper) | Flood impact on road network + **healthcare accessibility** | Closest to our hospital-access framing; flood-only, no utility layer |
| [jhenglu/openinfra](https://github.com/jhenglu/openinfra) | Co-simulation of datacenter/power/cooling "infrastructure nexus" | Same interdependency idea, different domain |
| Grid2Op (RTE France) | Power-grid cascade simulation for RL; overload → blackout | Single-layer, but its overload-cascade mechanic is the power-grid analogue of our Mechanism B |
| Deltares Criticality Tool | Road network criticality under flooding | Commercial/GIS equivalent of our ranking output |
| OSMnx / pandana ecosystem | Network extraction + accessibility analysis | The toolchain layer we build on, not a competitor |

**Honest read:** the *concept* is well-trodden — InfraRisk in particular
covers very similar ground. Our defensible differentiators are (a) the
outcome-based leave-one-out ranking presented *against* a centrality
baseline, (b) the deliberately small, runnable, fully-tested footprint, and
(c) the hospital-service framing over raw topology. Claiming novelty on
"multi-layer infrastructure graphs" alone would not survive scrutiny.

---

## 2. Research that backs our design choices

### Why flow redistribution, not percolation (our Mechanism B)

The single most useful citation for us:

- **"Cascading failures in interdependent systems under a flow
  redistribution model"** — [arXiv:1709.01651](https://arxiv.org/abs/1709.01651)
  Argues directly that most cascade literature uses **percolation** models
  where only the largest connected component stays functional — and that this
  "fails to capture the dependencies in systems carrying a flow (e.g. power
  systems, road transportation networks), where cascading failures are often
  triggered by redistribution of flows leading to overloading of lines."
  **This is precisely the argument for our damped-BPR overload loop over
  flood-fill propagation.**

- **"Local impacts on road networks and access to critical locations during
  extreme floods"** — [arXiv:2202.00292](https://arxiv.org/abs/2202.00292)
  Shows the percolation framework is "inadequate" for real flood scenarios:
  "the giant connected component is not relevant," and proposes
  accessibility-of-local-towns measures instead. Backs our choice to score
  impact by hospital reachability rather than component size.

- Related flow-redistribution work: [arXiv:2203.01295](https://arxiv.org/abs/2203.01295)
  (damping/coupling coefficients and robustness), [arXiv:1106.4499](https://arxiv.org/abs/1106.4499)
  (sandpile load cascades in coupled grids).

### Why redundancy degrades rather than hard-fails (our Mechanism A)

- **"The 'weak' interdependence of infrastructure systems produces mixed
  percolation transitions in multilayer networks"** (PMC5794991)
  Critiques models assuming "strong" interdependence — where a node's failure
  makes dependents fail "immediately and completely." Supports our
  `redundancy > 0 → status 0.6` degrade branch over binary failure.

- **"Reducing Cascading Failure Risk by Increasing Infrastructure Network
  Interdependency"** — [arXiv:1410.6836](https://arxiv.org/abs/1410.6836)
  Counterintuitive result: more coupling can *reduce* risk, and standard
  percolation models mis-model real cascade mechanisms. Good "known
  limitations" material.

### Why outcome-based ranking ≠ betweenness centrality (our core thesis)

- **"Comparisons of purely topological model, betweenness based model and
  direct current power flow model to analyze power grid vulnerability"**
  (PubMed 23822479) — the most direct precedent for our thesis test:
  compares topological vs. betweenness vs. physics-based flow models on the
  IEEE 300 grid and finds they only converge above a critical tolerance
  parameter. i.e. **topological ranking and physics-based ranking genuinely
  diverge** — which is exactly what our `W1` result demonstrates.

- Node-resilience vs. centrality comparison (PMC12818785) notes standard
  centrality measures "fail to dynamically capture cascade following node"
  removal.

- Classic attack-vulnerability baselines: Holme et al.,
  [cond-mat/0202410](https://arxiv.org/abs/cond-mat/0202410); flow-weighted
  centrality in intermodal freight, [arXiv:2601.00906](https://arxiv.org/abs/2601.00906).

### Road networks → healthcare access (our service-outcome metric)

- **"Stress-testing Road Networks and Access to Medical Care"** —
  [arXiv:2307.02250](https://arxiv.org/abs/2307.02250)
  Full national stress test (Austria) of road network disruption framed
  around *hospital* accessibility specifically, noting most prior work
  studied whole-network accessibility rather than priority destinations.
  Nearly identical framing to ours, at national scale.

- **"Measuring accessibility to public services and infrastructure criticality
  for disasters risk management"** (PMC9884248) — uses **OSMnx** for network
  extraction and identifies critical road segments per scenario; found 22% of
  Manila's population lost access to higher health services under flooding.
  Methodologically our closest sibling — same toolchain, same output shape.

- **"Robust component: a robustness measure that incorporates access to
  critical facilities under disruptions"** (PMC6731514) — argues the giant
  component is insufficient post-disaster and robustness should be measured
  by access to emergency services. Essentially the argument for our scoring
  function.

- Case studies: Cyclone Idai / Mozambique road criticality + healthcare
  access (PMC9559768); EMS accessibility in NYC
  ([arXiv:2412.04369](https://arxiv.org/abs/2412.04369)); flood disruption of
  EMS access (PubMed 39481560).

### Foundational / survey reading

- **"Catastrophic cascade of failures in interdependent networks"** —
  [arXiv:0907.1182](https://arxiv.org/abs/0907.1182) (Buldyrev et al., Nature
  2010). The canonical interdependent-cascade paper; percolation-based, and
  the thing most later work argues with.
- **"A Survey of Interdependency Models for Critical Infrastructure
  Networks"** — [arXiv:1702.05407](https://arxiv.org/abs/1702.05407)
- Rinaldi-style interdependency taxonomy (physical / geographic / cyber /
  logical) — discussed in PMC11787953.
- Percolation-theory review for multilayer infrastructure —
  [arXiv:2105.12701](https://arxiv.org/abs/2105.12701)
- **"Predicting Cascade Failures in Interdependent Urban Infrastructure
  Networks"** — [arXiv:2503.02890](https://arxiv.org/abs/2503.02890) (2025,
  the I³ model) — most recent conceptual sibling.
- **IIVA** — [arXiv:2212.06894](https://arxiv.org/abs/2212.06894) — vulnerability
  assessment explicitly designed for *incomplete* infrastructure data, which
  is the situation our `assumptions.md` documents.

---

## 3. What to say to judges

1. **Don't claim the concept is new.** Interdependent-infrastructure cascade
   modeling is an established field with production tools (IN-CORE, InfraRisk).
   Saying otherwise invites an easy takedown.
2. **Do claim the specific choices are literature-backed.** The flow-redistribution-
   over-percolation decision ([arXiv:1709.01651](https://arxiv.org/abs/1709.01651))
   and the access-to-critical-facilities robustness measure (PMC6731514) are
   both positions defended in peer-reviewed work — we didn't guess them.
3. **Do claim the outcome-vs-centrality divergence as a demonstrated result**,
   not just an assertion — we have the `W1` case, and PubMed 23822479 shows
   the same divergence holds in power-grid vulnerability analysis.
4. **Known gap:** we don't model probabilistic hazard fragility (IN-CORE does),
   time-dependent traffic (static equilibrium only), or restoration sequencing
   (InfraRisk does). Already listed in ARCHITECTURE.md §10.
