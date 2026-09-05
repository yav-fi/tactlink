"""Deterministic policy gate applied *after* the model, never by the model.

Model output is untrusted text.  Schema validation proves the shape; semantic
validation proves the objective is executable; this module proves the plan is
allowed.  Nothing here consults the model, so no prompt can widen it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from simulation.mission_plan import (
    ConstraintType,
    MissionObjective,
    MissionPlan,
    ObjectiveStatus,
    TaskType,
)

from .context import OperatorContext

# Only surfaced as a warning: the defence is validation, not pattern matching.
INJECTION_PATTERNS = (
    r"ignore (?:your|all|the|previous|above)\s+(?:system\s+)?(?:prompt|instructions?|rules?)",
    r"disregard (?:your|all|the|previous)\b",
    r"\bprint\s+python\b",
    r"\bexec\b|\beval\(|\bsubprocess\b|\bos\.system\b",
    r"you are now\b|new system prompt\b|act as (?:a|an)\b",
    r"return whatever json\b|even if invalid\b|skip validation\b",
    r"delete (?:the )?(?:constraints|rules|limits|safety)",
    r"reveal (?:your|the) (?:system )?prompt",
)

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class MissionPolicy(BaseModel):
    """Hard limits an operator plan may never exceed."""

    max_objectives: int = Field(default=8, ge=1)
    max_triggers: int = Field(default=8, ge=0)
    max_constraints: int = Field(default=8, ge=0)
    max_units_per_objective: int = Field(default=8, ge=1)
    max_units_per_plan: int = Field(default=12, ge=1)
    max_text_length: int = Field(default=400, ge=1)
    max_metadata_keys: int = Field(default=12, ge=0)
    reject_on_resource_shortfall: bool = True
    allow_unknown_entities: bool = True
    minimum_battery_reserve: float = Field(default=0.10, ge=0.0, le=1.0)


@dataclass
class PolicyVerdict:
    allowed: bool = True
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarification_slots: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.allowed = False
        self.violations.append(reason)


def detect_injection(text: str) -> list[str]:
    """Report suspicious operator/model text so the audit trail records it."""
    lowered = text.lower()
    return [pattern for pattern in INJECTION_PATTERNS if re.search(pattern, lowered)]


def sanitize_text(text: str, limit: int) -> str:
    cleaned = CONTROL_CHARACTERS.sub("", text).strip()
    return cleaned[:limit]


def evaluate_plan(
    plan: MissionPlan,
    context: OperatorContext,
    policy: MissionPolicy | None = None,
) -> PolicyVerdict:
    """Check a schema-valid, semantically valid plan against operator policy."""
    policy = policy or MissionPolicy()
    verdict = PolicyVerdict()

    if not plan.objectives:
        verdict.reject("plan contains no objectives")
        return verdict
    if len(plan.objectives) > policy.max_objectives:
        verdict.reject(
            f"plan requests {len(plan.objectives)} objectives; limit is {policy.max_objectives}"
        )
    if len(plan.triggers) > policy.max_triggers:
        verdict.reject(f"plan requests {len(plan.triggers)} triggers; limit is {policy.max_triggers}")
    if len(plan.constraints) > policy.max_constraints:
        verdict.reject(
            f"plan requests {len(plan.constraints)} constraints; limit is {policy.max_constraints}"
        )

    for objective in plan.objectives:
        _check_objective(objective, context, policy, verdict)

    requested = plan.total_desired_units()
    if requested > policy.max_units_per_plan:
        verdict.reject(
            f"plan commits {requested} units; policy limit is {policy.max_units_per_plan}"
        )
    available = context.availability.available_units
    if available and requested > available and policy.reject_on_resource_shortfall:
        verdict.reject(
            f"resource shortfall: plan needs {requested} units but {available} are available"
        )
    elif available and requested > available:
        verdict.warnings.append(
            f"plan needs {requested} units but only {available} are available; objectives will queue"
        )

    for constraint in plan.constraints:
        if (
            constraint.type == ConstraintType.BATTERY_RESERVE
            and constraint.value is not None
            and constraint.value < policy.minimum_battery_reserve
        ):
            verdict.warnings.append(
                f"battery reserve {constraint.value:.0%} is below the {policy.minimum_battery_reserve:.0%} floor; "
                f"raising it to the floor"
            )
            constraint.value = policy.minimum_battery_reserve
        if constraint.type == ConstraintType.REQUIRE_CAPABILITY and not constraint.capability:
            verdict.reject("REQUIRE_CAPABILITY constraint needs a capability name")

    known_ids = {objective.id for objective in plan.objectives}
    for trigger in plan.triggers:
        if trigger.objective_id not in known_ids:
            verdict.reject(f"trigger {trigger.id} targets unknown objective {trigger.objective_id}")
        if trigger.when.objective_id and trigger.when.objective_id not in known_ids:
            verdict.reject(
                f"trigger {trigger.id} watches unknown objective {trigger.when.objective_id}"
            )
        if trigger.when.threshold is not None and not 0.0 <= trigger.when.threshold <= 1.0:
            verdict.reject(f"trigger {trigger.id} threshold must be a 0..1 fraction")
    for dependency in plan.dependencies:
        if dependency.objective_id not in known_ids or dependency.depends_on not in known_ids:
            verdict.reject(f"dependency references an unknown objective: {dependency}")
        if dependency.objective_id == dependency.depends_on:
            verdict.reject(f"objective {dependency.objective_id} cannot depend on itself")
    if _has_dependency_cycle(plan):
        verdict.reject("objective dependencies contain a cycle")

    if len(plan.metadata) > policy.max_metadata_keys:
        verdict.reject("plan metadata exceeds the allowed key count")

    return verdict


def _check_objective(
    objective: MissionObjective,
    context: OperatorContext,
    policy: MissionPolicy,
    verdict: PolicyVerdict,
) -> None:
    if objective.desired_units > policy.max_units_per_objective:
        verdict.reject(
            f"objective {objective.label or objective.id} requests {objective.desired_units} units; "
            f"limit is {policy.max_units_per_objective}"
        )
    if objective.minimum_units > objective.desired_units:
        verdict.reject(f"objective {objective.id} minimum_units exceeds desired_units")
    if objective.status == ObjectiveStatus.CANCELLED:
        verdict.warnings.append(f"objective {objective.id} was emitted already cancelled")

    unknown = sorted(
        capability
        for capability in objective.required_capabilities
        if context.known_capabilities and capability not in context.known_capabilities
    )
    if unknown:
        verdict.reject(
            f"objective {objective.label or objective.id} requires unknown capabilities: {', '.join(unknown)}"
        )
    for capability in sorted(objective.required_capabilities):
        supply = context.availability.capability_counts.get(capability)
        if supply is not None and objective.desired_units > supply:
            verdict.reject(
                f"objective {objective.label or objective.id} needs {objective.desired_units} "
                f"{capability}-capable units but {supply} exist"
            )

    if objective.type == TaskType.FOLLOW and objective.target.entity_id:
        known = context.known_entities
        if known and objective.target.entity_id not in known and not policy.allow_unknown_entities:
            verdict.reject(f"unknown entity {objective.target.entity_id}")
        elif known and objective.target.entity_id not in known:
            verdict.warnings.append(
                f"{objective.target.entity_id} has not been observed yet; the objective waits for it"
            )
    if objective.target.region_id and context.known_regions:
        if objective.target.region_id not in context.known_regions:
            verdict.reject(f"unknown region {objective.target.region_id}")


def _has_dependency_cycle(plan: MissionPlan) -> bool:
    edges: dict[str, set[str]] = {}
    for dependency in plan.dependencies:
        edges.setdefault(dependency.objective_id, set()).add(dependency.depends_on)
    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str) -> bool:
        if node in visiting:
            return True
        if node in done:
            return False
        visiting.add(node)
        for successor in edges.get(node, ()):
            if walk(successor):
                return True
        visiting.discard(node)
        done.add(node)
        return False

    return any(walk(node) for node in list(edges))
