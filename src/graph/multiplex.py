import copy
import dataclasses
import json
from pathlib import Path

import networkx as nx

from .schema import Edge, Node


class CityGraph:
    """Three coupled layers per ARCHITECTURE.md §4: G_road and G_utility are kept
    as separate graphs (not one graph with a `layer` tag) so a road routing call
    can never accidentally cross a utility edge. `dependencies` is the only bridge
    between them, and only Mechanism A reads it.
    """

    def __init__(self):
        self.G_road = nx.Graph()
        self.G_utility = nx.Graph()
        self.dependencies = {}  # node_id -> list of node ids it depends on

    def add_node(self, node: Node, layer: str = "road"):
        g = self.G_road if layer == "road" else self.G_utility
        g.add_node(node.id, **dataclasses.asdict(node))

    def add_edge(self, edge: Edge, layer: str = "road"):
        # A -> A is not a segment anyone can drive along or block; four of them
        # survived ingestion once and crashed the impact analysis. Rejected at
        # the only door into the graph rather than filtered at each reader.
        if edge.source == edge.target:
            raise ValueError(f"self-loop edge at {edge.source!r} is not a road segment")
        g = self.G_road if layer == "road" else self.G_utility
        g.add_edge(edge.source, edge.target, **dataclasses.asdict(edge))

    def set_dependency(self, node_id: str, depends_on_ids):
        self.dependencies[node_id] = list(depends_on_ids)

    def node_attrs(self, node_id: str) -> dict:
        if node_id in self.G_road.nodes:
            return self.G_road.nodes[node_id]
        return self.G_utility.nodes[node_id]

    def routable_road(self) -> nx.Graph:
        """The road graph as traffic can actually use it — nodes that are out
        of service do not carry vehicles.

        Without this a `fire` or `flood` on a junction changed nothing: those
        events set status to 0, but routing read only the graph structure, so
        traffic drove straight through a destroyed junction. Only leave-one-out,
        which physically removes the node, ever blocked a route.

        A view, not a copy — this is called inside the convergence loop.
        """
        down = [n for n, d in self.G_road.nodes(data=True) if d.get("status", 1.0) <= 0]
        if not down:
            return self.G_road
        blocked = set(down)
        return nx.subgraph_view(self.G_road, filter_node=lambda n: n not in blocked)

    def nodes_of_type(self, node_type: str, layer: str = "road"):
        g = self.G_road if layer == "road" else self.G_utility
        return [n for n, data in g.nodes(data=True) if data["type"] == node_type]

    def copy(self) -> "CityGraph":
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        """Plain-dict snapshot for JSON responses / map rendering."""
        def layer_dict(g):
            return {
                "nodes": [{"id": n, **data} for n, data in g.nodes(data=True)],
                "edges": [{**data} for _, _, data in g.edges(data=True)],
            }

        return {
            "road": layer_dict(self.G_road),
            "utility": layer_dict(self.G_utility),
            "dependencies": self.dependencies,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CityGraph":
        """Inverse of to_dict — loads a precomputed city (e.g. real OSM data
        ingested offline by scripts/ingest_manipal.py) with no osmnx/geopandas
        dependency at load time, just networkx + stdlib json."""
        city = cls()
        for layer_name, g in (("road", city.G_road), ("utility", city.G_utility)):
            layer = data[layer_name]
            for node in layer["nodes"]:
                g.add_node(node["id"], **node)
            for edge in layer["edges"]:
                if edge["source"] == edge["target"]:
                    continue  # see add_edge: never a routable segment
                g.add_edge(edge["source"], edge["target"], **edge)
        city.dependencies = data["dependencies"]
        return city

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path) -> "CityGraph":
        return cls.from_dict(json.loads(Path(path).read_text()))
