"""Run the six reference scenarios against the real city extract and print a
readable report.

    python scripts/run_scenarios.py
    python scripts/run_scenarios.py --json out.json

This is the end-to-end check that the platform answers the questions it claims
to: what is affected, how badly, why, what happens next, which services are
hit, what the alternatives are, how many people are involved, how it compares
with normal conditions, and what to do now. Every figure printed here comes
from the same engine the API serves — nothing is computed twice or differently.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.analysis.impact import snap_facilities  # noqa: E402
from src.analysis.scenario import IncidentRequest, ScenarioError, simulate  # noqa: E402
from src.graph import areas as areas_module  # noqa: E402
from src.graph import dependencies as dependencies_module  # noqa: E402
from src.graph.builder import build_city  # noqa: E402

CITY = "data/city/manipal.json"


def pick(facilities, category, near, contains=None):
    """The *named* asset of this category closest to the urban core.

    Ordering by whatever OSM listed first picked a rural school 15 minutes
    from the only fire station, which is a true result but a poor
    demonstration of a city model.
    """
    from src.analysis.impact import meters_between
    options = [f for f in facilities if f.category == category and f.name
               and (not contains or contains.lower() in f.name.lower())]
    if not options:
        return None
    return min(options, key=lambda f: meters_between((f.lat, f.lon), near))


def feeds_most(facilities, category, model):
    """The asset of this category that the most others depend on.

    Picking the substation nearest the town centre found one that feeds two
    schools — a true result, but it shows none of the propagation the model is
    for. The interesting demonstration is the supplier with the deepest
    downstream, which is also the one an operator would most want to ask about.
    """
    from collections import Counter
    load = Counter(link.source for link in model.links)
    options = [f for f in facilities if f.category == category and load[f.id]]
    if not options:
        return None
    # Named first: the largest downstream here belongs to an unnamed substation,
    # and "Unnamed electrical substation near KRCL" is a worse thing to read in a
    # report than the named one two thirds its size.
    return max(options, key=lambda f: (bool(f.name), load[f.id]))


def urban_core(facilities):
    """Where the assets cluster — a good stand-in for the town centre."""
    lat = sum(f.lat for f in facilities) / len(facilities)
    lon = sum(f.lon for f in facilities) / len(facilities)
    return (lat, lon)


def busiest_road(city):
    """A named trunk/primary segment: the kind of road an operator would
    actually be reporting an incident on."""
    best = None
    for u, v, data in city.G_road.edges(data=True):
        if data.get("road_class") not in ("trunk", "primary", "secondary"):
            continue
        if not (data.get("name") or "").strip():
            continue
        score = data.get("max_capacity", 0)
        if best is None or score > best[0]:
            best = (score, (u, v))
    return best[1] if best else None


def show(title, result):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    lead = result["incidents"][0]
    print("  " + " + ".join(x["label"] + " at " + x["target"]["name"]
                            for x in result["incidents"]))
    print(f"  severity {lead['severity']}, {lead['duration_hours']} h, "
          f"zone {lead['radius_m']} m ({lead['radius_basis']})")
    if result["areas"]["areas"]:
        print("  areas: " + ", ".join(f"{a['name']} ({a['level_label']})"
                                      for a in result["areas"]["areas"]))

    score = result["score"]
    print(f"\n  OVERALL RISK: {score['level'].upper()} ({score['value']}/100) "
          f"· confidence {result['confidence']['level']}")
    for f in score["factors"][:5]:
        print(f"    +{f['points']:<5} {f['label']:<34} {f['detail']}")

    for d in result["direct"]:
        occupancy = f", ~{d['occupancy']} people present" if d["occupancy"] else ""
        print(f"\n  DIRECT: {d['name']} — {d['status']}, {d['function_lost_pct']}% of "
              f"{d['service_lost'] or 'function'} lost{occupancy}")

    summary = result["summary"]
    print(f"\n  ACCESS: {summary['affected_total']} facilities with changed access "
          f"{summary['by_level'] or ''}")
    if not summary["affected_total"]:
        print(f"    {summary['explanation']}")
    for f in result["facilities"][:4]:
        if f["level"] == "none":
            continue
        state = "cut off" if f["cut_off"] else f"+{f['added_min']} min"
        print(f"    {f['level']:<9} {f['name'][:44]:<46} {state}")

    print("\n  EMERGENCY RESPONSE:")
    for d in result["emergency"]["dispatch"]:
        if not d["available"]:
            print(f"    {d['category_label']:<20} none mapped in this extract")
            continue
        if d["unreachable"]:
            print(f"    {d['category_label']:<20} NO ROUTE")
            continue
        flag = "" if d["availability"] == "available" else f"  [{d['availability']}]"
        print(f"    {d['category_label']:<20} {(d['station'] or '')[:28]:<30} "
              f"on scene ~{d['response_low_min']}-{d['response_high_min']} min "
              f"({d['travel_min']} travel + {d['overhead_min']} overhead){flag}")
        if d.get("note"):
            print(f"    {'':<20} {d['note'][:92]}")

    cascade = result["cascade"]
    print(f"\n  DEPENDENCY CASCADE: {cascade['summary']}")
    for n in cascade["nodes"]:
        if n["round"] == 0:
            continue
        print(f"    round {n['round']} {n['level']:<9} {n['name'][:38]:<40} "
              f"via {n['via_network_label']} from {n['via_source_name'][:22]} "
              f"({n['confidence']})")

    for item in result["alternatives"]["items"]:
        print(f"\n  ALTERNATIVES TO {item['name']}:")
        for p in item["peers"]:
            state = ("inside cordon" if p.get("in_cordon")
                     else f"{p['travel_min']} min" if p["reachable"] else "no route")
            print(f"    {p['name'][:44]:<46} {state}")

    for site in result["evacuation"].get("sites", []):
        print(f"\n  EVACUATION of {site['name']} ({site['people_finding']['display']}):")
        for d in site["destinations"][:3]:
            if d.get("in_cordon"):
                state = "inside cordon"
            elif d["reachable"]:
                state = f"{d['walk_min']} min walk, {d.get('route_m') or d['distance_m']} m"
            else:
                state = "no route"
            cap = f"holds ~{d['capacity']:,}" if d.get("capacity") else "capacity unknown"
            kind = "designated shelter" if d.get("designated") else "open ground"
            print(f"    {d['name'][:38]:<40} {state:<28} {cap}, {kind}")
        if not site["destinations"]:
            print(f"    {site['note']}")
        if site.get("capacity_state", {}).get("message"):
            print(f"    -> {site['capacity_state']['message']}")


    print("\n  BASELINE vs DISRUPTED:")
    for row in result["comparison"]["rows"]:
        change = "" if row["change"] is None else f"{row['change']:+.1f}"
        print(f"    {row['metric'][:42]:<44} {str(row['baseline']):>8} -> "
              f"{str(row['disrupted']):>8}  {change:>7} {row['unit']}")

    print("\n  TIMELINE:")
    for e in result["timeline"]:
        print(f"    T+{e['minute']:<5} {e['label']}")

    print(f"\n  RECOMMENDED ACTIONS ({len(result['actions'])}):")
    for a in result["actions"]:
        print(f"    [{a['priority']:<8}] {a['area']:<14} {a['text']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="also write the full results to this file")
    ap.add_argument("--city", default=CITY)
    args = ap.parse_args()

    city_path = Path(args.city)
    print(f"Loading {city_path}...")
    template = build_city(str(city_path))
    raw = json.loads(city_path.with_suffix(".poi.json").read_text(encoding="utf-8"))["facilities"]
    facilities = snap_facilities(template, raw)
    model = dependencies_module.build(
        facilities,
        dependencies_module.load_overrides(city_path.with_suffix(".dependencies.json")))
    areas = areas_module.load(city_path.with_suffix(".areas.json"))
    print(f"  {template.G_road.number_of_nodes()} junctions, "
          f"{template.G_road.number_of_edges()} segments, {len(facilities)} assets, "
          f"{len(model.links)} dependency links, {len(areas.areas)} boundaries")
    for gap in model.gaps:
        print(f"  gap: {gap['message']}")

    core = urban_core(facilities)
    road = busiest_road(template)
    school = pick(facilities, "school", core)
    hospital = pick(facilities, "hospital", core)
    substation = feeds_most(facilities, "substation", model)

    scenarios = []
    if road:
        scenarios.append(("1. ROAD ACCIDENT — major road, high severity, 2 h",
                          [IncidentRequest("accident", edge=road, severity="high",
                                           duration_hours=2)]))
    if school:
        scenarios.append(("2. SCHOOL FIRE — critical, 2 h",
                          [IncidentRequest("fire", facility_id=school.id, severity="critical",
                                           duration_hours=2)]))
    if hospital:
        scenarios.append(("3. HOSPITAL FIRE — critical, 2 h",
                          [IncidentRequest("fire", facility_id=hospital.id, severity="critical",
                                           duration_hours=2)]))
    if substation:
        scenarios.append(("4. POWER SUBSTATION FAILURE — critical, 4 h",
                          [IncidentRequest("power_failure", facility_id=substation.id,
                                           severity="critical", duration_hours=4)]))
    if road:
        scenarios.append(("5. FLOODING — major road, high severity, 12 h",
                          [IncidentRequest("flooding", edge=road, severity="high",
                                           duration_hours=12)]))
    if road and substation:
        scenarios.append(("6. SIMULTANEOUS — flood, power failure and accident together",
                          [IncidentRequest("flooding", edge=road, severity="high",
                                           duration_hours=12),
                           IncidentRequest("power_failure", facility_id=substation.id,
                                           severity="critical", duration_hours=4),
                           IncidentRequest("accident", edge=road, severity="moderate",
                                           duration_hours=2)]))

    out = {}
    for title, requests in scenarios:
        start = time.time()
        try:
            result = simulate(template.copy(), facilities, requests, model, areas)
        except ScenarioError as e:
            print(f"\n{title}\n  could not run: {e}")
            continue
        show(title, result)
        print(f"\n  (computed in {time.time() - start:.2f}s)")
        json.dumps(result)  # the API has to be able to serialise this
        out[title] = result

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"\nFull results written to {args.json}")


if __name__ == "__main__":
    main()
