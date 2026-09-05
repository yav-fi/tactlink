"""Multi-drone work division.

Every function is deterministic given the same team roster, so independently
running drones reach the same division without negotiating. The roster is
derived from local peer belief, which means it degrades gracefully: when a peer
goes stale the survivors simply recompute a smaller roster and pick up the work.
"""

from __future__ import annotations

from math import pi

from .geometry import distance_xy, offset_point
from .models import PeerState, PlanningConfig, PlanningContext, PlanWarning, Vector3


def active_team(context: PlanningContext) -> tuple[list[str], list[PlanWarning]]:
    """Deterministic roster of drones this node believes are working the task.

    Rules:
    * this drone is always in the roster
    * an assigned peer we have never heard from is assumed alive (avoids
      duplicated coverage on startup)
    * an assigned peer we have heard from but that is stale or unavailable is
      dropped, and ``PEER_STATE_STALE`` is raised
    """

    task = context.current_task
    assigned = list(task.assigned_drones) if task else []
    if not assigned:
        assigned = [context.drone_id]
    known = {peer.drone_id: peer for peer in context.peers}
    warnings: list[PlanWarning] = []
    roster: list[str] = []
    for drone_id in sorted(set(assigned) | {context.drone_id}):
        if drone_id == context.drone_id:
            roster.append(drone_id)
            continue
        if drone_id not in assigned:
            continue
        peer = known.get(drone_id)
        if peer is None:
            roster.append(drone_id)
            continue
        if not peer.available:
            continue
        if context.now - peer.last_seen > context.config.peer_stale_seconds:
            if PlanWarning.PEER_STATE_STALE not in warnings:
                warnings.append(PlanWarning.PEER_STATE_STALE)
            continue
        roster.append(drone_id)
    return roster, warnings


def shard_index(team: list[str], drone_id: str) -> int:
    return team.index(drone_id) if drone_id in team else 0


def split_contiguous(items: list[str], parts: int) -> list[list[str]]:
    """Split into ``parts`` near-equal contiguous blocks (earlier blocks larger)."""

    parts = max(1, parts)
    base, remainder = divmod(len(items), parts)
    chunks: list[list[str]] = []
    cursor = 0
    for index in range(parts):
        size = base + (1 if index < remainder else 0)
        chunks.append(items[cursor : cursor + size])
        cursor += size
    return chunks


def ring_slot(center: Vector3, radius: float, index: int, count: int, phase: float = 0.0, z: float | None = None) -> Vector3:
    """Evenly spaced slot on a ring; the canonical anti-stacking primitive."""

    count = max(1, count)
    angle = phase + 2.0 * pi * (index % count) / count
    return offset_point(center, angle, radius, z=z)


def spread_from_peers(
    candidate: Vector3,
    peers: list[PeerState],
    config: PlanningConfig,
    minimum_gap: float | None = None,
) -> float:
    """How comfortable ``candidate`` is with respect to known peer positions."""

    gap = minimum_gap if minimum_gap is not None else config.minimum_horizontal_separation
    occupied = [peer.position for peer in peers if peer.position is not None and peer.available]
    if not occupied:
        return 1.0
    nearest = min(distance_xy(candidate, position) for position in occupied)
    return min(1.0, nearest / max(gap, 1e-6))


def stagger_offset(index: int, count: int, spacing: float) -> float:
    """Along-route spacing for TRACE so drones do not fly nose to tail."""

    return index * spacing if count > 1 else 0.0


def layered_altitude(base_altitude: float, index: int, count: int, layer: float) -> float:
    """Stratify a team vertically around ``base_altitude``, deterministically."""

    if count <= 1:
        return base_altitude
    centered = index - (count - 1) / 2.0
    return base_altitude + centered * layer
