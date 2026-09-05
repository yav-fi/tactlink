from __future__ import annotations

import random

from simulation.allocator import TaskAllocator
from simulation.config import AllocatorWeights, NetworkConfig
from simulation.events import EventBus
from simulation.interference import InterferenceEngine
from simulation.models import (
    DroneState,
    DroneStatusReport,
    InterferenceConfig,
    LocalEstimatedState,
    MessageType,
    MissionCommand,
    MissionTarget,
    MissionTask,
    MotionIntent,
    NetworkMessage,
    TaskType,
    Vector3,
)
from simulation.network import NetworkSimulator
from simulation.resilience import NetworkResilienceManager
from simulation.world import World, WorldDefinition

from .test_simulation import advance_until, engine_for_test


def spatial_network(
    positions: dict[str, Vector3], interference: float = 0.0, reference_range: float = 100.0
) -> NetworkSimulator:
    network = NetworkSimulator(
        NetworkConfig(
            base_latency=0,
            jitter=0,
            reference_range_m=reference_range,
            hard_range_m=300,
            minimum_usable_quality=0.05,
            healthy_link_quality=0.45,
        ),
        random.Random(7),
        InterferenceEngine(InterferenceConfig(network_interference=interference)),
        EventBus(),
        position_provider=positions.get,
    )
    for node_id in positions:
        network.register(node_id)
    return network


def test_physical_link_quality_decreases_with_distance_and_interference() -> None:
    positions = {"a": Vector3(), "b": Vector3(x=20)}
    network = spatial_network(positions)
    close = network.link_state("a", "b", 0).quality
    positions["b"] = Vector3(x=200)
    far = network.link_state("a", "b", 0).quality
    noisy = spatial_network({"a": Vector3(), "b": Vector3(x=20)}, interference=0.7)
    assert close > far
    assert noisy.link_state("a", "b", 0).quality < close


def test_packet_delivery_uses_physical_quality() -> None:
    positions = {"a": Vector3(), "b": Vector3(x=10)}
    network = spatial_network(positions)
    close = 0
    for sequence in range(100):
        close += network.send(
            NetworkMessage(sender_id="a", recipient_id="b", timestamp_sent=0, type=MessageType.HEARTBEAT, sequence_number=sequence),
            0,
        )
    positions["b"] = Vector3(x=400)
    far = sum(
        network.send(NetworkMessage(sender_id="a", recipient_id="b", timestamp_sent=1, type=MessageType.HEARTBEAT), 1)
        for _ in range(100)
    )
    assert close > 90
    assert far == 0


def test_communication_component_changes_allocator_choice() -> None:
    allocator = TaskAllocator(AllocatorWeights(distance=0, battery=0, workload=0, uncertainty=0, communication=100))
    task = MissionTask(type=TaskType.WATCH, target=MissionTarget(point=Vector3()))

    def report(node_id: str) -> DroneStatusReport:
        return DroneStatusReport(
            node_id=node_id, timestamp=0, state=DroneState.IDLE,
            estimated=LocalEstimatedState(position=Vector3()), current_task_id=None,
            workload=0, capabilities={"camera"},
        )

    profiles = {
        "isolated": {"mean_quality": 0.1, "reachable_peers": 0, "total_peers": 3, "nearest_peer_distance": 600, "articulation_point": False},
        "connected": {"mean_quality": 0.9, "reachable_peers": 3, "total_peers": 3, "nearest_peer_distance": 20, "articulation_point": False},
    }
    scores = allocator.select(task, [report("isolated"), report("connected")], set(), 1, profiles)
    assert scores[0].node_id == "connected"
    assert scores[0].components["communication"] > 0


def test_disconnected_graph_and_relay_policy() -> None:
    positions = {
        "d1": Vector3(x=0), "d2": Vector3(x=10),
        "d3": Vector3(x=240), "d4": Vector3(x=250),
    }
    definition = WorldDefinition()
    world = World(definition)
    for node_id, point in positions.items():
        world.add_drone(node_id, point)
    network = NetworkSimulator(
        NetworkConfig(reference_range_m=70, hard_range_m=180, minimum_usable_quality=0.05, healthy_link_quality=0.45),
        random.Random(4), InterferenceEngine(InterferenceConfig()), EventBus(),
        position_provider=lambda node_id: world.truth(node_id).position,
    )
    for node_id in positions:
        network.register(node_id)
    assert len(network.components(0)) == 2
    resilience = NetworkResilienceManager(EventBus(), 0.8, 0)
    command = resilience.evaluate(0, network, world, [], ["d4"])
    assert command is not None and command.type == TaskType.RELAY
    assert command.target.point == Vector3(x=125, y=0, z=30)


def test_relay_motion_improves_modeled_connectivity() -> None:
    definition = WorldDefinition()
    world = World(definition)
    world.add_drone("left", Vector3(x=0))
    world.add_drone("right", Vector3(x=220))
    world.add_drone("relay", Vector3(x=215), maximum_speed=20)
    network = NetworkSimulator(
        NetworkConfig(reference_range_m=80, hard_range_m=180, minimum_usable_quality=0.05),
        random.Random(2), InterferenceEngine(InterferenceConfig()), EventBus(),
        position_provider=lambda node_id: world.truth(node_id).position,
    )
    for node_id in world.node_ids:
        network.register(node_id)
    before = network.metrics(0).network_health
    before_components = len(network.components(0))
    world.set_motion_intent("relay", MotionIntent(target=Vector3(x=110), maximum_speed=20))
    for _ in range(60):
        world.update(0.1)
    after = network.metrics(6).network_health
    assert after > before
    assert len(network.components(6)) < before_components


def test_peer_auction_survives_control_loss_and_reassigns_after_drone_loss() -> None:
    engine = engine_for_test(drone_count=4)
    task = engine.submit_mission(
        MissionCommand(type=TaskType.WATCH, target=MissionTarget(point=Vector3(x=80, y=60, z=25)), required_capabilities={"camera"})
    )
    advance_until(engine, lambda: bool(task.assigned_nodes))
    original = task.assigned_nodes[0]
    engine.fail_control()
    engine.fail_drone(original)
    advance_until(engine, lambda: bool(task.assigned_nodes) and original not in task.assigned_nodes)
    advance_until(
        engine,
        lambda: len({tuple(node.task_assignments.get(task.id, [])) for node in engine.drones.values() if node.online}) == 1,
    )
    assert not engine.missions.control_available
    assert len({tuple(node.task_assignments[task.id]) for node in engine.drones.values() if node.online}) == 1


def test_snapshot_exposes_runtime_topology_and_paths() -> None:
    snapshot = engine_for_test().snapshot()
    assert snapshot.origin_lat and snapshot.origin_lon
    assert snapshot.network.active_nodes == 3
    assert snapshot.network.connected_components
    assert snapshot.control_available
    assert hasattr(snapshot.drones[0], "current_plan")
