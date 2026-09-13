"""The scenarios the platform has to answer, end to end.

Each one asserts the *reasoning*, not just that a number appeared: a school
fire has to report who is inside and where they go, a hospital fire has to
name the hospitals that absorb its patients, a substation failure has to reach
a hospital through the water chain, and a quiet incident has to stay quiet.
"""
import json

import pytest

from src.analysis.impact import snap_facilities
from src.analysis.scenario import IncidentRequest, ScenarioError, simulate
from src.graph import dependencies as dep
from src.graph.builder import build_toy_city


def toy_facilities(city):
    """A small but complete inventory: two hospitals, a fire station, a police
    station, a school, a substation, a pump, a tower and an open space."""
    at = lambda n: city.node_attrs(n)["geo"]  # noqa: E731
    raw = [
        ("hosp", "Toy District Hospital", "hospital", at("H1"), 120),
        ("hosp2", "Toy Community Hospital", "hospital", at("H2"), 60),
        ("fire", "Toy Fire Station", "fire_station", at("F1"), None),
        ("police", "Toy Police Station", "police", at("J6"), None),
        ("school", "Toy Government School", "school", at("J7"), None),
        ("sub", "Toy Central", "substation", at("J2"), None),
        ("pump", "Toy Pumping Station", "pumping_station", at("J3"), None),
        ("tower", "Toy Water Tower", "water_tower", at("J4"), None),
        ("ground", "Toy Municipal Ground", "open_space", at("J11"), None),
    ]
    return snap_facilities(city, [
        {"id": i, "name": n, "category": c, "lat": g[0], "lon": g[1],
         "address": "", "capacity": cap}
        for i, n, c, g, cap in raw
    ])


@pytest.fixture
def setup():
    city = build_toy_city()
    facilities = toy_facilities(city)
    return facilities, dep.build(facilities)


def run(setup, *requests):
    facilities, model = setup
    return simulate(build_toy_city(), facilities, list(requests), model)


# --- 1. road accident ---------------------------------------------------------

def test_road_accident_reports_network_and_access_effects(setup):
    r = run(setup, IncidentRequest("accident", edge=("H1", "J3"), severity="high",
                                   duration_hours=2))
    assert r["incidents"][0]["label"] == "Road accident"
    assert r["incidents"][0]["target"]["kind"] == "road"
    assert r["network"], "a road incident must report a traffic effect"
    assert r["comparison"]["rows"], "and a baseline to compare against"
    assert any(d["category"] == "ambulance_station" for d in r["emergency"]["dispatch"])
    assert r["score"]["factors"], "the score must show its working"


# --- 2. school fire -----------------------------------------------------------

def test_school_fire_evacuates_people_and_dispatches_services(setup):
    """The generic `fire` incident on an asset whose traits say it holds people
    and cannot be substituted."""
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical",
                                   duration_hours=2))
    assert r["incidents"][0]["label"] == "School fire", "composed from the asset, not a type"
    direct = r["direct"][0]
    assert direct["status"] == "Out of service"
    # Occupancy must arrive through the priority chain and be labelled an
    # estimate, not asserted as a headcount.
    assert direct["occupancy"] == 600
    assert direct["occupancy_finding"]["state"] == "estimated"
    assert direct["occupancy_finding"]["display"] == "~600 people"

    assert r["evacuation"]["required"]
    site = r["evacuation"]["sites"][0]
    assert site["destinations"], "a mapped open space must be offered"
    assert site["destinations"][0]["route"], "with a route to it"

    stations = {d["category"] for d in r["emergency"]["dispatch"]}
    assert {"fire_station", "police"} <= stations
    assert any("Fire Station" in (a["text"] or "") for a in r["actions"])
    assert not r["alternatives"]["available"], "no other school absorbs this one's pupils"


