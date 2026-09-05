"""FOLLOW - generic standoff tracking of a moving entity.

Observation only: the drone maintains a configurable distance and never closes
on the entity. Multiple followers occupy different bearings around it.
"""

from __future__ import annotations

from math import atan2

from ..coordination import active_team, ring_slot, shard_index
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
from .base import BehaviorOutcome, find_free_point

MAXIMUM_LEAD_SECONDS = 4.0


def plan_follow(
    context: PlanningContext,
    state: BehaviorState,
    environment: EnvironmentQuery | None,
    clearance: float,
) -> BehaviorOutcome:
    task = context.current_task
    assert task is not None
    config = context.config
    altitude = task.desired_altitude if task.desired_altitude is not None else config.default_altitude
    standoff = task.standoff if task.standoff is not None else config.follow_standoff

    tracked = context.entity(task.entity_id) if task.entity_id else None
    warnings: list[PlanWarning] = []

    if tracked is None:
        # Never seen it, or the observation dropped out entirely.
        if state.last_entity_seen is None:
            state.phase = BehaviorPhase.ACQUIRING
            warnings.append(PlanWarning.TARGET_UNKNOWN)
            anchor = task.point or context.estimated_position
            return BehaviorOutcome(
                mode=PlanMode.TRACK,
                phase=BehaviorPhase.ACQUIRING,
                targets=[find_free_point(environment, context, anchor.with_z(altitude), clearance)],
                completion=CompletionCondition(
                    kind=CompletionKind.CONTINUOUS, description="acquire the tracked entity"
                ),
                warnings=warnings,
                altitude=altitude,
                confidence=0.3,
                metadata={"entity_id": task.entity_id, "phase_reason": "no observation"},
            )
        state.phase = BehaviorPhase.LOST
        warnings.append(PlanWarning.TARGET_LOST)
        anchor = state.last_target or context.estimated_position
        return BehaviorOutcome(
            mode=PlanMode.TRACK,
            phase=BehaviorPhase.LOST,
            targets=[anchor.with_z(altitude)],
            completion=CompletionCondition(kind=CompletionKind.CONTINUOUS, description="reacquire entity"),
            warnings=warnings,
            altitude=altitude,
            hold=True,
            speed_scale=0.4,
            confidence=0.25,
            metadata={"entity_id": task.entity_id, "last_known": anchor.model_dump()},
        )

    age = context.now - tracked.last_seen
    state.last_entity_seen = tracked.last_seen
    if age > config.follow_reacquire_seconds:
        state.phase = BehaviorPhase.LOST
        warnings.append(PlanWarning.TARGET_LOST)
        confidence = 0.25
    elif age > config.follow_lost_seconds:
        state.phase = BehaviorPhase.REACQUIRING
        warnings.append(PlanWarning.TARGET_LOST)
        confidence = 0.5
    else:
        state.phase = BehaviorPhase.TRACKING
        confidence = 1.0

    lead = min(MAXIMUM_LEAD_SECONDS, max(0.0, age))
    predicted = tracked.position.moved(tracked.velocity, lead)

    team, team_warnings = active_team(context)
    warnings.extend(team_warnings)
    index = shard_index(team, context.drone_id)

    speed = tracked.velocity.magnitude()
    # Anchor bearing behind the entity when it is moving, otherwise due east.
    base_angle = atan2(-tracked.velocity.y, -tracked.velocity.x) if speed > 0.5 else 0.0
    slot = ring_slot(predicted, standoff, index, max(len(team), 1), phase=base_angle, z=altitude)
    target = find_free_point(environment, context, slot, clearance)
    state.last_target = target

    return BehaviorOutcome(
        mode=PlanMode.TRACK,
        phase=state.phase,
        targets=[target],
        completion=CompletionCondition(
            kind=CompletionKind.CONTINUOUS,
            point=target,
            radius=standoff * 0.4,
            description=f"hold {standoff:.0f}m standoff on {tracked.entity_id}",
        ),
        warnings=warnings,
        altitude=altitude,
        confidence=confidence,
        metadata={
            "entity_id": tracked.entity_id,
            "entity_position": tracked.position.model_dump(),
            "predicted_position": predicted.model_dump(),
            "observation_age": round(age, 3),
            "standoff": standoff,
            "bearing_index": index,
            "team": team,
        },
    )
