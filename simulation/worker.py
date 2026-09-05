"""External DroneNode WebSocket worker and synchronous host transport.

The simulation host keeps physics and truth.  A worker receives only node-local
measurements, observations, delivered messages, and time, then returns motion
and outbound-message intents.  The synchronous client keeps the existing
deterministic SimulationEngine API usable for local multi-process and LAN demos.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from typing import Any

from planning import PlannerResult
from websockets.sync.client import ClientConnection, connect
from websockets.sync.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .allocator import TaskAllocator
from .autonomy import PlanningAutonomy
from .config import SimulationConfig
from .drone import DroneNode
from .prediction import Prediction
from .models import (
    DroneState,
    GPSMeasurement,
    LocalEstimatedState,
    MissionTask,
    MotionIntent,
    NetworkMessage,
    NodeIdentity,
    Observation,
    PeerKnowledge,
    PolicyResult,
    TaskLease,
    Vector3,
)
from .workunits import (
    EdgeResourceProfile,
    WorkUnit,
    WorkUnitKind,
    execute_work_unit,
    result_digest,
)
from .world import WorldDefinition
from .world_model import CoverageGrid
from .authentication import MessageAuthenticator, deterministic_private_key


class WorkerRuntime:
    def __init__(self, expected_node_id: str | None = None) -> None:
        self.expected_node_id = expected_node_id
        self.node: DroneNode | None = None

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("kind")
        if kind == "initialize":
            self._initialize(request)
            return {"kind": "initialized", "node_id": self.node.identity.node_id}
        if kind == "work_unit":
            return run_work_unit_request(request)
        if kind != "tick" or self.node is None:
            raise ValueError("worker must be initialized before tick")
        return self._tick(request)

    def _initialize(self, request: dict[str, Any]) -> None:
        identity = NodeIdentity.model_validate(request["identity"])
        if self.expected_node_id and identity.node_id != self.expected_node_id:
            raise ValueError(f"expected {self.expected_node_id}, got {identity.node_id}")
        config = SimulationConfig.model_validate(request["config"])
        definition = WorldDefinition.model_validate(request["world_definition"])
        grids = [
            CoverageGrid(region.id, region.center, region.radius, config.sensing.cell_size_m)
            for region in definition.regions
        ]
        autonomy = PlanningAutonomy.from_world_definition(definition)
        self.node = DroneNode(
            identity,
            Vector3.model_validate(request["initial_estimate"]),
            config.localization,
            config.default_speed_mps,
            config.heartbeat_interval,
            config.status_interval,
            config.peer_timeout,
            autonomy=autonomy,
            allocator=TaskAllocator(config.allocator),
            auction_window=config.auction_window_seconds,
            auction_rebroadcast=config.auction_rebroadcast_seconds,
            coverage_grids=grids,
            distributed_coordination=request.get("mode", "fabric") == "fabric",
            lease_seconds=config.task_lease_seconds,
            authenticator=(
                MessageAuthenticator(
                    {str(key): str(value) for key, value in request.get("public_keys", {}).items()},
                    deterministic_private_key(config.seed, identity.node_id),
                )
                if config.security.enabled else None
            ),
            communication=config.adaptive.communication,
            prediction=config.adaptive.prediction,
        )

    def _tick(self, request: dict[str, Any]) -> dict[str, Any]:
        node = self.node
        assert node is not None
        now, dt = float(request["now"]), float(request["dt"])
        for message_data in request.get("messages", []):
            for reply in node.handle_message(NetworkMessage.model_validate(message_data), now):
                node._pending_messages.append(reply)
        measurement_data = request.get("gps_measurement")
        node.update_localization(GPSMeasurement.model_validate(measurement_data) if measurement_data else None, dt)
        node.update_battery_measurement(float(request["battery_measurement"]))
        node.update_sensor_confidence(float(request["sensor_confidence"]))
        for observation_data in request.get("local_observations", []):
            observation = Observation.model_validate(observation_data)
            if node.world_belief.incorporate(observation):
                node._sync_legacy_belief_views(observation)
        intent, outgoing, timed_out = node.tick(now, dt)
        return {
            "kind": "tick_result",
            "intent": intent.model_dump(mode="json"),
            "outgoing": [message.model_dump(mode="json") for message in outgoing],
            "timed_out": timed_out,
            "state": dump_node_state(node),
        }


def dump_node_state(node: DroneNode) -> dict[str, Any]:
    return {
        "estimated": node.estimated.model_dump(mode="json"),
        "state": node.state,
        "current_task": node.current_task.model_dump(mode="json") if node.current_task else None,
        "task_queue": [task.model_dump(mode="json") for task in node.task_queue],
        "task_progress": node.task_progress,
        "peers": {key: peer.model_dump(mode="json") for key, peer in node.peers.items()},
        "known_tasks": {key: task.model_dump(mode="json") for key, task in node.known_tasks.items()},
        "task_assignments": node.task_assignments,
        "task_leases": {
            task_id: {owner: lease.model_dump(mode="json") for owner, lease in leases.items()}
            for task_id, leases in node.task_leases.items()
        },
        "completed_tasks": sorted(node.completed_tasks),
        "mission_revision": node._mission_revision,
        "observations": [item.model_dump(mode="json") for item in node.world_belief.observations.values()],
        "plan": node.plan.model_dump(mode="json") if node.plan else None,
        "policy": node.last_policy_result.model_dump(mode="json"),
        "communication_cells": node.comm_belief.snapshot(),
        "peer_link_quality": dict(sorted(node.comm_belief.peer_quality.items())),
        "predictions": [item.model_dump(mode="json") for item in node.active_predictions],
        "handoff_requests": [dict(item) for item in node.handoff_requests],
    }


def apply_node_state(node: DroneNode, state: dict[str, Any], now: float) -> None:
    node.estimated = LocalEstimatedState.model_validate(state["estimated"])
    node.state = DroneState(state["state"])
    node.current_task = MissionTask.model_validate(state["current_task"]) if state.get("current_task") else None
    node.task_queue = deque(MissionTask.model_validate(item) for item in state.get("task_queue", []))
    node.task_progress = float(state.get("task_progress", 0.0))
    node.peers = {key: PeerKnowledge.model_validate(value) for key, value in state.get("peers", {}).items()}
    node.known_tasks = {key: MissionTask.model_validate(value) for key, value in state.get("known_tasks", {}).items()}
    node.task_assignments = {key: list(value) for key, value in state.get("task_assignments", {}).items()}
    node.task_leases = {
        task_id: {owner: TaskLease.model_validate(lease) for owner, lease in leases.items()}
        for task_id, leases in state.get("task_leases", {}).items()
    }
    node.completed_tasks = set(state.get("completed_tasks", []))
    node._mission_revision = int(state.get("mission_revision", 0))
    changed = node.world_belief.merge((Observation.model_validate(item) for item in state.get("observations", [])), now)
    for observation in changed:
        node._sync_legacy_belief_views(observation)
    node.plan = PlannerResult.model_validate(state["plan"]) if state.get("plan") else None
    node.last_policy_result = PolicyResult.model_validate(state.get("policy", {}))
    node.comm_belief.merge(state.get("communication_cells", []), now)
    node.comm_belief.peer_quality = {
        str(key): float(value) for key, value in state.get("peer_link_quality", {}).items()
    }
    node.active_predictions = [Prediction.model_validate(item) for item in state.get("predictions", [])]
    node.handoff_requests = [dict(item) for item in state.get("handoff_requests", [])]


class WebSocketWorkerClient:
    def __init__(
        self,
        uri: str,
        node: DroneNode,
        config: SimulationConfig,
        definition: WorldDefinition,
        mode: str,
        public_keys: dict[str, str] | None = None,
    ) -> None:
        self.uri = uri
        self.connection: ClientConnection = connect(uri, open_timeout=3)
        self.connection.send(json.dumps({
            "kind": "initialize",
            "identity": node.identity.model_dump(mode="json"),
            "initial_estimate": node.estimated.position.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
            "world_definition": definition.model_dump(mode="json"),
            "mode": mode,
            "public_keys": public_keys or {},
        }))
        response = json.loads(self.connection.recv(timeout=3))
        if response.get("kind") != "initialized":
            raise RuntimeError(f"worker initialization failed: {response}")

    def tick(
        self,
        node: DroneNode,
        now: float,
        dt: float,
        messages: list[NetworkMessage],
        measurement: GPSMeasurement | None,
        battery: float,
        sensor_confidence: float,
        observations: list[Observation],
    ) -> tuple[MotionIntent, list[NetworkMessage], list[str]]:
        self.connection.send(json.dumps({
            "kind": "tick", "now": now, "dt": dt,
            "messages": [item.model_dump(mode="json") for item in messages],
            "gps_measurement": measurement.model_dump(mode="json") if measurement else None,
            "battery_measurement": battery,
            "sensor_confidence": sensor_confidence,
            "local_observations": [item.model_dump(mode="json") for item in observations],
        }))
        response = json.loads(self.connection.recv(timeout=max(1.0, dt * 5)))
        apply_node_state(node, response["state"], now)
        return (
            MotionIntent.model_validate(response["intent"]),
            [NetworkMessage.model_validate(item) for item in response.get("outgoing", [])],
            [str(item) for item in response.get("timed_out", [])],
        )

    def close(self) -> None:
        self.connection.close()


# ---------------------------------------------------------------------------
# Edge compute transport
# ---------------------------------------------------------------------------


def run_work_unit_request(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one work unit for a remote host. Pure function of the request."""

    unit = WorkUnit.model_validate(request["unit"])
    result = execute_work_unit(unit)
    return {
        "kind": "work_unit_result",
        "unit_id": unit.unit_id,
        "work_kind": unit.kind.value,
        "result": result,
        "digest": result_digest(result),
    }


