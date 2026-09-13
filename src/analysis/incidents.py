"""Incident types.

An incident type describes *how the initial failure behaves* — nothing more.
What that failure means is decided by the thing it hits: a fire at a school
evacuates 600 people because the school category says it holds 600 people and
has no peer that absorbs its function; the same fire at a hospital diverts
patients because the hospital category says it is substitutable. There is no
`if incident == "school_fire"` anywhere in this codebase, and adding "warehouse
fire" requires no new type — it is `fire` applied to a warehouse.

Fields

  targets            what can be selected: a road segment, an asset, or an area
  asset_categories   when set, the incident only applies to those categories
                     (a pump failure is not something that happens to a school)
  blocks / slowdown  effect on a road segment: closed outright, or multiplied
                     travel time at full severity
  spatial            the event covers ground, so other segments in the radius
                     degrade too — flooding does not respect the one road the
                     operator clicked
  road_cordon_m      streets closed around an asset incident; a structure fire
                     shuts the road outside it whether or not anyone selected it
  asset_loss         fraction of the struck asset's own function lost at
                     severity 1.0
  area_damage        fraction of severity applied to other assets inside the
                     radius. Only for hazards that physically reach them; a
                     modelled footprint, labelled as one.
  evacuation         occupants have to be moved, so safe destinations and
                     routes are computed
  responders         which services are dispatched
  onset_min          minutes before downstream effects are felt, for the timeline
  demand_surge       multiplier on expected emergency demand
  clearance_min      after the incident is contained, how long the site takes to
                     clear and the roads to reopen
  asset_returns      whether the struck asset comes back within the modelled
                     horizon. A tripped substation does; a burnt-out school is
                     a reinstatement programme, not an incident timeline, and
                     the model says so rather than inventing a reopening date.
"""
from dataclasses import dataclass
from typing import Dict, Tuple

from ..graph.dependencies import NETWORK_OF_CATEGORY
from ..graph.infrastructure import CATEGORIES, label_of


@dataclass(frozen=True)
class IncidentType:
    key: str
    label: str
    group: str
    targets: Tuple[str, ...] = ("road",)
    asset_categories: Tuple[str, ...] = ()
    blocks: bool = False
    slowdown: float = 1.0
    spatial: bool = False
    base_radius_m: int = 800
    road_cordon_m: int = 0
    asset_loss: float = 0.0
    area_damage: float = 0.0
    evacuation: bool = False
    responders: Tuple[str, ...] = ()
    onset_min: int = 0
    demand_surge: float = 1.0
    clearance_min: int = 30
    asset_returns: bool = True
    note: str = ""


