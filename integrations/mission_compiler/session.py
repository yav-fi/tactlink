"""One live operator session: compile, start, amend, evaluate, explain.

This is the object an interface (HTTP API, CLI, demo) holds.  It owns the
current plan and the deterministic plan runtime; the model-facing pieces are
injected, so a session works with a local model, with the deterministic
baseline, or with a stub in tests without changing any behaviour downstream.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from simulation.mission_plan import (
    CompileStatus,
    MissionAmendResult,
    MissionCompileResult,
    MissionPlan,
    MissionQueryResult,
)
from simulation.models import MissionTask, SimulationEvent, SimulationSnapshot

from .amendments import MissionAmender, extend_plan
from .compiler import MissionPlanCompiler
from .context import OperatorContext
from .query import MissionQueryEngine
from .runtime import MissionPlanRuntime, MissionSubmitter, PlanAction


class MissionSession:
    def __init__(
        self,
        submit: MissionSubmitter,
        cancel: Callable[[str], MissionTask | None] | None = None,
        compiler: MissionPlanCompiler | None = None,
        amender: MissionAmender | None = None,
        query: MissionQueryEngine | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.compiler = compiler or MissionPlanCompiler()
        self.amender = amender or MissionAmender()
        self.query = query or MissionQueryEngine()
        self.runtime = MissionPlanRuntime(submit, cancel, clock)
        self.last_result: MissionCompileResult | None = None

    # -- compile / start --------------------------------------------------

    def compile(self, context: OperatorContext) -> MissionCompileResult:
        result = self.compiler.compile(context)
        self.last_result = result
        return result

    def start(
        self,
        plan: MissionPlan,
        snapshot: SimulationSnapshot | None = None,
    ) -> list[PlanAction]:
        return self.runtime.register(plan, snapshot)

    def compile_and_start(
        self,
        context: OperatorContext,
        snapshot: SimulationSnapshot | None = None,
    ) -> tuple[MissionCompileResult, list[PlanAction]]:
        result = self.compile(context)
        if result.status != CompileStatus.READY or result.plan is None:
            return result, []
        return result, self.start(result.plan, snapshot)

    @property
    def plans(self) -> list[MissionPlan]:
        return list(self.runtime.plans.values())

    def active_plan(self) -> MissionPlan | None:
        return next(iter(self.runtime.plans.values()), None)

    # -- amend ------------------------------------------------------------

    def amend(
        self,
        context: OperatorContext,
        plan_id: str | None = None,
        snapshot: SimulationSnapshot | None = None,
        allow_extension: bool = True,
    ) -> MissionAmendResult:
        """Amend the running plan, falling back to extending it with new work.

        "Prioritize Bravo over Alpha" changes an objective that exists.  "If
        vehicle 7 appears, follow it" adds one that does not, which the
        amendment vocabulary deliberately cannot express — so an unmatched
        amendment is retried through the full compiler and merged in.
        """
        plan = self.runtime.plan(plan_id) if plan_id else self.active_plan()
        if plan is None:
            return MissionAmendResult(
                status=CompileStatus.REJECTED,
                rejection_reason="no mission plan is running",
            )
        result = self.amender.amend(plan, context)
        if result.status == CompileStatus.READY or not allow_extension:
            if result.status == CompileStatus.READY:
                self.runtime.evaluate_plan_activation(plan, snapshot)
            return result
        if result.status != CompileStatus.NEEDS_CLARIFICATION:
            return result
        compiled = self.compiler.compile(context)
        if compiled.status != CompileStatus.READY or compiled.plan is None:
            result.warnings.append("the instruction did not match an existing objective either")
            return result
        applied = extend_plan(plan, compiled.plan)
        self.runtime.evaluate_plan_activation(plan, snapshot)
        return MissionAmendResult(
            status=CompileStatus.READY,
            amendments=[],
            plan=plan,
            applied=applied,
            warnings=result.warnings + compiled.warnings,
            interpretation_summary=compiled.interpretation_summary or "; ".join(applied),
            diagnostics={**result.diagnostics, "route": "extend"},
        )

    # -- evaluate / explain -----------------------------------------------

    def evaluate(
        self,
        snapshot: SimulationSnapshot,
        events: Sequence[SimulationEvent] = (),
        observed_entities: dict[str, float] | None = None,
    ) -> list[PlanAction]:
        return self.runtime.evaluate(snapshot, events, observed_entities)

    def explain(
        self,
        question: str,
        snapshot: SimulationSnapshot,
        events: Sequence[SimulationEvent] = (),
    ) -> MissionQueryResult:
        return self.query.answer(question, snapshot, events, self.plans)
