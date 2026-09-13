from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Node:
    id: str
    type: str  # road_junction | hospital | substation | water_pump | fire_station | depot
    geo: Tuple[float, float]
    capacity: float
    status: float = 1.0
    redundancy: int = 0
    depends_on: List[str] = field(default_factory=list)
    # Provenance, so an estimate is never presented as a measurement
    # (ARCHITECTURE.md §3.3). Carried through to the API and the UI.
    #   osm       - position/existence came from OpenStreetMap
    #   estimated - real asset, but this attribute is a documented estimate
    #   assumed   - the asset or link itself is our assumption, not observed
    source: str = "assumed"
    # population_weight: relative demand this node represents. 1.0 = unweighted.
    population_weight: float = 1.0
    # Real-world name where OSM has one. "Manipal substation" means something
    # to a city engineer in a way "S1" does not.
    name: str = ""


@dataclass
class Edge:
    source: str
    target: str
    layer: str  # road | power | water | supply_route
    base_weight: float
    max_capacity: float
    # Street name and classification from OSM. An operator selects "NH 66",
    # not the pair of node ids either end of the segment.
    name: str = ""
    road_class: str = ""
    # Physical facts about the segment, kept alongside the derived travel time.
    # An earlier version stored only the minutes, which meant nothing
    # downstream could report a distance, time a walk, or re-derive the time
    # under different speeds.
    length_m: float = 0.0
    speed_kph: float = 0.0
    # Whether that speed came from an OSM maxspeed tag or was imputed by class.
    speed_source: str = "assumed"
    current_weight: Optional[float] = None
    current_load: float = 0.0
    directed: bool = False

    def __post_init__(self):
        if self.current_weight is None:
            self.current_weight = self.base_weight
