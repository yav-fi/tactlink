from __future__ import annotations

import random

import pytest

from simulation.config import NetworkConfig, SimulationConfig
from simulation.events import EventBus
from simulation.interference import InterferenceEngine
from simulation.models import (
    EventType,
    InterferenceConfig,
    MessageType,
    MissionCommand,
    MissionTarget,
    NetworkMessage,
    ScenarioEvent,
    TaskType,
    Vector3,
)
from simulation.network import NetworkSimulator
from simulation.scenarios import ScenarioPreset
from simulation.simulation import SimulationEngine


def engine_for_test(seed: int = 17, drone_count: int = 3) -> SimulationEngine:
    config = SimulationConfig(
        seed=seed,
        tick_rate_hz=10,
        heartbeat_interval=0.2,
        status_interval=0.2,
        peer_timeout=0.6,
        network=NetworkConfig(base_latency=0.0, jitter=0.0, base_packet_loss=0.0),
    )
    engine = SimulationEngine(config, scenario=ScenarioPreset.NORMAL, drone_count=drone_count)
    engine.set_interference(InterferenceConfig())
    return engine


def advance_until(engine: SimulationEngine, predicate: object, limit: int = 100) -> None:
    for _ in range(limit):
        engine.tick(0.1)
        if predicate():  # type: ignore[operator]
            return
    raise AssertionError("condition was not reached")


def assign_single_task(engine: SimulationEngine, target: Vector3 | None = None) -> object:
    task = engine.submit_mission(
        MissionCommand(
            type=TaskType.GOTO if target else TaskType.WATCH,
            target=MissionTarget(point=target or Vector3(x=80, y=60, z=25)),
            required_capabilities={"camera"},
            desired_units=1,
            minimum_units=1,
        )
    )
    advance_until(engine, lambda: bool(task.assigned_nodes))
    return task


def test_central_mode_keeps_delivered_assignments_in_operator_projection() -> None:
    config = SimulationConfig(
        seed=31,
        tick_rate_hz=10,
        heartbeat_interval=0.2,
        status_interval=0.2,
        peer_timeout=0.6,
        network=NetworkConfig(base_latency=0.0, jitter=0.0, base_packet_loss=0.0),
    )
    engine = SimulationEngine(config, scenario=ScenarioPreset.NORMAL, drone_count=3, mode="baseline")
    engine.set_interference(InterferenceConfig())
    task = engine.submit_mission(
        MissionCommand(
            type=TaskType.SEARCH,
            target=MissionTarget(region_id="ALPHA"),
            required_capabilities={"camera"},
            desired_units=2,
            minimum_units=1,
        )
    )
    advance_until(engine, lambda: len(task.assigned_nodes) == 2)
    failed = task.assigned_nodes[0]
    engine.fail_drone(failed)
    advance_until(engine, lambda: failed not in task.assigned_nodes and bool(task.assigned_nodes))
    assert all(engine.world.is_online(node_id) for node_id in task.assigned_nodes)


def test_drone_moves_toward_waypoint() -> None:
    engine = engine_for_test()
    target = Vector3(x=100, y=100, z=30)
    task = assign_single_task(engine, target)
    node_id = task.assigned_nodes[0]
    before = engine.world.truth(node_id).position.distance_to(target)
    engine.run_steps(15, 0.1)
    after = engine.world.truth(node_id).position.distance_to(target)
    assert after < before


def test_gps_degradation_grows_uncertainty_without_moving_truth() -> None:
    engine = engine_for_test()
    node_id = "drone-1"
    truth_before = engine.world.truth(node_id).position
    uncertainty_before = engine.drones[node_id].estimated.position_uncertainty
    engine.inject_event(
        ScenarioEvent(timestamp=0, type="GPS_OUTAGE", affected_nodes=[node_id], severity=1, duration=5)
    )
    engine.run_steps(20, 0.1)
    assert engine.world.truth(node_id).position == truth_before
    assert engine.drones[node_id].estimated.position_uncertainty > uncertainty_before + 2
    assert any(event.event_type == EventType.GPS_LOST for event in engine.events.recent(100))


