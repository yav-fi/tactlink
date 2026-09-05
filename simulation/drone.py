"""Deterministic node autonomy operating only on local belief and messages."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from typing import Any

from planning import PlannerResult

from .allocator import AllocationScore, TaskAllocator
from .autonomy import PlanningAutonomy
from .comms_belief import CommunicationBelief
from .config import AllocatorWeights
from .config import CommunicationBeliefConfig, LocalizationConfig, PredictionConfig
from .explain import DecisionExplanation, DecisionFactor, ExplanationLog
from .messaging import stamped
from .prediction import Prediction, PredictiveMonitor
from .models import (
    DroneState,
    DroneStatusReport,
    GPSMeasurement,
    LocalEstimatedState,
    LocalizationMode,
    MessageType,
    MissionTask,
    MotionIntent,
    NetworkMessage,
    NodeIdentity,
    Observation,
    ObservationType,
    PeerKnowledge,
    PolicyResult,
    TaskStatus,
    TaskLease,
    TaskType,
    Vector3,
)
from .world_model import CoverageGrid, WorldBelief
from .authentication import MessageAuthenticator
from .policy import LocalPolicyEngine


WAYPOINT_CAPTURE_RADIUS = 6.0
ARRIVAL_RADIUS = 2.5
# Added to a bid for a task this node predicted it cannot finish.
HANDOFF_BID_PENALTY = 5_000.0


class DroneNode:
    """Portable autonomy core; it deliberately has no World/Simulation reference."""

    def __init__(
        self,
        identity: NodeIdentity,
        initial_estimate: Vector3,
        localization: LocalizationConfig,
        maximum_speed: float = 12.0,
        heartbeat_interval: float = 0.5,
        status_interval: float = 1.0,
        peer_timeout: float = 3.0,
        autonomy: "PlanningAutonomy | None" = None,
        allocator: TaskAllocator | None = None,
        auction_window: float = 0.8,
        auction_rebroadcast: float = 0.25,
        coverage_grids: list[CoverageGrid] | None = None,
        distributed_coordination: bool = True,
        lease_seconds: float = 4.0,
        authenticator: MessageAuthenticator | None = None,
        communication: CommunicationBeliefConfig | None = None,
        prediction: PredictionConfig | None = None,
    ) -> None:
        self.identity = identity
        self.estimated = LocalEstimatedState(position=initial_estimate.model_copy(deep=True))
        self.state = DroneState.IDLE
        self.current_task: MissionTask | None = None
        self.task_queue: deque[MissionTask] = deque()
        self.task_progress = 0.0
        self.peers: dict[str, PeerKnowledge] = {}
        self.world_belief = WorldBelief(coverage_grids or [])
        self.known_obstacles: list[dict[str, Any]] = []
        self.known_entities: dict[str, Vector3] = {}
        self.local_mission_state: dict[str, dict[str, Any]] = {}
        self.known_tasks: dict[str, MissionTask] = {}
        self.known_bids: dict[str, dict[int, dict[str, AllocationScore]]] = {}
        self.task_assignments: dict[str, list[str]] = {}
        self.completed_tasks: set[str] = set()
        self.online = True
        self._localization = localization
        self._maximum_speed = maximum_speed
        self._heartbeat_interval = heartbeat_interval
        self._status_interval = status_interval
        self._peer_timeout = peer_timeout
        self._last_heartbeat = -heartbeat_interval
        self._last_status = -status_interval
        self._sequence = 0
        self._trace_index = 0
        self._autonomy = autonomy
        self._allocator = allocator or TaskAllocator(AllocatorWeights())
        self._auction_window = auction_window
        self._auction_rebroadcast = auction_rebroadcast
        self._auction_rounds: dict[str, int] = {}
        self._auction_deadlines: dict[str, float] = {}
        self._last_bid_sent: dict[str, float] = {}
        self._pending_messages: deque[NetworkMessage] = deque()
        self._mission_revision = 0
        self._planner = autonomy.new_planner() if autonomy else None
        self.plan: PlannerResult | None = None
        self._waypoint_index = 0
        self._recently_completed: list[str] = []
        self._home = initial_estimate.model_copy(deep=True)
        self._observation_sequence = 0
        self._world_update_dirty = False
        self._distributed_coordination = distributed_coordination
        self._lease_seconds = lease_seconds
        self.task_leases: dict[str, dict[str, TaskLease]] = {}
        self._authenticator = authenticator
        self.rejected_signature_count = 0
        self._policy = LocalPolicyEngine()
        self.last_policy_result = PolicyResult()
        self._communication_config = communication or CommunicationBeliefConfig()
        self._prediction_config = prediction or PredictionConfig()
        # Learned from delivered packets only; never from simulator RF truth.
        self.comm_belief = CommunicationBelief(
            identity.node_id,
            cell_size=self._communication_config.cell_size_m,
            half_life=self._communication_config.half_life_seconds,
            minimum_samples=self._communication_config.minimum_samples,
        )
        self.predictions = PredictiveMonitor(
            window_seconds=self._prediction_config.window_seconds,
            minimum_samples=self._prediction_config.minimum_samples,
            minimum_confidence=self._prediction_config.minimum_confidence,
            cooldown_seconds=self._prediction_config.alert_cooldown_seconds,
        )
        self.explanations = ExplanationLog(limit=20)
        self.active_predictions: list[Prediction] = []
        self.handoff_requests: list[dict[str, Any]] = []
        self._handed_off: set[str] = set()
        self._battery_seeded = False
        self._last_comm_sample = -self._communication_config.sample_interval_seconds
        self._last_comm_share = -self._communication_config.share_interval_seconds

    def update_battery_measurement(self, battery: float) -> None:
        if self._battery_seeded:
            self.estimated.battery_estimate += (battery - self.estimated.battery_estimate) * 0.25
        else:
            # Seeding avoids a filter-convergence transient that a trend
            # estimator would otherwise read as a steep, fictitious discharge.
            self.estimated.battery_estimate = battery
            self._battery_seeded = True

    @property
    def home(self) -> Vector3:
        return self._home.model_copy(deep=True)

    @property
    def maximum_speed(self) -> float:
        return self._maximum_speed

    def update_sensor_confidence(self, confidence: float) -> None:
        self.estimated.sensor_confidence += (confidence - self.estimated.sensor_confidence) * 0.4

    def update_entity_measurement(self, entity_id: str, position: Vector3) -> None:
        self.known_entities[entity_id] = position.model_copy(deep=True)

    def create_observation(
        self,
        now: float,
        observation_type: ObservationType,
        domain_key: str,
        geometry: dict[str, Any],
        confidence: float,
        uncertainty: float,
        metadata: dict[str, Any] | None = None,
    ) -> Observation:
        """Incorporate a simulator-produced sensor observation locally."""
        self._observation_sequence += 1
        observation = Observation(
            observation_id=f"obs-{self.identity.node_id}-{self._observation_sequence:08d}",
            source_node_id=self.identity.node_id,
            timestamp=now,
            observation_type=observation_type,
            domain_key=domain_key,
            geometry=geometry,
            confidence=confidence,
            uncertainty=uncertainty,
            metadata=metadata or {},
        )
        if self.world_belief.incorporate(observation):
            self._world_update_dirty = True
            self._sync_legacy_belief_views(observation)
        return observation

    def _sync_legacy_belief_views(self, observation: Observation) -> None:
        """Keep planner-facing entity fields compatible during the transition."""
        entity_id = str(observation.metadata.get("entity_id", ""))
        position = observation.geometry.get("position")
        if entity_id and position:
            self.known_entities[entity_id] = Vector3.model_validate(position)

    def update_localization(self, measurement: GPSMeasurement | None, dt: float) -> tuple[LocalizationMode, LocalizationMode]:
        previous = self.estimated.localization_mode
        if measurement is None or not measurement.available:
            self.estimated.position = self.estimated.position.moved(self.estimated.velocity, dt)
            self.estimated.position_uncertainty += self._localization.dead_reckoning_growth_mps * dt
            self.estimated.localization_mode = LocalizationMode.DEAD_RECKONING
        else:
            rate = self._localization.convergence_rate
            current = self.estimated.position
            observed_velocity = Vector3(
                x=(measurement.position.x - current.x) / max(dt, 1e-6),
                y=(measurement.position.y - current.y) / max(dt, 1e-6),
                z=(measurement.position.z - current.z) / max(dt, 1e-6),
            )
            self.estimated.position = Vector3(
                x=current.x + (measurement.position.x - current.x) * rate,
                y=current.y + (measurement.position.y - current.y) * rate,
                z=current.z + (measurement.position.z - current.z) * rate,
            )
            self.estimated.velocity = Vector3(
                x=self.estimated.velocity.x * 0.7 + observed_velocity.x * 0.3,
                y=self.estimated.velocity.y * 0.7 + observed_velocity.y * 0.3,
                z=self.estimated.velocity.z * 0.7 + observed_velocity.z * 0.3,
            )
            self.estimated.position_uncertainty += (
                max(self._localization.nominal_uncertainty, measurement.accuracy_meters)
                - self.estimated.position_uncertainty
            ) * rate
            self.estimated.localization_mode = (
                LocalizationMode.GPS
                if measurement.accuracy_meters <= self._localization.degraded_noise_meters * 0.5
                else LocalizationMode.DEGRADED_GPS
            )
        return previous, self.estimated.localization_mode

    def handle_message(self, message: NetworkMessage, now: float) -> list[NetworkMessage]:
        replies: list[NetworkMessage] = []
        if self._authenticator is not None and not self._authenticator.verify(message):
            self.rejected_signature_count += 1
            return replies
        # Every packet that actually arrived is evidence about the link that
        # carried it: which sequence numbers made it, and how long they took.
        if self._communication_config.enabled:
            self.comm_belief.observe_reception(
                message.sender_id, message.sequence_number, message.timestamp_sent, now
            )
        if message.type == MessageType.COMM_OBSERVATION:
            if self._communication_config.enabled:
                self.comm_belief.merge(message.payload.get("cells", []), now)
            return replies
        if message.type == MessageType.PREDICTIVE_ALERT:
            self._record_peer_prediction(message, now)
            return replies
        if message.type in {MessageType.HEARTBEAT, MessageType.STATUS, MessageType.POSITION_UPDATE}:
            payload = message.payload
            position_data = payload.get("estimated_position")
            self.peers[message.sender_id] = PeerKnowledge(
                node_id=message.sender_id,
                last_seen=now,
                estimated_link_quality=float(payload.get("link_quality", 1.0)),
                last_position=Vector3.model_validate(position_data) if position_data else None,
                state=DroneState(payload.get("state", DroneState.IDLE)),
                available=True,
            )
        elif message.type == MessageType.TASK_ASSIGNMENT:
            task = MissionTask.model_validate(message.payload["task"])
            self._accept_task(task)
            replies.append(
                self._message(
                    now,
                    MessageType.TASK_ACK,
                    {"task_id": task.id, "accepted": True},
                    recipient_id=message.sender_id,
                )
            )
        elif message.type == MessageType.MISSION_STATE:
            task_id = str(message.payload.get("task_id", ""))
            self.local_mission_state[task_id] = dict(message.payload)
        elif message.type == MessageType.MISSION_ANNOUNCE:
            task = MissionTask.model_validate(message.payload["task"])
            if self._learn_task(task, now):
                replies.append(self._message(now, MessageType.MISSION_ANNOUNCE, {"task": task.model_dump(mode="json")}))
        elif message.type == MessageType.TASK_BID:
            self._record_bid(message.payload, now)
        elif message.type == MessageType.TASK_AWARD:
            self._record_award(message.payload, now)
        elif message.type == MessageType.TASK_RELEASE:
            task_id = str(message.payload.get("task_id", ""))
            released = str(message.payload.get("node_id", ""))
            if released == self.identity.node_id:
                # A reachable node is authoritative about its own liveness. In
                # a partition it keeps executing instead of obeying a stale
                # failure suspicion from the other side.
                return replies
            if task_id in self.known_tasks and released in self.task_assignments.get(task_id, []):
                self.task_assignments[task_id] = [node for node in self.task_assignments[task_id] if node != released]
                self._start_auction(task_id, now, int(message.payload.get("round", 0)), fast=True)
        elif message.type == MessageType.TASK_COMPLETE:
            task_id = str(message.payload.get("task_id", ""))
            self.completed_tasks.add(task_id)
            self.task_assignments.pop(task_id, None)
            if task_id in self.known_tasks:
                self.known_tasks[task_id].status = TaskStatus.COMPLETED
                self.known_tasks[task_id].progress = 1.0
        elif message.type == MessageType.MISSION_SYNC:
            self._merge_mission_sync(message.payload, now)
        elif message.type == MessageType.WORLD_UPDATE:
            incoming = [Observation.model_validate(item) for item in message.payload.get("observations", [])]
            changed = self.world_belief.merge(incoming, now)
            if changed:
                self._world_update_dirty = True
                for observation in changed:
                    self._sync_legacy_belief_views(observation)
        return replies

    def _accept_task(self, task: MissionTask) -> None:
        if self.current_task and self.current_task.id == task.id:
            return
        if any(queued.id == task.id for queued in self.task_queue):
            return
        if self.current_task is None:
            self.current_task = task
            self.state = DroneState.EXECUTING
        elif task.priority > self.current_task.priority:
            self.task_queue.append(self.current_task)
            self.current_task = task
        else:
            self.task_queue.append(task)

    def _learn_task(self, task: MissionTask, now: float) -> bool:
        if task.id in self.known_tasks or task.id in self.completed_tasks:
            return False
        self.known_tasks[task.id] = task.model_copy(deep=True)
        self.local_mission_state[task.id] = {
            "task_id": task.id,
            "revision": 1,
            "status": TaskStatus.PENDING,
            "assigned_nodes": [],
        }
        if self._distributed_coordination:
            self._start_auction(task.id, now)
        self._mission_revision += 1
        return True

    def introduce_mission(self, task: MissionTask, now: float) -> list[NetworkMessage]:
        """Create local replicated state, then gossip it through normal transport."""
        if not self._learn_task(task, now):
            return []
        return [self._message(now, MessageType.MISSION_ANNOUNCE, {"task": task.model_dump(mode="json")})]

    def _start_auction(self, task_id: str, now: float, observed_round: int = 0, fast: bool = False) -> None:
        if task_id in self.completed_tasks:
            return
        new_round = max(self._auction_rounds.get(task_id, 0) + 1, observed_round + 1)
        self._auction_rounds[task_id] = new_round
        self._auction_deadlines[task_id] = now + (min(0.1, self._auction_window) if fast else self._auction_window)
        self.known_bids.setdefault(task_id, {})[new_round] = {}
        self._last_bid_sent[task_id] = -self._auction_rebroadcast
        self.local_mission_state.setdefault(task_id, {})["round"] = new_round

    def _record_bid(self, payload: dict[str, Any], now: float) -> None:
        task_id = str(payload.get("task_id", ""))
        node_id = str(payload.get("node_id", ""))
        round_number = int(payload.get("round", 0))
        if not task_id or task_id not in self.known_tasks or not node_id or round_number <= 0:
            return
        if round_number > self._auction_rounds.get(task_id, 0):
            self._auction_rounds[task_id] = round_number
            self._auction_deadlines[task_id] = now + self._auction_window
        try:
            score = AllocationScore(
                node_id=node_id,
                cost=float(payload["cost"]),
                components={key: float(value) for key, value in payload.get("components", {}).items()},
                details=dict(payload.get("details", {})),
            )
        except (KeyError, TypeError, ValueError):
            return
        self.known_bids.setdefault(task_id, {}).setdefault(round_number, {})[node_id] = score

    def _record_award(self, payload: dict[str, Any], now: float) -> None:
        task_id = str(payload.get("task_id", ""))
        round_number = int(payload.get("round", 0))
        winners = sorted({str(node) for node in payload.get("winners", [])})
        if task_id not in self.known_tasks or round_number < self._auction_rounds.get(task_id, 0):
            return
        incoming_leases = {
            lease.owner: lease
            for lease in (TaskLease.model_validate(item) for item in payload.get("leases", []))
            if lease.task_id == task_id and lease.owner in winners
        }
        current = self.task_assignments.get(task_id)
        if round_number == self._auction_rounds.get(task_id, 0) and current:
            current_leases = self.task_leases.get(task_id, {})
            incoming_expiry = max((lease.lease_expires for lease in incoming_leases.values()), default=0.0)
            current_expiry = max((lease.lease_expires for lease in current_leases.values()), default=0.0)
            if incoming_expiry < current_expiry or (
                incoming_expiry == current_expiry and tuple(winners) >= tuple(current)
            ):
                return
        self._auction_rounds[task_id] = round_number
        self._auction_deadlines.pop(task_id, None)
        self._apply_assignment(task_id, winners, incoming_leases or self._new_leases(task_id, winners, round_number, now))

    def _merge_mission_sync(self, payload: dict[str, Any], now: float) -> None:
        for record in payload.get("tasks", []):
            task_data = record.get("task")
            if task_data:
                self._learn_task(MissionTask.model_validate(task_data), now)
            task_id = str(record.get("task_id", ""))
            if record.get("completed"):
                self.completed_tasks.add(task_id)
                self._release_task(task_id)
                continue
            round_number = int(record.get("round", 0))
            if round_number >= self._auction_rounds.get(task_id, 0) and record.get("winners"):
                self._record_award({
                    "task_id": task_id,
                    "round": round_number,
                    "winners": record.get("winners", []),
                    "leases": record.get("leases", []),
                }, now)

    def _auction_messages(self, now: float) -> list[NetworkMessage]:
        messages: list[NetworkMessage] = []
        for task_id in sorted(self._auction_deadlines):
            if task_id in self.completed_tasks:
                continue
            task = self.known_tasks[task_id]
            round_number = self._auction_rounds[task_id]
            bids = self.known_bids.setdefault(task_id, {}).setdefault(round_number, {})
            if task.required_capabilities <= self.identity.capabilities:
                score = self._allocator.score(self.status_report(now), task, self._local_communication_profile())
                if task_id in self._handed_off:
                    # Still eligible (so the task is never orphaned), but a peer
                    # that is not about to hit reserve should win instead.
                    score = replace(score, cost=score.cost + HANDOFF_BID_PENALTY)
                bids[self.identity.node_id] = score
                if now - self._last_bid_sent.get(task_id, -99.0) >= self._auction_rebroadcast - 1e-9:
                    messages.append(self._message(now, MessageType.TASK_BID, self._bid_payload(task_id, round_number, score)))
                    self._last_bid_sent[task_id] = now
            if now + 1e-9 < self._auction_deadlines[task_id]:
                continue
            eligible = [score for score in bids.values() if score.components.get("capability_mismatch", 1.0) == 0.0]
            winners = [score.node_id for score in sorted(eligible, key=lambda score: (score.cost, score.node_id))[: task.desired_units]]
            leases = self._new_leases(task_id, winners, round_number, now)
            self._apply_assignment(task_id, winners, leases)
            messages.append(
                self._message(
                    now,
                    MessageType.TASK_AWARD,
                    {
                        "task_id": task_id,
                        "round": round_number,
                        "winners": winners,
                        "bids": [self._bid_payload(task_id, round_number, score) for score in sorted(eligible, key=lambda item: item.node_id)],
                        "leases": [lease.model_dump(mode="json") for lease in leases.values()],
                    },
                )
            )
            self._auction_deadlines.pop(task_id, None)
        return messages

    @staticmethod
    def _bid_payload(task_id: str, round_number: int, score: AllocationScore) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "round": round_number,
            "node_id": score.node_id,
            "cost": score.cost,
            "components": score.components,
            "details": score.details,
        }

    def _new_leases(self, task_id: str, winners: list[str], revision: int, now: float) -> dict[str, TaskLease]:
        return {
            owner: TaskLease(
                task_id=task_id,
                owner=owner,
                lease_id=f"lease-{task_id}-{revision}-{owner}",
                lease_expires=round(now + self._lease_seconds, 6),
                revision=revision,
            )
            for owner in winners
        }

    def _apply_assignment(
        self,
        task_id: str,
        winners: list[str],
        leases: dict[str, TaskLease] | None = None,
    ) -> None:
        if task_id in self.completed_tasks:
            return
        winners = sorted(dict.fromkeys(winners))
        changed = winners != self.task_assignments.get(task_id)
        self.task_assignments[task_id] = winners
        if leases is not None:
            self.task_leases[task_id] = leases
        task = self.known_tasks[task_id].model_copy(deep=True)
        task.assigned_nodes = winners
        task.leases = list(self.task_leases.get(task_id, {}).values())
        relay_targets = task.metadata.get("relay_targets", [])
        if task.type == TaskType.RELAY and self.identity.node_id in winners and relay_targets:
            index = winners.index(self.identity.node_id) % len(relay_targets)
            task.target.point = Vector3.model_validate(relay_targets[index])
        task.status = TaskStatus.ASSIGNED if winners else TaskStatus.PENDING
        self.known_tasks[task_id] = task
        state = self.local_mission_state.setdefault(task_id, {"task_id": task_id})
        state.update({"revision": self._auction_rounds.get(task_id, 0), "assigned_nodes": winners, "status": task.status})
        if self.identity.node_id in winners:
            self._accept_task(task)
        else:
            self._release_task(task_id)
        if changed:
            self._mission_revision += 1

    def _release_task(self, task_id: str) -> None:
        if self.current_task and self.current_task.id == task_id:
            if self._planner is not None:
                self._planner.forget(self.identity.node_id, task_id)
            self.current_task = self.task_queue.popleft() if self.task_queue else None
            self.plan = None
            self._waypoint_index = 0
            self.task_progress = 0.0
        self.task_queue = deque(task for task in self.task_queue if task.id != task_id)

    def _expire_and_renew_leases(self, now: float) -> list[NetworkMessage]:
        if not self._distributed_coordination:
            return []
        messages: list[NetworkMessage] = []
        for task_id, winners in list(self.task_assignments.items()):
            if self.identity.node_id not in winners or task_id in self.completed_tasks:
                continue
            lease = self.task_leases.get(task_id, {}).get(self.identity.node_id)
            if lease is None or lease.lease_expires <= now:
                self._release_task(task_id)
                self.task_assignments[task_id] = [owner for owner in winners if owner != self.identity.node_id]
                self._start_auction(task_id, now, fast=True)
                messages.append(self._message(now, MessageType.TASK_RELEASE, {
                    "task_id": task_id,
                    "node_id": self.identity.node_id,
                    "round": self._auction_rounds.get(task_id, 0),
                    "reason": "lease_expired",
                    "lease_id": lease.lease_id if lease else None,
                }))
                continue
            if lease.lease_expires - now <= self._lease_seconds / 2 and any(peer.available for peer in self.peers.values()):
                renewed = lease.model_copy(update={"lease_expires": round(now + self._lease_seconds, 6)})
                self.task_leases[task_id][self.identity.node_id] = renewed
                messages.append(self._message(now, MessageType.TASK_AWARD, {
                    "task_id": task_id,
                    "round": lease.revision,
                    "winners": winners,
                    "leases": [item.model_dump(mode="json") for item in self.task_leases[task_id].values()],
                    "renewal": True,
                }))
        return messages

    def _release_lost_peer(self, peer_id: str, now: float) -> list[NetworkMessage]:
        messages: list[NetworkMessage] = []
        if not self._distributed_coordination:
            return messages
        for task_id, winners in sorted(self.task_assignments.items()):
            if peer_id not in winners or task_id in self.completed_tasks:
                continue
            self.task_assignments[task_id] = [node for node in winners if node != peer_id]
            self._start_auction(task_id, now, fast=True)
            messages.append(
                self._message(
                    now,
                    MessageType.TASK_RELEASE,
                    {"task_id": task_id, "node_id": peer_id, "round": self._auction_rounds[task_id] - 1},
                )
            )
        return messages

    def _local_communication_profile(self) -> dict[str, float | int | bool]:
        known = list(self.peers.values())
        available = [peer for peer in known if peer.available]
        distances = [
            self.estimated.position.distance_to(peer.last_position)
            for peer in available if peer.last_position is not None
        ]
        return {
            "mean_quality": sum(peer.estimated_link_quality for peer in available) / len(available) if available else (1.0 if not known else 0.0),
            "reachable_peers": len(available),
            "total_peers": max(1, len(known)),
            "nearest_peer_distance": min(distances, default=650.0 if known else 0.0),
            "articulation_point": False,
        }

    def _mission_sync_payload(self) -> dict[str, Any]:
        return {
            "revision": self._mission_revision,
            "tasks": [
                {
                    "task_id": task_id,
                    "task": task.model_dump(mode="json"),
                    "round": self._auction_rounds.get(task_id, 0),
                    "completed": task_id in self.completed_tasks,
                    **(
                        {"winners": self.task_assignments[task_id]}
                        if self.task_assignments.get(task_id) else {}
                    ),
                    "leases": [
                        lease.model_dump(mode="json")
                        for lease in self.task_leases.get(task_id, {}).values()
                    ],
                }
                for task_id, task in sorted(self.known_tasks.items())
            ],
        }

    def tick(self, now: float, dt: float) -> tuple[MotionIntent, list[NetworkMessage], list[str]]:
        if not self.online:
            return MotionIntent(hold=True), [], []
        timed_out = self.detect_peer_loss(now)
        messages: list[NetworkMessage] = list(self._pending_messages)
        self._pending_messages.clear()
        for peer_id in timed_out:
            messages.extend(self._release_lost_peer(peer_id, now))
        messages.extend(self._expire_and_renew_leases(now))
        if now - self._last_heartbeat >= self._heartbeat_interval - 1e-9:
            messages.append(self._status_message(now, MessageType.HEARTBEAT))
            self._last_heartbeat = now
        if now - self._last_status >= self._status_interval - 1e-9:
            messages.append(self._status_message(now, MessageType.STATUS))
            messages.append(self._message(now, MessageType.MISSION_SYNC, self._mission_sync_payload()))
            # Periodic anti-entropy makes beliefs converge after a healed
            # partition even when the original observation packet was lost.
            if self.world_belief.domain_latest:
                messages.append(
                    self._message(
                        now,
                        MessageType.WORLD_UPDATE,
                        {
                            "observations": [
                                item.model_dump(mode="json")
                                for _, item in sorted(self.world_belief.domain_latest.items())
                            ]
                        },
                    )
                )
                self._world_update_dirty = False
            self._last_status = now
        messages.extend(self._auction_messages(now))
        messages.extend(self._communication_belief_messages(now))
        messages.extend(self._predictive_messages(now))
        if self.estimated.position_uncertainty > 25.0:
            self.state = DroneState.DEGRADED
        elif self.current_task:
            self.state = DroneState.EXECUTING
        else:
            self.state = DroneState.IDLE
        intent = self.choose_action(now, dt)
        for task_id in self._recently_completed:
            self.completed_tasks.add(task_id)
            self.task_assignments.pop(task_id, None)
            messages.append(
                self._message(
                    now,
                    MessageType.TASK_COMPLETE,
                    {"task_id": task_id},
                )
            )
        self._recently_completed.clear()
        return intent, messages, timed_out


    # -- learned communication terrain --------------------------------------

    def _communication_belief_messages(self, now: float) -> list[NetworkMessage]:
        """Close the observation window, then gossip what we learned."""

        if not self._communication_config.enabled:
            return []
        messages: list[NetworkMessage] = []
        if now - self._last_comm_sample >= self._communication_config.sample_interval_seconds - 1e-9:
            self._last_comm_sample = now
            self.comm_belief.sample(self.estimated.position, now)
        if (
            self.comm_belief.cells
            and now - self._last_comm_share >= self._communication_config.share_interval_seconds - 1e-9
        ):
            self._last_comm_share = now
            messages.append(
                self._message(
                    now,
                    MessageType.COMM_OBSERVATION,
                    {"cells": self.comm_belief.export(self._communication_config.maximum_shared_cells)},
                )
            )
        return messages

    def communication_cells(self) -> list[dict[str, Any]]:
        return self.comm_belief.snapshot()

    def predicted_link_quality(self, point: Vector3) -> float | None:
        return self.comm_belief.predicted_quality(point)

    # -- prediction ----------------------------------------------------------

    def _predictive_messages(self, now: float) -> list[NetworkMessage]:
        """Extrapolate local trends and act *before* the threshold is crossed."""

        if not self._prediction_config.enabled:
            return []
        self.predictions.observe("battery", now, self.estimated.battery_estimate)
        self.predictions.observe("link_quality", now, self.comm_belief.mean_peer_quality())
        self.predictions.observe("uncertainty", now, self.estimated.position_uncertainty)
        alerts: list[Prediction] = []
        messages: list[NetworkMessage] = []

        battery = self.predictions.predict(
            "battery",
            kind="BATTERY_RESERVE",
            subject=self.identity.node_id,
            metric="battery",
            threshold=self._prediction_config.battery_reserve,
            horizon=self._prediction_config.battery_horizon_seconds,
            metadata={"task_id": self.current_task.id if self.current_task else None},
        )
        if battery is not None:
            alerts.append(battery)
            messages.extend(self._preemptive_handoff(battery, now))

        link = self.predictions.predict(
            "link_quality",
            kind="LINK_DEGRADATION",
            subject=self.identity.node_id,
            metric="observed link quality",
            threshold=self._prediction_config.link_failure_threshold,
            horizon=self._prediction_config.link_horizon_seconds,
            metadata={"weakest_peer": (self.comm_belief.weakest_peer() or ("", 0.0))[0]},
        )
        if link is not None:
            alerts.append(link)

        uncertainty = self.predictions.predict(
            "uncertainty",
            kind="LOCALIZATION_DRIFT",
            subject=self.identity.node_id,
            metric="position uncertainty",
            threshold=35.0,
            horizon=self._prediction_config.link_horizon_seconds,
            falling=False,
        )
        if uncertainty is not None:
            alerts.append(uncertainty)

        self.active_predictions = alerts
        for prediction in alerts:
            name = f"share:{prediction.kind}"
            if self.predictions.should_alert(name, now):
                messages.append(
                    self._message(
                        now,
                        MessageType.PREDICTIVE_ALERT,
                        {"prediction": prediction.model_dump(mode="json"), "safety_critical": prediction.kind == "BATTERY_RESERVE"},
                    )
                )
        return messages

    def _preemptive_handoff(self, prediction: Prediction, now: float) -> list[NetworkMessage]:
        """Release a task early so a peer can take over before we hit reserve."""

        task = self.current_task
        if not self._prediction_config.preemptive_handoff or task is None:
            return []
        if not self._distributed_coordination or task.id in self._handed_off:
            return []
        if task.type == TaskType.RETURN:
            return []
        if not any(peer.available for peer in self.peers.values()):
            return []  # nobody could take it; keep flying the mission
        self._handed_off.add(task.id)
        explanation = DecisionExplanation(
            decision_id=self.explanations.next_id(f"handoff-{self.identity.node_id}"),
            timestamp=now,
            kind="PREEMPTIVE_HANDOFF",
            subject=self.identity.node_id,
            headline="WHY HANDOFF? battery reserve projected before task completion",
            rationale=[
                prediction.explanation,
                f"{len([peer for peer in self.peers.values() if peer.available])} reachable peer(s) can bid",
            ],
            factors=[
                DecisionFactor(
                    name="battery",
                    value=round(self.estimated.battery_estimate, 4),
                    baseline=self._prediction_config.battery_reserve,
                    delta=round(self.estimated.battery_estimate - self._prediction_config.battery_reserve, 4),
                    unit="fraction",
                ),
                DecisionFactor(
                    name="seconds_to_reserve",
                    value=float(prediction.seconds_to_threshold or 0.0),
                    unit="s",
                ),
            ],
            confidence=prediction.confidence,
            metadata={"task_id": task.id},
        )
        self.explanations.add(explanation)
        self.handoff_requests.append(
            {
                "task_id": task.id,
                "node_id": self.identity.node_id,
                "prediction": prediction.model_dump(mode="json"),
                "explanation": explanation.model_dump(mode="json"),
            }
        )
        task_id = task.id
        self._release_task(task_id)
        self.task_assignments[task_id] = [
            node for node in self.task_assignments.get(task_id, []) if node != self.identity.node_id
        ]
        self._start_auction(task_id, now, fast=True)
        return [
            self._message(
                now,
                MessageType.TASK_RELEASE,
                {
                    "task_id": task_id,
                    "node_id": self.identity.node_id,
                    "round": self._auction_rounds.get(task_id, 0),
                    "reason": "predictive_handoff",
                    "safety_critical": True,
                    "prediction": prediction.model_dump(mode="json"),
                    "explanation": explanation.model_dump(mode="json"),
                },
            )
        ]

    def _record_peer_prediction(self, message: NetworkMessage, now: float) -> None:
        """Keep a peer's shared forecast so local bidding can account for it."""

        payload = message.payload.get("prediction")
        if not isinstance(payload, dict):
            return
        knowledge = self.peers.get(message.sender_id)
        if knowledge is not None:
            knowledge.last_seen = now

    def detect_peer_loss(self, now: float) -> list[str]:
        lost: list[str] = []
        for peer in self.peers.values():
            if peer.available and now - peer.last_seen > self._peer_timeout:
                peer.available = False
                peer.state = DroneState.LOST
                lost.append(peer.node_id)
                if self._communication_config.enabled:
                    self.comm_belief.observe_silence(peer.node_id, now)
        return lost

    def choose_action(self, now: float, dt: float) -> MotionIntent:
        """Use planner-driven motion when configured, otherwise fly directly."""

        task = self.current_task
        if task is None:
            self.plan = None
            self.last_policy_result = PolicyResult()
            return MotionIntent(hold=True)
        self.last_policy_result = self._policy.evaluate(self.estimated, task, self._home)
        if not self.last_policy_result.allowed:
            self.state = DroneState.DEGRADED
            return MotionIntent(hold=True)
        if self._planner is not None:
            intent = self._planned_action(now, dt, task)
            if intent is not None:
                return intent
            return MotionIntent(hold=True)
        return self._direct_action(dt, task)

    def _planned_action(self, now: float, dt: float, task: MissionTask) -> MotionIntent | None:
        """Ask MissionPlanner where to go; ``None`` means planning failed."""

        try:
            context = self._autonomy.build_context(self, now)
            stale = (
                self.plan is None
                or self.plan.task_id != task.id
                or self._planner.should_replan(context, self.plan).should_replan
            )
            if stale:
                self.plan = self._planner.plan(context)
                self._waypoint_index = 0
        except Exception:  # a planner fault must never stop the runtime
            self.plan = None
            return None

        plan = self.plan
        if plan is None or not plan.waypoints:
            return MotionIntent(hold=True)

        # Advance through the planned path instead of re-targeting waypoint 0 forever.
        last = len(plan.waypoints) - 1
        while (
            self._waypoint_index < last
            and self.estimated.position.distance_to(self._as_vector(plan.waypoints[self._waypoint_index]))
            < WAYPOINT_CAPTURE_RADIUS
        ):
            self._waypoint_index += 1

        target = self._as_vector(plan.waypoints[self._waypoint_index])
        final = self._as_vector(plan.waypoints[last])
        at_final = self._waypoint_index == last and self.estimated.position.distance_to(final) < ARRIVAL_RADIUS

        if self._advance_planned_progress(task, plan, at_final, dt):
            return MotionIntent(hold=True)

        speed = plan.desired_speed if plan.desired_speed > 0 else self._maximum_speed
        if plan.hold and at_final:
            return MotionIntent(target=target, maximum_speed=speed * 0.2, hold=True)
        return MotionIntent(target=target, maximum_speed=speed)

    def _advance_planned_progress(
        self, task: MissionTask, plan: PlannerResult, at_final: bool, dt: float
    ) -> bool:
        """Update task progress; return True when the task just completed."""

        if task.type == TaskType.SEARCH:
            coverage = float(plan.metadata.get("coverage_fraction", 0.0))
            self.task_progress = max(self.task_progress, min(0.99, coverage))
            # Planner lane completion is not mission completion. SEARCH remains
            # active until the outcome evaluator sees actual sensed coverage.
            return False
        if task.type in {TaskType.GOTO, TaskType.RETURN, TaskType.TRACE}:
            if at_final:
                self.task_progress = 1.0
                self._finish_planned_task(task)
                return True
            self.task_progress = max(self.task_progress, min(0.95, self.task_progress + dt * 0.01))
            return False
        # WATCH / HOLD / FOLLOW / REGROUP are continuous behaviours.
        if at_final:
            self.task_progress = min(0.99, self.task_progress + dt * 0.02)
        else:
            self.task_progress = max(self.task_progress, min(0.95, self.task_progress + dt * 0.01))
        return False

    def _finish_planned_task(self, task: MissionTask) -> None:
        if self._planner is not None:
            self._planner.forget(self.identity.node_id, task.id)
        self.plan = None
        self._waypoint_index = 0
        self._complete_current_task()

    @staticmethod
    def _as_vector(point: object) -> Vector3:
        return Vector3(x=point.x, y=point.y, z=point.z)

    def _direct_action(self, dt: float, task: MissionTask) -> MotionIntent:
        target = self._task_target(task)
        if target is None:
            return MotionIntent(hold=True)
        distance = self.estimated.position.distance_to(target)
        if task.type in {TaskType.GOTO, TaskType.RETURN} and distance < 2.0:
            self.task_progress = 1.0
            self._complete_current_task()
            return MotionIntent(hold=True)
        if task.type == TaskType.TRACE and distance < 2.0:
            if self._trace_index >= len(task.target.waypoints) - 1:
                self.task_progress = 1.0
                self._complete_current_task()
                return MotionIntent(hold=True)
            self._trace_index += 1
            target = task.target.waypoints[self._trace_index]
            self.task_progress = self._trace_index / max(1, len(task.target.waypoints) - 1)
        if task.type == TaskType.SEARCH and task.target.waypoints and distance < 3.0:
            self._trace_index = (self._trace_index + 1) % len(task.target.waypoints)
            target = task.target.waypoints[self._trace_index]
            self.task_progress = max(self.task_progress, self._trace_index / len(task.target.waypoints))
        if task.type in {TaskType.WATCH, TaskType.HOLD, TaskType.REGROUP} and distance < 3.0:
            self.task_progress = min(0.99, self.task_progress + dt * 0.02)
            return MotionIntent(target=target, maximum_speed=self._maximum_speed * 0.2, hold=True)
        self.task_progress = max(self.task_progress, min(0.95, self.task_progress + dt * 0.01))
        conservative_factor = max(0.25, 1.0 - self.estimated.position_uncertainty / 100.0)
        return MotionIntent(target=target, maximum_speed=self._maximum_speed * conservative_factor)

    def _task_target(self, task: MissionTask) -> Vector3 | None:
        if task.type in {TaskType.TRACE, TaskType.SEARCH} and task.target.waypoints:
            return task.target.waypoints[self._trace_index % len(task.target.waypoints)]
        if task.type == TaskType.FOLLOW and task.target.entity_id:
            return self.known_entities.get(task.target.entity_id)
        if task.type == TaskType.RETURN:
            return self._home
        return task.target.point

    def _complete_current_task(self) -> None:
        if self.current_task:
            self._recently_completed.append(self.current_task.id)
        self.current_task = self.task_queue.popleft() if self.task_queue else None
        self._trace_index = 0
        self.task_progress = 0.0 if self.current_task else 1.0
        self.state = DroneState.EXECUTING if self.current_task else DroneState.IDLE

    def _status_message(self, now: float, message_type: MessageType) -> NetworkMessage:
        payload = {
            "state": self.state,
            "estimated_position": self.estimated.position.model_dump(mode="json"),
            "position_uncertainty": self.estimated.position_uncertainty,
            "battery_estimate": self.estimated.battery_estimate,
            "current_task_id": self.current_task.id if self.current_task else None,
            "workload": len(self.task_queue) + int(self.current_task is not None),
            "capabilities": sorted(self.identity.capabilities),
            "link_quality": self._mean_link_quality(),
        }
        return self._message(now, message_type, payload)

    def status_report(self, now: float) -> DroneStatusReport:
        return DroneStatusReport(
            node_id=self.identity.node_id,
            timestamp=now,
            state=self.state,
            estimated=self.estimated.model_copy(deep=True),
            current_task_id=self.current_task.id if self.current_task else None,
            workload=len(self.task_queue) + int(self.current_task is not None),
            capabilities=self.identity.capabilities,
        )

    def _mean_link_quality(self) -> float:
        active = [peer.estimated_link_quality for peer in self.peers.values() if peer.available]
        return sum(active) / len(active) if active else 1.0

    def _message(
        self,
        now: float,
        message_type: MessageType,
        payload: dict[str, Any],
        recipient_id: str | None = None,
    ) -> NetworkMessage:
        self._sequence += 1
        message = stamped(
            NetworkMessage(
                sender_id=self.identity.node_id,
                recipient_id=recipient_id,
                timestamp_sent=now,
                type=message_type,
                payload=payload,
                sequence_number=self._sequence,
            )
        )
        return self._authenticator.sign(message) if self._authenticator is not None else message

    def fail(self) -> None:
        self.online = False
        self.state = DroneState.OFFLINE

    def recover(self, now: float) -> None:
        self.online = True
        self.state = DroneState.IDLE
        self._last_heartbeat = now - self._heartbeat_interval
        self._last_status = now - self._status_interval
