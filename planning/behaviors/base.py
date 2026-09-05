"""Shared behaviour plumbing.

A behaviour answers one question: *where should this drone go next, and what
does "done" mean?* It returns coarse targets; the planner is responsible for
obstacle routing, energy, deconfliction, and speed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import pi

from ..environment import EnvironmentQuery
from ..geometry import distance_xy
from ..models import (
    BehaviorPhase,
    CompletionCondition,
    CompletionKind,
    PlanMode,
    PlanningContext,
    PlanWarning,
    Vector3,
)


@dataclass
class BehaviorOutcome:
    mode: PlanMode
    phase: BehaviorPhase
    targets: list[Vector3] = field(default_factory=list)
    completion: CompletionCondition = field(default_factory=CompletionCondition)
    confidence: float = 1.0
    warnings: list[PlanWarning] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    altitude: float | None = None
    speed_scale: float = 1.0
    hold: bool = False


def within_radius(point: Vector3, radius: float, description: str = "") -> CompletionCondition:
    return CompletionCondition(
        kind=CompletionKind.WITHIN_RADIUS,
        point=point,
        radius=radius,
        description=description,
    )


def find_free_point(
    environment: EnvironmentQuery | None,
    context: PlanningContext,
    candidate: Vector3,
    clearance: float,
    search_radius: float = 40.0,
) -> Vector3:
    """Nudge ``candidate`` to the nearest free, in-bounds point.

    Deterministic ring search: rings of increasing radius, 16 fixed bearings.
    """

    bounds = context.world_bounds()
    margin = context.config.bounds_margin
    candidate = bounds.clamped(candidate, margin)
    if environment is None or environment.is_free(candidate, clearance):
        return candidate
    from ..geometry import offset_point

    steps = 16
    for ring in (search_radius * 0.4, search_radius * 0.8, search_radius * 1.4, search_radius * 2.2):
        for step in range(steps):
            probe = bounds.clamped(
                offset_point(candidate, 2.0 * pi * step / steps, ring, z=candidate.z), margin
            )
            if environment.is_free(probe, clearance):
                return probe
    return candidate


def best_spread_point(
    environment: EnvironmentQuery | None,
    context: PlanningContext,
    center: Vector3,
    radius: float,
    nominal_angle: float,
    clearance: float,
    altitude: float,
) -> tuple[Vector3, float]:
    """Pick an observation point near ``nominal_angle`` that is free and not
    crowded by known peers. Returns the point and a 0..1 quality score."""

    from ..geometry import offset_point

    occupied = [peer.position for peer in context.peers if peer.available and peer.position is not None]
    separation = context.config.minimum_horizontal_separation
    best: tuple[float, Vector3] | None = None
    for radius_scale in (1.0, 1.25, 0.8, 1.5):
        for angle_step in (0, 1, -1, 2, -2, 3, -3, 4, -4):
            angle = nominal_angle + angle_step * (pi / 12.0)
            probe = offset_point(center, angle, radius * radius_scale, z=altitude)
            probe = context.world_bounds().clamped(probe, context.config.bounds_margin)
            if environment is not None and not environment.is_free(probe, clearance):
                continue
            crowding = (
                min((distance_xy(probe, position) for position in occupied), default=separation * 2.0)
                if occupied
                else separation * 2.0
            )
            angular_penalty = abs(angle_step) * 0.05
            radius_penalty = abs(radius_scale - 1.0) * 0.15
            score = min(1.0, crowding / max(separation, 1e-6)) - angular_penalty - radius_penalty
            if best is None or score > best[0]:
                best = (score, probe)
            if score >= 1.0 - 1e-9:
                return probe, 1.0
    if best is None:
        fallback = find_free_point(environment, context, center, clearance)
        return fallback, 0.3
    return best[1], max(0.0, min(1.0, best[0]))
