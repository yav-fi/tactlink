"""GOTO / HOLD / RETURN - the simple point behaviours."""

from __future__ import annotations

from ..environment import EnvironmentQuery
from ..models import (
    BehaviorPhase,
    BehaviorState,
    CompletionCondition,
    CompletionKind,
    PlanMode,
    PlanningContext,
    PlanWarning,
)
from .base import BehaviorOutcome, find_free_point, within_radius

ARRIVAL_RADIUS = 4.0


def plan_goto(
    context: PlanningContext,
    state: BehaviorState,
    environment: EnvironmentQuery | None,
    clearance: float,
) -> BehaviorOutcome:
    task = context.current_task
    assert task is not None
    if task.point is None:
        return BehaviorOutcome(
            mode=PlanMode.HOLD,
            phase=BehaviorPhase.HOLDING,
            hold=True,
            warnings=[PlanWarning.TARGET_UNKNOWN],
            confidence=0.2,
            targets=[context.estimated_position],
        )

    altitude = task.desired_altitude if task.desired_altitude is not None else context.config.default_altitude
    goal = find_free_point(environment, context, task.point.with_z(altitude), clearance)
    adjusted = goal.distance_xy(task.point) > 1e-6
    distance = context.estimated_position.distance_xy(goal)
    arrived = distance <= ARRIVAL_RADIUS
    warnings = [PlanWarning.ROUTE_ADJUSTED] if adjusted else []
    return BehaviorOutcome(
        mode=PlanMode.TRANSIT,
        phase=BehaviorPhase.ARRIVED if arrived else BehaviorPhase.EN_ROUTE,
        targets=[goal],
        completion=within_radius(goal, ARRIVAL_RADIUS, "reach the assigned point"),
        warnings=warnings,
        altitude=altitude,
        hold=arrived,
        metadata={"goal": goal.model_dump(), "requested_goal": task.point.model_dump()},
    )


def plan_hold(
    context: PlanningContext,
    state: BehaviorState,
    environment: EnvironmentQuery | None,
    clearance: float,
) -> BehaviorOutcome:
    task = context.current_task
    altitude = context.estimated_position.z
    if task is not None and task.desired_altitude is not None:
        altitude = task.desired_altitude
    anchor = state.last_target or context.estimated_position.with_z(altitude)
    if task is not None and task.point is not None:
        anchor = task.point.with_z(altitude)
    return BehaviorOutcome(
        mode=PlanMode.HOLD,
        phase=BehaviorPhase.HOLDING,
        targets=[anchor],
        completion=CompletionCondition(kind=CompletionKind.NONE, description="held until reassigned"),
        altitude=altitude,
        hold=True,
        speed_scale=0.2,
        metadata={"anchor": anchor.model_dump()},
    )


def plan_return(
    context: PlanningContext,
    state: BehaviorState,
    environment: EnvironmentQuery | None,
    clearance: float,
) -> BehaviorOutcome:
    home = context.home_position
    if home is None:
        outcome = plan_hold(context, state, environment, clearance)
        outcome.warnings.append(PlanWarning.NO_HOME_POSITION)
        outcome.confidence = 0.4
        return outcome
    altitude = context.config.default_altitude
    goal = home.with_z(altitude)
    arrived = context.estimated_position.distance_xy(goal) <= ARRIVAL_RADIUS
    return BehaviorOutcome(
        mode=PlanMode.RETURN,
        phase=BehaviorPhase.ARRIVED if arrived else BehaviorPhase.RETURNING,
        targets=[goal],
        completion=within_radius(home, ARRIVAL_RADIUS, "reach home"),
        altitude=altitude,
        hold=arrived,
        metadata={"home": home.model_dump()},
    )
