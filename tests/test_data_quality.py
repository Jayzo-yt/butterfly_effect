"""Data-quality guarantees.

Each of these is a bug that reached the screen once: four self-loop "roads"
that crashed the impact analysis, and one hospital appearing twice in every
list because OSM maps it as both a node and a building.
"""
import pytest

from src.graph.multiplex import CityGraph
from src.graph.schema import Edge, Node
from src.graph.validate import clean, dedupe_facilities, report


def city_dict(edges, nodes=None):
    nodes = nodes or [{"id": n, "type": "road_junction", "geo": [13.35, 74.79]}
                      for n in {e for pair in edges for e in pair}]
    return {
        "road": {"nodes": nodes,
                 "edges": [{"source": u, "target": v} for u, v in edges]},
        "utility": {"nodes": [], "edges": []},
        "dependencies": {},
    }


def test_self_loops_are_fatal_and_removable():
    data = city_dict([("a", "b"), ("b", "b")])
    rep = report(data)
    assert rep["layers"]["road"]["self_loops"] == 1
    assert not rep["ok"], "a self-loop must fail validation, not warn"

    cleaned, removed = clean(data)
    assert any("self-loop" in line for line in removed)
    assert report(cleaned)["ok"]


def test_the_graph_itself_refuses_a_self_loop():
    """The loader and the graph API are the last line: a stale file cannot put
    one back."""
    city = CityGraph()
    city.add_node(Node(id="a", type="road_junction", geo=(13.35, 74.79), capacity=800.0),
                  layer="road")
    with pytest.raises(ValueError, match="self-loop"):
        city.add_edge(Edge(source="a", target="a", layer="road", base_weight=1.0,
                           max_capacity=100.0))

    loaded = CityGraph.from_dict(city_dict([("a", "b"), ("b", "b")]))
    assert loaded.G_road.number_of_edges() == 1


def test_dangling_and_duplicate_edges_are_caught():
    data = city_dict([("a", "b"), ("a", "b")])
    data["road"]["edges"].append({"source": "a", "target": "ghost"})
    rep = report(data)
    assert rep["layers"]["road"]["duplicate_edges"] == 1
    assert rep["layers"]["road"]["dangling_edges"] == 1
    cleaned, _ = clean(data)
    assert report(cleaned)["ok"]
    assert len(cleaned["road"]["edges"]) == 1


def test_the_same_asset_mapped_twice_is_merged():
    facilities = [
        {"id": "node/1", "name": "TMA Pai Hospital", "category": "hospital",
         "lat": 13.3500, "lon": 74.7900},
        {"id": "way/2", "name": "T M A Pai Hospital", "category": "hospital",
         "lat": 13.3502, "lon": 74.7901},
    ]
    kept, removed = dedupe_facilities(facilities)
    assert len(kept) == 1 and len(removed) == 1


def test_two_different_assets_in_one_building_are_both_kept():
    """Proximity alone once deleted Acharya ENT Clinic because another clinic
    shares its building. Different names mean different assets."""
    facilities = [
        {"id": "node/1", "name": "Acharya ENT Clinic", "category": "clinic",
         "lat": 13.3500, "lon": 74.7900},
        {"id": "node/2", "name": "Anugraha Medical Centre", "category": "clinic",
         "lat": 13.3501, "lon": 74.7901},
    ]
    kept, removed = dedupe_facilities(facilities)
    assert len(kept) == 2 and not removed


def test_unnamed_neighbours_of_the_same_kind_are_merged():
    """Two unnamed substations 20 m apart are one substation mapped twice."""
    facilities = [
        {"id": "way/1", "name": "", "category": "substation", "lat": 13.35, "lon": 74.79},
        {"id": "way/2", "name": "", "category": "substation", "lat": 13.3501, "lon": 74.7901},
    ]
    kept, _ = dedupe_facilities(facilities)
    assert len(kept) == 1


def test_validation_reports_every_required_count():
    rep = report(city_dict([("a", "b")]), facilities=[
        {"id": "x", "name": "", "category": "hospital", "lat": 13.35, "lon": 74.79}])
    required = {"assets", "edges", "self_loops", "duplicate_edges", "missing_coordinates",
                "invalid_geometry", "disconnected_components", "missing_asset_types"}
    assert required <= set(rep["layers"]["road"])
    assert "duplicate_assets" in rep["facilities"]
