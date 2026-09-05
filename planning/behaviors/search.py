"""SEARCH - boustrophedon coverage of a region, divided across the team.

Lanes are generated deterministically from the region polygon, so every drone
computes the identical lane list. Each drone then takes a contiguous block of
the *uncovered* lanes based on its index in the active roster. Losing a drone
shrinks the roster, which redistributes the remaining lanes automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from ..coordination import active_team, shard_index, split_contiguous
from ..environment import EnvironmentQuery
from ..geometry import clip_segment_to_polygon, interpolate, polygon_bounds
from ..models import (
    BehaviorPhase,
    BehaviorState,
    CompletionCondition,
    CompletionKind,
    PlanMode,
    PlanningContext,
    PlanWarning,
    Region,
    Vector3,
)
from .base import BehaviorOutcome

LANE_ARRIVAL_RADIUS = 25.0


@dataclass(frozen=True)
class Lane:
    id: str
    start: Vector3
    end: Vector3

    def length(self) -> float:
        return self.start.distance_xy(self.end)


def resolve_region(context: PlanningContext, environment: EnvironmentQuery | None) -> Region | None:
    task = context.current_task
    if task is None:
        return None
    if task.region is not None and task.region.is_valid():
        return task.region
    if task.region_id and environment is not None:
        region = environment.region_geometry(task.region_id)
        if region is not None and region.is_valid():
            return region
    if task.waypoints and len(task.waypoints) >= 3:
        return Region(id=task.region_id or task.task_id, polygon=list(task.waypoints))
    return None


def generate_lanes(region: Region, spacing: float, margin: float = 10.0) -> list[Lane]:
    """Sweep lanes across the region's short axis, running along the long axis."""

    polygon = region.polygon
    min_x, min_y, max_x, max_y = polygon_bounds(polygon)
    width, height = max_x - min_x, max_y - min_y
    along_x = width >= height
    span = height if along_x else width
    count = max(1, int(ceil(span / max(spacing, 1e-6))))
    lanes: list[Lane] = []
    for index in range(count):
        offset = (index + 0.5) * span / count
        if along_x:
            y = min_y + offset
            a = Vector3(x=min_x - 1.0, y=y)
            b = Vector3(x=max_x + 1.0, y=y)
        else:
            x = min_x + offset
            a = Vector3(x=x, y=min_y - 1.0)
            b = Vector3(x=x, y=max_y + 1.0)
        for piece, (t0, t1) in enumerate(clip_segment_to_polygon(a, b, polygon)):
            start = interpolate(a, b, t0)
            end = interpolate(a, b, t1)
            if start.distance_xy(end) < margin:
                continue
            length = start.distance_xy(end)
            inset_fraction = min(margin, length * 0.25) / max(length, 1e-6)
            lanes.append(
                Lane(
                    id=f"{region.id}:L{index}.{piece}",
                    start=interpolate(start, end, inset_fraction),
                    end=interpolate(start, end, 1.0 - inset_fraction),
                )
            )
    if not lanes:  # degenerate region: cover its centroid
        from ..geometry import polygon_centroid

        centroid = polygon_centroid(polygon)
        lanes.append(Lane(id=f"{region.id}:L0.0", start=centroid, end=centroid))
    return lanes


def plan_search(
    context: PlanningContext,
    state: BehaviorState,
    environment: EnvironmentQuery | None,
    clearance: float,
) -> BehaviorOutcome:
    task = context.current_task
    assert task is not None
    region = resolve_region(context, environment)
    if region is None:
        return BehaviorOutcome(
            mode=PlanMode.HOLD,
            phase=BehaviorPhase.HOLDING,
            hold=True,
            warnings=[PlanWarning.REGION_UNKNOWN],
            confidence=0.2,
            targets=[context.estimated_position],
        )

    config = context.config
    altitude = task.desired_altitude if task.desired_altitude is not None else config.default_altitude
    spacing = float(task.metadata.get("lane_spacing", config.search_lane_spacing))
    lanes = generate_lanes(region, spacing, config.search_lane_margin)

    team, warnings = active_team(context)
    index = shard_index(team, context.drone_id)

    shared_completed = {str(value) for value in task.metadata.get("completed_lane_ids", [])}
    completed = set(state.completed_lane_ids) | shared_completed
    remaining = [lane for lane in lanes if lane.id not in completed]

    if not remaining:
        state.phase = BehaviorPhase.COMPLETE
        return BehaviorOutcome(
            mode=PlanMode.COVER,
            phase=BehaviorPhase.COMPLETE,
            targets=[context.estimated_position.with_z(altitude)],
            completion=CompletionCondition(
                kind=CompletionKind.WAYPOINTS_CONSUMED, description="region fully covered"
            ),
            warnings=[*warnings, PlanWarning.COVERAGE_COMPLETE],
            altitude=altitude,
            hold=True,
            metadata={
                "region_id": region.id,
                "team": team,
                "lane_count": len(lanes),
                "assigned_lane_ids": [],
            },
        )

    blocks = split_contiguous([lane.id for lane in remaining], len(team))
    assigned_ids = blocks[index] if index < len(blocks) else []
    lane_by_id = {lane.id: lane for lane in remaining}
    assigned = [lane_by_id[lane_id] for lane_id in assigned_ids]

    # Auto-retire lanes we have clearly finished so a later replan moves on.
    if assigned and context.estimated_position.distance_xy(assigned[0].end) <= LANE_ARRIVAL_RADIUS:
        state.completed_lane_ids.append(assigned[0].id)
        completed.add(assigned[0].id)
        assigned = assigned[1:]
        assigned_ids = assigned_ids[1:]

    state.assigned_lane_ids = list(assigned_ids)
    state.last_team = list(team)

    targets: list[Vector3] = []
    cursor = context.estimated_position
    for lane in assigned:
        # boustrophedon: enter each lane at whichever end is closer
        forward = cursor.distance_xy(lane.start) <= cursor.distance_xy(lane.end)
        entry, exit_point = (lane.start, lane.end) if forward else (lane.end, lane.start)
        targets.append(entry.with_z(altitude))
        targets.append(exit_point.with_z(altitude))
        cursor = exit_point

    if not targets:
        targets = [context.estimated_position.with_z(altitude)]
        phase = BehaviorPhase.COMPLETE
    else:
        phase = BehaviorPhase.COVERING if state.phase == BehaviorPhase.COVERING else BehaviorPhase.STARTING
        if state.phase in {BehaviorPhase.STARTING, BehaviorPhase.COMPLETE}:
            phase = BehaviorPhase.COVERING if completed else BehaviorPhase.STARTING
        state.phase = BehaviorPhase.COVERING

    coverage = 1.0 - len(remaining) / max(len(lanes), 1)
    return BehaviorOutcome(
        mode=PlanMode.COVER,
        phase=phase,
        targets=targets,
        completion=CompletionCondition(
            kind=CompletionKind.WAYPOINTS_CONSUMED,
            description=f"fly all {len(assigned)} assigned lanes of {region.id}",
        ),
        warnings=list(warnings),
        altitude=altitude,
        confidence=0.9 if len(team) > 1 else 1.0,
        metadata={
            "region_id": region.id,
            "team": team,
            "team_index": index,
            "lane_count": len(lanes),
            "lane_spacing": spacing,
            "assigned_lane_ids": list(assigned_ids),
            "remaining_lane_ids": [lane.id for lane in remaining],
            "completed_lane_ids": sorted(completed),
            "coverage_fraction": round(coverage, 4),
        },
    )
