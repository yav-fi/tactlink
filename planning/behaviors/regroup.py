"""REGROUP - loose formation slots around a rally point."""

from __future__ import annotations

from math import pi

from ..coordination import active_team, shard_index
from ..environment import EnvironmentQuery
from ..geometry import polygon_centroid
from ..models import (
    BehaviorPhase,
    BehaviorState,
    CompletionCondition,
    CompletionKind,
    PlanMode,
    PlanningContext,
    Vector3,
)
from .base import BehaviorOutcome, best_spread_point
from .search import resolve_region

ARRIVAL_RADIUS = 8.0


def plan_regroup(
    context: PlanningContext,
    state: BehaviorState,
    environment: EnvironmentQuery | None,
    clearance: float,
) -> BehaviorOutcome:
    task = context.current_task
    assert task is not None
    config = context.config
    altitude = task.desired_altitude if task.desired_altitude is not None else config.default_altitude

    rally = task.point
    if rally is None:
        region = resolve_region(context, environment)
        if region is not None:
            rally = polygon_centroid(region.polygon)
    if rally is None:
        known = [peer.position for peer in context.peers if peer.available and peer.position is not None]
        known.append(context.estimated_position)
        rally = Vector3(
            x=sum(point.x for point in known) / len(known),
            y=sum(point.y for point in known) / len(known),
            z=altitude,
        )

    team, warnings = active_team(context)
    index = shard_index(team, context.drone_id)
    radius = task.standoff if task.standoff is not None else config.regroup_radius
    if len(team) == 1:
        radius = 0.0

    nominal_angle = 2.0 * pi * index / max(len(team), 1)
    if radius <= 0.0:
        slot, quality = rally.with_z(altitude), 1.0
    else:
        slot, quality = best_spread_point(
            environment, context, rally.with_z(altitude), radius, nominal_angle, clearance, altitude
        )

    arrived = context.estimated_position.distance_xy(slot) <= ARRIVAL_RADIUS
    state.phase = BehaviorPhase.ARRIVED if arrived else BehaviorPhase.EN_ROUTE

    return BehaviorOutcome(
        mode=PlanMode.FORMATION,
        phase=state.phase,
        targets=[slot],
        completion=CompletionCondition(
            kind=CompletionKind.WITHIN_RADIUS,
            point=slot,
            radius=ARRIVAL_RADIUS,
            description="occupy the assigned formation slot",
        ),
        warnings=list(warnings),
        altitude=altitude,
        hold=arrived,
        confidence=max(0.4, quality),
        metadata={
            "rally_point": rally.model_dump(),
            "formation_slot": slot.model_dump(),
            "slot_index": index,
            "formation_radius": radius,
            "team": team,
        },
    )