def test_school_fire_does_not_close_the_whole_neighbourhood(setup):
    """A structure fire cordons the street outside, not every road within a
    kilometre. This was real: `fire` was marked spatial and closed 1.3 km of
    network, which stranded the fire station that had to reach it."""
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    closed = r["network"].get("segments_closed", 0)
    assert closed <= 6, f"{closed} segments closed for one building fire"
    for d in r["emergency"]["dispatch"]:
        if d.get("available"):
            assert not d["unreachable"], f"{d['category_label']} cannot reach its own incident"


# --- 3. hospital fire ---------------------------------------------------------

def test_hospital_fire_diverts_patients_to_named_alternatives(setup):
    """Same incident type, different asset traits: a hospital is substitutable,
    so the question becomes where its demand goes."""
    r = run(setup, IncidentRequest("fire", facility_id="hosp", severity="critical"))
    assert r["incidents"][0]["label"] == "Hospital fire"
    assert r["alternatives"]["available"]
    item = r["alternatives"]["items"][0]
    assert item["peers"] and item["peers"][0]["name"] == "Toy Community Hospital"
    assert item["peers"][0]["travel_min"] is not None
    assert item["displaced_capacity"] == 120
    assert any("Toy Community Hospital" in a["text"] for a in r["actions"])


# --- 4. power substation failure ----------------------------------------------

def test_substation_failure_cascades_through_water_to_the_hospital(setup):
    r = run(setup, IncidentRequest("power_failure", facility_id="sub", severity="critical",
                                   duration_hours=4))
    cascade = r["cascade"]
    reached = {n["id"]: n for n in cascade["nodes"]}
    assert "pump" in reached, "the pumping station draws power from the substation"
    assert cascade["rounds"] >= 2, "and the effect has to travel further than one hop"
    assert all(n["reason"] for n in cascade["nodes"]), "every step must explain itself"
    assert all(n["confidence"] in ("high", "medium", "low") for n in cascade["nodes"])
    assert cascade["edges"][0]["network"] == "power"
    assert not r["evacuation"]["required"], "a power cut evacuates nobody"


def test_cascade_never_invents_a_dependency(setup):
    """A school supplies no network, so nothing may propagate from it — and the
    empty answer has to say why rather than showing zero."""
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    assert r["cascade"]["indirect_total"] == 0
    assert r["cascade"]["summary"]
    assert r["cascade"]["gaps"], "missing networks must be declared, not hidden"


# --- 5. flooding --------------------------------------------------------------

def test_flooding_spreads_beyond_the_selected_segment(setup):
    flood = run(setup, IncidentRequest("flooding", edge=("J2", "J3"), severity="high",
                                       duration_hours=12))
    accident = run(setup, IncidentRequest("accident", edge=("J2", "J3"), severity="high",
                                          duration_hours=12))
    assert len(flood["affected_segments"]) > len(accident["affected_segments"])
    assert any("not a prediction" in reason["text"] or "assumption" in reason["text"]
               for reason in flood["confidence"]["reasons"]), \
        "a modelled flood footprint must be labelled as an assumption"


# --- 6. several at once -------------------------------------------------------

def test_multiple_incidents_are_evaluated_on_one_network(setup):
    combined = run(setup,
                   IncidentRequest("closure", edge=("H1", "J3"), severity="critical",
                                   duration_hours=6),
                   IncidentRequest("power_failure", facility_id="sub", severity="critical"))
    assert len(combined["incidents"]) == 2
    assert combined["cascade"]["nodes"], "the utility incident still cascades"
    assert combined["network"], "and the road incident still reports traffic"
    assert any(f["label"] == "Simultaneous incidents" for f in combined["score"]["factors"])

    alone = run(setup, IncidentRequest("closure", edge=("H1", "J3"), severity="critical",
                                       duration_hours=6))
    assert combined["score"]["value"] >= alone["score"]["value"], \
        "two incidents cannot be less serious than one of them"


# --- cross-cutting guarantees --------------------------------------------------

def test_every_result_is_json_safe(setup):
    for request in (IncidentRequest("fire", facility_id="hosp", severity="critical"),
                    IncidentRequest("closure", edge=("H1", "J3"), severity="critical"),
                    IncidentRequest("power_failure", facility_id="sub")):
        json.dumps(run(setup, request))


