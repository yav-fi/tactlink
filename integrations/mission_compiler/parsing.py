"""Strict parsing, deictic grounding, and semantic validation of model output.

The model emits one JSON object in a bounded plan DSL.  Nothing here trusts
it: unknown keys are refused, every enum is re-parsed, deictic references are
substituted from real operator context, and each objective is checked against
the same executability rules the single-command path already enforces.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from simulation.mission_plan import (
    ActivationMode,
    CompileStatus,
    CompletionCondition,
    ContextReference,
    MissionCompileResult,
    MissionConstraint,
    MissionObjective,
    MissionPlan,
    MissionTrigger,
    ObjectiveDependency,
    ObjectiveStatus,
    TerminationCondition,
    TriggerCondition,
)
from simulation.models import MissionTarget, TaskType, Vector3

from .context import OperatorContext
from .policy import MissionPolicy, PolicyVerdict, detect_injection, evaluate_plan, sanitize_text

TOP_LEVEL_KEYS = {
    "status",
    "summary",
    "clarification_question",
    "name",
    "objectives",
    "triggers",
    "constraints",
    "dependencies",
    "termination",
}
OBJECTIVE_KEYS = {
    "id",
    "label",
    "type",
    "target",
    "priority",
    "desired_units",
    "minimum_units",
    "required_capabilities",
    "activation",
    "completion",
}
TARGET_KEYS = {"point", "waypoints", "region_id", "entity_id", "reference"}
TRIGGER_KEYS = {"id", "when", "action", "objective_id", "priority", "once"}
WHEN_KEYS = {"event", "entity_id", "objective_id", "node_id", "region_id", "threshold"}
CONSTRAINT_KEYS = {"id", "type", "value", "capability", "objective_id", "enforcement"}
DEPENDENCY_KEYS = {"objective_id", "depends_on", "kind"}
TERMINATION_KEYS = {"type", "value"}

# MAINTAIN_NETWORK is the operator's word for the runtime's RELAY task type.
TYPE_ALIASES = {
    "MAINTAIN_NETWORK": TaskType.RELAY,
    "MAINTAIN_CONNECTIVITY": TaskType.RELAY,
    "NETWORK": TaskType.RELAY,
    "RELAY": TaskType.RELAY,
    "MONITOR": TaskType.WATCH,
    "OBSERVE": TaskType.WATCH,
    "PATROL": TaskType.SEARCH,
    "TRACK": TaskType.FOLLOW,
    "RTB": TaskType.RETURN,
    "RETURN_TO_BASE": TaskType.RETURN,
    "LAND": TaskType.HOLD,
    "LOITER": TaskType.HOLD,
}

# Objective types that legitimately carry no target of their own.
TARGETLESS_TYPES = {TaskType.HOLD, TaskType.RETURN, TaskType.RELAY}


class PlanParseError(ValueError):
    """The model response was not safe to treat as a mission plan."""


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else stripped


def load_json_object(text: str) -> dict[str, Any]:
    raw = strip_markdown_fence(text)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanParseError(f"local model returned invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise PlanParseError("local model response must be one JSON object")
    return payload


def _refuse_unknown(payload: dict[str, Any], allowed: set[str], where: str) -> None:
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise PlanParseError(f"unexpected {where} fields: {', '.join(unexpected)}")


def _as_int(value: Any, field: str, low: int, high: int, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanParseError(f"{field} must be a number")
    number = int(value)
    if not low <= number <= high:
        raise PlanParseError(f"{field} must be between {low} and {high}")
    return number


def _as_vector(value: Any, field: str) -> Vector3:
    if not isinstance(value, dict):
        raise PlanParseError(f"{field} must be an object with x/y/z")
    _refuse_unknown(value, {"x", "y", "z"}, field)
    try:
        return Vector3.model_validate(value)
    except ValidationError as exc:
        raise PlanParseError(f"{field} is not a valid point: {exc.error_count()} error(s)") from exc


def _task_type(value: Any) -> TaskType:
    if not isinstance(value, str):
        raise PlanParseError("objective type must be a string")
    name = value.strip().upper()
    if name in TYPE_ALIASES:
        return TYPE_ALIASES[name]
    try:
        return TaskType(name)
    except ValueError as exc:
        raise PlanParseError(f"unsupported objective type: {value}") from exc


class _GroundedTarget:
    """A target plus the clarification slot it needs, if it could not ground."""

    def __init__(self, target: MissionTarget, slot: str | None = None, note: str | None = None) -> None:
        self.target = target
        self.slot = slot
        self.note = note


def _ground_target(
    payload: Any,
    objective_type: TaskType,
    label: str,
    context: OperatorContext,
) -> _GroundedTarget:
    target = MissionTarget()
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise PlanParseError("objective target must be an object")
    _refuse_unknown(payload, TARGET_KEYS, "target")

    reference = payload.get("reference")
    if reference is not None:
        if not isinstance(reference, str):
            raise PlanParseError("target reference must be a string")
        try:
            token = ContextReference(reference.strip().upper())
        except ValueError as exc:
            raise PlanParseError(f"unsupported context reference: {reference}") from exc
        resolved = context.resolve(token)
        if resolved is None:
            return _GroundedTarget(
                target,
                slot=f"target:{label}",
                note=f"the operator referred to {token.value.replace('_', ' ').lower()} but none was provided",
            )
        if token == ContextReference.SELECTED_REGION:
            target.region_id = str(resolved)
        elif token in {ContextReference.MAP_CURSOR, ContextReference.OPERATOR_POSITION}:
            target.point = Vector3.model_validate(resolved)
        elif token == ContextReference.SELECTED_DRONE:
            # A named drone constrains *who* executes, not *where*; keep it in metadata.
            return _GroundedTarget(target, note=f"selected drone {resolved}")
        return _GroundedTarget(target)

    if payload.get("region_id") is not None:
        raw_region = payload["region_id"]
        if not isinstance(raw_region, str):
            raise PlanParseError("region_id must be a string")
        canonical = context.canonical_region(raw_region)
        if canonical is None:
            return _GroundedTarget(
                target,
                slot=f"region:{label}",
                note=f"'{sanitize_text(raw_region, 40)}' does not match a known region",
            )
        target.region_id = canonical
    if payload.get("entity_id") is not None:
        raw_entity = payload["entity_id"]
        if not isinstance(raw_entity, str):
            raise PlanParseError("entity_id must be a string")
        target.entity_id = context.canonical_entity(raw_entity)
    if payload.get("point") is not None:
        target.point = _as_vector(payload["point"], "target.point")
    waypoints = payload.get("waypoints")
    if waypoints:
        if not isinstance(waypoints, list) or len(waypoints) > 24:
            raise PlanParseError("waypoints must be a list of at most 24 points")
        target.waypoints = [_as_vector(item, "waypoint") for item in waypoints]

    selectors = [
        target.point is not None,
        bool(target.waypoints),
        target.region_id is not None,
        target.entity_id is not None,
    ]
    if sum(selectors) > 1 and not (target.region_id and target.waypoints):
        raise PlanParseError("objective target must use exactly one selector")
    if not any(selectors) and objective_type not in TARGETLESS_TYPES:
        # A READY model response with an empty target is malformed, not an
        # operator-context gap. Genuine deictic ambiguity is represented by a
        # typed ``reference`` above and becomes NEEDS_CLARIFICATION there.
        raise PlanParseError(
            f"{objective_type.value} objective {label} omitted its required target"
        )
    return _GroundedTarget(target)


def _validate_objective_semantics(objective: MissionObjective) -> None:
    """Same executability rules the single-command path enforces."""
    target = objective.target
    if objective.type in {TaskType.GOTO, TaskType.WATCH, TaskType.REGROUP} and not (
        target.point or target.region_id
    ):
        raise PlanParseError(f"{objective.type} requires a point or region target")
    if objective.type == TaskType.SEARCH and not (target.region_id or target.waypoints):
        raise PlanParseError("SEARCH requires a region or waypoints")
    if objective.type == TaskType.TRACE and not target.waypoints:
        raise PlanParseError("TRACE requires waypoints")
    if objective.type == TaskType.FOLLOW and not target.entity_id:
        raise PlanParseError("FOLLOW requires an entity_id")
    if objective.minimum_units > objective.desired_units:
        raise PlanParseError("minimum_units cannot exceed desired_units")


def parse_objective(
    payload: Any,
    index: int,
    context: OperatorContext,
    slots: list[str],
    notes: list[str],
) -> MissionObjective:
    if not isinstance(payload, dict):
        raise PlanParseError("each objective must be a JSON object")
    _refuse_unknown(payload, OBJECTIVE_KEYS, "objective")
    objective_type = _task_type(payload.get("type"))
    raw_label = payload.get("label") or f"{objective_type.value} {index + 1}"
    label = sanitize_text(str(raw_label), 60)
    # A clarification reads back to the operator, so name the objective by type
    # when the label is a whole clause rather than a short name.
    display = label if len(label) <= 28 else f"the {objective_type.value} objective"
    grounded = _ground_target(payload.get("target"), objective_type, display, context)
    if grounded.slot:
        slots.append(grounded.slot)
    if grounded.note:
        notes.append(f"{label}: {grounded.note}")

    capabilities = payload.get("required_capabilities") or []
    if not isinstance(capabilities, list) or len(capabilities) > 6:
        raise PlanParseError("required_capabilities must be a list of at most 6 names")
    capability_set = {sanitize_text(str(item), 32).lower() for item in capabilities if str(item).strip()}

    activation_raw = payload.get("activation") or ActivationMode.IMMEDIATE.value
    try:
        activation = ActivationMode(str(activation_raw).strip().upper())
    except ValueError as exc:
        raise PlanParseError(f"unsupported activation mode: {activation_raw}") from exc

    completion = CompletionCondition()
    if payload.get("completion") is not None:
        completion_payload = payload["completion"]
        if not isinstance(completion_payload, dict):
            raise PlanParseError("completion must be an object")
        _refuse_unknown(completion_payload, {"type", "value"}, "completion")
        try:
            completion = CompletionCondition.model_validate(completion_payload)
        except ValidationError as exc:
            raise PlanParseError(f"invalid completion condition: {exc.error_count()} error(s)") from exc

    objective_id = sanitize_text(str(payload.get("id") or f"obj-{index + 1}"), 40) or f"obj-{index + 1}"
    desired = _as_int(payload.get("desired_units"), "desired_units", 1, 999, 1)
    minimum = _as_int(payload.get("minimum_units"), "minimum_units", 1, 999, 1)
    objective = MissionObjective(
        id=objective_id,
        label=label,
        type=objective_type,
        target=grounded.target,
        priority=_as_int(payload.get("priority"), "priority", 0, 100, 50),
        desired_units=desired,
        minimum_units=min(minimum, desired),
        required_capabilities=capability_set,
        activation=activation,
        completion=completion,
        metadata={"source": "llm-mission-compiler"},
    )
    if grounded.note and grounded.note.startswith("selected drone"):
        objective.metadata["preferred_node_id"] = context.selected_drone_id
    if not grounded.slot:
        _validate_objective_semantics(objective)
    return objective


def _parse_trigger(payload: Any, index: int) -> MissionTrigger:
    if not isinstance(payload, dict):
        raise PlanParseError("each trigger must be a JSON object")
    _refuse_unknown(payload, TRIGGER_KEYS, "trigger")
    when_payload = payload.get("when")
    if not isinstance(when_payload, dict):
        raise PlanParseError("trigger.when must be an object")
    _refuse_unknown(when_payload, WHEN_KEYS, "trigger.when")
    try:
        condition = TriggerCondition.model_validate(when_payload)
        trigger = MissionTrigger(
            id=sanitize_text(str(payload.get("id") or f"trg-{index + 1}"), 40),
            when=condition,
            action=payload.get("action", "ACTIVATE_OBJECTIVE"),
            objective_id=sanitize_text(str(payload.get("objective_id", "")), 40),
            priority=payload.get("priority"),
            once=bool(payload.get("once", True)),
        )
    except ValidationError as exc:
        raise PlanParseError(f"invalid trigger: {exc.error_count()} error(s)") from exc
    if not trigger.objective_id:
        raise PlanParseError("trigger must name the objective it acts on")
    return trigger


def _parse_constraint(payload: Any, index: int) -> MissionConstraint:
    if not isinstance(payload, dict):
        raise PlanParseError("each constraint must be a JSON object")
    _refuse_unknown(payload, CONSTRAINT_KEYS, "constraint")
    try:
        return MissionConstraint.model_validate(
            {**payload, "id": sanitize_text(str(payload.get("id") or f"con-{index + 1}"), 40)}
        )
    except ValidationError as exc:
        raise PlanParseError(f"invalid constraint: {exc.error_count()} error(s)") from exc


def _clarification_question(slots: list[str], notes: list[str], context: OperatorContext) -> str:
    regions = ", ".join(context.known_regions) if context.known_regions else "none loaded"
    subjects = [slot.split(":", 1)[1] for slot in slots if ":" in slot]
    subject_text = subjects[0] if len(subjects) == 1 else ", ".join(subjects)
    detail = f" ({notes[0]})" if notes else ""
    if any(slot.startswith("region:") for slot in slots):
        return (
            f"Which region should {subject_text} use?{detail} Known regions: {regions}."
        )
    return (
        f"Which location or region should {subject_text} use?{detail} "
        f"Select a region on the map or name one of: {regions}."
    )


def parse_plan_response(
    text: str,
    context: OperatorContext,
    policy: MissionPolicy | None = None,
    now: float = 0.0,
) -> MissionCompileResult:
    """Turn one raw model response into a validated MissionCompileResult."""
    policy = policy or MissionPolicy()
    warnings: list[str] = []
    injections = detect_injection(text)
    if injections:
        warnings.append(f"model output matched {len(injections)} injection pattern(s); output was ignored beyond schema")

    payload = load_json_object(text)
    _refuse_unknown(payload, TOP_LEVEL_KEYS, "top-level")

    declared_status = str(payload.get("status", CompileStatus.READY.value)).strip().upper()
    if declared_status not in {item.value for item in CompileStatus}:
        raise PlanParseError(f"unsupported status: {declared_status}")
    summary = sanitize_text(str(payload.get("summary", "")), policy.max_text_length)

    if declared_status == CompileStatus.REJECTED.value:
        return MissionCompileResult(
            status=CompileStatus.REJECTED,
            rejection_reason=summary or "the model declined to compile this instruction",
            interpretation_summary=summary,
            warnings=warnings,
        )

    slots: list[str] = []
    notes: list[str] = []
    raw_objectives = payload.get("objectives") or []
    if not isinstance(raw_objectives, list):
        raise PlanParseError("objectives must be a list")
    if len(raw_objectives) > policy.max_objectives:
        raise PlanParseError(
            f"plan declares {len(raw_objectives)} objectives; limit is {policy.max_objectives}"
        )
    objectives = [
        parse_objective(item, index, context, slots, notes)
        for index, item in enumerate(raw_objectives)
    ]

    if declared_status == CompileStatus.NEEDS_CLARIFICATION.value or slots:
        question = sanitize_text(str(payload.get("clarification_question") or ""), 240)
        if slots or not question:
            question = _clarification_question(slots or ["target:this objective"], notes, context)
        return MissionCompileResult(
            status=CompileStatus.NEEDS_CLARIFICATION,
            clarification_question=question,
            clarification_slots=sorted(set(slots)),
            interpretation_summary=summary,
            warnings=warnings + notes,
        )

    raw_triggers = payload.get("triggers") or []
    raw_constraints = payload.get("constraints") or []
    raw_dependencies = payload.get("dependencies") or []
    raw_termination = payload.get("termination") or []
    for name, value in (
        ("triggers", raw_triggers),
        ("constraints", raw_constraints),
        ("dependencies", raw_dependencies),
        ("termination", raw_termination),
    ):
        if not isinstance(value, list):
            raise PlanParseError(f"{name} must be a list")

    triggers = [_parse_trigger(item, index) for index, item in enumerate(raw_triggers)]
    constraints = [_parse_constraint(item, index) for index, item in enumerate(raw_constraints)]
    dependencies: list[ObjectiveDependency] = []
    for item in raw_dependencies:
        if not isinstance(item, dict):
            raise PlanParseError("each dependency must be a JSON object")
        _refuse_unknown(item, DEPENDENCY_KEYS, "dependency")
        try:
            dependencies.append(ObjectiveDependency.model_validate(item))
        except ValidationError as exc:
            raise PlanParseError(f"invalid dependency: {exc.error_count()} error(s)") from exc
    termination: list[TerminationCondition] = []
    for item in raw_termination:
        if not isinstance(item, dict):
            raise PlanParseError("each termination condition must be a JSON object")
        _refuse_unknown(item, TERMINATION_KEYS, "termination")
        try:
            termination.append(TerminationCondition.model_validate(item))
        except ValidationError as exc:
            raise PlanParseError(f"invalid termination condition: {exc.error_count()} error(s)") from exc

    # An ON_TRIGGER objective only makes sense if some trigger activates it.
    activated = {trigger.objective_id for trigger in triggers}
    for objective in objectives:
        if objective.activation == ActivationMode.ON_TRIGGER and objective.id not in activated:
            warnings.append(
                f"objective {objective.label} waits on a trigger that was never defined; holding it PENDING"
            )

    plan = MissionPlan(
        name=sanitize_text(str(payload.get("name", "")), 80),
        created_at=now,
        objectives=objectives,
        triggers=triggers,
        constraints=constraints,
        dependencies=dependencies,
        termination=termination or [TerminationCondition()],
        metadata={"utterance": sanitize_text(context.utterance, policy.max_text_length)},
    )
    verdict: PolicyVerdict = evaluate_plan(plan, context, policy)
    warnings.extend(verdict.warnings)
    if not verdict.allowed:
        return MissionCompileResult(
            status=CompileStatus.REJECTED,
            rejection_reason="; ".join(verdict.violations),
            interpretation_summary=summary,
            warnings=warnings,
        )

    for objective in plan.objectives:
        objective.status = _initial_status(objective, plan)
    return MissionCompileResult(
        status=CompileStatus.READY,
        plan=plan,
        interpretation_summary=summary or describe_plan(plan),
        warnings=warnings,
    )


def _initial_status(objective: MissionObjective, plan: MissionPlan) -> ObjectiveStatus:
    """PENDING means "allocate now"; PROPOSED means "wait for a trigger or dependency"."""
    blocked = any(item.objective_id == objective.id for item in plan.dependencies)
    if objective.activation != ActivationMode.IMMEDIATE or blocked:
        return ObjectiveStatus.PROPOSED
    return ObjectiveStatus.PENDING


def describe_plan(plan: MissionPlan) -> str:
    """Deterministic one-line reading of the plan, used when the model gives none."""
    parts = [
        f"{objective.type.value} {objective.target.region_id or objective.target.entity_id or 'position'}"
        f" x{objective.desired_units} @p{objective.priority}"
        for objective in plan.objectives
    ]
    suffix = f" with {len(plan.triggers)} trigger(s)" if plan.triggers else ""
    return "; ".join(parts) + suffix
