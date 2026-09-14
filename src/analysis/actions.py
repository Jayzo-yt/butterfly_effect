"""Recommended actions, generated from the result.

Every action is a consequence of something the simulation measured, and names
the asset, road or service it measured it on. Nothing here fires unless its
condition holds, so a scenario with no traffic effect produces no traffic
action — an operator who sees "close the junction" on every incident stops
reading the list.

Priorities

  critical  intervention now; life safety or a severed route
  high      within minutes
  moderate  prepare, monitor, pre-position
  low       after the incident: mitigation worth planning for
"""
from typing import List

ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
# Severity ranking, defensively: an unknown value used to raise ValueError from
# list.index and surface as a 500. Requests are validated upstream now; this is
# the second line.
SEVERITY_RANK = {"low": 0, "moderate": 1, "high": 2, "critical": 3}


def _action(priority: str, area: str, text: str, basis: str) -> dict:
    return {"priority": priority, "area": area, "text": text, "basis": basis}


def build(result: dict) -> List[dict]:
    out: List[dict] = []
    incidents = result["incidents"]
    worst_severity = max((i["severity"] for i in incidents),
                         key=lambda s: SEVERITY_RANK.get(s, 2), default="high")
    urgent = SEVERITY_RANK.get(worst_severity, 2) >= 2

    # --- dispatch ----------------------------------------------------------
    # Each line names the asset, the figure, and why that asset: "nearest with
    # a free unit", not "contact the fire department".
    for d in result["emergency"].get("dispatch", []):
        if not d.get("available"):
            # Two different problems that used to read identically: the service
            # does not exist here, or it exists and is already committed.
            if d.get("availability") == "busy":
                out.append(_action(
                    "critical", "Immediate",
                    f"Request {d['category_label'].lower()} mutual aid from outside the area — "
                    f"every mapped station is already committed to another incident in this "
                    f"scenario.",
                    d.get("note") or "All units of this service are assigned."))
            else:
                out.append(_action(
                    "high", "Immediate",
                    f"No {d['category_label'].lower()} is mapped in this area — confirm cover "
                    f"with the district control room before relying on it.",
                    d.get("note") or "No asset of this type exists in the extract."))
            continue
        if d.get("unreachable"):
            out.append(_action(
                "critical", "Immediate",
                f"{d['category_label']} cannot reach the site by road. Request mutual aid "
                f"from outside the affected area or an air asset.",
                d.get("note") or "No path exists from any station on the disrupted network."))
            continue

        low, high = d.get("response_low_min"), d.get("response_high_min")
        travel, overhead = d.get("travel_min"), d.get("overhead_min")
        window = f"~{low:g}–{high:g} min" if low is not None else "an unknown time"
        because = (f"nearest {d['category_label'].lower()} with a free unit — "
                   f"{d['displaced_from']} is closer but committed elsewhere"
                   if d.get("displaced_from")
                   else f"nearest mapped {d['category_label'].lower()}")
        detail = (f"{travel:g} min road travel plus {overhead:g} min call handling and turnout"
                  if travel is not None and overhead else "")
        delay = (f"; {d['added_min']:g} min more travel than normal"
                 if d.get("added_min") else "")
        out.append(_action(
            "critical" if urgent else "high", "Immediate",
            f"Dispatch {d['station']} — on scene in {window}. It is the {because}.",
            (f"{detail}{delay}. " if detail else "")
            + (d.get("note") or "Upper bound allows 30% for congestion.")))

    # --- evacuation --------------------------------------------------------
    for site in result.get("evacuation", {}).get("sites", []):
        # An assembly point inside the closed area moves people from one part
        # of the hazard zone to another, so it is not a destination.
        reachable = [d for d in site["destinations"]
                     if d["reachable"] and not d.get("in_cordon")]
        people = f"~{site['people']:,} occupants" if site.get("people") else "occupants"
        if reachable:
            best = reachable[0]
            walk = f"{best['walk_min']:g} min on foot" if best.get("walk_min") else "on foot"
            kind = "designated shelter" if best.get("designated") else "mapped open ground"
            out.append(_action(
                "critical", "Public safety",
                f"Evacuate {people} of {site['name']} to {best['name']} — {walk}, "
                f"{best.get('route_m') or best['distance_m']} m.",
                f"Nearest reachable assembly point of {len(reachable)} within 3 km. It is "
                f"{kind}, not a designated evacuation centre."
                if not best.get("designated") else
                f"Nearest reachable designated shelter of {len(reachable)} within 3 km."))
            fit = best.get("sufficiency") or {}
            if fit.get("state") == "insufficient":
                out.append(_action(
                    "critical", "Public safety",
                    f"Open a second assembly point: {best['name']} cannot hold everyone. "
                    f"{fit['message']}",
                    "Estimated occupants against estimated ground capacity."))
            elif fit.get("state") == "unknown":
                out.append(_action(
                    "moderate", "Public safety",
                    f"Confirm {best['name']} can hold {people} — its capacity is not "
                    f"established by the available data.",
                    fit.get("message", "")))
        else:
            out.append(_action(
                "critical", "Public safety",
                f"Evacuate {people} of {site['name']} — no assembly point is mapped nearby, "
                f"so a destination must be named by the incident commander.",
                site["note"] or "No safe-haven asset in the dataset within range."))

    # --- traffic -----------------------------------------------------------
    for incident, net in zip(incidents, result.get("networks", [])):
        if not net:
            continue
        road = incident["target"]["name"]
        if net.get("severed"):
            out.append(_action(
                "critical", "Traffic",
                f"Close {road} at both ends and post diversions: no route remains between "
                f"its own endpoints.",
                "Shortest-path search found no alternative on the disrupted network."))
        elif net.get("added_travel_min"):
            count = net.get("diverted_count", 0)
            out.append(_action(
                "high" if net["added_travel_min"] >= 2 else "moderate", "Traffic",
                f"Signpost the diversion around {road}: the way round costs "
                f"+{net['added_travel_min']:.1f} min and loads {count} adjacent road(s).",
                f"Detour measured between the segment's own endpoints "
                f"({net.get('detour_hops', 0)} segments)."))
        elif net.get("segments_closed"):
            out.append(_action(
                "high", "Traffic",
                f"Hold the cordon around {road}: {net['segments_closed']} street(s) closed "
                f"for the response.",
                net["note"]))

    # --- healthcare access --------------------------------------------------
    # The struck asset's own closure is reported under direct impact; repeating
    # it here as an access problem would double-count it.
    cut_off = [f for f in result["facilities"]
               if f.get("cut_off") and not f.get("is_incident_site")]
    for f in [x for x in cut_off if not x.get("in_cordon")][:5]:
        out.append(_action(
            "critical", "Healthcare" if f["group"] == "Healthcare" else "Public safety",
            f"{f['name']} has no remaining route — station a unit on its side of the "
            f"closure or open an emergency access.",
            f["reason"]))
    inside = [f for f in cut_off if f.get("in_cordon")]
    if inside:
        names = ", ".join(f["name"] for f in inside[:3])
        out.append(_action(
            "high", "Public safety",
            f"{len(inside)} facilit{'y is' if len(inside) == 1 else 'ies are'} inside the "
            f"cordon and unreachable by vehicle until it lifts: {names}"
            + (f" and {len(inside) - 3} more." if len(inside) > 3 else "."),
            "Within the closed area around the incident, not severed from the network."))

    delayed = [f for f in result["facilities"]
               if f["level"] in ("critical", "high") and not f.get("cut_off")]
    for f in delayed[:4]:
        out.append(_action(
            "high", "Healthcare" if f["group"] == "Healthcare" else "Infrastructure",
            f"Warn {f['name']}: access is {f['added_min']:.1f} min slower while the "
            f"incident stands.",
            f["reason"]))

    # --- substitution -------------------------------------------------------
    for item in result.get("alternatives", {}).get("items", []):
        peers = [p for p in item["peers"] if p["reachable"] and not p.get("in_cordon")]
        closed = [p for p in item["peers"] if p.get("in_cordon")]
        if closed:
            out.append(_action(
                "high", "Healthcare" if "Hospital" in item["category_label"] else "Infrastructure",
                f"{closed[0]['name']} is the nearest peer but sits inside the cordon — it can "
                f"only take diverted demand once the closure is managed.",
                "Inside the closed area around the incident."))
        if not peers:
            continue
        named = ", ".join(f"{p['name']} ({p['travel_min']:.0f} min)" for p in peers[:2])
        volume = (f"{item['displaced_capacity']:.0f} " if item.get("displaced_capacity") else "")
        out.append(_action(
            "critical", "Healthcare" if "Hospital" in item["category_label"] else "Infrastructure",
            f"Divert {volume}demand from {item['name']} to {named}; notify them to expect "
            f"overflow.",
            item["capacity_note"] or "Peers of the same category, routed on the disrupted "
                                     "network."))

    # --- dependency cascade --------------------------------------------------
    # Grouped by the supply that failed. One "back up X" line per affected
    # asset reads as fourteen separate instructions for what is one decision,
    # and pushes the hospital in the middle of the list out of sight.
    groups: dict = {}
    for node in result["cascade"]["nodes"]:
        if node["round"] == 0:
            continue
        # A hospital on standby generation scores "moderate" because it keeps
        # running — but a hospital running on a generator is exactly what an
        # operator needs told about, so criticality overrides the level filter.
        serious = node["level"] in ("critical", "high")
        essential = node["group"] in ("Healthcare", "Emergency")
        if not serious and not essential:
            continue
        groups.setdefault((node["via_network_label"], node["via_source_name"]), []).append(node)

    for (network, source), affected in groups.items():
        critical_first = sorted(affected, key=lambda n: (n["group"] != "Healthcare",
                                                         n["group"] != "Emergency"))
        priority = [n for n in critical_first if n["group"] in ("Healthcare", "Emergency")]
        named = ", ".join(n["name"] for n in critical_first[:3])
        more = f" and {len(affected) - 3} more" if len(affected) > 3 else ""
        out.append(_action(
            "critical" if priority else "high", "Utilities",
            f"Restore or back up {network.lower()} supply from {source}: "
            f"{len(affected)} asset(s) depend on it, including {named}{more}.",
            f"{critical_first[0]['reason']} (confidence: {critical_first[0]['confidence']})."))
        if priority:
            out.append(_action(
                "critical", "Healthcare" if priority[0]["group"] == "Healthcare" else "Immediate",
                f"Confirm standby power and water at {', '.join(n['name'] for n in priority[:3])}"
                f" — {'they are' if len(priority) > 1 else 'it is'} the "
                f"{'services' if len(priority) > 1 else 'service'} on this supply that cannot "
                f"pause.",
                "Healthcare and emergency assets among the dependents of the failed supply."))

    for gap in result["cascade"].get("gaps", [])[:2]:
        out.append(_action(
            "low", "Infrastructure",
            f"Obtain {gap['label'].lower()} network data from the utility: "
            f"{gap['message']}",
            "Dependency coverage gap reported by the model."))

    # --- population ---------------------------------------------------------
    people = result["population"].get("estimated_people")
    if people and people > 1000:
        zone_km = result["incidents"][0]["radius_m"] / 1000
        out.append(_action(
            "high" if urgent else "moderate", "Public safety",
            f"Issue a public advisory to the ~{people:,} residents estimated within the "
            f"{zone_km:.1f} km zone.",
            result["population"]["basis"] + " — an estimate, not a headcount."))

    # --- duration -----------------------------------------------------------
    longest = max(i["duration_hours"] for i in incidents)
    if longest >= 6:
        out.append(_action(
            "moderate", "Immediate",
            f"Plan relief crews and a welfare point: the incident is expected to run "
            f"{longest:g} hours.",
            "Stated incident duration."))

    # --- administrative -----------------------------------------------------
    for area in result.get("areas", {}).get("areas", [])[:2]:
        out.append(_action(
            "moderate", "Public safety",
            f"Notify the {area['name']} {area['level_label'].lower()} control room.",
            "The incident falls inside this administrative boundary."))

    if not out:
        out.append(_action(
            "low", "Immediate",
            "No intervention beyond clearing the incident: no facility's access changed, "
            "no service was lost and nothing downstream depends on what was hit.",
            result["summary"].get("explanation") or "Measured, not assumed."))

    # Two incidents on the same road, or two assets sharing a name, produce the
    # same sentence twice. The duplicate carries no extra information.
    seen, unique = set(), []
    for a in out:
        if a["text"] in seen:
            continue
        seen.add(a["text"])
        unique.append(a)

    unique.sort(key=lambda a: ORDER[a["priority"]])
    return unique