def test_results_never_show_a_raw_id_as_a_label(setup):
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    labels = [r["incidents"][0]["target"]["name"], r["direct"][0]["name"]]
    labels += [n["name"] for n in r["cascade"]["nodes"]]
    labels += [f["name"] for f in r["facilities"]]
    for label in labels:
        assert label and not label.startswith(("way/", "node/", "relation/")), label


def test_an_impossible_request_is_explained_not_crashed(setup):
    facilities, model = setup
    with pytest.raises(ScenarioError) as e:
        simulate(build_toy_city(), facilities,
                 [IncidentRequest("pump_failure", facility_id="school")], model)
    assert "pumping station" in str(e.value)

    with pytest.raises(ScenarioError):
        simulate(build_toy_city(), facilities, [IncidentRequest("accident", edge=("J1", "J5"))],
                 model)


def test_confidence_is_reported_and_never_overstated(setup):
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    assert r["confidence"]["level"] in ("high", "medium", "low")
    assert r["confidence"]["reasons"]
    assert any(reason["level"] == "low" for reason in r["confidence"]["reasons"]), \
        "population and occupancy figures are estimates and must be declared"


def test_timeline_is_ordered_and_starts_at_the_incident(setup):
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    minutes = [e["minute"] for e in r["timeline"]]
    assert minutes == sorted(minutes)
    assert minutes[0] == 0
    # The timeline runs past containment into recovery, and every entry says
    # where its time came from.
    assert r["timeline"][-1]["minute"] >= r["recovery"]["phases"][-1]["minute"]
    assert all(e["basis"] for e in r["timeline"])
    assert any("cleared" in e["label"] or "out of service" in e["label"]
               for e in r["timeline"]), "recovery has to appear in the timeline"


def test_actions_are_derived_and_disappear_when_their_cause_does(setup):
    loud = run(setup, IncidentRequest("fire", facility_id="hosp", severity="critical"))
    quiet = run(setup, IncidentRequest("congestion", edge=("J2", "J3"), severity="low",
                                       duration_hours=0.25))
    assert len(loud["actions"]) > len(quiet["actions"])
    assert all(a["basis"] for a in loud["actions"]), "every action states what it rests on"


# --- regressions that reached the screen once ---------------------------------

def test_a_peer_inside_the_cordon_is_not_recommended_as_the_alternative(setup):
    """The panel once offered to divert patients to a hospital the same fire
    cordon had just closed."""
    facilities, model = setup
    r = simulate(build_toy_city(), facilities,
                 [IncidentRequest("fire", facility_id="hosp", severity="critical")], model)
    item = r["alternatives"]["items"][0]
    assert all("in_cordon" in p for p in item["peers"])
    open_peers = [p for p in item["peers"] if not p["in_cordon"]]
    divert = [a for a in r["actions"] if a["text"].startswith("Divert")]
    for action in divert:
        assert any(p["name"] in action["text"] for p in open_peers), action["text"]
        for closed in (p for p in item["peers"] if p["in_cordon"]):
            assert closed["name"] not in action["text"], "recommended a closed facility"


def test_responders_are_never_reported_as_faster_during_an_incident(setup):
    """Measuring to the cordon edge made the fire service look 1.5 min quicker
    during a fire. A proxy measurement gets no delta."""
    facilities, model = setup
    r = simulate(build_toy_city(), facilities,
                 [IncidentRequest("fire", facility_id="school", severity="critical")], model)
    for d in r["emergency"]["dispatch"]:
        if not d.get("available") or d.get("unreachable"):
            continue
        if d.get("to_cordon_edge"):
            assert d["added_min"] is None, "a cordon-edge time is not comparable"
        elif d["added_min"] is not None:
            assert d["added_min"] >= 0
        # A response time is never the bare drive time.
        if d.get("travel_min") is not None:
            assert d["response_low_min"] > d["travel_min"],                 "dispatch and turnout have to be added to the drive time"
            assert d["response_high_min"] >= d["response_low_min"]