def network_outcomes(seed: int) -> tuple[list[bool], int]:
    events = EventBus()
    interference = InterferenceEngine(InterferenceConfig(network_interference=0.5))
    network = NetworkSimulator(
        NetworkConfig(base_latency=0, jitter=0), random.Random(seed), interference, events
    )
    network.register("a")
    network.register("b")
    outcomes = []
    for sequence in range(40):
        outcomes.append(
            network.send(
                NetworkMessage(
                    sender_id="a",
                    recipient_id="b",
                    timestamp_sent=0,
                    type=MessageType.HEARTBEAT,
                    sequence_number=sequence,
                ),
                0,
            )
        )
    network.update(1)
    return outcomes, len(network.receive("b"))


def test_packet_loss_is_real_and_seeded() -> None:
    first = network_outcomes(99)
    second = network_outcomes(99)
    assert first == second
    assert 0 < first[1] < 40


def test_failed_drone_stops_heartbeats_and_timeout_is_delayed() -> None:
    engine = engine_for_test()
    engine.run_steps(8, 0.1)
    observer = engine.drones["drone-1"]
    assert observer.peers["drone-2"].available
    engine.fail_drone("drone-2")
    engine.run_steps(3, 0.1)
    assert observer.peers["drone-2"].available
    last_contact = engine.missions.last_contact["drone-2"]
    engine.run_steps(8, 0.1)
    assert not observer.peers["drone-2"].available
    assert engine.missions.last_contact["drone-2"] == last_contact
    assert "drone-2" in engine.missions.unavailable


def test_failed_assignment_reallocated_and_capability_recovers() -> None:
    engine = engine_for_test()
    task = assign_single_task(engine)
    original = task.assigned_nodes[0]
    advance_until(engine, lambda: engine.missions.overall_capability == 1.0)
    engine.fail_drone(original)

    dropped = False
    for _ in range(20):
        engine.tick(0.1)
        if engine.missions.overall_capability == 0.0:
            dropped = True
            break
    assert dropped
    assert original not in task.assigned_nodes

    engine.tick(0.1)
    assert task.assigned_nodes
    assert original not in task.assigned_nodes
    assert engine.missions.overall_capability == 1.0
    event_types = [event.event_type for event in engine.events.recent(100)]
    assert EventType.TASK_REASSIGNED in event_types
    assert event_types.count(EventType.MISSION_CAPABILITY_CHANGED) >= 3


def test_partition_blocks_messages_then_reconnects() -> None:
    events = EventBus()
    network = NetworkSimulator(
        NetworkConfig(base_latency=0, jitter=0),
        random.Random(1),
        InterferenceEngine(InterferenceConfig()),
        events,
    )
    network.register("a")
    network.register("b")
    message = NetworkMessage(
        sender_id="a", recipient_id="b", timestamp_sent=0, type=MessageType.HEARTBEAT
    )
    network.partition({"a"}, {"b"}, until=10, now=0)
    assert not network.send(message, 0)
    network.update(0)
    assert not network.receive("b")
    network.reconnect_all(1)
    assert network.send(message, 1)
    network.update(1)
    assert len(network.receive("b")) == 1


def seeded_scenario_signature(seed: int) -> list[tuple[float, str, tuple[str, ...]]]:
    engine = engine_for_test(seed=seed, drone_count=4)
    engine.set_scenario(ScenarioPreset.CHAOS)
    engine.run_steps(300, 0.1)
    return [
        (event.timestamp, str(event.payload.get("type")), tuple(event.affected_entities))
        for event in engine.events.recent(500)
        if event.event_type == EventType.RANDOM_EVENT
    ]


def test_seeded_scenario_repeats_event_sequence() -> None:
    first = seeded_scenario_signature(49281)
    second = seeded_scenario_signature(49281)
    assert first
    assert first == second


