"""Deterministic node autonomy operating only on local belief and messages."""

from __future__ import annotations

from collections import deque
from typing import Any

from planning import PlannerResult

from .allocator import AllocationScore, TaskAllocator
from .autonomy import PlanningAutonomy
from .config import AllocatorWeights
from .config import LocalizationConfig
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
    PeerKnowledge,
    TaskStatus,
    TaskType,
    Vector3,
)


WAYPOINT_CAPTURE_RADIUS = 6.0
ARRIVAL_RADIUS = 2.5


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
    ) -> None:
        self.identity = identity
        self.estimated = LocalEstimatedState(position=initial_estimate.model_copy(deep=True))
        self.state = DroneState.IDLE
        self.current_task: MissionTask | None = None
        self.task_queue: deque[MissionTask] = deque()
        self.task_progress = 0.0
        self.peers: dict[str, PeerKnowledge] = {}
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

    def update_battery_measurement(self, battery: float) -> None:
        self.estimated.battery_estimate += (battery - self.estimated.battery_estimate) * 0.25

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
        current = self.task_assignments.get(task_id)
        if (
            current
            and self.identity.node_id in current
            and self.identity.node_id not in winners
            and self.current_task is not None
            and self.current_task.id == task_id
        ):
            return
        if round_number == self._auction_rounds.get(task_id, 0) and current:
            winners = list(min(tuple(current), tuple(winners)))
        self._auction_rounds[task_id] = round_number
        self._auction_deadlines.pop(task_id, None)
        self._apply_assignment(task_id, winners)

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
                self._record_award(
                    {"task_id": task_id, "round": round_number, "winners": record.get("winners", [])}, now
                )

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
                bids[self.identity.node_id] = score
                if now - self._last_bid_sent.get(task_id, -99.0) >= self._auction_rebroadcast - 1e-9:
                    messages.append(self._message(now, MessageType.TASK_BID, self._bid_payload(task_id, round_number, score)))
                    self._last_bid_sent[task_id] = now
            if now + 1e-9 < self._auction_deadlines[task_id]:
                continue
            eligible = [score for score in bids.values() if score.components.get("capability_mismatch", 1.0) == 0.0]
            winners = [score.node_id for score in sorted(eligible, key=lambda score: (score.cost, score.node_id))[: task.desired_units]]
            self._apply_assignment(task_id, winners)
            messages.append(
                self._message(
                    now,
                    MessageType.TASK_AWARD,
                    {
                        "task_id": task_id,
                        "round": round_number,
                        "winners": winners,
                        "bids": [self._bid_payload(task_id, round_number, score) for score in sorted(eligible, key=lambda item: item.node_id)],
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

    def _apply_assignment(self, task_id: str, winners: list[str]) -> None:
        if task_id in self.completed_tasks:
            return
        winners = sorted(dict.fromkeys(winners))
        changed = winners != self.task_assignments.get(task_id)
        self.task_assignments[task_id] = winners
        task = self.known_tasks[task_id].model_copy(deep=True)
        task.assigned_nodes = winners
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

    def _release_lost_peer(self, peer_id: str, now: float) -> list[NetworkMessage]:
        messages: list[NetworkMessage] = []
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
        if now - self._last_heartbeat >= self._heartbeat_interval - 1e-9:
            messages.append(self._status_message(now, MessageType.HEARTBEAT))
            self._last_heartbeat = now
        if now - self._last_status >= self._status_interval - 1e-9:
            messages.append(self._status_message(now, MessageType.STATUS))
            messages.append(self._message(now, MessageType.MISSION_SYNC, self._mission_sync_payload()))
            self._last_status = now
        messages.extend(self._auction_messages(now))
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

    def detect_peer_loss(self, now: float) -> list[str]:
        lost: list[str] = []
        for peer in self.peers.values():
            if peer.available and now - peer.last_seen > self._peer_timeout:
                peer.available = False
                peer.state = DroneState.LOST
                lost.append(peer.node_id)
        return lost

    def choose_action(self, now: float, dt: float) -> MotionIntent:
        """Use planner-driven motion when configured, otherwise fly directly."""

        task = self.current_task
        if task is None:
            self.plan = None
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
            if str(plan.phase) == "COMPLETE":
                self.task_progress = 1.0
                self._finish_planned_task(task)
                return True
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
        return NetworkMessage(
            sender_id=self.identity.node_id,
            recipient_id=recipient_id,
            timestamp_sent=now,
            type=message_type,
            payload=payload,
            sequence_number=self._sequence,
        )

    def fail(self) -> None:
        self.online = False
        self.state = DroneState.OFFLINE

    def recover(self, now: float) -> None:
        self.online = True
        self.state = DroneState.IDLE
        self._last_heartbeat = now - self._heartbeat_interval
        self._last_status = now - self._status_interval
