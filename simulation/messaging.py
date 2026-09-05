"""Deterministic message classification and bandwidth-aware egress scheduling.

The classifier is a pure function of a message's type and payload, so a node,
the transport, and an offline analysis tool all agree on a packet's tier
without any shared state.  The scheduler that consumes those tiers never
exempts a packet from physical transmission loss; it only decides *which*
already-transmitted packets a congested link gets to deliver first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .models import MessagePriority, MessageType, NetworkMessage

# Priority order used everywhere a tier has to become a sortable number.
PRIORITY_RANK: dict[MessagePriority, int] = {
    MessagePriority.CRITICAL: 0,
    MessagePriority.HIGH: 1,
    MessagePriority.MEDIUM: 2,
    MessagePriority.LOW: 3,
}

# Base criticality per tier; payload evidence nudges within the tier only.
_TIER_CRITICALITY: dict[MessagePriority, float] = {
    MessagePriority.CRITICAL: 0.95,
    MessagePriority.HIGH: 0.70,
    MessagePriority.MEDIUM: 0.45,
    MessagePriority.LOW: 0.20,
}

# Seconds after ``timestamp_sent`` at which the information stops being useful.
_TIER_TTL: dict[MessagePriority, float] = {
    MessagePriority.CRITICAL: 8.0,
    MessagePriority.HIGH: 4.0,
    MessagePriority.MEDIUM: 2.5,
    MessagePriority.LOW: 1.5,
}

_TYPE_TTL: dict[MessageType, float] = {
    MessageType.HEARTBEAT: 2.0,
    MessageType.STATUS: 3.0,
    MessageType.POSITION_UPDATE: 1.5,
    MessageType.TASK_BID: 2.0,
    MessageType.WORLD_UPDATE: 6.0,
    MessageType.MISSION_SYNC: 4.0,
    MessageType.COMM_OBSERVATION: 6.0,
}

# Status-like traffic whose newest copy fully supersedes any queued older copy.
_COALESCABLE: frozenset[MessageType] = frozenset(
    {
        MessageType.HEARTBEAT,
        MessageType.STATUS,
        MessageType.POSITION_UPDATE,
        MessageType.CAPABILITY_UPDATE,
        MessageType.MISSION_SYNC,
        MessageType.WORLD_UPDATE,
        MessageType.COMM_OBSERVATION,
    }
)

# Beacons whose value is their *arrival time*: two identical copies are not
# redundant, so these are coalescable but never deduplicated.
_LIVENESS: frozenset[MessageType] = frozenset(
    {
        MessageType.HEARTBEAT,
        MessageType.STATUS,
        MessageType.POSITION_UPDATE,
    }
)

# Full-snapshot traffic where identical content really is a pure retransmission.
_DEDUPABLE: frozenset[MessageType] = frozenset(
    {
        MessageType.MISSION_SYNC,
        MessageType.WORLD_UPDATE,
        MessageType.COMM_OBSERVATION,
        MessageType.TASK_BID,
        MessageType.MISSION_ANNOUNCE,
    }
)

# Safety / coordination facts that another node cannot re-derive on its own.
_ALWAYS_CRITICAL: frozenset[MessageType] = frozenset(
    {
        MessageType.TASK_RELEASE,
        MessageType.TASK_COMPLETE,
        MessageType.TASK_ASSIGNMENT,
        MessageType.TASK_ACK,
    }
)

_HIGH: frozenset[MessageType] = frozenset(
    {
        MessageType.HEARTBEAT,
        MessageType.MISSION_ANNOUNCE,
        MessageType.MISSION_STATE,
        MessageType.MISSION_SYNC,
        MessageType.REPLAN_REQUEST,
        MessageType.PREDICTIVE_ALERT,
    }
)

_LOW: frozenset[MessageType] = frozenset(
    {
        MessageType.POSITION_UPDATE,
        MessageType.CAPABILITY_UPDATE,
    }
)

HEADER_BYTES = 96
"""Fixed per-packet overhead added to the measured payload size."""


@dataclass(frozen=True)
class MessageClass:
    """Everything the transport needs to schedule one packet."""

    priority: MessagePriority
    criticality: float
    ttl_seconds: float
    payload_bytes: int
    replaceable: bool
    dedupable: bool
    coalesce_key: str | None
    reason: str

    @property
    def rank(self) -> int:
        return PRIORITY_RANK[self.priority]


def payload_bytes(message: NetworkMessage) -> int:
    """Real serialized size of the payload plus a fixed header allowance."""

    try:
        encoded = json.dumps(message.payload, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):  # pragma: no cover - payloads are JSON models
        encoded = str(message.payload)
    return HEADER_BYTES + len(encoded.encode("utf-8"))


def classify(message: NetworkMessage) -> MessageClass:
    """Assign a deterministic tier from the message type and payload evidence."""

    payload = message.payload
    kind = message.type
    reason = ""

    if bool(payload.get("emergency")) or bool(payload.get("safety_critical")):
        priority, reason = MessagePriority.CRITICAL, "emergency/safety state"
    elif bool(payload.get("lease_conflict")):
        priority, reason = MessagePriority.CRITICAL, "lease conflict"
    elif bool(payload.get("control_transition")):
        priority, reason = MessagePriority.CRITICAL, "critical control-state transition"
    elif kind == MessageType.TASK_AWARD:
        if payload.get("renewal"):
            priority, reason = MessagePriority.LOW, "routine lease renewal"
        else:
            priority, reason = MessagePriority.CRITICAL, "task award"
    elif kind in _ALWAYS_CRITICAL:
        priority, reason = MessagePriority.CRITICAL, f"{kind.value.lower()} coordination"
    elif kind == MessageType.WORLD_UPDATE:
        if _has_important_observation(payload):
            priority, reason = MessagePriority.HIGH, "high-confidence important observation"
        else:
            priority, reason = MessagePriority.MEDIUM, "world-model delta"
    elif kind in _HIGH:
        priority, reason = MessagePriority.HIGH, f"{kind.value.lower()} liveness/mission state"
    elif kind in _LOW:
        priority, reason = MessagePriority.LOW, "routine low-value telemetry"
    elif kind == MessageType.STATUS:
        priority, reason = MessagePriority.MEDIUM, "normal status"
    else:
        priority, reason = MessagePriority.MEDIUM, "default tier"

    criticality = _TIER_CRITICALITY[priority]
    task_priority = payload.get("task_priority")
    if isinstance(task_priority, (int, float)):
        criticality = min(1.0, criticality + (float(task_priority) - 50.0) / 500.0)

    ttl = _TYPE_TTL.get(kind, _TIER_TTL[priority])
    if priority == MessagePriority.CRITICAL:
        ttl = max(ttl, _TIER_TTL[MessagePriority.CRITICAL])

    replaceable = kind in _COALESCABLE and priority != MessagePriority.CRITICAL
    key = f"{message.sender_id}|{message.recipient_id or '*'}|{kind.value}" if replaceable else None
    dedupable = kind in _DEDUPABLE and kind not in _LIVENESS and priority != MessagePriority.CRITICAL
    return MessageClass(
        priority=priority,
        criticality=round(criticality, 4),
        ttl_seconds=ttl,
        payload_bytes=payload_bytes(message),
        replaceable=replaceable,
        dedupable=dedupable,
        coalesce_key=key,
        reason=reason,
    )


def stamped(message: NetworkMessage) -> NetworkMessage:
    """Return ``message`` with its classification written onto the model.

    Called before signing so the fields are covered by the signature and are
    visible to API consumers; the transport re-derives the same values.
    """

    grade = classify(message)
    message.priority = grade.priority
    message.criticality = grade.criticality
    message.ttl_seconds = grade.ttl_seconds
    message.payload_bytes = grade.payload_bytes
    message.replaceable = grade.replaceable
    message.coalesce_key = grade.coalesce_key
    return message


def _has_important_observation(payload: dict[str, object]) -> bool:
    observations = payload.get("observations")
    if not isinstance(observations, list):
        return False
    for item in observations:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("observation_type", ""))
        confidence = item.get("confidence", 0.0)
        if kind in {"ENTITY_OBSERVED", "OBSTACLE_OBSERVED"} and float(confidence or 0.0) >= 0.70:
            return True
    return False


@dataclass
class MessagingCounters:
    """Cumulative, replay-friendly transport accounting."""

    attempted: int = 0
    transmitted: int = 0
    delivered: int = 0
    bytes_attempted: int = 0
    bytes_delivered: int = 0
    expired: int = 0
    coalesced: int = 0
    deduplicated: int = 0
    bandwidth_deferred: int = 0
    saturated_ticks: int = 0
    attempted_by_priority: dict[str, int] = field(default_factory=dict)
    delivered_by_priority: dict[str, int] = field(default_factory=dict)
    dropped_by_reason: dict[str, int] = field(default_factory=dict)

    def note_attempt(self, grade: MessageClass) -> None:
        self.attempted += 1
        self.bytes_attempted += grade.payload_bytes
        key = grade.priority.value
        self.attempted_by_priority[key] = self.attempted_by_priority.get(key, 0) + 1

    def note_delivery(self, grade: MessageClass) -> None:
        self.delivered += 1
        self.bytes_delivered += grade.payload_bytes
        key = grade.priority.value
        self.delivered_by_priority[key] = self.delivered_by_priority.get(key, 0) + 1

    def note_drop(self, reason: str) -> None:
        self.dropped_by_reason[reason] = self.dropped_by_reason.get(reason, 0) + 1

    def delivery_ratio(self, priority: MessagePriority) -> float:
        attempted = self.attempted_by_priority.get(priority.value, 0)
        if not attempted:
            return 1.0
        return self.delivered_by_priority.get(priority.value, 0) / attempted