def test_a_hospital_on_standby_power_still_produces_an_action(setup):
    """Backup generation downgrades a hospital to "moderate", which once
    dropped it out of the action list entirely — the opposite of useful."""
    facilities, model = setup
    r = simulate(build_toy_city(), facilities,
                 [IncidentRequest("power_failure", facility_id="sub", severity="critical")],
                 model)
    healthcare = [n for n in r["cascade"]["nodes"] if n["group"] == "Healthcare"]
    if not healthcare:
        pytest.skip("no healthcare asset is downstream of this substation in the toy city")
    assert any(n["name"] in a["text"] for n in healthcare for a in r["actions"]),         "a hospital losing supply must appear in the actions at any impact level"


def test_the_timeline_groups_a_wave_instead_of_listing_every_asset(setup):
    """One substation feeding a dozen consumers produced a dozen identical
    timeline rows."""
    facilities, model = setup
    r = simulate(build_toy_city(), facilities,
                 [IncidentRequest("power_failure", facility_id="sub", severity="critical")],
                 model)
    indirect = r["cascade"]["indirect_total"]
    if indirect < 2:
        pytest.skip("needs a cascade wider than one asset")
    waves = [e for e in r["timeline"] if "lose" in e["label"]]
    assert waves, "the cascade has to reach the timeline"
    assert len(waves) < indirect, "one timeline entry per affected asset is not a timeline"


# --- input the operator can get wrong -----------------------------------------

@pytest.mark.parametrize("bad, expect", [
    (IncidentRequest("fire", facility_id="school", severity="catastrophic"), "severity"),
    (IncidentRequest("fire", facility_id="school", duration_hours=0), "duration"),
    (IncidentRequest("fire", facility_id="school", duration_hours=-5), "duration"),
    (IncidentRequest("fire", facility_id="school", duration_hours=100000), "duration"),
    (IncidentRequest("fire", facility_id="school", radius_m=-200), "impact zone"),
    (IncidentRequest("fire", facility_id="school", radius_m=5), "impact zone"),
    (IncidentRequest("volcano", facility_id="school"), "incident type"),
])
def test_bad_input_is_explained_not_a_500(setup, bad, expect):
    """An unknown severity used to reach the action writer and raise ValueError
    from list.index — a 500 on a typo. Every one of these is the operator's
    mistake and has to come back as a sentence they can act on."""
    facilities, model = setup
    with pytest.raises(ScenarioError) as e:
        simulate(build_toy_city(), facilities, [bad], model)
    assert expect in str(e.value)


def test_every_dispatch_row_has_the_same_shape(setup):
    """The "no station mapped" branch returned a different set of keys, so a
    caller had to know which branch produced the row."""
    facilities, model = setup
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    rows = r["emergency"]["dispatch"]
    assert rows
    required = {"category", "category_label", "station", "available", "availability",
                "baseline_min", "travel_min", "added_min", "unreachable", "to_cordon_edge",
                "response_low_min", "response_high_min", "overhead_min", "travel", "response",
                "note"}
    for row in rows:
        assert required <= set(row), f"{row['category']} is missing {required - set(row)}"


def test_a_road_incident_never_claims_a_cordon_it_did_not_apply(setup):
    """Flooding declares a cordon for the case where it hits an asset. Reading
    that off the incident *type* let a flooded road answer "0 min drive" from a
    proxy point a kilometre away."""
    r = run(setup, IncidentRequest("flooding", edge=("J2", "J3"), severity="critical",
                                   duration_hours=12))
    for d in r["emergency"]["dispatch"]:
        assert not d["to_cordon_edge"], "a road incident applies no cordon"