def test_planner_drives_motion_and_routes_around_obstacles() -> None:
    """GOTO across a known obstacle must produce planner waypoints, not a collision."""

    from planning.models import Vector3 as PlanningVector3

    engine = engine_for_test()
    target = Vector3(x=100, y=100, z=30)
    task = assign_single_task(engine, target)
    node_id = task.assigned_nodes[0]
    node = engine.drones[node_id]
    environment = engine.autonomy.environment

    engine.run_steps(5, 0.1)
    assert node.plan is not None, "the planner, not choose_action's direct path, is driving"
    assert str(node.plan.mode) == "TRANSIT"
    assert len(node.plan.waypoints) > 1, "a detour around block-east is required"
    assert environment.segment_intersects_obstacle(
        PlanningVector3(x=node.home.x, y=node.home.y, z=30), PlanningVector3(x=100, y=100, z=30), 0.0
    ), "the naive straight line really would have hit the obstacle"

    track = []
    for _ in range(200):
        engine.tick(0.1)
        track.append(engine.world.truth(node_id).position)

    assert not [
        point
        for point in track
        if not environment.is_free(PlanningVector3(x=point.x, y=point.y, z=point.z), 0.0)
    ], "the flown track never enters an obstacle"
    assert engine.world.truth(node_id).position.distance_to(target) < 3.0
    assert node.task_progress == 1.0


def test_planner_exception_holds_instead_of_flying_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = engine_for_test()
    task = assign_single_task(engine, Vector3(x=100, y=100, z=30))
    node = engine.drones[task.assigned_nodes[0]]
    node.plan = None

    def fail_planning(*args: object, **kwargs: object) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(node._planner, "plan", fail_planning)

    intent = node.choose_action(engine.time, 0.1)

    assert intent.hold
    assert intent.target is None


def test_every_task_type_plans_without_crashing() -> None:
    commands = [
        (TaskType.WATCH, MissionTarget(region_id="ALPHA")),
        (TaskType.SEARCH, MissionTarget(region_id="BRAVO")),
        (TaskType.TRACE, MissionTarget(waypoints=[Vector3(x=0, y=40, z=25), Vector3(x=90, y=90, z=25)])),
        (TaskType.FOLLOW, MissionTarget(entity_id="vehicle-1")),
        (TaskType.REGROUP, MissionTarget(point=Vector3(x=-100, y=-60, z=25))),
        (TaskType.HOLD, MissionTarget(point=Vector3(x=-20, y=-20, z=20))),
    ]
    for task_type, target in commands:
        engine = engine_for_test()
        task = engine.submit_mission(
            MissionCommand(
                type=task_type,
                target=target,
                required_capabilities={"camera"},
                desired_units=2,
                minimum_units=1,
            )
        )
        engine.run_steps(60, 0.1)
        assert task.assigned_nodes, f"{task_type} was never assigned"
        for node_id in task.assigned_nodes:
            plan = engine.drones[node_id].plan
            assert plan is not None and plan.waypoints, f"{task_type} produced no plan for {node_id}"


def test_drone_loss_does_not_break_planning() -> None:
    engine = engine_for_test()
    task = engine.submit_mission(
        MissionCommand(
            type=TaskType.SEARCH,
            target=MissionTarget(region_id="BRAVO"),
            required_capabilities={"camera"},
            desired_units=2,
            minimum_units=1,
        )
    )
    advance_until(engine, lambda: len(task.assigned_nodes) == 2)
    engine.run_steps(20, 0.1)
    lost = task.assigned_nodes[0]
    engine.fail_drone(lost)
    engine.run_steps(60, 0.1)

    survivors = [node_id for node_id in task.assigned_nodes if node_id != lost]
    assert survivors, "the task was reassigned after the loss"
    for node_id in survivors:
        plan = engine.drones[node_id].plan
        assert plan is not None and plan.waypoints
        assert lost not in plan.metadata.get("team", []), "the lost drone left the planning roster"
