"""The local mission compiler: operator turn in, validated MissionPlan out.

This is the only place that calls the model for planning.  Everything after
the call is deterministic — parse, ground, validate, gate — so a bad or
hostile generation can at worst produce REJECTED, never an executed action.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from chat_client import chat_sync
from simulation.mission_plan import (
    CompileStatus,
    MissionCompileResult,
    MissionPlan,
)

from .context import OperatorContext
from .parsing import PlanParseError, describe_plan, parse_plan_response
from .policy import MissionPolicy, detect_injection, sanitize_text
from .prompts import COMPILE_SYSTEM_PROMPT, compile_user_prompt


class MissionCompilerError(RuntimeError):
    """The local model backend could not be reached or returned nothing."""


def _completion_text(result: Any) -> str:
    text = result if isinstance(result, str) else getattr(result, "text", None)
    if not isinstance(text, str):
        raise MissionCompilerError("local model client returned no text")
    return text


def _timings(result: Any) -> dict[str, Any]:
    timings = getattr(result, "timings", None)
    return dict(timings) if isinstance(timings, dict) else {}


class MissionPlanCompiler:
    """Compiles a multimodal operator turn into a typed MissionPlan."""

    def __init__(
        self,
        completion: Callable[..., Any] = chat_sync,
        policy: MissionPolicy | None = None,
        chat_base_url: str | None = None,
        n_predict: int = 640,
    ) -> None:
        self._completion = completion
        self.policy = policy or MissionPolicy()
        self.chat_base_url = chat_base_url
        self.n_predict = n_predict

    def compile(self, context: OperatorContext) -> MissionCompileResult:
        if not context.utterance.strip():
            return MissionCompileResult(
                status=CompileStatus.REJECTED,
                rejection_reason="operator instruction cannot be empty",
                interpretation_summary="",
            )
        injection_hits = detect_injection(context.utterance)
        started = time.perf_counter()
        try:
            result = self._completion(
                compile_user_prompt(context),
                system_message=COMPILE_SYSTEM_PROMPT,
                n_predict=self.n_predict,
                temperature=0.0,
                base_url=self.chat_base_url,
            )
        except OSError as exc:
            return MissionCompileResult(
                status=CompileStatus.REJECTED,
                rejection_reason=f"local model backend unavailable: {exc}",
                warnings=["start the chat server (scripts/start_chat_server.sh) or use the deterministic baseline"],
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        raw = _completion_text(result)
        diagnostics: dict[str, Any] = {
            "latency_ms": round(elapsed_ms, 2),
            "response_characters": len(raw),
            "injection_patterns_in_utterance": len(injection_hits),
            **_decode_rate(_timings(result)),
        }

        try:
            compiled = parse_plan_response(raw, context, self.policy, now=context.timestamp)
        except PlanParseError as exc:
            return MissionCompileResult(
                status=CompileStatus.REJECTED,
                rejection_reason=sanitize_text(str(exc), self.policy.max_text_length),
                interpretation_summary="",
                warnings=["model output failed strict validation and was discarded"],
                diagnostics=diagnostics,
            )
        compiled.diagnostics = {**compiled.diagnostics, **diagnostics}
        if injection_hits and compiled.status == CompileStatus.READY:
            compiled.warnings.append(
                "instruction contained prompt-injection phrasing; only schema-valid mission content was kept"
            )
        if compiled.plan is not None:
            compiled.plan.metadata.setdefault("compiler", "local-llm")
            if not compiled.interpretation_summary:
                compiled.interpretation_summary = describe_plan(compiled.plan)
        return compiled


def render_plan(plan: MissionPlan) -> str:
    """Operator-facing plan listing used by the CLI and the demo."""
    lines = ["MISSION PLAN", f"id: {plan.id}  revision: {plan.revision}"]
    for objective in plan.objectives:
        where = (
            objective.target.region_id
            or objective.target.entity_id
            or (
                f"({objective.target.point.x:.0f},{objective.target.point.y:.0f},{objective.target.point.z:.0f})"
                if objective.target.point
                else "-"
            )
        )
        capabilities = (
            f" caps={','.join(sorted(objective.required_capabilities))}"
            if objective.required_capabilities
            else ""
        )
        lines.append(
            f"  {objective.type.value:<16} {where:<12} "
            f"{objective.desired_units} unit(s)  priority {objective.priority}  "
            f"{objective.activation.value}  [{objective.status.value}]{capabilities}"
        )
    for trigger in plan.triggers:
        detail = trigger.when.entity_id or trigger.when.objective_id or (
            f"{trigger.when.threshold:.2f}" if trigger.when.threshold is not None else ""
        )
        lines.append(
            f"  TRIGGER          when {trigger.when.event.value} {detail} -> "
            f"{trigger.action.value} {trigger.objective_id}"
        )
    for constraint in plan.constraints:
        value = f" {constraint.value}" if constraint.value is not None else ""
        lines.append(f"  CONSTRAINT       {constraint.type.value}{value} ({constraint.enforcement.value})")
    for dependency in plan.dependencies:
        lines.append(
            f"  DEPENDENCY       {dependency.objective_id} {dependency.kind.value} {dependency.depends_on}"
        )
    for termination in plan.termination:
        value = f" {termination.value}" if termination.value is not None else ""
        lines.append(f"  TERMINATION      {termination.type.value}{value}")
    return "\n".join(lines)


def _decode_rate(timings: dict[str, Any]) -> dict[str, Any]:
    predicted_n = timings.get("predicted_n") or 0
    predicted_ms = timings.get("predicted_ms") or 0
    prompt_n = timings.get("prompt_n") or 0
    if not predicted_ms:
        return {}
    return {
        "prompt_tokens": prompt_n,
        "generated_tokens": predicted_n,
        "decode_tok_s": round(predicted_n / (predicted_ms / 1000.0), 2),
    }
