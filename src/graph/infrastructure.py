"""Critical-infrastructure taxonomy.

One registry, so a new category is a table entry rather than a code change
(ingestion, dependency wiring, scoring, filtering and the UI all read from
here).

The engine is deliberately generic: an incident type says *how the initial
failure behaves*, and the traits below say *what that means for this kind of
asset*. "School fire" is not a feature — it is the `fire` incident applied to
an asset whose traits say it holds people, can be evacuated, and has no peer
that absorbs its function. Change the traits and the consequences change,
with no new branch anywhere.

Judgement values, stated once so they can be argued with in one place:

`criticality`  how much this category's loss matters, 1-10.
`occupancy`    people typically present. An order-of-magnitude planning
               figure, not a measurement — OSM carries no occupancy data, and
               anything derived from it is labelled an estimate downstream.
`needs`        utility networks whose loss materially degrades the service.
               Deliberately not "everything uses electricity": only where an
               outage changes what the facility can do for the city.
`backup`       networks this category can ride through for a while — standby
               generation, or stored volume in the case of a reservoir — so a
               supply failure degrades it rather than stopping it.

`osm` lists the OpenStreetMap tag filters that populate the category. Tags
are `key=value`, or `key=*` for "any value".
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    group: str
    criticality: float
    osm: List[str] = field(default_factory=list)
    # Emergency services must reach people; people must reach healthcare.
    # Which direction matters changes what "access lost" means.
    responds_outward: bool = False
    occupancy: int = 0
    # Demand can shift to another facility of the same category (one hospital
    # closing diverts patients; one water tower closing diverts nothing).
    substitutable: bool = False
    needs: Tuple[str, ...] = ()
    backup: Tuple[str, ...] = ()
    # Somewhere people can be sent when an area is evacuated.
    safe_haven: bool = False
    service: str = ""


CATEGORIES: Dict[str, Category] = {c.key: c for c in [
    # --- healthcare -------------------------------------------------------
    Category("hospital", "Hospital", "Healthcare", 10.0, ["amenity=hospital"],
             occupancy=400, substitutable=True, needs=("power", "water"),
             backup=("power",), service="inpatient and emergency care"),
    Category("clinic", "Clinic", "Healthcare", 6.0, ["amenity=clinic"],
             occupancy=40, substitutable=True, needs=("power", "water"),
             service="outpatient care"),
    Category("health_centre", "Primary health centre", "Healthcare", 7.0, ["amenity=doctors"],
             occupancy=30, substitutable=True, needs=("power", "water"),
             service="primary care"),
    Category("pharmacy", "Pharmacy", "Healthcare", 4.0, ["amenity=pharmacy"],
             occupancy=10, substitutable=True, needs=("power",), service="medicine supply"),

    # --- emergency services ----------------------------------------------
    Category("fire_station", "Fire station", "Emergency", 10.0,
             ["amenity=fire_station"], responds_outward=True, occupancy=15,
             substitutable=True, needs=("power", "water", "telecom"), backup=("power",),
             service="fire and rescue response"),
    Category("police", "Police station", "Emergency", 9.0,
             ["amenity=police"], responds_outward=True, occupancy=25,
             substitutable=True, needs=("power", "telecom"), backup=("power",),
             service="policing and incident control"),
    Category("ambulance_station", "Ambulance station", "Emergency", 10.0,
             ["emergency=ambulance_station"], responds_outward=True, occupancy=10,
             substitutable=True, needs=("power", "telecom"), backup=("power",),
             service="ambulance response"),

    # --- utilities --------------------------------------------------------
    Category("substation", "Electrical substation", "Utilities", 9.0,
             ["power=substation"], service="electricity distribution"),
    Category("power_plant", "Power plant", "Utilities", 10.0,
             ["power=plant", "power=generator"], service="electricity generation"),
    Category("water_works", "Water treatment plant", "Utilities", 9.0,
             ["man_made=water_works"], needs=("power",), service="water treatment"),
    # Needs power (towers are filled by pumps) but rides through an outage on
    # stored volume, so a supply failure degrades it rather than emptying it.
    Category("water_tower", "Water tower or reservoir", "Utilities", 8.0,
             ["man_made=water_tower", "man_made=storage_tank", "man_made=reservoir_covered"],
             needs=("power",), backup=("power",),
             service="water storage and distribution pressure"),
    Category("pumping_station", "Pumping station", "Utilities", 8.0,
             ["man_made=pumping_station", "man_made=water_well"], needs=("power",),
             service="water pumping"),
    Category("wastewater", "Sewage treatment", "Utilities", 7.0,
             ["man_made=wastewater_plant"], needs=("power",), service="sewage treatment"),
    Category("fuel", "Fuel station", "Utilities", 5.0, ["amenity=fuel"],
             occupancy=8, substitutable=True, needs=("power",), service="vehicle fuel"),

    # --- transport --------------------------------------------------------
    Category("bus_station", "Bus station", "Transport", 6.0, ["amenity=bus_station"],
             occupancy=120, substitutable=True, needs=("power",), service="public transport"),
    Category("railway_station", "Railway station", "Transport", 8.0,
             ["railway=station", "railway=halt"], occupancy=200, needs=("power",),
             service="rail transport"),
    Category("airport", "Airport", "Transport", 9.0, ["aeroway=aerodrome"],
             occupancy=300, needs=("power", "telecom"), backup=("power",),
             service="air transport"),

    # --- public services --------------------------------------------------
    Category("school", "School", "Public services", 6.0, ["amenity=school"],
             occupancy=600, needs=("power", "water"), service="education"),
    Category("college", "College or university", "Public services", 6.0,
             ["amenity=college", "amenity=university"], occupancy=1500,
             needs=("power", "water"), service="higher education"),
    Category("government", "Government office", "Public services", 6.0,
             ["office=government", "amenity=townhall"], occupancy=80,
             needs=("power", "telecom"), service="civic administration"),
    Category("post_office", "Post office", "Public services", 3.0, ["amenity=post_office"],
             occupancy=15, substitutable=True, needs=("power",), service="postal service"),

    # --- commercial -------------------------------------------------------
    Category("market", "Market", "Commercial", 5.0,
             ["amenity=marketplace", "shop=supermarket"], occupancy=150,
             substitutable=True, needs=("power",), service="food retail"),
    Category("mall", "Shopping centre", "Commercial", 4.0, ["shop=mall"],
             occupancy=400, substitutable=True, needs=("power",), service="retail"),
    Category("industrial", "Industrial site", "Commercial", 5.0, ["landuse=industrial"],
             occupancy=120, needs=("power", "water"), service="industrial production"),

    # --- other critical ---------------------------------------------------
    Category("telecom", "Telecom tower", "Communications", 7.0,
             ["man_made=mast", "man_made=communications_tower"], needs=("power",),
             backup=("power",), service="mobile and data coverage"),
    Category("shelter", "Shelter or evacuation point", "Emergency", 8.0,
             ["amenity=shelter", "emergency=assembly_point"], safe_haven=True,
             service="emergency shelter"),
    Category("open_space", "Open space or playing field", "Public services", 2.0,
             ["leisure=park", "leisure=pitch", "leisure=recreation_ground",
              "landuse=recreation_ground"], safe_haven=True,
             service="open ground usable as an assembly point"),
    Category("waste", "Waste facility", "Utilities", 4.0,
             ["amenity=waste_transfer_station", "landuse=landfill"], needs=("power",),
             service="waste handling"),
]}

# Categories whose loss is an emergency-response problem rather than an
# access problem — used by the emergency analysis.
EMERGENCY_KEYS = [k for k, c in CATEGORIES.items() if c.responds_outward]
HEALTHCARE_KEYS = [k for k, c in CATEGORIES.items() if c.group == "Healthcare"]
SAFE_HAVEN_KEYS = [k for k, c in CATEGORIES.items() if c.safe_haven]


def criticality_of(category_key: str) -> float:
    cat = CATEGORIES.get(category_key)
    return cat.criticality if cat else 3.0


def label_of(category_key: str) -> str:
    cat = CATEGORIES.get(category_key)
    return cat.label if cat else category_key.replace("_", " ").title()


def group_of(category_key: str) -> str:
    cat = CATEGORIES.get(category_key)
    return cat.group if cat else "Other"


def overpass_filters() -> Dict[str, List[str]]:
    """{category key: [osm tag filters]} for the ingestion script."""
    return {key: cat.osm for key, cat in CATEGORIES.items() if cat.osm}
