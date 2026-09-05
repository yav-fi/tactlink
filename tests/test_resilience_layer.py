from __future__ import annotations

from simulation.authentication import MessageAuthenticator, deterministic_private_key, public_key_text
from simulation.benchmark import run_mode
from simulation.config import SecurityConfig, SimulationConfig
from simulation.models import MessageType, MissionCommand, MissionTarget, NetworkMessage, TaskType, Vector3
from simulation.replay import Replay
from simulation.scenarios import ScenarioPreset
from simulation.simulation import SimulationEngine

from .test_simulation import advance_until, engine_for_test


def test_ed25519_authentication_accepts_valid_and_rejects_tampering() -> None:
    key = deterministic_private_key(7, "node-a")
    authenticator = MessageAuthenticator({"node-a": public_key_text(key)}, key)
    message = NetworkMessage(sender_id="node-a", timestamp_sent=1, type=MessageType.STATUS, payload={"ok": True})
    signed = authenticator.sign(message)
    assert authenticator.verify(signed)
    assert not authenticator.verify(signed.model_copy(update={"payload": {"ok": False}}))


def test_secure_engine_nodes_exchange_signed_messages() -> None:
    engine = SimulationEngine(
        SimulationConfig(seed=7, tick_rate_hz=10, security=SecurityConfig(enabled=True)),
        scenario=ScenarioPreset.NORMAL,
        drone_count=3,
    )
    engine.run_steps(20, 0.1)
    assert engine.drones["drone-1"].peers["drone-2"].available
    assert not [event for event in engine.events.recent(100) if str(event.event_type) == "SIGNATURE_REJECTED"]


def test_task_lease_renews_while_owner_has_peer_contact() -> None:
    engine = engine_for_test(drone_count=3)
    task = engine.submit_mission(
        MissionCommand(type=TaskType.WATCH, target=MissionTarget(point=Vector3(x=20, y=20, z=20)), required_capabilities={"camera"})
    )
    advance_until(engine, lambda: bool(task.assigned_nodes))
    owner = task.assigned_nodes[0]
    initial = engine.drones[owner].task_leases[task.id][owner].lease_expires
    engine.run_steps(30, 0.1)
    renewed = engine.drones[owner].task_leases[task.id][owner].lease_expires
    assert renewed > initial
    assert renewed > engine.time


def test_benchmark_recording_is_deterministic_and_replay_matches(tmp_path) -> None:
    first = run_mode("fabric", 123, 4.0, tmp_path / "first")
    second = run_mode("fabric", 123, 4.0, tmp_path / "second")
    keys = ["mission_effectiveness", "mission_capability", "search_coverage", "network_health", "observation_count"]
    assert {key: first[key] for key in keys} == {key: second[key] for key in keys}
    final = Replay(first["replay"]).final_snapshot()
    assert final.mission_effectiveness == first["mission_effectiveness"]
    assert final.world_knowledge.observation_count == first["observation_count"]
