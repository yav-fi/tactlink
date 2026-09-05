"""WATCH - observe a point or region from a useful standoff position.

Deliberately does not fly to the centre: an observer sitting on top of the thing
it is watching sees nothing useful and collides with its teammates. Drones are
placed on a standoff ring, one slot each, then each slot is nudged to free,
in-bounds, uncrowded space.
"""

from __future__ import annotations

from math import pi

from ..coordination import active_team, shard_index
from ..environment import EnvironmentQuery
from ..geometry import polygon_centroid, polygon_radius
from ..models import (
    BehaviorPhase,
    BehaviorState,
    CompletionCondition,
    CompletionKind,
    PlanMode,
    PlanningContext,
    PlanWarning,
)
from .base import BehaviorOutcome, best_spread_point
from .search import resolve_region


def plan_watch(
    context: PlanningContext,
    state: BehaviorState,
    environment: EnvironmentQuery | None,
    clearance: float,
) -> BehaviorOutcome:
    task = context.current_task
    assert task is not None
    config = context.config
    region = resolve_region(context, environment)

    if region is not None:
        center = polygon_centroid(region.polygon)
        extent = polygon_radius(region.polygon)
        label = region.id
    elif task.point is not None:
        center = task.point
        extent = 0.0
        label = task.task_id
    else:
        return BehaviorOutcome(
            mode=PlanMode.HOLD,
            phase=BehaviorPhase.HOLDING,
            hold=True,
            warnings=[PlanWarning.TARGET_UNKNOWN],
            confidence=0.2,
            targets=[context.estimated_position],
        )

    altitude = task.desired_altitude if task.desired_altitude is not None else config.default_altitude
    standoff = task.standoff if task.standoff is not None else config.watch_standoff
    radius = max(config.watch_minimum_standoff, extent + standoff)

    team, warnings = active_team(context)
    index = shard_index(team, context.drone_id)
    nominal_angle = 2.0 * pi * index / max(len(team), 1)

    observation, quality = best_spread_point(
        environment, context, center.with_z(altitude), radius, nominal_angle, clearance, altitude
    )
    state.observation_point = observation

    distance = context.estimated_position.distance_xy(observation)
    arrived = distance <= config.watch_arrival_radius
    if arrived:
        state.phase = BehaviorPhase.OBSERVING
    elif state.phase == BehaviorPhase.OBSERVING:
        state.phase = BehaviorPhase.REPOSITIONING
    else:
        state.phase = BehaviorPhase.MOVING_TO_POSITION

    return BehaviorOutcome(
        mode=PlanMode.OBSERVE,
        phase=state.phase,
        targets=[observation],
        completion=CompletionCondition(
            kind=CompletionKind.CONTINUOUS,
            point=observation,
            radius=config.watch_arrival_radius,
            description=f"observe {label} from standoff",
        ),
        warnings=list(warnings),
        altitude=altitude,
        hold=arrived,
        speed_scale=0.5 if arrived else 1.0,
        confidence=max(0.4, quality),
        metadata={
            "watch_target": center.model_dump(),
            "observation_point": observation.model_dump(),
            "standoff": radius,
            "bearing_index": index,
            "team": team,
            "spread_quality": round(quality, 3),
        },
    )
