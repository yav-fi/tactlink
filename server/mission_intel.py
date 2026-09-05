"""HTTP surface for the local mission-intelligence subsystem.

Kept out of ``server/main.py`` so the simulation API and the compiler API stay
independently readable.  ``install`` adds routes and returns the session the
app should evaluate on its tick; it never changes existing routes.

The write path is narrow on purpose: compile and amend produce validated
plans, and only ``MissionPlanRuntime`` turns those into MissionCommands.  The
query route constructs its engine without a submitter, so it cannot act.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from integrations.mission_compiler.amendments import MissionAmender
from integrations.mission_compiler.baseline import BaselineMissionAmender, BaselineMissionCompiler
from integrations.mission_compiler.compiler import MissionPlanCompiler
from integrations.mission_compiler.context import OperatorContext, UnitAvailability
from integrations.mission_compiler.runtime import PlanAction, plan_to_commands
from integrations.mission_compiler.session import MissionSession
from simulation.mission_plan import (
    CompileStatus,
    MissionAmendResult,
    MissionCompileResult,
    MissionPlan,
    MissionQueryResult,
)
from simulation.models import MissionCommand, Vector3
from simulation.simulation import SimulationEngine


class OperatorTurn(BaseModel):
    """One multimodal operator turn as the ground station sends it.

    World knowledge (regions, entities, capabilities, unit availability) is
    filled in from the live runtime, so the client only reports what the
    operator actually did.
    """

    utterance: str
    selected_region: str | None = None
    map_cursor: Vector3 | None = None
    gesture: str | None = None
    operator_position: Vector3 | None = None
    selected_drone_id: str | None = None
    plan_id: str | None = None
    start: bool = Field(default=False, description="register and submit the plan when it compiles")


class MissionQuestion(BaseModel):
    question: str


class CompileResponse(BaseModel):
    result: MissionCompileResult
    actions: list[PlanAction] = Field(default_factory=list)
    commands: list[MissionCommand] = Field(default_factory=list)


class AmendResponse(BaseModel):
    result: MissionAmendResult
    actions: list[PlanAction] = Field(default_factory=list)


def build_context(turn: OperatorTurn, engine: SimulationEngine) -> OperatorContext:
    """Fuse the operator turn with what the runtime currently knows."""
    capabilities: dict[str, int] = {}
    available = 0
    for drone in engine.drones.values():
        node_id = drone.identity.node_id
        online = node_id in engine.world.node_ids and engine.world.truth(node_id).online
        if not online:
            continue
        available += 1
        for capability in drone.identity.capabilities:
            capabilities[capability] = capabilities.get(capability, 0) + 1
    return OperatorContext(
        utterance=turn.utterance,
        selected_region=turn.selected_region,
        map_cursor=turn.map_cursor,
        gesture=turn.gesture,
        operator_position=turn.operator_position,
        selected_drone_id=turn.selected_drone_id,
        known_regions=[region.id for region in engine.world.definition.regions],
        known_entities=[entity.id for entity in engine.world.definition.entities],
        known_capabilities=sorted(capabilities),
        availability=UnitAvailability(
            total_units=len(engine.drones),
            available_units=available,
            capability_counts=capabilities,
        ),
        timestamp=engine.time,
    )


def build_session(engine: SimulationEngine, backend: str = "local-llm") -> MissionSession:
    """Wire a session to the engine through typed calls only."""
    baseline = backend == "baseline"
    return MissionSession(
        submit=engine.submit_mission,
        cancel=lambda task_id: engine.missions.cancel_task(task_id, engine.time),
        compiler=BaselineMissionCompiler() if baseline else MissionPlanCompiler(),
        amender=BaselineMissionAmender() if baseline else MissionAmender(),
        clock=lambda: engine.time,
    )


def install(app: FastAPI, engine: SimulationEngine, backend: str = "local-llm") -> MissionSession:
    session = build_session(engine, backend)
    router = APIRouter(prefix="/api/mission-plans", tags=["mission-intelligence"])

    @router.post("/compile", response_model=CompileResponse)
    async def compile_plan(turn: OperatorTurn) -> CompileResponse:
        context = build_context(turn, engine)
        result = session.compile(context)
        if not turn.start or result.status != CompileStatus.READY or result.plan is None:
            return CompileResponse(result=result)
        actions = session.start(result.plan, engine.snapshot(event_limit=0))
        return CompileResponse(
            result=result,
            actions=actions,
            commands=plan_to_commands(result.plan, engine.snapshot(event_limit=0)),
        )

    @router.post("/{plan_id}/start", response_model=CompileResponse)
    async def start_plan(plan_id: str, plan: MissionPlan) -> CompileResponse:
        if plan.id != plan_id:
            raise HTTPException(400, "plan id in the body does not match the path")
        actions = session.start(plan, engine.snapshot(event_limit=0))
        return CompileResponse(
            result=MissionCompileResult(status=CompileStatus.READY, plan=plan),
            actions=actions,
        )

    @router.get("", response_model=list[MissionPlan])
    async def list_plans() -> list[MissionPlan]:
        return session.plans

    @router.get("/actions", response_model=list[PlanAction])
    async def plan_actions(limit: int = 40) -> list[PlanAction]:
        return session.runtime.recent_actions(limit)

    @router.post("/amend", response_model=AmendResponse)
    async def amend_plan(turn: OperatorTurn) -> AmendResponse:
        if not session.plans:
            raise HTTPException(409, "no mission plan is running")
        context = build_context(turn, engine)
        result = session.amend(context, turn.plan_id, engine.snapshot(event_limit=0))
        return AmendResponse(result=result, actions=session.runtime.recent_actions(8))

    app.include_router(router)

    @app.post("/api/mission-intel/query", response_model=MissionQueryResult, tags=["mission-intelligence"])
    async def query_mission(question: MissionQuestion) -> MissionQueryResult:
        """Read-only. This route holds no submitter and issues no commands."""
        snapshot = engine.snapshot()
        return session.explain(question.question, snapshot, snapshot.events)

    return session
