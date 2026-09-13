from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_get_city_returns_all_layers():
    body = client.get("/city").json()
    assert {"road", "utility", "dependencies"} <= body.keys()
    road_ids = {n["id"] for n in body["road"]["nodes"]}
    assert "H1" in road_ids and "J1" in road_ids


def test_simulate_power_outage_cascades_to_hospital():
    body = client.post("/simulate", json={
        "events": [{"event": "power_outage", "target": "W1", "severity": 1.0}]
    }).json()
    h2_status = next(n["status"] for n in body["after_city"]["road"]["nodes"] if n["id"] == "H2")
    # Degraded, not erased: a hospital without mains water still functions on
    # stored supply, so it stays on the map at reduced capacity.
    assert 0 < h2_status < 1
    assert body["disconnected_delta"] >= 0


def test_simulate_edge_event_accepts_json_list_target():
    body = client.post("/simulate", json={
        "events": [{"event": "accident", "target": ["J2", "J3"], "severity": 0.5}]
    }).json()
    assert body["cascade_depth"] >= 1


def test_simulate_accident_without_second_node_returns_400_not_500():
    # This is exactly the bug the frontend hit: an edge event with a bare
    # node id used to crash the server (`u, v = "J1"` unpacks characters).
    res = client.post("/simulate", json={
        "events": [{"event": "accident", "target": "J1", "severity": 1.0}]
    })
    assert res.status_code == 400


def test_simulate_accident_on_non_adjacent_pair_returns_400_not_500():
    res = client.post("/simulate", json={
        "events": [{"event": "accident", "target": ["J1", "J4"], "severity": 1.0}]
    })
    assert res.status_code == 400


def test_criticality_endpoint_shape():
    body = client.get("/criticality").json()
    assert "W1" in body["outcome_scores"]
    assert "W1" not in body["betweenness_baseline"]



if __name__ == "__main__":
    test_get_city_returns_all_layers()
    test_simulate_power_outage_cascades_to_hospital()
    test_simulate_edge_event_accepts_json_list_target()
    test_simulate_accident_without_second_node_returns_400_not_500()
    test_simulate_accident_on_non_adjacent_pair_returns_400_not_500()
    test_criticality_endpoint_shape()
    print("ok")
