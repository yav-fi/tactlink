"""Bounded mission-context formatter for the explanation path.

The model is never handed a raw snapshot.  This module projects the runtime's
own state into a small, typed digest — objectives, effectiveness, network,
nodes, world knowledge, and a whitelist of decision-relevant events — and
renders it as short lines.  Bounded input is what keeps an explanation
grounded and a local model fast.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from simulation.mission_plan import MissionPlan
from simulation.models import (
    EventType,
    SimulationEvent,
    SimulationSnapshot,
)

# Events that explain *why* the mission looks the way it does. Routine
# telemetry (heartbeats, per-tick link quality) is deliberately excluded.
EXPLANATORY_EVENTS = frozenset(
    {
        EventType.TASK_CREATED,
        EventType.TASK_ASSIGNED,
        EventType.TASK_REASSIGNED,
        EventType.TASK_COMPLETED,
        EventType.TASK_AUCTION_WON,
        EventType.TASK_RELEASED,
        EventType.LEASE_EXPIRED,
        EventType.OBJECTIVE_DEGRADED,
        EventType.OBJECTIVE_RECOVERED,
        EventType.RESOURCE_REPRIORITIZED,
        EventType.MISSION_CAPABILITY_CHANGED,
        EventType.MISSION_EFFECTIVENESS_CHANGED,
        EventType.NETWORK_PARTITION,
        EventType.NETWORK_PARTITION_RISK,
        EventType.NETWORK_RECONNECTED,
        EventType.NETWORK_HEALED,
        EventType.RELAY_TASK_CREATED,
        EventType.RELAY_REPOSITIONING,
        EventType.RELAY_ESTABLISHED,
        EventType.HEARTBEAT_TIMEOUT,
        EventType.NODE_FAILED,
        EventType.NODE_RECOVERED,
        EventType.GPS_DEGRADED,
        EventType.GPS_LOST,
        EventType.GPS_RECOVERED,
        EventType.CONTROL_LOST,
        EventType.CONTROL_RECOVERED,
        EventType.INFORMATION_STALE,
    }
)


class ObjectiveDigest(BaseModel):
    task_id: str
    label: str = ""
    type: str
    target: str
    priority: int
    status: str
    assigned_nodes: list[str] = Field(default_factory=list)
    desired_units: int = 1
    progress: float = 0.0
    capability: float = 0.0
    effectiveness: float = 0.0
    effectiveness_components: dict[str, float] = Field(default_factory=dict)


class NodeDigest(BaseModel):
    node_id: str
    state: str
    role: str
    battery: float
    localization: str
    current_task_id: str | None = None
    online: bool = True


class RegionDigest(BaseModel):
    region_id: str
    coverage: float
    fresh_coverage: float
    mean_confidence: float


class EventDigest(BaseModel):
    sequence: int
    timestamp: float
    event_type: str
    summary: str
    entities: list[str] = Field(default_factory=list)


class MissionContextDigest(BaseModel):
    """Everything the explanation path is allowed to see, and nothing else."""

    simulation_time: float = 0.0
    scenario: str = ""
    control_available: bool = True
    mission_capability: float = 0.0
    mission_effectiveness: float = 0.0
    network_health: float = 1.0
    mean_link_quality: float = 1.0
    largest_component_fraction: float = 1.0
    active_nodes: int = 0
    degraded_nodes: int = 0
    gps_degraded_count: int = 0
    relay_nodes: list[str] = Field(default_factory=list)
    lost_nodes: list[str] = Field(default_factory=list)
    objectives: list[ObjectiveDigest] = Field(default_factory=list)
    nodes: list[NodeDigest] = Field(default_factory=list)
    regions: list[RegionDigest] = Field(default_factory=list)
    recent_events: list[EventDigest] = Field(default_factory=list)
    plan_objectives: list[str] = Field(default_factory=list)
    weakest_objective: str | None = None
    least_explored_region: str | None = None

    def render(self) -> str:
        lines = [
            f"time: {self.simulation_time:.1f}s  scenario: {self.scenario}  "
            f"control_link: {'online' if self.control_available else 'OFFLINE'}",
            f"mission_capability: {self.mission_capability:.0%}  "
            f"mission_effectiveness: {self.mission_effectiveness:.0%}",
            f"network: health {self.network_health:.0%}, mean_link {self.mean_link_quality:.0%}, "
            f"largest_component {self.largest_component_fraction:.0%}, active {self.active_nodes}, "
            f"degraded {self.degraded_nodes}, gps_degraded {self.gps_degraded_count}, "
            f"relays {self.relay_nodes or 'none'}, unreachable {self.lost_nodes or 'none'}",
            "objectives:",
        ]
        for objective in self.objectives:
            components = ", ".join(
                f"{key} {value:.0%}" for key, value in sorted(objective.effectiveness_components.items())
            )
            lines.append(
                f"  {objective.task_id} {objective.type} {objective.target} priority {objective.priority} "
                f"status {objective.status} assigned {objective.assigned_nodes or 'none'}"
                f"/{objective.desired_units} capability {objective.capability:.0%} "
                f"effectiveness {objective.effectiveness:.0%}"
                + (f" ({components})" if components else "")
            )
        lines.append("drones:")
        for node in self.nodes:
            lines.append(
                f"  {node.node_id} {node.state} role {node.role} battery {node.battery:.0%} "
                f"localization {node.localization} task {node.current_task_id or 'none'}"
                + ("" if node.online else " OFFLINE")
            )
        if self.regions:
            lines.append("world knowledge:")
            for region in self.regions:
                lines.append(
                    f"  {region.region_id} coverage {region.coverage:.0%} fresh {region.fresh_coverage:.0%} "
                    f"confidence {region.mean_confidence:.0%}"
                )
        if self.plan_objectives:
            lines.append("operator plan objectives: " + "; ".join(self.plan_objectives))
        lines.append("recent events (oldest first):")
        for event in self.recent_events:
            lines.append(f"  [{event.sequence}] t={event.timestamp:.1f} {event.event_type}: {event.summary}")
        if self.weakest_objective:
            lines.append(f"weakest objective: {self.weakest_objective}")
        if self.least_explored_region:
            lines.append(f"least explored region: {self.least_explored_region}")
        return "\n".join(lines)


def build_digest(
    snapshot: SimulationSnapshot,
    events: Sequence[SimulationEvent] = (),
    plans: Sequence[MissionPlan] = (),
    event_limit: int = 18,
) -> MissionContextDigest:
    """Project a live snapshot into the bounded explanation context."""
    objectives = [
        ObjectiveDigest(
            task_id=task.id,
            label=str(task.metadata.get("objective_label", "")),
            type=task.type.value,
            target=(
                task.target.region_id
                or task.target.entity_id
                or (
                    f"({task.target.point.x:.0f},{task.target.point.y:.0f},{task.target.point.z:.0f})"
                    if task.target.point
                    else "-"
                )
            ),
            priority=task.priority,
            status=task.status.value,
            assigned_nodes=list(task.assigned_nodes),
            desired_units=task.desired_units,
            progress=round(task.progress, 3),
            capability=round(task.capability, 3),
            effectiveness=round(task.effectiveness, 3),
            effectiveness_components=dict(task.effectiveness_components),
        )
        for task in snapshot.missions
    ]
    nodes = [
        NodeDigest(
            node_id=drone.identity.node_id,
            state=drone.state.value,
            role=drone.role,
            battery=round(drone.estimated.battery_estimate, 3),
            localization=drone.estimated.localization_mode.value,
            current_task_id=drone.current_task_id,
            online=drone.truth.online,
        )
        for drone in snapshot.drones
    ]
    regions = [
        RegionDigest(
            region_id=region.region_id,
            coverage=round(region.coverage, 3),
            fresh_coverage=round(region.fresh_coverage, 3),
            mean_confidence=round(region.mean_confidence, 3),
        )
        for region in snapshot.world_knowledge.regions
    ]
    interesting = [event for event in events if event.event_type in EXPLANATORY_EVENTS]
    recent = [
        EventDigest(
            sequence=event.sequence,
            timestamp=event.timestamp,
            event_type=event.event_type.value,
            summary=event.human_readable_summary,
            entities=list(event.affected_entities)[:4],
        )
        for event in interesting[-event_limit:]
    ]
    live = {drone.identity.node_id for drone in snapshot.drones if drone.truth.online}
    lost = sorted({drone.identity.node_id for drone in snapshot.drones} - live)
    active = [item for item in objectives if item.status not in {"COMPLETED", "CANCELLED"}]
    weakest = min(active, key=lambda item: (item.effectiveness, -item.priority), default=None)
    least_explored = min(regions, key=lambda item: item.coverage, default=None)
    return MissionContextDigest(
        simulation_time=snapshot.simulation_time,
        scenario=str(snapshot.scenario),
        control_available=snapshot.control_available,
        mission_capability=snapshot.mission_capability,
        mission_effectiveness=snapshot.mission_effectiveness,
        network_health=snapshot.network.network_health,
        mean_link_quality=snapshot.network.mean_link_quality,
        largest_component_fraction=snapshot.network.largest_component_fraction,
        active_nodes=snapshot.network.active_nodes,
        degraded_nodes=snapshot.network.degraded_nodes,
        gps_degraded_count=snapshot.network.gps_degraded_count,
        relay_nodes=list(snapshot.network.relay_nodes),
        lost_nodes=lost,
        objectives=objectives,
        nodes=nodes,
        regions=regions,
        recent_events=recent,
        plan_objectives=[
            f"{objective.label or objective.id} ({objective.type.value}, {objective.status.value}, p{objective.priority})"
            for plan in plans
            for objective in plan.objectives
        ],
        weakest_objective=(
            f"{weakest.task_id} {weakest.type} at {weakest.effectiveness:.0%} effectiveness"
            if weakest
            else None
        ),
        least_explored_region=(
            f"{least_explored.region_id} at {least_explored.coverage:.0%} coverage"
            if least_explored
            else None
        ),
    )