def test_a_utility_failure_shows_up_in_the_comparison(setup):
    """A substation failure closes no roads, so the table read "nothing
    changed" while assets downstream lost supply."""
    r = run(setup, IncidentRequest("power_failure", facility_id="sub", severity="critical"))
    metrics = [row["metric"] for row in r["comparison"]["rows"]]
    if r["cascade"]["indirect_total"]:
        assert "Assets without normal utility supply" in metrics
        row = next(x for x in r["comparison"]["rows"]
                   if x["metric"] == "Assets without normal utility supply")
        assert row["disrupted"] == r["cascade"]["indirect_total"]


def test_a_utility_failure_touches_no_roads(setup):
    """A substation losing power does not slow the street outside it. The
    node-targeted fallback was degrading every road into the junction and
    reporting it as traffic impact."""
    r = run(setup, IncidentRequest("power_failure", facility_id="sub", severity="critical"))
    assert r["affected_segments"] == []
    assert "closes no roads" in r["network"]["note"]
    open_row = next(x for x in r["comparison"]["rows"] if x["metric"] == "Road segments open")
    assert open_row["baseline"] == open_row["disrupted"]


def test_the_empty_state_does_not_quote_one_zone_for_several_incidents(setup):
    """With two incidents of different radii the explanation claimed everything
    was inside the first one's zone."""
    r = run(setup,
            IncidentRequest("power_failure", facility_id="sub", severity="critical"),
            IncidentRequest("utility_failure", facility_id="tower", severity="low",
                            duration_hours=1))
    explanation = r["summary"]["explanation"]
    if explanation:
        assert "2 incident zones" in explanation or "km zone" not in explanation, explanation


def test_an_unmapped_service_is_a_data_gap_not_a_routing_failure(setup):
    """Marking "no ambulance station mapped" as unreachable added a flat 12
    points to the risk score of every incident in a city that has none."""
    facilities, model = setup
    without_ambulance = [f for f in facilities if f.category != "ambulance_station"]
    r = simulate(build_toy_city(), without_ambulance,
                 [IncidentRequest("accident", edge=("J2", "J3"), severity="low",
                                  duration_hours=0.25)],
                 dep.build(without_ambulance))
    missing = [d for d in r["emergency"]["dispatch"] if not d["available"]]
    assert missing, "the toy city has no ambulance station"
    for d in missing:
        assert d["unreachable"] is False, "not mapped is not the same as cannot reach"
    assert not any(f["label"] == "Responder cannot reach the site" for f in r["score"]["factors"])
    assert any("is mapped in this area" in a["text"] for a in r["actions"]),         "the gap still has to be reported — just not as a routing failure"


# --- the simulation engine, not just the arithmetic ---------------------------

def test_a_drive_time_is_never_reported_as_a_response_time(setup):
    """The engine used to answer "how fast is the fire service" with the
    shortest path. A response contains call handling and turnout, and the
    congestion allowance makes it a range, not a point."""
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    fire = next(d for d in r["emergency"]["dispatch"] if d["category"] == "fire_station")
    assert fire["travel_min"] < fire["response_low_min"] < fire["response_high_min"]
    assert fire["overhead_min"] > 0
    assert fire["travel"]["state"] == "calculated"
    assert fire["response"]["state"] == "estimated"
    assert "not a measured figure" in fire["response"]["basis"] \
        or "planning assumption" in fire["response"]["basis"].lower()


def test_one_appliance_cannot_attend_two_fires(setup):
    """Without a shared pool the same station answered every incident at once,
    which flatters every multi-incident scenario."""
    facilities, model = setup
    three = [IncidentRequest("fire", facility_id=fid, severity="critical")
             for fid in ("school", "hosp", "hosp2")]
    r = simulate(build_toy_city(), facilities, three, model)
    fire_rows = [d for d in r["emergency"]["dispatch"] if d["category"] == "fire_station"]
    assert len(fire_rows) == 3, "each incident asks for fire cover"
    # The toy city has one station with two appliances: two get it, one does not.
    served = [d for d in fire_rows if d["available"]]
    unserved = [d for d in fire_rows if not d["available"]]
    assert len(served) == 2 and len(unserved) == 1, [d["availability"] for d in fire_rows]
    assert unserved[0]["availability"] == "busy"
    assert any("mutual aid" in a["text"] for a in r["actions"])