_TYPES = [
    # --- transportation ---------------------------------------------------
    IncidentType("accident", "Road accident", "Transportation", ("road",),
                 slowdown=2.5, base_radius_m=800, responders=("ambulance_station", "police"),
                 onset_min=0, clearance_min=20,
                 note="Lane blockage and rubbernecking; road stays passable."),
    IncidentType("closure", "Road closure", "Transportation", ("road",),
                 blocks=True, base_radius_m=1200, responders=("police",), onset_min=0,
                 clearance_min=10,
                 note="Segment closed to traffic for the stated duration."),
    IncidentType("congestion", "Traffic congestion", "Transportation", ("road",),
                 slowdown=2.0, base_radius_m=700, onset_min=10,
                 clearance_min=15,
                 note="Demand exceeds capacity; travel times rise without closure."),
    IncidentType("construction", "Roadworks", "Transportation", ("road",),
                 slowdown=1.8, base_radius_m=600, onset_min=0,
                 clearance_min=10,
                 note="Reduced capacity for a planned period."),
    IncidentType("flooded_road", "Flooded road", "Transportation", ("road",),
                 blocks=True, spatial=True, base_radius_m=1200, onset_min=15,
                 responders=("fire_station", "police"),
                 clearance_min=90,
                 note="Standing water closes the carriageway and nearby low-lying links."),
    IncidentType("bridge_failure", "Bridge failure", "Transportation", ("road",),
                 blocks=True, base_radius_m=1500, responders=("fire_station", "police"),
                 clearance_min=2880, asset_returns=False,
                 note="Structural failure severs the crossing; no partial capacity."),
    IncidentType("fallen_tree", "Fallen tree or debris", "Transportation", ("road",),
                 blocks=True, base_radius_m=500, responders=("fire_station",),
                 clearance_min=30,
                 note="Blockage clearable within hours."),

    # --- fire and emergency ----------------------------------------------
    # One generic fire. The asset it hits decides the consequences.
    # Not spatial: a structure fire closes the street outside it, not every
    # road within 800 m. The radius is the zone of *interest* — who is nearby
    # and may need warning — while road closures come from the cordon.
    IncidentType("fire", "Fire", "Fire & emergency", ("asset", "road"),
                 blocks=True, spatial=False, base_radius_m=800, road_cordon_m=150,
                 asset_loss=1.0, evacuation=True,
                 responders=("fire_station", "ambulance_station", "police"),
                 onset_min=0, demand_surge=2.0,
                 clearance_min=120, asset_returns=False,
                 note="Structure or vehicle fire: the asset is out of use, the street "
                      "outside it is cordoned, and fire, ambulance and police respond."),
    IncidentType("chemical_spill", "Chemical or hazardous spill", "Fire & emergency",
                 ("asset", "road"), blocks=True, spatial=True, base_radius_m=1500,
                 road_cordon_m=400, asset_loss=0.8, evacuation=True, area_damage=0.3,
                 responders=("fire_station", "ambulance_station", "police"),
                 onset_min=5, demand_surge=2.5,
                 clearance_min=240, asset_returns=False,
                 note="Exclusion zone around the release; downwind spread is not modelled."),
    IncidentType("building_collapse", "Building collapse", "Infrastructure failure",
                 ("asset",), asset_loss=1.0, road_cordon_m=180, evacuation=True,
                 blocks=True, spatial=False, base_radius_m=600,
                 responders=("fire_station", "ambulance_station", "police"),
                 demand_surge=2.0,
                 clearance_min=1440, asset_returns=False,
                 note="Asset destroyed; adjacent streets cordoned for search and rescue."),

    # --- utilities ---------------------------------------------------------
    # Same mechanic throughout: the asset stops, and whatever network it feeds
    # stops with it. Which network that is comes from the asset's category, so
    # none of these carry any per-utility logic.
    IncidentType("power_failure", "Power failure", "Utilities", ("asset",),
                 asset_categories=("substation", "power_plant"),
                 asset_loss=1.0, onset_min=0,
                 clearance_min=15,
                 note="Supply from this asset stops; dependents are found through the "
                      "dependency model."),
    IncidentType("water_supply_failure", "Water supply failure", "Utilities", ("asset",),
                 asset_categories=("water_works", "water_tower"),
                 asset_loss=1.0, onset_min=30,
                 clearance_min=30,
                 note="Piped supply from this asset stops; stored volume delays the effect."),
    IncidentType("pump_failure", "Pump failure", "Utilities", ("asset",),
                 asset_categories=("pumping_station",),
                 asset_loss=1.0, onset_min=20,
                 clearance_min=30,
                 note="Pressure loss downstream of this station."),
    IncidentType("sewage_failure", "Sewage system failure", "Utilities", ("asset",),
                 asset_categories=("wastewater",), asset_loss=1.0,
                 onset_min=60, responders=("fire_station",),
                 clearance_min=60,
                 note="Treatment stops; a public-health rather than an access problem."),
    IncidentType("telecom_failure", "Telecom failure", "Utilities", ("asset",),
                 asset_categories=("telecom",), asset_loss=1.0,
                 onset_min=0,
                 clearance_min=15,
                 note="Coverage loss degrades emergency dispatch in the served area."),
    IncidentType("utility_failure", "Other utility failure", "Utilities", ("asset", "road"),
                 slowdown=1.4, base_radius_m=600, asset_loss=0.7,
                 clearance_min=30,
                 note="Generic supply interruption at an asset not covered above."),

    # --- natural hazards ---------------------------------------------------
    IncidentType("flooding", "Flooding", "Natural hazard", ("road", "asset"),
                 blocks=True, spatial=True, base_radius_m=1500, road_cordon_m=500,
                 asset_loss=0.6, area_damage=0.25, evacuation=True,
                 responders=("fire_station", "police", "ambulance_station"),
                 onset_min=15, demand_surge=1.8,
                 clearance_min=180,
                 note="Uniform radius around the selected point. No hydrological model "
                      "and no flood-hazard layer for this area, so the footprint is an "
                      "assumption, not a prediction."),
    IncidentType("landslide", "Landslide", "Natural hazard", ("road", "asset"),
                 blocks=True, spatial=True, base_radius_m=1000, road_cordon_m=300,
                 asset_loss=0.8, area_damage=0.3, evacuation=True,
                 responders=("fire_station", "ambulance_station", "police"),
                 onset_min=0, demand_surge=1.5,
                 clearance_min=720, asset_returns=False,
                 note="Slope failure severs links in the affected band."),
    IncidentType("storm", "Cyclone or severe storm", "Natural hazard", ("road", "asset"),
                 blocks=False, slowdown=1.6, spatial=True, base_radius_m=4000,
                 asset_loss=0.3, area_damage=0.2,
                 responders=("fire_station", "police"), onset_min=30, demand_surge=2.0,
                 clearance_min=120,
                 note="Wide-area wind and rain: slower roads, scattered damage. Intensity "
                      "is uniform across the radius — no wind field is modelled."),
    IncidentType("earthquake", "Earthquake", "Natural hazard", ("road", "asset"),
                 blocks=True, spatial=True, base_radius_m=5000, asset_loss=0.7,
                 area_damage=0.5, evacuation=True,
                 responders=("fire_station", "ambulance_station", "police"),
                 onset_min=0, demand_surge=3.0,
                 clearance_min=1440, asset_returns=False,
                 note="Uniform shaking across the radius. No ground-motion or building "
                      "fragility data, so damage is indicative only."),
    IncidentType("extreme_heat", "Extreme heat", "Natural hazard", ("area", "road"),
                 slowdown=1.1, spatial=True, base_radius_m=5000, demand_surge=1.6,
                 onset_min=120,
                 clearance_min=0,
                 note="Health demand rises city-wide; the network itself is barely affected."),

    # --- infrastructure failure -------------------------------------------
    IncidentType("structural_failure", "Structural failure", "Infrastructure failure",
                 ("road",), blocks=True, base_radius_m=1200,
                 responders=("fire_station", "police"),
                 clearance_min=720, asset_returns=False,
                 note="Carriageway, culvert or retaining structure failure."),
    IncidentType("facility_failure", "Critical facility failure", "Infrastructure failure",
                 ("asset",), asset_loss=1.0, onset_min=0,
                 clearance_min=60,
                 note="The asset is out of service for non-fire reasons — equipment "
                      "failure, contamination, staffing."),
]

