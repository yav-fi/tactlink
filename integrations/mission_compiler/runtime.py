"""Compiles a MissionPlan down into MissionCommands and runs its logic.

The model never touches this module's state.  It produces validated intent;
everything here is a deterministic function of that intent plus the runtime's
own snapshot.  Triggers are enum comparisons, not expressions, so a plan can
only ever cause an action the runtime already knows how to perform.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field

from simulation.mission_plan import (
    ActivationMode,
    ConstraintType,
    DependencyKind,
    Enforcement,
    MissionConstraint,
    MissionObjective,
    MissionPlan,
    ObjectiveStatus,
    TriggerAction,
    TriggerEvent,
)
from simulation.models import (
    MissionCommand,
    MissionTarget,
    MissionTask,
    SimulationEvent,
    SimulationSnapshot,
    TaskStatus,
    TaskType,
    Vector3,
)

# Battery a node is assumed to spend per metre of the flight home. Matches
# SimulationConfig.battery_drain_per_meter; used only for the *prediction* in
# "anything predicted to fall below reserve should return".
DEFAULT_DRAIN_PER_METER = 0.00008
DEFAULT_BATTERY_RESERVE = 0.20


class MissionSubmitter(Protocol):
    """The narrow, typed surface the plan runtime is allowed to drive."""

    def __call__(self, command: MissionCommand) -> MissionTask: ...


class PlanAction(BaseModel):
    """One thing the deterministic runtime did, for the operator's audit log."""

    timestamp: float = 0.0
    plan_id: str = ""
    kind: str
    objective_id: str | None = None
    task_id: str | None = None
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


def objective_to_command(
    objective: MissionObjective,
    plan: MissionPlan,
    snapshot: SimulationSnapshot | None = None,
) -> MissionCommand | None:
    """Down-compile one objective into the canonical runtime contract.

    Returns None when the objective cannot be expressed yet — today only a
    connectivity objective with no station point derivable from a snapshot.
    """
    target = objective.target.model_copy(deep=True)
    if objective.type == TaskType.RELAY and target.point is None:
        station = _relay_station(snapshot)
        if station is None:
            return None
        target = MissionTarget(point=station)
    metadata: dict[str, Any] = {
        "plan_id": plan.id,
        "objective_id": objective.id,
        "objective_label": objective.label,
        "input_source": "mission-plan",
        **objective.metadata,
    }
    if objective.type == TaskType.RELAY:
        metadata.setdefault("network_support", True)
    return MissionCommand(
        type=objective.type,
        target=target,
        priority=objective.priority,
        required_capabilities=set(objective.required_capabilities),
        desired_units=objective.desired_units,
        minimum_units=objective.minimum_units,
        metadata=metadata,
    )


def plan_to_commands(
    plan: MissionPlan,
    snapshot: SimulationSnapshot | None = None,
) -> list[MissionCommand]:
    """Every command a plan would submit right now, in priority order."""
    ready = [
        objective
        for objective in plan.objectives
        if objective.status in {ObjectiveStatus.PENDING, ObjectiveStatus.ACTIVE}
    ]
    commands = [objective_to_command(objective, plan, snapshot) for objective in ready]
    return [command for command in commands if command is not None]


def _relay_station(snapshot: SimulationSnapshot | None) -> Vector3 | None:
    """Hold the middle of the formation; the resilience layer refines from there."""
    if snapshot is None:
        return None
    positions = [
        drone.estimated.position
        for drone in snapshot.drones
        if drone.truth.online
    ]
    if not positions:
        return None
    count = float(len(positions))
    return Vector3(
        x=round(sum(item.x for item in positions) / count, 3),
        y=round(sum(item.y for item in positions) / count, 3),
        z=round(sum(item.z for item in positions) / count, 3),
    )


