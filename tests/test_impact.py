"""Impact engine tests, including the four scenarios the spec requires.

The last one matters most: a disruption with nothing around it must return
zero *with an explanation*, proving the other numbers are computed rather
than manufactured to populate the screen.
"""
import json
import math

from src.analysis.impact import Disruption, analyze, snap_facilities
from src.graph.builder import build_toy_city


def facilities_from_toy(city):
    """The toy city's own service nodes, as facilities."""
    mapping = {"hospital": "hospital", "fire_station": "fire_station"}
    out = []
    for n, d in city.G_road.nodes(data=True):
        if d["type"] in mapping:
            out.append({"id": n, "name": f"Toy {d['type']} {n}", "category": mapping[d["type"]],
                        "lat": d["geo"][0], "lon": d["geo"][1], "address": "", "capacity": None})
    return snap_facilities(city, out)


def run(kind, severity, hours, edge=("J2", "J3"), radius=None):
    city = build_toy_city()
    facilities = facilities_from_toy(city)
    return analyze(city, facilities,
                   Disruption(kind=kind, severity=severity, duration_hours=hours,
                              edge=edge, radius_m=radius))


def test_1_accident_high_two_hours_reports_real_impact():
    """H1's only road link is H1-J3, so an accident there has no alternative
    route and must register. This is the case that used to report +0.0000."""
    r = run("accident", "high", 2, edge=("H1", "J3"))
    assert r["disruption"]["label"] == "Road accident"
    assert r["disruption"]["segments_affected"] >= 1
    assert r["summary"]["affected_total"] >= 1
    assert any(f["added_min"] > 0 for f in r["facilities"])


def test_accident_with_an_equal_alternative_route_correctly_reports_zero():
    """J2-J3 has an equal-cost detour via J7-J8, so rerouting costs nothing
    and zero is the right answer — reported with an explanation rather than a
    bare 0. Proves the engine measures rather than manufactures."""
    r = run("accident", "high", 2, edge=("J2", "J3"))
    assert r["summary"]["affected_total"] == 0
    assert r["summary"]["explanation"]


def test_2_closure_critical_six_hours_is_worse_than_accident():
    accident = run("accident", "high", 2)
    closure = run("closure", "critical", 6)
    assert closure["disruption"]["radius_m"] > accident["disruption"]["radius_m"]
    worst_closure = max((f["score"] for f in closure["facilities"]), default=0)
    worst_accident = max((f["score"] for f in accident["facilities"]), default=0)
    assert worst_closure >= worst_accident


def test_3_flooding_spreads_beyond_the_selected_segment():
    """Flooding is spatial: it degrades other segments in the zone, so it must
    affect strictly more road than a single-segment accident."""
    flood = run("flooding", "high", 12)
    accident = run("accident", "high", 12)
    assert flood["disruption"]["segments_affected"] > accident["disruption"]["segments_affected"]


def test_4_isolated_disruption_returns_zero_with_an_explanation():
    """A disruption where nothing is reachable-affected must say zero and say
    why — not fabricate impact to look populated."""
    city = build_toy_city()
    # A facility far outside the toy city, so nothing is near the incident.
    remote = snap_facilities(city, [{
        "id": "remote", "name": "Remote clinic", "category": "clinic",
        "lat": 13.30, "lon": 74.70, "address": "", "capacity": None,
    }], max_snap_m=50)
    r = analyze(city, remote, Disruption(kind="accident", severity="low",
                                         duration_hours=0.25, edge=("J9", "J10")))
    assert r["summary"]["affected_total"] == 0
    assert r["summary"]["explanation"]
    assert "zone" in r["summary"]["explanation"].lower()


def test_network_impact_is_reported_even_when_no_facility_is_affected():
    """A closure in a grid often changes nobody's access — traffic goes round
    the block. Reporting only facility impact then reads as "no effect",
    which is useless to a traffic operator. The detour is real regardless."""
    r = run("closure", "critical", 6, edge=("J2", "J3"))
    assert r["summary"]["affected_total"] == 0
    net = r["network"]
    assert net["added_travel_min"] > 0, "a closure must cost a detour"
    assert net["diverted_count"] > 0, "traffic has to go somewhere"


def test_cut_off_facility_is_flagged_not_reported_as_infinite_minutes():
    """Blocking sets edge weight to infinity rather than deleting the segment,
    so an unreachable node returns an infinite distance instead of being
    absent. That used to surface in the UI as '+inf min'."""
    city = build_toy_city()
    facilities = facilities_from_toy(city)
    # H1's only link is H1-J3; severing it strands the hospital.
    r = analyze(city, facilities,
                Disruption(kind="closure", severity="critical", duration_hours=6,
                           edge=("H1", "J3")))
    stranded = [f for f in r["facilities"] if f["cut_off"]]
    assert stranded, "severing a hospital's only road must flag it cut off"
    for f in r["facilities"]:
        assert f["added_min"] != float("inf")
        assert "inf" not in f["reason"].lower()


def test_report_is_json_serialisable_even_when_a_facility_is_unreachable():
    """An unreachable facility has an infinite travel time internally, and
    JSON has no infinity. Letting it through raised 'Out of range float
    values are not JSON compliant' — a 500 on a valid analysis."""
    city = build_toy_city()
    facilities = facilities_from_toy(city)
    r = analyze(city, facilities,
                Disruption(kind="closure", severity="critical", duration_hours=24,
                           edge=("H1", "J3")))
    json.dumps(r)  # raises if any infinity survived
    for f in r["facilities"]:
        for key in ("added_min", "score", "baseline_min", "after_min", "distance_m"):
            assert f[key] is None or math.isfinite(f[key]), f"{key} not JSON-safe"


def test_severity_and_duration_change_the_radius():
    small = run("accident", "low", 0.25)
    large = run("accident", "critical", 24)
    assert large["disruption"]["radius_m"] > small["disruption"]["radius_m"]


def test_operator_can_override_the_radius():
    r = run("accident", "high", 2, radius=2500)
    assert r["disruption"]["radius_m"] == 2500
    assert r["disruption"]["radius_overridden"] is True


def test_population_is_labelled_as_an_estimate():
    r = run("accident", "high", 2)
    assert r["population"]["is_estimate"] is True
    assert "estimate" in r["population"]["basis"].lower() or "building" in r["population"]["basis"].lower()


def test_every_affected_facility_carries_a_reason():
    r = run("closure", "critical", 6)
    for f in r["facilities"]:
        assert f["reason"], f"{f['name']} has no explanation"
        assert f["level"] in {"critical", "high", "moderate", "low", "none"}


if __name__ == "__main__":
    test_1_accident_high_two_hours_reports_real_impact()
    test_2_closure_critical_six_hours_is_worse_than_accident()
    test_3_flooding_spreads_beyond_the_selected_segment()
    test_4_isolated_disruption_returns_zero_with_an_explanation()
    print("ok")