INCIDENT_TYPES: Dict[str, IncidentType] = {t.key: t for t in _TYPES}

# The impact engine reads a plain dict of the road-facing fields. Derived here
# so the two can never disagree about what "flooding" does to a road.
DISRUPTION_TYPES: Dict[str, dict] = {
    t.key: {"label": t.label, "blocks": t.blocks, "slowdown": t.slowdown,
            "spatial": t.spatial, "base_radius_m": t.base_radius_m,
            "road_cordon_m": t.road_cordon_m}
    for t in _TYPES
}

SEVERITY_WEIGHT = {"low": 0.25, "moderate": 0.5, "high": 0.75, "critical": 1.0}


def applies_to_asset(incident_key: str, category: str) -> bool:
    """Can this incident happen to this kind of asset?"""
    t = INCIDENT_TYPES.get(incident_key)
    if not t or "asset" not in t.targets:
        return False
    return not t.asset_categories or category in t.asset_categories


def incident_label(incident_key: str, category: str = "") -> str:
    """"Fire" at a school reads better as "School fire" — composed from the
    asset's own category rather than registered as a separate type."""
    t = INCIDENT_TYPES.get(incident_key)
    if not t:
        return incident_key.replace("_", " ").capitalize()
    if category and category in CATEGORIES and "asset" in t.targets:
        return f"{label_of(category)} {t.label.lower()}"
    return t.label


def stops_supply(incident_key: str) -> bool:
    """An asset that loses most of its function stops supplying whatever it
    feeds. Derived rather than declared, so a fire at a substation cuts power
    without `fire` knowing that substations exist."""
    t = INCIDENT_TYPES.get(incident_key)
    return bool(t and t.asset_loss >= 0.5)


def networks_stopped(incident_key: str, category: str) -> list:
    """Which networks stop being supplied when this incident hits this asset.

    Derived from what the asset feeds, so no incident type names a utility.
    """
    if not stops_supply(incident_key):
        return []
    network = NETWORK_OF_CATEGORY.get(category)
    return [network] if network else []


def catalogue() -> list:
    """The registry as the UI needs it — grouped, with what each can target."""
    return [
        {"key": t.key, "label": t.label, "group": t.group, "targets": list(t.targets),
         "asset_categories": list(t.asset_categories), "blocks": t.blocks,
         "spatial": t.spatial, "base_radius_m": t.base_radius_m,
         "evacuation": t.evacuation, "responders": list(t.responders),
         "stops_supply": stops_supply(t.key), "note": t.note}
        for t in _TYPES
    ]


def demo():
    assert applies_to_asset("fire", "school") and applies_to_asset("fire", "hospital")
    assert not applies_to_asset("pump_failure", "school"), "a school has no pumps"
    assert applies_to_asset("power_failure", "substation")
    assert networks_stopped("power_failure", "substation") == ["power"]
    # The generic rule, not a special case: a fire that destroys a substation
    # cuts power, while the same fire at a school cuts nothing downstream.
    assert networks_stopped("fire", "substation") == ["power"]
    assert networks_stopped("fire", "school") == []
    assert networks_stopped("congestion", "substation") == []
    assert incident_label("fire", "school") == "School fire"
    assert incident_label("closure") == "Road closure"
    assert set(DISRUPTION_TYPES) == set(INCIDENT_TYPES)
    print(f"ok — {len(INCIDENT_TYPES)} incident types")


if __name__ == "__main__":
    demo()
