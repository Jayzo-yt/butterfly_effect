"""The time dimension: how the incident unfolds, and how the city comes back.

An incident does not stop existing at the duration the operator typed. That is
the point at which it is *contained*; the site still has to be cleared, roads
reopened, and services restarted, and each of those takes its own time.

    ACTIVE -> CONTAINED -> CLEARANCE -> NETWORK RESTORED -> SERVICE RESTORED

Two honesty rules run through this module.

Every timeline entry is generated from something the simulation computed — a
measured response time, a network's storage delay, the stated duration — never
invented to fill the chart. If nothing happens at a given moment, nothing is
written there.

And the model refuses to guess a reopening date it cannot know. A tripped
substation is back when the fault is cleared. A burnt-out school is a
reinstatement programme measured in months, and `asset_returns=False` makes the
timeline say exactly that instead of drawing a tidy line back to normal.
"""
from typing import Dict, List

from ..graph.dependencies import NETWORKS
from ..graph.infrastructure import CATEGORIES
from . import provenance as prov
from .incidents import INCIDENT_TYPES

# Road state through the incident.
CLOSED, RESTRICTED, OPEN = "closed", "restricted", "open"
# Facility state.
OPERATIONAL, AFFECTED, UNAVAILABLE, PARTIAL = "operational", "affected", "unavailable", "partial"


def _phase(minute, label, state, detail, basis):
    return {"minute": int(round(minute)), "label": label, "state": state,
            "detail": detail, "basis": basis}


WAVE_RANK = {"low": 0, "moderate": 1, "high": 2, "critical": 3}


def plan(requests, result) -> dict:
    """Recovery stages for the scenario, from the incidents' own profiles."""
    longest = max(r.duration_hours for r in requests) * 60.0
    specs = [INCIDENT_TYPES[r.type] for r in requests]
    clearance = max(s.clearance_min for s in specs)
    returns = all(s.asset_returns for s in specs)
    struck = result.get("direct", [])

    phases = [
        _phase(0, "Active", "active",
               "Incident under way; effects as reported.",
               "Stated start of the incident."),
        _phase(longest, "Contained", "contained",
               "Spread stopped. The site is still occupied and roads stay shut.",
               f"The {longest / 60:g} h duration entered by the operator is read as time to "
               f"containment, not time to normal."),
        _phase(longest + clearance, "Site cleared", "clearance",
               "Crews and equipment withdraw; the cordon is lifted.",
               f"{clearance} min clearance for "
               f"{', '.join(sorted({s.label.lower() for s in specs}))} — a planning "
               f"assumption per incident type, not a local measurement."),
    ]

    # Roads come back when the cordon lifts; that is a network fact the model
    # can stand behind.
    phases.append(_phase(
        longest + clearance, "Roads reopened", "network_restored",
        "Closed segments return to normal capacity and detours are stood down.",
        "Road closures in this model last exactly as long as the cordon."))

    # Services downstream restart on their own network's delay.
    networks_hit = sorted({n.get("via_network") for n in result["cascade"]["nodes"]
                           if n.get("via_network")})
    for key in networks_hit:
        net = NETWORKS[key]
        phases.append(_phase(
            longest + clearance + net.propagation_delay_min,
            f"{net.label} service restored", "service_restored",
            f"Assets downstream regain {net.label.lower()} once supply resumes.",
            f"{net.propagation_delay_min} min restart delay for {net.label.lower()} — the same "
            f"figure used for its propagation, applied in reverse."))

    # The struck asset itself. This is where the model most often would be
    # tempted to invent a date.
    for d in struck:
        if returns:
            phases.append(_phase(
                longest + clearance, f"{d['name']} back in service", "asset_restored",
                "The asset resumes its normal function.",
                "Utility-type incidents restore when the fault clears."))
        else:
            phases.append(_phase(
                longest + clearance, f"{d['name']} remains out of service", "asset_lost",
                "Reinstatement is a works programme, not part of this incident. Plan for "
                "the service to be absent.",
                "This incident type destroys rather than interrupts, so the model states "
                "no reopening date rather than guessing one."))

    phases.sort(key=lambda p: p["minute"])
    horizon = phases[-1]["minute"]
    return {
        "phases": phases,
        "horizon_min": horizon,
        "asset_returns": returns,
        "road_state": [
            {"minute": 0, "state": CLOSED, "detail": "Cordon in force"},
            {"minute": int(longest), "state": RESTRICTED,
             "detail": "Contained; access for crews only"},
            {"minute": int(longest + clearance), "state": OPEN, "detail": "Reopened"},
        ],
        "note": ("Containment comes from the duration entered; everything after it is a "
                 "per-incident-type planning assumption stated beside each stage."),
    }


