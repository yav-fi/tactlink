from __future__ import annotations

import random

import pytest

from planning.comms import GridCommunicationField, choose_route, measure_route
from simulation.comms_belief import CommunicationBelief
from simulation.config import NetworkConfig
from simulation.counterfactual import (
    CandidateAction,
    ForecastNode,
    ForecastState,
    ShadowEvaluator,
)
from simulation.events import EventBus
from simulation.interference import InterferenceEngine
from simulation.messaging import classify
from simulation.models import (
    InterferenceConfig,
    MessagePriority,
    MessageType,
    NetworkMessage,
    Vector3,
)
from simulation.network import NetworkSimulator
from simulation.prediction import PredictiveMonitor
from simulation.relay_planning import ClusterPair, RelayPlanner, RelayVehicle
from simulation.workunits import (
    EdgeComputeBroker,
    EdgeResourceProfile,
    WorkUnit,
    WorkUnitKind,
    execute_work_unit,
)

from .test_simulation import engine_for_test


def message(kind: MessageType, sequence: int = 1, **payload: object) -> NetworkMessage:
    return NetworkMessage(
        sender_id="a",
        recipient_id="b",
        timestamp_sent=0.0,
        type=kind,
        sequence_number=sequence,
        payload=payload,
    )


def network(config: NetworkConfig, interference: float = 0.0) -> NetworkSimulator:
    result = NetworkSimulator(
        config,
        random.Random(11),
        InterferenceEngine(InterferenceConfig(network_interference=interference)),
        EventBus(),
    )
    result.register("a")
    result.register("b")
    return result


def test_classifier_promotes_control_and_demotes_telemetry() -> None:
    assert classify(message(MessageType.TASK_ASSIGNMENT)).priority == MessagePriority.CRITICAL
    assert classify(message(MessageType.POSITION_UPDATE)).priority == MessagePriority.LOW
    assert classify(message(MessageType.STATUS, emergency=True)).priority == MessagePriority.CRITICAL


def test_adaptive_scheduler_delivers_critical_before_low_under_congestion() -> None:
    transport = network(
        NetworkConfig(
            base_latency=0,
            jitter=0,
            bandwidth_messages_per_tick=1,
            base_packet_loss=0,
            adaptive_messaging=True,
        )
    )
    assert transport.send(message(MessageType.POSITION_UPDATE), 0)
    assert transport.send(message(MessageType.TASK_ASSIGNMENT), 0)
    transport.update(0)
    delivered = transport.receive("b")
    assert [item.type for item in delivered] == [MessageType.TASK_ASSIGNMENT]
    assert transport.counters.bandwidth_deferred == 1


def test_priority_never_bypasses_physical_loss() -> None:
    transport = network(
        NetworkConfig(base_latency=0, jitter=0, base_packet_loss=0),
        interference=1.0,
    )
    assert not transport.send(message(MessageType.TASK_ASSIGNMENT), 0)
    transport.update(0)
    assert transport.receive("b") == []
    assert transport.counters.dropped_by_reason["physical_link"] == 1


def test_transport_queue_is_bounded_and_zero_message_budget_means_unlimited() -> None:
    transport = network(
        NetworkConfig(
            base_latency=0,
            jitter=0,
            bandwidth_messages_per_tick=0,
            maximum_queue_messages=2,
            adaptive_messaging=True,
        )
    )
    for sequence in range(1, 5):
        assert transport.send(message(MessageType.POSITION_UPDATE, sequence, x=sequence), 0)
    assert len(transport._pending) + len(transport._ready) == 2
    assert transport.counters.dropped_by_reason["queue_overflow"] == 2
    transport.update(0)
    assert len(transport.receive("b")) == 1  # replaceable telemetry coalesces


def test_communication_belief_learns_only_from_delivered_sequence_evidence() -> None:
    belief = CommunicationBelief("b", minimum_samples=2)
    belief.observe_reception("a", 1, 0.0, 0.1)
    belief.observe_reception("a", 3, 0.5, 0.7)
    records = belief.sample(Vector3(x=5, y=5), 1.0)
    assert len(records) == 1
    assert 0.0 < records[0]["quality"] < 1.0
    assert records[0]["success_rate"] == pytest.approx(2 / 3, abs=1e-5)

    peer = CommunicationBelief("c")
    shared = dict(records[0], source="b")
    assert peer.merge([shared], 1.1) == 1
    assert peer.predicted_quality(Vector3(x=5, y=5)) is not None