def demo():
    """Actions must come from the result and disappear when the cause does."""
    empty = {
        "incidents": [{"severity": "low", "duration_hours": 0.25, "radius_m": 500,
                       "target": {"name": "Some street"}}],
        "emergency": {"dispatch": []}, "evacuation": {"sites": []}, "networks": [{}],
        "facilities": [], "alternatives": {"items": []},
        "cascade": {"nodes": [], "gaps": []}, "population": {"estimated_people": 0},
        "summary": {"explanation": "Nothing changed."}, "areas": {"areas": []},
    }
    out = build(empty)
    assert len(out) == 1 and out[0]["priority"] == "low", out
    assert "No intervention" in out[0]["text"]

    loud = dict(empty)
    loud["emergency"] = {"dispatch": [{"available": True, "category_label": "Fire station",
                                       "station": "Udupi Fire Station", "disrupted_min": 9.0,
                                       "added_min": 3.0, "unreachable": False, "note": "x"}]}
    loud["facilities"] = [{"name": "KMC Hospital", "level": "critical", "cut_off": True,
                           "group": "Healthcare", "reason": "Route severed", "added_min": 0}]
    out = build(loud)
    assert out[0]["priority"] == "critical"
    assert any("Udupi Fire Station" in a["text"] for a in out)
    assert any("KMC Hospital" in a["text"] for a in out)
    print(f"ok — {len(out)} actions from a loud scenario, 1 from a quiet one")


if __name__ == "__main__":
    demo()