def timeline(requests, result, recovery: dict) -> List[dict]:
    """The whole sequence, in order, each entry earned by a computed value."""
    events: List[dict] = []

    def add(minute, label, detail, basis, level="low"):
        events.append({"minute": max(0, int(round(minute))), "label": label,
                       "detail": detail, "basis": basis, "level": level})

    incidents = result["incidents"]
    add(0, "Incident reported",
        "; ".join(f"{i['label']} at {i['target']['name']}" for i in incidents),
        "Stated start of the simulation.", "critical")

    # Emergency response: three separate moments, not one number.
    for d in result["emergency"].get("dispatch", []):
        if not d.get("available"):
            add(0, f"No {d['category_label'].lower()} mapped",
                d.get("note", ""), "Data gap, not a delay.", "high")
            continue
        if d.get("unreachable"):
            add(0, f"{d['category_label']} has no route",
                d.get("note", ""), "No path exists on the disrupted network.", "critical")
            continue
        overhead = d.get("overhead_min") or 0
        if overhead:
            add(overhead, f"{d['category_label']} mobilised",
                f"{d['station']} rolling after call handling and turnout.",
                d["response"]["basis"], "moderate")
        low, high = d.get("response_low_min"), d.get("response_high_min")
        if low is not None:
            add(low, f"{d['category_label']} on scene",
                f"{d['station']} — {low:g}–{high:g} min from the call, of which "
                f"{d['travel_min']:g} min is road travel.",
                "Response range: measured travel time plus stated overheads, "
                "upper bound allowing for congestion.", "high")

    # Traffic only when a detour was actually measured.
    onset = max((i["onset_min"] for i in incidents), default=0)
    net = result.get("network") or {}
    if net.get("added_travel_min"):
        add(max(onset, 10), "Traffic disruption measurable",
            f"Through traffic takes the detour: +{net['added_travel_min']:.1f} min between the "
            f"segment's own endpoints, across {net.get('diverted_count', 0)} adjacent roads.",
            "Shortest path between the closed segment's endpoints on the disrupted network.",
            "moderate")

    # Evacuation, where the incident type calls for one.
    for site in result.get("evacuation", {}).get("sites", []):
        best = next((d for d in site["destinations"]
                     if d.get("reachable") and not d.get("in_cordon")), None)
        detail = (f"~{site['people']:,} occupants to {best['name']}, {best['walk_min']:g} min on "
                  f"foot" if best and site.get("people") and best.get("walk_min") is not None
                  else site.get("note") or "Occupants moved clear of the cordon.")
        add(site.get("start_min", 10), f"Evacuation of {site['name']}", detail,
            "Assembly points are mapped open ground; walking times use a "
            f"{site.get('walk_speed_kmh', 4.5)} km/h assumption.", "high")

    # Cascade waves, grouped — one line per wave, not one per asset.
    waves: Dict[tuple, list] = {}
    for node in result["cascade"]["nodes"]:
        if node["round"] == 0:
            continue
        network = node.get("via_network")
        delay = NETWORKS[network].propagation_delay_min if network in NETWORKS else 15
        waves.setdefault((max(onset, delay * node["round"]), network), []).append(node)

    for (minute, network), group in waves.items():
        # One line per wave, graded by the worst asset in it. Keying on level as
        # well split a single wave into two entries at the same minute.
        level = max((n["level"] for n in group), key=lambda x: WAVE_RANK.get(x, 0))
        label = NETWORKS[network].label.lower() if network in NETWORKS else "service"
        group.sort(key=lambda n: -(CATEGORIES[n["category"]].criticality
                                   if n["category"] in CATEGORIES else 0))
        names = ", ".join(n["name"] for n in group[:3])
        more = f" and {len(group) - 3} more" if len(group) > 3 else ""
        add(minute, f"{len(group)} asset{'s' if len(group) > 1 else ''} lose {label} supply",
            f"{names}{more}.",
            f"Propagation round over the dependency model, at that network's "
            f"{NETWORKS[network].propagation_delay_min if network in NETWORKS else 15} min delay."
            if network in NETWORKS else "Dependency propagation.", level)

    # Recovery stages.
    for phase in recovery["phases"]:
        if phase["state"] == "active":
            continue
        add(phase["minute"], phase["label"], phase["detail"], phase["basis"],
            "critical" if phase["state"] == "asset_lost" else "low")

    events.sort(key=lambda e: e["minute"])
    return events


def demo():
    from types import SimpleNamespace
    requests = [SimpleNamespace(type="fire", duration_hours=2)]
    result = {
        "incidents": [{"label": "School fire", "target": {"name": "Toy School"}, "onset_min": 0}],
        "direct": [{"name": "Toy School"}],
        "cascade": {"nodes": []},
        "emergency": {"dispatch": [{
            "available": True, "unreachable": False, "category_label": "Fire station",
            "station": "Toy Fire Station", "travel_min": 4.0, "overhead_min": 3.0,
            "response_low_min": 7.0, "response_high_min": 8.2,
            "response": {"basis": "planning assumption"},
        }]},
        "network": {"added_travel_min": 0},
        "evacuation": {"sites": []},
    }
    rec = plan(requests, result)
    states = [p["state"] for p in rec["phases"]]
    assert "contained" in states and "clearance" in states
    assert "asset_lost" in states, "a fire must not invent a reopening date"
    assert rec["horizon_min"] > 120, "recovery has to run past containment"

    line = timeline(requests, result, rec)
    minutes = [e["minute"] for e in line]
    assert minutes == sorted(minutes)
    assert any("on scene" in e["label"] for e in line)
    assert all(e["basis"] for e in line), "every entry must say where it came from"
    print(f"ok — {len(rec['phases'])} recovery phases, {len(line)} timeline entries, "
          f"horizon {rec['horizon_min']} min")


if __name__ == "__main__":
    demo()