def test_connectivity_critical_route_can_trade_distance_for_link_quality() -> None:
    class CorridorField:
        def predicted_quality(self, point: Vector3) -> float:
            return 0.95 if point.y >= 10 else 0.1

        def confidence(self, point: Vector3) -> float:
            return 1.0

    field = CorridorField()
    start = Vector3(x=0, y=0)
    direct = measure_route("direct", start, [Vector3(x=30, y=0), Vector3(x=60, y=0)], field)
    detour = measure_route(
        "connected",
        start,
        [Vector3(x=30, y=10), Vector3(x=60, y=0)],
        field,
    )
    choice = choose_route([direct, detour], connectivity_priority=1.0)
    assert choice is not None
    assert choice.changed
    assert choice.selected.route_id == "connected"
    assert "WHY LONGER PATH" in choice.explanation


def test_predictor_emits_a_bounded_threshold_crossing() -> None:
    monitor = PredictiveMonitor(
        window_seconds=10,
        minimum_samples=4,
        minimum_confidence=0.5,
        minimum_span_seconds=3,
    )
    for second, value in enumerate([0.9, 0.8, 0.7, 0.6]):
        monitor.observe("link", float(second), value)
    prediction = monitor.predict(
        "link", "LINK_FAILURE", "a-b", "quality", threshold=0.3, horizon=5
    )
    assert prediction is not None
    assert 2.5 < prediction.seconds_to_threshold < 3.5
    assert prediction.confidence >= 0.5


def test_counterfactual_and_multi_relay_plans_are_deterministic_and_distinct() -> None:
    state = ForecastState(
        now=0,
        nodes=[
            ForecastNode("left", Vector3(x=0), relay_capable=True),
            ForecastNode("right", Vector3(x=400)),
        ],
    )
    result = ShadowEvaluator(horizon_seconds=8, step_seconds=2).evaluate(
        state,
        [
            CandidateAction("hold", "hold position"),
            CandidateAction("bridge", "bridge split", {"left": ("RELAY", Vector3(x=200))}),
        ],
    )
    assert result is not None
    assert result.selected.option_id == "bridge"
    assert result.steps == 4

    planner = RelayPlanner(reference_range_m=100, falloff_power=2, hard_range_m=500)
    pairs = [
        ClusterPair(("a",), ("b",), Vector3(x=0), Vector3(x=300)),
        ClusterPair(("c",), ("d",), Vector3(x=0, y=100), Vector3(x=300, y=100)),
    ]
    plan = planner.plan(
        pairs,
        [RelayVehicle("r1", Vector3(x=120)), RelayVehicle("r2", Vector3(x=120, y=100))],
    )
    assert len(plan.stations) == 2
    assert len({station.assigned_to for station in plan.stations}) == 2
    assert plan.stations[0].point.distance_to(plan.stations[1].point) >= 15


class _Executor:
    def __init__(self, node_id: str, fail: bool = False) -> None:
        self.node_id = node_id
        self.fail = fail

    def execute(self, unit: WorkUnit) -> dict[str, object]:
        if self.fail:
            raise ConnectionError("edge left")
        return execute_work_unit(unit)


def test_edge_work_is_real_and_falls_back_locally_after_disconnect() -> None:
    broker = EdgeComputeBroker(maximum_attempts=1)
    profile = EdgeResourceProfile(node_id="edge-a", cpu_score=10, memory_mb=2048)
    broker.join(profile, _Executor("edge-a"))
    remote = broker.run(WorkUnitKind.MISSION_METRICS, {"tasks": []})
    assert remote.executed_by == "edge-a"
    assert remote.result["overall_effectiveness"] == 1.0

    broker.join(profile, _Executor("edge-a", fail=True))
    recovered = broker.run(WorkUnitKind.MISSION_METRICS, {"tasks": []})
    assert recovered.executed_by == "local"
    assert broker.reassigned == 1
    assert broker.failures == 1


def test_snapshot_exposes_adaptive_state_without_changing_public_contract() -> None:
    snapshot = engine_for_test().snapshot()
    assert snapshot.adaptive.adaptive_messaging
    assert snapshot.network.messaging.adaptive
    assert snapshot.drones[0].adaptive.learned_cells >= 0