class EdgeRuntime:
    """A non-mobile compute contributor: advertises resources, runs work units."""

    def __init__(self, profile: EdgeResourceProfile) -> None:
        self.profile = profile
        self.completed = 0

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("kind")
        if kind == "hello":
            return {"kind": "edge_profile", "profile": self.profile.model_dump(mode="json")}
        if kind == "work_unit":
            response = run_work_unit_request(request)
            self.completed += 1
            return response
        raise ValueError(f"unsupported edge request: {kind}")


class WebSocketEdgeClient:
    """Host-side handle to a remote edge node; satisfies ``WorkExecutor``."""

    def __init__(self, uri: str, expected_node_id: str | None = None, timeout: float = 5.0) -> None:
        self.uri = uri
        self.timeout = timeout
        self.connection: ClientConnection = connect(uri, open_timeout=3)
        self.connection.send(json.dumps({"kind": "hello"}))
        response = json.loads(self.connection.recv(timeout=timeout))
        if response.get("kind") != "edge_profile":
            raise RuntimeError(f"edge handshake failed: {response}")
        self.profile = EdgeResourceProfile.model_validate(response["profile"])
        if expected_node_id and self.profile.node_id != expected_node_id:
            raise RuntimeError(f"expected edge node {expected_node_id}, got {self.profile.node_id}")

    @property
    def node_id(self) -> str:
        return self.profile.node_id

    def execute(self, unit: WorkUnit) -> dict[str, Any]:
        self.connection.send(json.dumps({"kind": "work_unit", "unit": unit.model_dump(mode="json")}))
        response = json.loads(self.connection.recv(timeout=self.timeout))
        if response.get("kind") != "work_unit_result":
            raise RuntimeError(f"edge worker error: {response}")
        return dict(response["result"])

    def close(self) -> None:
        self.connection.close()


class InProcessEdgeClient:
    """Same contract as the WebSocket client, for tests and single-process demos."""

    def __init__(self, profile: EdgeResourceProfile) -> None:
        self.runtime = EdgeRuntime(profile)
        self.profile = profile
        self.available = True

    @property
    def node_id(self) -> str:
        return self.profile.node_id

    def execute(self, unit: WorkUnit) -> dict[str, Any]:
        if not self.available:
            raise ConnectionError(f"{self.profile.node_id} is disconnected")
        return dict(self.runtime.handle({"kind": "work_unit", "unit": unit.model_dump(mode="json")})["result"])

    def disconnect(self) -> None:
        self.available = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    runtime = WorkerRuntime(args.node_id)

    def handler(connection: ServerConnection) -> None:
        try:
            for raw in connection:
                try:
                    connection.send(json.dumps(runtime.handle(json.loads(raw))))
                except Exception as exc:
                    connection.send(json.dumps({"kind": "error", "error": str(exc)}))
        except ConnectionClosed:
            return

    print(f"worker {args.node_id} listening on ws://{args.host}:{args.port}")
    with serve(handler, args.host, args.port) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
