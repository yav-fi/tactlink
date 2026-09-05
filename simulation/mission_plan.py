"""Canonical multi-objective mission plan contracts.

``MissionCommand`` stays the single execution primitive of the runtime.  A
``MissionPlan`` is a *typed, declarative* layer above it: several objectives,
the conditions that activate them, the constraints they run under, and the
conditions that end them.  Nothing here executes anything.  A plan is compiled
down into ordinary ``MissionCommand`` values by
``integrations.mission_compiler.runtime`` before the runtime ever sees it.

Everything is plain structured data on purpose: no expressions, no callables,
no generated code.  A trigger is a fixed enum plus operands, so evaluating one
is a deterministic comparison the runtime owns.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .models import MissionTarget, TaskType


class ObjectiveStatus(StrEnum):
    PROPOSED = "PROPOSED"
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ActivationMode(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    ON_TRIGGER = "ON_TRIGGER"
    ON_DEPENDENCY = "ON_DEPENDENCY"


class TriggerEvent(StrEnum):
    """Conditions the runtime can already answer from its own state."""

    ENTITY_OBSERVED = "ENTITY_OBSERVED"
    OBJECTIVE_COMPLETED = "OBJECTIVE_COMPLETED"
    OBJECTIVE_DEGRADED = "OBJECTIVE_DEGRADED"
    BATTERY_BELOW = "BATTERY_BELOW"
    NETWORK_HEALTH_BELOW = "NETWORK_HEALTH_BELOW"
    EFFECTIVENESS_BELOW = "EFFECTIVENESS_BELOW"
    COVERAGE_ABOVE = "COVERAGE_ABOVE"
    CONTROL_LOST = "CONTROL_LOST"
    LOCALIZATION_DEGRADED = "LOCALIZATION_DEGRADED"


class TriggerAction(StrEnum):
    ACTIVATE_OBJECTIVE = "ACTIVATE_OBJECTIVE"
    SUSPEND_OBJECTIVE = "SUSPEND_OBJECTIVE"
    CANCEL_OBJECTIVE = "CANCEL_OBJECTIVE"
    SET_PRIORITY = "SET_PRIORITY"


class ConstraintType(StrEnum):
    BATTERY_RESERVE = "BATTERY_RESERVE"
    MAINTAIN_NETWORK = "MAINTAIN_NETWORK"
    MAX_UNITS = "MAX_UNITS"
    KEEP_UNITS_AVAILABLE = "KEEP_UNITS_AVAILABLE"
    REQUIRE_CAPABILITY = "REQUIRE_CAPABILITY"


class Enforcement(StrEnum):
    ADVISORY = "ADVISORY"
    BLOCKING = "BLOCKING"


class CompletionType(StrEnum):
    TASK_COMPLETE = "TASK_COMPLETE"
    COVERAGE_AT_LEAST = "COVERAGE_AT_LEAST"
    DURATION_SECONDS = "DURATION_SECONDS"
    OPERATOR_ONLY = "OPERATOR_ONLY"


class TerminationType(StrEnum):
    ALL_OBJECTIVES_COMPLETE = "ALL_OBJECTIVES_COMPLETE"
    EFFECTIVENESS_BELOW = "EFFECTIVENESS_BELOW"
    TIME_LIMIT_SECONDS = "TIME_LIMIT_SECONDS"
    OPERATOR_ABORT = "OPERATOR_ABORT"


class DependencyKind(StrEnum):
    REQUIRES_COMPLETION = "REQUIRES_COMPLETION"
    REQUIRES_ACTIVE = "REQUIRES_ACTIVE"


class ContextReference(StrEnum):
    """Deictic operator references resolved deterministically, never guessed."""

    SELECTED_REGION = "SELECTED_REGION"
    MAP_CURSOR = "MAP_CURSOR"
    OPERATOR_POSITION = "OPERATOR_POSITION"
    SELECTED_DRONE = "SELECTED_DRONE"


class CompletionCondition(BaseModel):
    type: CompletionType = CompletionType.TASK_COMPLETE
    value: float | None = None


class MissionObjective(BaseModel):
    """One plan objective; ``type``/``target`` are the canonical runtime types."""

    id: str = Field(default_factory=lambda: f"obj-{uuid4().hex[:8]}")
    label: str = ""
    type: TaskType
    target: MissionTarget = Field(default_factory=MissionTarget)
    priority: int = Field(default=50, ge=0, le=100)
    desired_units: int = Field(default=1, ge=1)
    minimum_units: int = Field(default=1, ge=1)
    required_capabilities: set[str] = Field(default_factory=set)
    activation: ActivationMode = ActivationMode.IMMEDIATE
    completion: CompletionCondition = Field(default_factory=CompletionCondition)
    status: ObjectiveStatus = ObjectiveStatus.PROPOSED
    task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TriggerCondition(BaseModel):
    event: TriggerEvent
    entity_id: str | None = None
    objective_id: str | None = None
    node_id: str | None = None
    region_id: str | None = None
    threshold: float | None = None


class MissionTrigger(BaseModel):
    id: str = Field(default_factory=lambda: f"trg-{uuid4().hex[:8]}")
    when: TriggerCondition
    action: TriggerAction
    objective_id: str
    priority: int | None = Field(default=None, ge=0, le=100)
    once: bool = True
    fired_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionConstraint(BaseModel):
    id: str = Field(default_factory=lambda: f"con-{uuid4().hex[:8]}")
    type: ConstraintType
    value: float | None = None
    capability: str | None = None
    objective_id: str | None = None
    enforcement: Enforcement = Enforcement.ADVISORY
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObjectiveDependency(BaseModel):
    objective_id: str
    depends_on: str
    kind: DependencyKind = DependencyKind.REQUIRES_COMPLETION


class TerminationCondition(BaseModel):
    type: TerminationType = TerminationType.ALL_OBJECTIVES_COMPLETE
    value: float | None = None


class MissionPlan(BaseModel):
    """A validated, declarative mission that compiles into MissionCommands."""

    id: str = Field(default_factory=lambda: f"plan-{uuid4().hex[:10]}")
    name: str = ""
    revision: int = 0
    created_at: float = 0.0
    objectives: list[MissionObjective] = Field(default_factory=list)
    triggers: list[MissionTrigger] = Field(default_factory=list)
    constraints: list[MissionConstraint] = Field(default_factory=list)
    dependencies: list[ObjectiveDependency] = Field(default_factory=list)
    termination: list[TerminationCondition] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def objective(self, objective_id: str) -> MissionObjective | None:
        return next((item for item in self.objectives if item.id == objective_id), None)

    def total_desired_units(self) -> int:
        return sum(
            objective.desired_units
            for objective in self.objectives
            if objective.status not in {ObjectiveStatus.CANCELLED, ObjectiveStatus.COMPLETED}
        )


class CompileStatus(StrEnum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    REJECTED = "REJECTED"


class MissionCompileResult(BaseModel):
    """The only thing a compiler hands back; a plan is optional by design."""

    status: CompileStatus
    plan: MissionPlan | None = None
    clarification_question: str | None = None
    clarification_slots: list[str] = Field(default_factory=list)
    rejection_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    interpretation_summary: str = ""
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class AmendmentType(StrEnum):
    SET_PRIORITY = "SET_PRIORITY"
    CANCEL_OBJECTIVE = "CANCEL_OBJECTIVE"
    SUSPEND_OBJECTIVE = "SUSPEND_OBJECTIVE"
    RESUME_OBJECTIVE = "RESUME_OBJECTIVE"
    SET_UNITS = "SET_UNITS"
    ADD_UNITS = "ADD_UNITS"
    ADD_OBJECTIVE = "ADD_OBJECTIVE"
    ADD_TRIGGER = "ADD_TRIGGER"
    RECALL_ALL = "RECALL_ALL"


class ObjectiveSelector(BaseModel):
    """How an amendment names an existing objective without runtime handles."""

    objective_id: str | None = None
    objective_type: TaskType | None = None
    region_id: str | None = None
    entity_id: str | None = None
    label: str | None = None


class MissionAmendment(BaseModel):
    type: AmendmentType
    selector: ObjectiveSelector = Field(default_factory=ObjectiveSelector)
    value: int | None = None
    objective: MissionObjective | None = None
    trigger: MissionTrigger | None = None
    rationale: str = ""


class MissionAmendResult(BaseModel):
    status: CompileStatus
    amendments: list[MissionAmendment] = Field(default_factory=list)
    plan: MissionPlan | None = None
    applied: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    rejection_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    interpretation_summary: str = ""
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class MissionQueryResult(BaseModel):
    """Read-only explanation. There is no action field, by construction."""

    question: str
    answer: str
    read_only: bool = True
    citations: list[str] = Field(default_factory=list)
    context_digest: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
