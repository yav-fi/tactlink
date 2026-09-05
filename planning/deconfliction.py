"""Lightweight local deconfliction.

Two layers:

1. *Structural* - a team working one task is stratified into altitude layers so
   two drones never share the same cell of airspace by construction.
2. *Reactive* - if a peer is inside the horizontal separation bubble and this
   drone is the yielding party, the next waypoint is nudged laterally and the
   original route is rejoined afterwards.

Priority rule (deterministic, needs no negotiation): higher task priority wins;
on a tie the lexicographically smaller drone id holds its route.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .environment import EnvironmentQuery
from .geometry import distance_xy, perpendicular_xy
from .models import (
    PeerState,
    PlanningConfig,
    PlanningContext,
    PlanWarning,
    Vector3,
)


@dataclass
class DeconflictionOutcome:
    waypoints: list[Vector3]
    altitude: float
    yielding: bool = False
    conflicts: list[str] = field(default_factory=list)
    warnings: list[PlanWarning] = field(default_factory=list)
    speed_scale: float = 1.0


def holds_right_of_way(context: PlanningContext, peer: PeerState) -> bool:
    """True when this drone keeps its route and the peer is expected to yield."""

    own_priority = context.current_task.priority if context.current_task else 50
    if own_priority != peer.priority:
        return own_priority > peer.priority
    return context.drone_id < peer.drone_id


def relevant_peers(context: PlanningContext) -> list[PeerState]:
    config = context.config
    return [
        peer
        for peer in sorted(context.peers, key=lambda item: item.drone_id)
        if peer.available
        and peer.position is not None
        and context.now - peer.last_seen <= config.peer_stale_seconds
    ]


def deconflict(
    context: PlanningContext,
    waypoints: list[Vector3],
    altitude: float,
    clearance: float,
    environment: EnvironmentQuery | None = None,
) -> DeconflictionOutcome:
    config: PlanningConfig = context.config
    outcome = DeconflictionOutcome(waypoints=list(waypoints), altitude=altitude)
    if not waypoints:
        return outcome

    peers = relevant_peers(context)
    if not peers:
        return outcome

    position = context.estimated_position
    target = waypoints[0]
    conflicting: list[PeerState] = []
    for peer in peers:
        assert peer.position is not None
        horizontal = distance_xy(position, peer.position)
        peer_altitude = peer.altitude if peer.altitude is not None else peer.position.z
        vertical = abs(altitude - peer_altitude)
        converging = distance_xy(target, peer.position) < config.minimum_horizontal_separation
        if horizontal < config.minimum_horizontal_separation and vertical < config.minimum_vertical_separation:
            conflicting.append(peer)
        elif converging and horizontal < config.minimum_horizontal_separation * 2.0:
            conflicting.append(peer)

    if not conflicting:
        return outcome

    outcome.conflicts = [peer.drone_id for peer in conflicting]
    yielding = any(not holds_right_of_way(context, peer) for peer in conflicting)
    if not yielding:
        # Hold the route but slow down slightly so the encounter resolves.
        outcome.speed_scale = 0.8
        outcome.warnings.append(PlanWarning.DECONFLICTION_APPLIED)
        return outcome

    outcome.yielding = True
    outcome.warnings.append(PlanWarning.DECONFLICTION_APPLIED)
    outcome.warnings.append(PlanWarning.DECONFLICTION_YIELDING)
    outcome.speed_scale = 0.7

    # Vertical: step out of the conflicting layer.
    blocked_altitudes = [
        peer.altitude if peer.altitude is not None else peer.position.z  # type: ignore[union-attr]
        for peer in conflicting
    ]
    outcome.altitude = _free_altitude(altitude, blocked_altitudes, config, context)

    # Lateral: insert a temporary offset waypoint, then rejoin the route.
    detour = _lateral_detour(context, target, conflicting, clearance, outcome.altitude, environment)
    if detour is not None:
        outcome.waypoints = [detour, *outcome.waypoints]
    return outcome


def _free_altitude(
    altitude: float,
    blocked: list[float],
    config: PlanningConfig,
    context: PlanningContext,
) -> float:
    bounds = context.world_bounds()
    for step in range(1, 4):
        for candidate in (altitude + step * config.altitude_layer, altitude - step * config.altitude_layer):
            if candidate < bounds.minimum.z + 5.0 or candidate > bounds.maximum.z:
                continue
            if all(abs(candidate - value) >= config.minimum_vertical_separation for value in blocked):
                return candidate
    return altitude


def _lateral_detour(
    context: PlanningContext,
    target: Vector3,
    conflicting: list[PeerState],
    clearance: float,
    altitude: float,
    environment: EnvironmentQuery | None,
) -> Vector3 | None:
    config = context.config
    position = context.estimated_position
    dx, dy = target.x - position.x, target.y - position.y
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None
    px, py = perpendicular_xy(dx, dy)

    # Prefer the side away from the peers we are yielding to.
    peer_x = sum(peer.position.x for peer in conflicting) / len(conflicting)  # type: ignore[union-attr]
    peer_y = sum(peer.position.y for peer in conflicting) / len(conflicting)  # type: ignore[union-attr]
    toward_peer = (peer_x - position.x) * px + (peer_y - position.y) * py
    sign = -1.0 if toward_peer > 0 else 1.0

    for magnitude in (config.lateral_avoidance_offset, config.lateral_avoidance_offset * 1.6):
        for direction in (sign, -sign):
            candidate = Vector3(
                x=position.x + dx * 0.4 + px * direction * magnitude,
                y=position.y + dy * 0.4 + py * direction * magnitude,
                z=altitude,
            )
            candidate = context.world_bounds().clamped(candidate, config.bounds_margin)
            if environment is not None and not environment.is_free(candidate, clearance):
                continue
            if all(
                distance_xy(candidate, peer.position) >= config.minimum_horizontal_separation  # type: ignore[arg-type]
                for peer in conflicting
            ):
                return candidate
    return None
