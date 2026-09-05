"""Central mission command ingress and delayed failure-aware allocation view."""

from __future__ import annotations

from .allocator import AllocationScore, TaskAllocator
from .events import EventBus
from .models import (
    DroneState,
    DroneStatusReport,
    EventCategory,
    EventType,
    MessageType,
    MissionCommand,
    MissionTask,
    NetworkMessage,
    TaskStatus,
)


class MissionManager:
    def __init__(self, allocator: TaskAllocator, events: EventBus, peer_timeout: float) -> None:
        self.allocator = allocator
        self.events = events
        self.peer_timeout = peer_timeout
        self.tasks: dict[str, MissionTask] = {}
        self.reports: dict[str, DroneStatusReport] = {}
        self.last_contact: dict[str, float] = {}
        self.unavailable: set[str] = set()
        self.pending_replan = False
        self._replan_not_before = 0.0
        self._sequence = 0
        self.overall_capability = 0.0

    def submit_mission(self, command: MissionCommand, now: float) -> MissionTask:
        task = MissionTask(**command.model_dump(), created_at=now)
        self.tasks[task.id] = task
        self.pending_replan = True
        self.events.emit(
            now,
            EventCategory.MISSION,
            EventType.TASK_CREATED,
            "mission-control",
            f"Created {task.type} task {task.id}",
            [task.id],
            {"task": task.model_dump(mode="json")},
        )
        return task

    def handle_message(self, message: NetworkMessage, now: float) -> None:
        if message.type in {MessageType.HEARTBEAT, MessageType.STATUS}:
            payload = message.payload
            self.reports[message.sender_id] = DroneStatusReport(
                node_id=message.sender_id,
                timestamp=now,
                state=DroneState(payload["state"]),
                estimated={
                    "position": payload["estimated_position"],
                    "position_uncertainty": payload["position_uncertainty"],
                    "battery_estimate": payload["battery_estimate"],
                },
                current_task_id=payload.get("current_task_id"),
                workload=int(payload.get("workload", 0)),
                capabilities=set(payload.get("capabilities", [])),
            )
            self.last_contact[message.sender_id] = now
            if message.sender_id in self.unavailable:
                self.unavailable.remove(message.sender_id)
                self.pending_replan = True
        elif message.type == MessageType.TASK_ACK:
            task = self.tasks.get(str(message.payload.get("task_id")))
            if task and message.payload.get("accepted"):
                task.status = TaskStatus.IN_PROGRESS
        elif message.type == MessageType.TASK_COMPLETE:
            task = self.tasks.get(str(message.payload.get("task_id")))
            if task:
                task.progress = 1.0
                task.status = TaskStatus.COMPLETED
                self.events.emit(
                    now,
                    EventCategory.MISSION,
                    EventType.TASK_COMPLETED,
                    message.sender_id,
                    f"{message.sender_id} completed {task.id}",
                    [task.id, message.sender_id],
                )

    def detect_timeouts(self, now: float) -> list[str]:
        newly_lost: list[str] = []
        for node_id, last_seen in sorted(self.last_contact.items()):
            if node_id not in self.unavailable and now - last_seen > self.peer_timeout:
                self.unavailable.add(node_id)
                newly_lost.append(node_id)
                self.events.emit(
                    now,
                    EventCategory.FAILURE,
                    EventType.HEARTBEAT_TIMEOUT,
                    "mission-control",
                    f"No heartbeat from {node_id} for {now - last_seen:.1f}s",
                    [node_id],
                    {"last_seen": last_seen, "timeout": self.peer_timeout},
                )
                for task in self.tasks.values():
                    if node_id in task.assigned_nodes:
                        task.assigned_nodes.remove(node_id)
                        task.status = TaskStatus.DEGRADED
                        self.pending_replan = True
                        self._replan_not_before = now + 1e-6
        return newly_lost

    def allocate(self, now: float) -> list[NetworkMessage]:
        if not self.pending_replan or now < self._replan_not_before:
            return []
        reports = [
            report
            for node_id, report in sorted(self.reports.items())
            if node_id not in self.unavailable and now - self.last_contact.get(node_id, 0.0) <= self.peer_timeout
        ]
        if not reports:
            return []
        outgoing: list[NetworkMessage] = []
        still_pending = False
        for task in sorted(self.tasks.values(), key=lambda item: (-item.priority, item.created_at, item.id)):
            if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                continue
            needed = max(0, task.desired_units - len(task.assigned_nodes))
            if needed == 0:
                continue
            scores = self.allocator.select(task, reports, set(task.assigned_nodes), needed)
            if not scores:
                still_pending = True
                continue
            was_degraded = task.status == TaskStatus.DEGRADED
            for score in scores:
                if score.node_id in task.assigned_nodes:
                    continue
                task.assigned_nodes.append(score.node_id)
                self.reports[score.node_id].workload += 1
                outgoing.append(self._assignment(task, score, now))
                event_type = EventType.TASK_REASSIGNED if was_degraded else EventType.TASK_ASSIGNED
                self.events.emit(
                    now,
                    EventCategory.ALLOCATION,
                    event_type,
                    "mission-control",
                    f"Assigned {score.node_id} to {task.id} (cost {score.cost:.1f})",
                    [task.id, score.node_id],
                    {"cost": score.cost, "components": score.components},
                )
            task.status = TaskStatus.ASSIGNED
            if len(task.assigned_nodes) < task.desired_units:
                still_pending = True
        self.pending_replan = still_pending
        self._replan_not_before = 0.0
        return outgoing

    def _assignment(self, task: MissionTask, score: AllocationScore, now: float) -> NetworkMessage:
        self._sequence += 1
        return NetworkMessage(
            sender_id="mission-control",
            recipient_id=score.node_id,
            timestamp_sent=now,
            type=MessageType.TASK_ASSIGNMENT,
            payload={"task": task.model_dump(mode="json"), "allocation_cost": score.cost},
            sequence_number=self._sequence,
        )

    def update_capability(self, now: float) -> float:
        previous = self.overall_capability
        weighted = 0.0
        weights = 0.0
        for task in self.tasks.values():
            if task.status == TaskStatus.COMPLETED:
                task.capability = 1.0
            else:
                effective = sum(1 for node in task.assigned_nodes if node not in self.unavailable)
                resource = min(1.0, effective / task.desired_units)
                minimum_gate = 1.0 if effective >= task.minimum_units else (effective / task.minimum_units)
                task.capability = max(0.0, min(1.0, resource * minimum_gate))
            weight = max(1, task.priority)
            weighted += task.capability * weight
            weights += weight
        self.overall_capability = weighted / weights if weights else 1.0
        if abs(previous - self.overall_capability) >= 0.001:
            self.events.emit(
                now,
                EventCategory.MISSION,
                EventType.MISSION_CAPABILITY_CHANGED,
                "mission-control",
                f"Overall mission capability changed from {previous:.0%} to {self.overall_capability:.0%}",
                self.tasks.keys(),
                {"previous": previous, "current": self.overall_capability},
            )
        return self.overall_capability
