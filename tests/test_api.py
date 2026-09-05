from __future__ import annotations

from fastapi.testclient import TestClient

from server.main import create_app
from simulation.models import InterferenceConfig

from .test_simulation import engine_for_test


def test_api_creates_mission_and_serializes_state() -> None:
    engine = engine_for_test()
    app = create_app(engine, start_runner=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/missions",
            json={
                "type": "WATCH",
                "target": {"region_id": "ALPHA"},
                "priority": 80,
                "required_capabilities": ["camera"],
                "desired_units": 1,
                "minimum_units": 1,
            },
        )
        assert response.status_code == 201
        state = client.get("/api/state")
        assert state.status_code == 200
        assert state.json()["missions"][0]["target"]["point"] == {"x": 120.0, "y": 70.0, "z": 30.0}


def test_websocket_emits_frontend_contract() -> None:
    app = create_app(engine_for_test(), start_runner=False)
    with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
        payload = websocket.receive_json()
        assert payload["type"] == "state"
        assert len(payload["drones"]) == 3
        assert "truth" in payload["drones"][0]
        assert "estimated" in payload["drones"][0]
        assert "links" in payload
        assert "mission_capability" in payload
        assert "adaptive" in payload
        assert "messaging" in payload["network"]


def test_runtime_world_and_mission_intelligence_contracts() -> None:
    app = create_app(engine_for_test(), start_runner=False, mission_backend="baseline")
    with TestClient(app) as client:
        world = client.get("/api/world")
        assert world.status_code == 200
        assert {region["id"] for region in world.json()["regions"]} >= {"ALPHA", "BRAVO"}

        compiled = client.post(
            "/api/mission-plans/compile",
            json={"utterance": "search region ALPHA with two", "selected_region": "ALPHA"},
        )
        assert compiled.status_code == 200
        payload = compiled.json()
        assert payload["result"]["status"] == "READY"
        plan = payload["result"]["plan"]

        started = client.post(f"/api/mission-plans/{plan['id']}/start", json=plan)
        assert started.status_code == 200
        assert started.json()["actions"]

        answer = client.post("/api/mission-intel/query", json={"question": "what is active?"})
        assert answer.status_code == 200
        assert answer.json()["read_only"] is True


def test_controls_fail_recover_and_interference() -> None:
    engine = engine_for_test()
    app = create_app(engine, start_runner=False)
    with TestClient(app) as client:
        assert client.post("/api/drones/drone-2/fail").status_code == 200
        assert not engine.world.is_online("drone-2")
        assert client.post("/api/drones/drone-2/recover").status_code == 200
        config = InterferenceConfig(gps_interference=0.4, network_interference=0.3)
        response = client.post("/api/interference", json=config.model_dump(mode="json"))
        assert response.status_code == 200
        assert response.json()["gps_interference"] == 0.4


def test_control_failure_is_simulated_without_stopping_api() -> None:
    engine = engine_for_test(drone_count=4)
    app = create_app(engine, start_runner=False)
    with TestClient(app) as client:
        response = client.post("/api/control/fail")
        assert response.status_code == 200
        state = client.get("/api/state").json()
        assert state["control_available"] is False
        assert state["running"] is True
