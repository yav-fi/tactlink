"""TRACE - follow a supplied polyline as closely as practical.

Route points are validated (deduplicated, nudged out of obstacles, clamped to
bounds) but never reordered: the operator asked for this route. Multiple drones
are staggered along the route so they do not fly nose to tail.
"""

from __future__ import annotations

from ..coordination import active_team, shard_index
from ..environment import EnvironmentQuery
from ..models import (
    BehaviorPhase,
    BehaviorState,
    CompletionCondition,
    CompletionKind,
    PlanMode,
    PlanningContext,
    PlanWarning,
    Vector3,
)
from .base import BehaviorOutcome, find_free_point

MINIMUM_SPACING = 3.0
ARRIVAL_RADIUS = 6.0


def validate_route(
    context: PlanningContext,
    environment: EnvironmentQuery | None,
    route: list[Vector3],
    clearance: float,
    altitude: float,
) -> tuple[list[Vector3], bool]:
    cleaned: list[Vector3] = []
    adjusted = False
    for point in route:
        candidate = point.with_z(altitude)
        if cleaned and cleaned[-1].distance_xy(candidate) < MINIMUM_SPACING:
            continue
        free = find_free_point(environment, context, candidate, clearance)
        if free.distance_xy(candidate) > 1e-6:
            adjusted = True
        cleaned.append(free)
    return cleaned, adjusted


def plan_trace(
    context: PlanningContext,
    state: BehaviorState,
    environment: EnvironmentQuery | None,
    clearance: float,
) -> BehaviorOutcome:
    task = context.current_task
    assert task is not None
    config = context.config
    altitude = task.desired_altitude if task.desired_altitude is not None else config.default_altitude

    if not task.waypoints:
        return BehaviorOutcome(
            mode=PlanMode.HOLD,
            phase=BehaviorPhase.HOLDING,
            hold=True,
            warnings=[PlanWarning.TARGET_UNKNOWN],
            confidence=0.2,
            targets=[context.estimated_position],
        )

    route, adjusted = validate_route(context, environment, task.waypoints, clearance, altitude)
    team, warnings = active_team(context)
    index = shard_index(team, context.drone_id)

    stagger = bool(task.metadata.get("stagger", True)) and len(team) > 1
    start_index = min(index, len(route) - 1) if stagger else 0

    # Resume from wherever we already are along the route.
    if state.last_target is not None:
        for position, point in enumerate(route):
            if point.distance_xy(state.last_target) < 1e-6:
                start_index = max(start_index, position)
                break

    remaining = route[start_index:]
    while len(remaining) > 1 and context.estimated_position.distance_xy(remaining[0]) <= ARRIVAL_RADIUS:
        remaining = remaining[1:]

    if adjusted:
        warnings.append(PlanWarning.ROUTE_ADJUSTED)

    arrived = len(remaining) == 1 and context.estimated_position.distance_xy(remaining[0]) <= ARRIVAL_RADIUS
    state.phase = BehaviorPhase.ARRIVED if arrived else BehaviorPhase.EN_ROUTE

    return BehaviorOutcome(
        mode=PlanMode.TRACE,
        phase=state.phase,
        targets=remaining,
        completion=CompletionCondition(
            kind=CompletionKind.WAYPOINTS_CONSUMED,
            point=route[-1],
            radius=ARRIVAL_RADIUS,
            description="fly the supplied route to its end",
        ),
        warnings=warnings,
        altitude=altitude,
        hold=arrived,
        metadata={
            "route_points": len(route),
            "start_index": start_index,
            "stagger": stagger,
            "team": team,
            "route_adjusted": adjusted,
        },
    )