class MissionPlanRuntime:
    """Owns registered plans and applies their logic on every evaluation."""

    def __init__(
        self,
        submit: MissionSubmitter,
        cancel: Callable[[str], MissionTask | None] | None = None,
        clock: Callable[[], float] | None = None,
        drain_per_meter: float = DEFAULT_DRAIN_PER_METER,
    ) -> None:
        self._submit = submit
        self._cancel = cancel
        self._clock = clock or (lambda: 0.0)
        self._drain_per_meter = drain_per_meter
        self.plans: dict[str, MissionPlan] = {}
        self.actions: list[PlanAction] = []
        self._recalled_nodes: set[str] = set()
        self._deferred: set[tuple[str, str]] = set()

    # -- registration -----------------------------------------------------

    def register(
        self,
        plan: MissionPlan,
        snapshot: SimulationSnapshot | None = None,
    ) -> list[PlanAction]:
        """Store a compiled plan and submit its immediately-active objectives."""
        self.plans[plan.id] = plan
        actions: list[PlanAction] = [
            self._record(plan, "PLAN_REGISTERED", detail=f"{len(plan.objectives)} objective(s)")
        ]
        actions.extend(self._activate_ready(plan, snapshot))
        return actions

    def plan(self, plan_id: str) -> MissionPlan | None:
        return self.plans.get(plan_id)

    # -- evaluation -------------------------------------------------------

    def evaluate(
        self,
        snapshot: SimulationSnapshot,
        events: Sequence[SimulationEvent] = (),
        observed_entities: dict[str, float] | None = None,
    ) -> list[PlanAction]:
        """Advance every registered plan against the current runtime state."""
        actions: list[PlanAction] = []
        observed = observed_entities or {}
        for plan in list(self.plans.values()):
            self._sync_objective_status(plan, snapshot)
            actions.extend(self._fire_triggers(plan, snapshot, events, observed))
            actions.extend(self._advance_dependencies(plan))
            actions.extend(self._enforce_constraints(plan, snapshot))
            actions.extend(self._activate_ready(plan, snapshot))
        self.actions.extend(actions)
        return actions

    def _sync_objective_status(self, plan: MissionPlan, snapshot: SimulationSnapshot) -> None:
        tasks = {task.id: task for task in snapshot.missions}
        for objective in plan.objectives:
            task = tasks.get(objective.task_id or "")
            if task is None:
                continue
            if task.status == TaskStatus.COMPLETED:
                objective.status = ObjectiveStatus.COMPLETED
            elif task.status == TaskStatus.CANCELLED:
                objective.status = ObjectiveStatus.CANCELLED
            elif objective.status == ObjectiveStatus.PENDING:
                objective.status = ObjectiveStatus.ACTIVE

    def _activate_ready(
        self,
        plan: MissionPlan,
        snapshot: SimulationSnapshot | None,
    ) -> list[PlanAction]:
        actions: list[PlanAction] = []
        for objective in sorted(plan.objectives, key=lambda item: -item.priority):
            if objective.status != ObjectiveStatus.PENDING or objective.task_id:
                continue
            command = objective_to_command(objective, plan, snapshot)
            if command is None:
                key = (plan.id, objective.id)
                if key not in self._deferred:
                    self._deferred.add(key)
                    actions.append(
                        self._record(
                            plan,
                            "OBJECTIVE_DEFERRED",
                            objective_id=objective.id,
                            detail="waiting for a runtime snapshot to place the relay station",
                        )
                    )
                continue
            self._deferred.discard((plan.id, objective.id))
            task = self._submit(command)
            objective.task_id = task.id
            objective.status = ObjectiveStatus.ACTIVE
            actions.append(
                self._record(
                    plan,
                    "OBJECTIVE_SUBMITTED",
                    objective_id=objective.id,
                    task_id=task.id,
                    detail=f"{objective.type.value} x{objective.desired_units} at priority {objective.priority}",
                )
            )
        return actions

    def evaluate_plan_activation(
        self,
        plan: MissionPlan,
        snapshot: SimulationSnapshot | None = None,
    ) -> list[PlanAction]:
        """Submit anything an amendment just made ready, without a full tick."""
        actions = self._activate_ready(plan, snapshot)
        self.actions.extend(actions)
        return actions

    def _advance_dependencies(self, plan: MissionPlan) -> list[PlanAction]:
        actions: list[PlanAction] = []
        for dependency in plan.dependencies:
            objective = plan.objective(dependency.objective_id)
            upstream = plan.objective(dependency.depends_on)
            if objective is None or upstream is None:
                continue
            if objective.status != ObjectiveStatus.PROPOSED:
                continue
            if objective.activation == ActivationMode.ON_TRIGGER:
                continue
            satisfied = (
                upstream.status == ObjectiveStatus.COMPLETED
                if dependency.kind == DependencyKind.REQUIRES_COMPLETION
                else upstream.status in {ObjectiveStatus.ACTIVE, ObjectiveStatus.COMPLETED}
            )
            if satisfied:
                objective.status = ObjectiveStatus.PENDING
                actions.append(
                    self._record(
                        plan,
                        "DEPENDENCY_SATISFIED",
                        objective_id=objective.id,
                        detail=f"{upstream.label or upstream.id} reached {upstream.status.value}",
                    )
                )
        return actions

    # -- triggers ---------------------------------------------------------

    def _fire_triggers(
        self,
        plan: MissionPlan,
        snapshot: SimulationSnapshot,
        events: Sequence[SimulationEvent],
        observed_entities: dict[str, float],
    ) -> list[PlanAction]:
        actions: list[PlanAction] = []
        for trigger in plan.triggers:
            if trigger.once and trigger.fired_at is not None:
                continue
            if not self._condition_met(trigger.when, plan, snapshot, events, observed_entities):
                continue
            objective = plan.objective(trigger.objective_id)
            if objective is None:
                continue
            trigger.fired_at = snapshot.simulation_time
            applied = self._apply_trigger_action(trigger.action, trigger.priority, objective, plan)
            actions.append(
                self._record(
                    plan,
                    "TRIGGER_FIRED",
                    objective_id=objective.id,
                    detail=f"{trigger.when.event.value} -> {trigger.action.value} ({applied})",
                    payload={"trigger_id": trigger.id},
                )
            )
        return actions

    def _apply_trigger_action(
        self,
        action: TriggerAction,
        priority: int | None,
        objective: MissionObjective,
        plan: MissionPlan,
    ) -> str:
        if action == TriggerAction.ACTIVATE_OBJECTIVE:
            if objective.status in {ObjectiveStatus.PROPOSED, ObjectiveStatus.SUSPENDED}:
                objective.status = ObjectiveStatus.PENDING
                return "activated"
            return f"already {objective.status.value}"
        if action == TriggerAction.SUSPEND_OBJECTIVE:
            objective.status = ObjectiveStatus.SUSPENDED
            self._release(objective)
            return "suspended"
        if action == TriggerAction.CANCEL_OBJECTIVE:
            objective.status = ObjectiveStatus.CANCELLED
            self._release(objective)
            return "cancelled"
        if action == TriggerAction.SET_PRIORITY and priority is not None:
            objective.priority = priority
            return f"priority set to {priority}"
        return "no-op"

    def _condition_met(
        self,
        condition: Any,
        plan: MissionPlan,
        snapshot: SimulationSnapshot,
        events: Sequence[SimulationEvent],
        observed_entities: dict[str, float],
    ) -> bool:
        event = condition.event
        threshold = condition.threshold
        if event == TriggerEvent.ENTITY_OBSERVED:
            entity_id = condition.entity_id
            if not entity_id:
                return False
            if entity_id in observed_entities:
                return True
            return any(entity_id in item.affected_entities for item in events)
        if event in {TriggerEvent.OBJECTIVE_COMPLETED, TriggerEvent.OBJECTIVE_DEGRADED}:
            watched = plan.objective(condition.objective_id or "")
            if watched is None or watched.task_id is None:
                return False
            task = next((item for item in snapshot.missions if item.id == watched.task_id), None)
            if task is None:
                return False
            if event == TriggerEvent.OBJECTIVE_COMPLETED:
                return task.status == TaskStatus.COMPLETED
            return task.status == TaskStatus.DEGRADED or task.effectiveness < (threshold or 0.45)
        if event == TriggerEvent.BATTERY_BELOW:
            limit = threshold if threshold is not None else DEFAULT_BATTERY_RESERVE
            drones = [
                drone for drone in snapshot.drones
                if condition.node_id is None or drone.identity.node_id == condition.node_id
            ]
            return any(drone.estimated.battery_estimate < limit for drone in drones)
        if event == TriggerEvent.NETWORK_HEALTH_BELOW:
            return snapshot.network.network_health < (threshold if threshold is not None else 0.5)
        if event == TriggerEvent.EFFECTIVENESS_BELOW:
            return snapshot.mission_effectiveness < (threshold if threshold is not None else 0.5)
        if event == TriggerEvent.COVERAGE_ABOVE:
            region_id = condition.region_id
            limit = threshold if threshold is not None else 0.9
            return any(
                region.region_id == region_id and region.coverage >= limit
                for region in snapshot.world_knowledge.regions
            )
        if event == TriggerEvent.CONTROL_LOST:
            return not snapshot.control_available
        if event == TriggerEvent.LOCALIZATION_DEGRADED:
            return snapshot.network.gps_degraded_count > 0
        return False

    # -- constraints ------------------------------------------------------

    def _enforce_constraints(
        self,
        plan: MissionPlan,
        snapshot: SimulationSnapshot,
    ) -> list[PlanAction]:
        actions: list[PlanAction] = []
        for constraint in plan.constraints:
            if constraint.type == ConstraintType.BATTERY_RESERVE:
                actions.extend(self._enforce_battery_reserve(plan, constraint, snapshot))
            elif constraint.type == ConstraintType.MAINTAIN_NETWORK:
                floor = constraint.value if constraint.value is not None else 0.5
                if snapshot.network.network_health < floor:
                    actions.append(
                        self._record(
                            plan,
                            "CONSTRAINT_BREACHED",
                            detail=(
                                f"network health {snapshot.network.network_health:.0%} is below the "
                                f"{floor:.0%} floor the operator set"
                            ),
                            payload={"constraint_id": constraint.id},
                        )
                    )
        return actions

    def _enforce_battery_reserve(
        self,
        plan: MissionPlan,
        constraint: MissionConstraint,
        snapshot: SimulationSnapshot,
    ) -> list[PlanAction]:
        reserve = constraint.value if constraint.value is not None else DEFAULT_BATTERY_RESERVE
        base = Vector3()
        at_risk: list[str] = []
        for drone in snapshot.drones:
            if not drone.truth.online:
                continue
            node_id = drone.identity.node_id
            if node_id in self._recalled_nodes:
                continue
            distance_home = drone.estimated.position.distance_to(base)
            predicted = drone.estimated.battery_estimate - distance_home * self._drain_per_meter
            if predicted < reserve:
                at_risk.append(node_id)
        if not at_risk:
            return []
        self._recalled_nodes.update(at_risk)
        actions = [
            self._record(
                plan,
                "RESERVE_RECALL",
                detail=(
                    f"{', '.join(at_risk)} predicted below the {reserve:.0%} reserve on return; "
                    f"submitting a recall objective"
                ),
                payload={"nodes": at_risk, "reserve": reserve},
            )
        ]
        if constraint.enforcement == Enforcement.BLOCKING:
            command = MissionCommand(
                type=TaskType.RETURN,
                target=MissionTarget(),
                priority=95,
                desired_units=len(at_risk),
                minimum_units=1,
                metadata={
                    "plan_id": plan.id,
                    "input_source": "mission-plan-constraint",
                    "constraint_id": constraint.id,
                    "recall_nodes": at_risk,
                    "battery_reserve": reserve,
                },
            )
            task = self._submit(command)
            actions.append(
                self._record(
                    plan,
                    "OBJECTIVE_SUBMITTED",
                    task_id=task.id,
                    detail=f"RETURN x{len(at_risk)} enforcing the battery reserve",
                )
            )
        return actions

    # -- helpers ----------------------------------------------------------

    def _release(self, objective: MissionObjective) -> None:
        if objective.task_id and self._cancel is not None:
            self._cancel(objective.task_id)
        objective.task_id = None

    def _record(
        self,
        plan: MissionPlan,
        kind: str,
        objective_id: str | None = None,
        task_id: str | None = None,
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> PlanAction:
        return PlanAction(
            timestamp=self._clock(),
            plan_id=plan.id,
            kind=kind,
            objective_id=objective_id,
            task_id=task_id,
            detail=detail,
            payload=payload or {},
        )

    def recent_actions(self, limit: int = 40) -> list[PlanAction]:
        return self.actions[-limit:]


def summarize_actions(actions: Iterable[PlanAction]) -> str:
    return "\n".join(f"  {action.kind}: {action.detail}" for action in actions)
