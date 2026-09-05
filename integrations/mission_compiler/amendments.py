"""Live mission amendments: typed operator intent, deterministic application.

The model is shown a digest of the running plan and answers with a short list
of amendments.  It never receives a handle on a plan object, a task, or a
node.  ``apply_amendments`` is the only writer, and it resolves every selector
against the plan itself, so an amendment naming something that is not there
becomes a clarification rather than a silent no-op.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from chat_client import chat_sync
from simulation.mission_plan import (
    AmendmentType,
    CompileStatus,
    MissionAmendResult,
    MissionAmendment,
    MissionObjective,
    MissionPlan,
    ObjectiveSelector,
    ObjectiveStatus,
)
from simulation.models import TaskType

from .context import OperatorContext
from .parsing import PlanParseError, load_json_object, _refuse_unknown
from .policy import MissionPolicy, detect_injection, sanitize_text
from .prompts import AMEND_SYSTEM_PROMPT, amend_user_prompt

AMENDMENT_KEYS = {"type", "selector", "value", "rationale"}
SELECTOR_KEYS = {"objective_id", "objective_type", "region_id", "entity_id", "label"}
TOP_LEVEL_KEYS = {"status", "summary", "clarification_question", "amendments"}


def plan_digest(plan: MissionPlan) -> str:
    """What the model may see about a running plan: ids, types, targets, state."""
    lines = [f"plan_id: {plan.id}  revision: {plan.revision}"]
    for objective in plan.objectives:
        where = objective.target.region_id or objective.target.entity_id or (
            "point" if objective.target.point else "-"
        )
        lines.append(
            f"- id={objective.id} type={objective.type.value} target={where} "
            f"units={objective.desired_units} priority={objective.priority} status={objective.status.value}"
        )
    for constraint in plan.constraints:
        lines.append(f"- constraint {constraint.type.value} value={constraint.value}")
    return "\n".join(lines)


def resolve_objective(plan: MissionPlan, selector: ObjectiveSelector) -> list[MissionObjective]:
    """Every objective the selector names; empty means the operator must clarify."""
    candidates = [
        objective
        for objective in plan.objectives
        if objective.status not in {ObjectiveStatus.CANCELLED, ObjectiveStatus.COMPLETED}
    ]
    if selector.objective_id:
        exact = [item for item in candidates if item.id == selector.objective_id]
        if exact:
            return exact
    matched = candidates
    if selector.objective_type is not None:
        matched = [item for item in matched if item.type == selector.objective_type]
    if selector.region_id:
        wanted = selector.region_id.strip().lower()
        matched = [item for item in matched if (item.target.region_id or "").lower() == wanted]
    if selector.entity_id:
        wanted = selector.entity_id.strip().lower()
        matched = [item for item in matched if (item.target.entity_id or "").lower() == wanted]
    if selector.label:
        wanted = selector.label.strip().lower()
        matched = [item for item in matched if wanted in (item.label or "").lower()]
    if matched is candidates and not any(
        [selector.objective_type, selector.region_id, selector.entity_id, selector.label]
    ):
        return []
    return matched


def parse_amendment_response(
    text: str,
    plan: MissionPlan,
    policy: MissionPolicy | None = None,
) -> MissionAmendResult:
    """Strictly parse an amendment response; no plan is mutated here."""
    policy = policy or MissionPolicy()
    payload = load_json_object(text)
    _refuse_unknown(payload, TOP_LEVEL_KEYS, "top-level")
    declared = str(payload.get("status", CompileStatus.READY.value)).strip().upper()
    if declared not in {item.value for item in CompileStatus}:
        raise PlanParseError(f"unsupported status: {declared}")
    summary = sanitize_text(str(payload.get("summary", "")), policy.max_text_length)

    if declared == CompileStatus.REJECTED.value:
        return MissionAmendResult(
            status=CompileStatus.REJECTED,
            rejection_reason=summary or "the model declined to amend this plan",
            interpretation_summary=summary,
        )
    if declared == CompileStatus.NEEDS_CLARIFICATION.value:
        return MissionAmendResult(
            status=CompileStatus.NEEDS_CLARIFICATION,
            clarification_question=sanitize_text(
                str(payload.get("clarification_question") or "Which objective should change?"), 240
            ),
            interpretation_summary=summary,
        )

    raw_amendments = payload.get("amendments") or []
    if not isinstance(raw_amendments, list):
        raise PlanParseError("amendments must be a list")
    if not raw_amendments:
        raise PlanParseError("amendment response contained no amendments")
    if len(raw_amendments) > policy.max_objectives:
        raise PlanParseError(f"too many amendments: {len(raw_amendments)}")

    amendments: list[MissionAmendment] = []
    for item in raw_amendments:
        if not isinstance(item, dict):
            raise PlanParseError("each amendment must be a JSON object")
        _refuse_unknown(item, AMENDMENT_KEYS, "amendment")
        selector_payload = item.get("selector") or {}
        if not isinstance(selector_payload, dict):
            raise PlanParseError("amendment selector must be an object")
        _refuse_unknown(selector_payload, SELECTOR_KEYS, "selector")
        cleaned = {key: value for key, value in selector_payload.items() if value not in (None, "")}
        if "objective_type" in cleaned:
            raw_type = str(cleaned["objective_type"]).strip().upper()
            from .parsing import TYPE_ALIASES

            cleaned["objective_type"] = (
                TYPE_ALIASES[raw_type].value if raw_type in TYPE_ALIASES else raw_type
            )
        try:
            amendment = MissionAmendment.model_validate(
                {
                    "type": str(item.get("type", "")).strip().upper(),
                    "selector": cleaned,
                    "value": item.get("value"),
                    "rationale": sanitize_text(str(item.get("rationale", "")), 160),
                }
            )
        except ValidationError as exc:
            raise PlanParseError(f"invalid amendment: {exc.error_count()} error(s)") from exc
        amendments.append(amendment)

    unresolved = [
        amendment
        for amendment in amendments
        if amendment.type != AmendmentType.RECALL_ALL and not resolve_objective(plan, amendment.selector)
    ]
    if unresolved:
        wanted = unresolved[0].selector
        named = wanted.objective_id or wanted.region_id or wanted.label or (
            wanted.objective_type.value if wanted.objective_type else "that objective"
        )
        return MissionAmendResult(
            status=CompileStatus.NEEDS_CLARIFICATION,
            amendments=amendments,
            clarification_question=(
                f"No active objective matches {named}. Which of these should change: "
                f"{', '.join(f'{item.id} ({item.type.value})' for item in plan.objectives) or 'none'}?"
            ),
            interpretation_summary=summary,
        )
    return MissionAmendResult(
        status=CompileStatus.READY,
        amendments=amendments,
        interpretation_summary=summary,
    )


def apply_amendments(
    plan: MissionPlan,
    amendments: list[MissionAmendment],
    policy: MissionPolicy | None = None,
) -> tuple[list[str], list[str]]:
    """Deterministically apply validated amendments. Returns (applied, warnings)."""
    policy = policy or MissionPolicy()
    applied: list[str] = []
    warnings: list[str] = []
    for amendment in amendments:
        targets = resolve_objective(plan, amendment.selector)
        if amendment.type == AmendmentType.RECALL_ALL:
            for objective in plan.objectives:
                if objective.status in {ObjectiveStatus.PENDING, ObjectiveStatus.ACTIVE}:
                    objective.status = ObjectiveStatus.CANCELLED
            plan.objectives.append(
                MissionObjective(
                    label="Recall all units",
                    type=TaskType.RETURN,
                    priority=95,
                    status=ObjectiveStatus.PENDING,
                    metadata={"source": "amendment", "rationale": amendment.rationale},
                )
            )
            applied.append("recalled every active objective and queued RETURN")
            continue
        for objective in targets:
            applied.extend(_apply_one(plan, objective, amendment, policy, warnings))
    if applied:
        plan.revision += 1
    return applied, warnings


def _apply_one(
    plan: MissionPlan,
    objective: MissionObjective,
    amendment: MissionAmendment,
    policy: MissionPolicy,
    warnings: list[str],
) -> list[str]:
    label = objective.label or objective.id
    if amendment.type == AmendmentType.SET_PRIORITY:
        if amendment.value is None or not 0 <= amendment.value <= 100:
            warnings.append(f"ignored SET_PRIORITY on {label}: priority must be 0..100")
            return []
        previous = objective.priority
        objective.priority = amendment.value
        return [f"{label} priority {previous} -> {objective.priority}"]
    if amendment.type == AmendmentType.CANCEL_OBJECTIVE:
        objective.status = ObjectiveStatus.CANCELLED
        return [f"{label} cancelled"]
    if amendment.type == AmendmentType.SUSPEND_OBJECTIVE:
        objective.status = ObjectiveStatus.SUSPENDED
        return [f"{label} suspended"]
    if amendment.type == AmendmentType.RESUME_OBJECTIVE:
        if objective.status in {ObjectiveStatus.SUSPENDED, ObjectiveStatus.PROPOSED}:
            objective.status = ObjectiveStatus.PENDING
            return [f"{label} resumed"]
        warnings.append(f"{label} was already {objective.status.value}")
        return []
    if amendment.type in {AmendmentType.SET_UNITS, AmendmentType.ADD_UNITS}:
        if amendment.value is None or amendment.value < (0 if amendment.type == AmendmentType.ADD_UNITS else 1):
            warnings.append(f"ignored {amendment.type.value} on {label}: invalid unit count")
            return []
        previous = objective.desired_units
        wanted = (
            amendment.value
            if amendment.type == AmendmentType.SET_UNITS
            else objective.desired_units + amendment.value
        )
        if wanted > policy.max_units_per_objective:
            warnings.append(
                f"ignored {amendment.type.value} on {label}: {wanted} units exceeds the "
                f"{policy.max_units_per_objective}-unit limit"
            )
            return []
        objective.desired_units = wanted
        objective.minimum_units = min(objective.minimum_units, wanted)
        # A running objective must be resubmitted to change its unit count.
        objective.task_id = None
        objective.status = ObjectiveStatus.PENDING
        return [f"{label} units {previous} -> {objective.desired_units}"]
    if amendment.type == AmendmentType.ADD_OBJECTIVE and amendment.objective is not None:
        plan.objectives.append(amendment.objective)
        return [f"added objective {amendment.objective.label or amendment.objective.id}"]
    if amendment.type == AmendmentType.ADD_TRIGGER and amendment.trigger is not None:
        plan.triggers.append(amendment.trigger)
        return [f"added trigger {amendment.trigger.id}"]
    warnings.append(f"ignored unsupported amendment {amendment.type.value} on {label}")
    return []


def extend_plan(plan: MissionPlan, addition: MissionPlan) -> list[str]:
    """Merge a newly compiled plan into a running one, keeping ids unique.

    "If vehicle 7 appears, have a camera drone follow it" is not a change to an
    existing objective; it is a new conditional objective plus its trigger. The
    compiler already produced both, so extension is a rename-and-append.
    """
    applied: list[str] = []
    existing = {objective.id for objective in plan.objectives}
    renames: dict[str, str] = {}
    for objective in addition.objectives:
        new_id = objective.id
        suffix = 2
        while new_id in existing:
            new_id = f"{objective.id}-{suffix}"
            suffix += 1
        if new_id != objective.id:
            renames[objective.id] = new_id
        objective.id = new_id
        existing.add(new_id)
        plan.objectives.append(objective)
        applied.append(f"added objective {objective.label or objective.id} ({objective.type.value})")
    trigger_ids = {trigger.id for trigger in plan.triggers}
    for trigger in addition.triggers:
        trigger.objective_id = renames.get(trigger.objective_id, trigger.objective_id)
        if trigger.when.objective_id:
            trigger.when.objective_id = renames.get(trigger.when.objective_id, trigger.when.objective_id)
        new_id = trigger.id
        suffix = 2
        while new_id in trigger_ids:
            new_id = f"{trigger.id}-{suffix}"
            suffix += 1
        trigger.id = new_id
        trigger_ids.add(new_id)
        plan.triggers.append(trigger)
        applied.append(
            f"added trigger on {trigger.when.event.value} -> {trigger.action.value} {trigger.objective_id}"
        )
    for constraint in addition.constraints:
        plan.constraints.append(constraint)
        applied.append(f"added constraint {constraint.type.value}")
    for dependency in addition.dependencies:
        dependency.objective_id = renames.get(dependency.objective_id, dependency.objective_id)
        dependency.depends_on = renames.get(dependency.depends_on, dependency.depends_on)
        plan.dependencies.append(dependency)
        applied.append(f"added dependency {dependency.objective_id} -> {dependency.depends_on}")
    if applied:
        plan.revision += 1
    return applied


class MissionAmender:
    """Turns a live operator utterance into validated, applied amendments."""

    def __init__(
        self,
        completion: Callable[..., Any] = chat_sync,
        policy: MissionPolicy | None = None,
        chat_base_url: str | None = None,
        n_predict: int = 420,
    ) -> None:
        self._completion = completion
        self.policy = policy or MissionPolicy()
        self.chat_base_url = chat_base_url
        self.n_predict = n_predict

    def amend(self, plan: MissionPlan, context: OperatorContext) -> MissionAmendResult:
        if not context.utterance.strip():
            return MissionAmendResult(
                status=CompileStatus.REJECTED,
                rejection_reason="operator instruction cannot be empty",
            )
        started = time.perf_counter()
        try:
            result = self._completion(
                amend_user_prompt(context, plan_digest(plan)),
                system_message=AMEND_SYSTEM_PROMPT,
                n_predict=self.n_predict,
                temperature=0.0,
                base_url=self.chat_base_url,
            )
        except OSError as exc:
            return MissionAmendResult(
                status=CompileStatus.REJECTED,
                rejection_reason=f"local model backend unavailable: {exc}",
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        text = result if isinstance(result, str) else getattr(result, "text", None)
        if not isinstance(text, str):
            return MissionAmendResult(
                status=CompileStatus.REJECTED,
                rejection_reason="local model client returned no text",
            )
        diagnostics: dict[str, Any] = {"latency_ms": round(elapsed_ms, 2)}
        try:
            parsed = parse_amendment_response(text, plan, self.policy)
        except PlanParseError as exc:
            return MissionAmendResult(
                status=CompileStatus.REJECTED,
                rejection_reason=sanitize_text(str(exc), self.policy.max_text_length),
                warnings=["model output failed strict validation and was discarded"],
                diagnostics=diagnostics,
            )
        parsed.diagnostics = {**parsed.diagnostics, **diagnostics}
        if detect_injection(context.utterance):
            parsed.warnings.append("instruction contained prompt-injection phrasing; only typed amendments were kept")
        if parsed.status == CompileStatus.READY:
            applied, warnings = apply_amendments(plan, parsed.amendments, self.policy)
            parsed.applied = applied
            parsed.warnings.extend(warnings)
            parsed.plan = plan
            if not parsed.interpretation_summary:
                parsed.interpretation_summary = "; ".join(applied)
        return parsed