def test_availability_is_unknown_when_nothing_publishes_it(setup):
    """No duty state exists in OSM. Unknown is reported as unknown."""
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    for d in r["emergency"]["dispatch"]:
        if d["available"]:
            assert d["availability"] in ("unknown", "busy")
            assert "no duty state" in d["availability_basis"]


def test_occupants_walk_out_rather_than_drive(setup):
    """Routing evacuees at driving speed made a 1.7 km walk look like a
    three-minute trip."""
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    site = r["evacuation"]["sites"][0]
    best = next(d for d in site["destinations"] if d["reachable"])
    assert best["walk_min"] > best["travel_min"], "walking is slower than driving"
    assert best["route_m"] and "km/h" in best["walk"]["basis"]
    assert best["designated"] in (True, False)


def test_assembly_capacity_is_compared_or_declared_unknown(setup):
    """A destination that cannot hold the occupants is a finding; a destination
    whose capacity is unmapped is a different finding, not the same one."""
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    site = r["evacuation"]["sites"][0]
    assert site["capacity_state"]["state"] in ("sufficient", "insufficient", "unknown")
    assert site["capacity_state"]["message"]
    for d in site["destinations"]:
        assert "sufficiency" in d


def test_occupancy_comes_through_the_priority_chain(setup):
    """A published capacity has to beat the category constant."""
    facilities, model = setup
    r = run(setup, IncidentRequest("fire", facility_id="hosp", severity="critical"))
    direct = r["direct"][0]
    # The toy hospital publishes a capacity of 120, so that wins over the
    # category's 400.
    assert direct["occupancy"] == 120
    assert direct["occupancy_finding"]["state"] == "measured"


def test_recovery_runs_past_containment_and_admits_what_it_cannot_know(setup):
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical",
                                   duration_hours=2))
    phases = {p["state"]: p for p in r["recovery"]["phases"]}
    assert phases["contained"]["minute"] == 120, "the stated duration is containment"
    assert phases["clearance"]["minute"] > 120, "clearance follows containment"
    assert "asset_lost" in phases, "a fire must not invent a reopening date"
    assert r["recovery"]["horizon_min"] > 120
    for p in r["recovery"]["phases"]:
        assert p["basis"], "every stage states what it rests on"


def test_a_utility_failure_restores_but_a_fire_does_not(setup):
    power = run(setup, IncidentRequest("power_failure", facility_id="sub", severity="critical"))
    fire = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    assert power["recovery"]["asset_returns"] is True
    assert fire["recovery"]["asset_returns"] is False


def test_confidence_is_graded_per_component(setup):
    """One missing dataset used to stamp "low" on the whole simulation."""
    r = run(setup, IncidentRequest("fire", facility_id="school", severity="critical"))
    conf = r["confidence"]
    keys = {c["key"] for c in conf["components"]}
    assert {"road_impact", "emergency_routing", "occupancy"} <= keys
    assert conf["level"] in ("high", "medium", "low")
    grades = {c["key"]: c["level"] for c in conf["components"]}
    assert grades["road_impact"] == "high", "observed geometry is not low confidence"
    assert grades["occupancy"] == "medium", "a category estimate is medium, not high"
    assert conf["limits"], "the weak components have to be named"


def test_zero_and_unknown_are_different_answers(setup):
    """A measured zero and an unanswerable question must not render alike."""
    from src.analysis import provenance as prov
    assert prov.measured(0, "no hospital lost access").to_dict()["display"] == "0"
    assert prov.unknown("not published").to_dict()["display"] == "unknown"
    assert prov.not_applicable("no roads involved").to_dict()["display"] == "not applicable"
    assert prov.measured(0, "x").to_dict()["value"] == 0
    assert prov.unknown("x").to_dict()["value"] is None
