"""Seeded message transport with spatial, interference, and obstruction loss.

The simulator may use ground-truth positions to model the physical network.
Those positions are never placed on ``DroneNode``; nodes learn only through
messages that actually traverse this transport.
"""

from __future__ import annotations

import heapq
import json
import random
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha1
from math import sqrt
from typing import Any

from .config import NetworkConfig
from .events import EventBus
from .interference import InterferenceEngine
from .messaging import MessageClass, MessagingCounters, classify
from .models import (
    EventCategory,
    EventType,
    LinkState,
    MessagePriority,
    MessagingMetrics,
    NetworkMessage,
    NetworkMetrics,
    Vector3,
)


@dataclass(order=True)
class _Delivery:
    deliver_at: float
    order: int
    recipient: str = field(compare=False)
    message: NetworkMessage = field(compare=False)
    grade: MessageClass | None = field(compare=False, default=None)
    dropped: bool = field(compare=False, default=False)
    drop_reason: str = field(compare=False, default="")


class NetworkSimulator:
    CONTROL_ID = "mission-control"

    def __init__(
        self,
        config: NetworkConfig,
        rng: random.Random,
        interference: InterferenceEngine,
        events: EventBus,
        position_provider: Callable[[str], Vector3 | None] | None = None,
        world_definition: Any | None = None,
    ) -> None:
        self.config = config
        self.rng = rng
        self.interference = interference
        self.events = events
        self._position_provider = position_provider
        self._world_definition = world_definition
        self._endpoints: set[str] = {self.CONTROL_ID}
        self._online: dict[str, bool] = {self.CONTROL_ID: True}
        self._pending: list[_Delivery] = []
        self._ready: list[_Delivery] = []
        self._recent_digests: deque[tuple[str, str, float]] = deque(maxlen=512)
        self._digest_index: dict[tuple[str, str], float] = {}
        self._inboxes: dict[str, deque[NetworkMessage]] = defaultdict(deque)
        self._partitions: dict[frozenset[str], float] = {}
        self._recent_outcomes: deque[bool] = deque(maxlen=500)
        self._counter = 0
        self.counters = MessagingCounters()

    def register(self, endpoint_id: str) -> None:
        self._endpoints.add(endpoint_id)
        self._online[endpoint_id] = True

    def set_online(self, endpoint_id: str, online: bool) -> None:
        self._online[endpoint_id] = online
        if not online:
            self._inboxes[endpoint_id].clear()

    def is_online(self, endpoint_id: str) -> bool:
        return self._online.get(endpoint_id, False)

    def partition(self, first: set[str], second: set[str], until: float, now: float) -> None:
        for source in first:
            for target in second:
                if source != target:
                    self._partitions[frozenset((source, target))] = until
        self.events.emit(
            now, EventCategory.NETWORK, EventType.NETWORK_PARTITION, "network",
            f"Network partition separated {sorted(first)} from {sorted(second)}",
            first | second, {"until": until},
        )

    def reconnect_all(self, now: float) -> None:
        if self._partitions:
            affected = set().union(*self._partitions.keys())
            self._partitions.clear()
            self.events.emit(
                now, EventCategory.NETWORK, EventType.NETWORK_RECONNECTED, "network",
                "All explicit network partitions were cleared", affected,
            )

    def update(self, now: float) -> None:
        expired = [pair for pair, until in self._partitions.items() if until <= now]
        if expired:
            affected = set().union(*expired)
            for pair in expired:
                del self._partitions[pair]
            self.events.emit(
                now, EventCategory.NETWORK, EventType.NETWORK_RECONNECTED, "network",
                "A temporary network partition ended", affected,
            )
        self._drain(now)

    # -- scheduling ---------------------------------------------------------

    def _drain(self, now: float) -> None:
        """Move transmitted packets into inboxes under the bandwidth budget.

        Physical loss already happened in :meth:`send`.  This stage models the
        *scheduling* side of a congested radio: a finite number of messages and
        bytes get through per tick, and adaptive mode spends that budget on the
        most valuable packets instead of on whatever arrived first.
        """

        while self._pending and self._pending[0].deliver_at <= now + 1e-9:
            self._ready.append(heapq.heappop(self._pending))

        adaptive = self.config.adaptive_messaging
        survivors: list[_Delivery] = []
        for item in self._ready:
            if item.dropped:
                continue
            grade = item.grade or classify(item.message)
            if adaptive and grade.ttl_seconds > 0.0 and now - item.message.timestamp_sent > grade.ttl_seconds:
                item.dropped, item.drop_reason = True, "ttl_expired"
                self.counters.expired += 1
                self.counters.note_drop("ttl_expired")
                continue
            survivors.append(item)

        if adaptive:
            survivors = self._coalesce(survivors)
            survivors.sort(
                key=lambda entry: (
                    (entry.grade or classify(entry.message)).rank,
                    entry.deliver_at,
                    entry.order,
                )
            )
        else:
            survivors.sort(key=lambda entry: (entry.deliver_at, entry.order))

        message_budget = self.config.bandwidth_messages_per_tick
        byte_budget = self.config.bandwidth_bytes_per_tick
        delivered = 0
        spent_bytes = 0
        remainder: list[_Delivery] = []
        for index, item in enumerate(survivors):
            if (message_budget > 0 and delivered >= message_budget) or (
                byte_budget > 0 and spent_bytes >= byte_budget
            ):
                remainder = survivors[index:]
                break
            grade = item.grade or classify(item.message)
            delivered += 1
            spent_bytes += grade.payload_bytes
            if self._online.get(item.recipient, False) and not self._is_partitioned(
                item.message.sender_id, item.recipient, now
            ):
                self._inboxes[item.recipient].append(item.message)
                self.counters.note_delivery(grade)
            else:
                self.counters.note_drop("recipient_unreachable")
        if remainder:
            self.counters.bandwidth_deferred += len(remainder)
            self.counters.saturated_ticks += 1
        self._ready = remainder

    def _coalesce(self, survivors: list[_Delivery]) -> list[_Delivery]:
        """Keep only the newest of each replaceable stream still awaiting bandwidth.

        Coalescing applies to packets that are *queued behind congestion*, never
        to ones merely propagating: replacing an in-flight beacon would silently
        lower its arrival rate on a high-latency link.
        """

        newest: dict[tuple[str, str], _Delivery] = {}
        for item in survivors:
            grade = item.grade or classify(item.message)
            if grade.coalesce_key is None:
                continue
            key = (item.recipient, grade.coalesce_key)
            current = newest.get(key)
            if current is None or (item.message.timestamp_sent, item.order) > (
                current.message.timestamp_sent,
                current.order,
            ):
                newest[key] = item
        if not newest:
            return survivors
        kept: list[_Delivery] = []
        for item in survivors:
            grade = item.grade or classify(item.message)
            key = (item.recipient, grade.coalesce_key) if grade.coalesce_key else None
            if key is not None and newest.get(key) is not item:
                item.dropped, item.drop_reason = True, "coalesced"
                self.counters.coalesced += 1
                self.counters.note_drop("coalesced")
                continue
            kept.append(item)
        return kept

    def send(self, message: NetworkMessage, now: float) -> bool:
        if not self._online.get(message.sender_id, False):
            return False
        grade = classify(message)
        recipients = sorted(self._endpoints - {message.sender_id}) if message.recipient_id is None else [message.recipient_id]
        accepted = False
        for recipient in recipients:
            if recipient not in self._endpoints or not self._online.get(recipient, False):
                continue
            self.counters.note_attempt(grade)
            link = self.link_state(message.sender_id, recipient, now)
            # Physical loss is evaluated before any prioritisation: a CRITICAL
            # packet is exactly as fragile on the air as a LOW one.
            if not link.available:
                reason = "partition" if link.partitioned else "physical_link"
                self._drop(message, recipient, now, reason)
                self.counters.note_drop(reason)
                self._recent_outcomes.append(False)
                continue
            if self.rng.random() < link.packet_loss:
                self._drop(message, recipient, now, "packet_loss")
                self.counters.note_drop("packet_loss")
                self._recent_outcomes.append(False)
                continue
            self.counters.transmitted += 1
            latency = max(0.0, link.latency_seconds + self.rng.uniform(-self.config.jitter, self.config.jitter))
            self._queue(message, recipient, now + latency, grade, now)
            self._recent_outcomes.append(True)
            accepted = True
            if self.config.duplication_probability and self.rng.random() < self.config.duplication_probability:
                self._queue(message, recipient, now + latency + 0.01, grade, now)
        return accepted

    def _queue(
        self,
        message: NetworkMessage,
        recipient: str,
        deliver_at: float,
        grade: MessageClass | None = None,
        now: float | None = None,
    ) -> None:
        grade = grade or classify(message)
        moment = now if now is not None else message.timestamp_sent
        if self.config.adaptive_messaging and grade.dedupable and self._is_duplicate(message, recipient, grade, moment):
            self.counters.deduplicated += 1
            self.counters.note_drop("duplicate")
            return
        self._counter += 1
        item = _Delivery(deliver_at, self._counter, recipient, message.model_copy(deep=True), grade)
        heapq.heappush(self._pending, item)
        self._enforce_queue_bound()

    def _enforce_queue_bound(self) -> None:
        """Bound transport memory while preserving the most valuable packets."""

        maximum = self.config.maximum_queue_messages
        while len(self._pending) + len(self._ready) > maximum:
            candidates = self._pending + self._ready
            if self.config.adaptive_messaging:
                victim = max(
                    candidates,
                    key=lambda item: (
                        (item.grade or classify(item.message)).rank,
                        item.deliver_at,
                        item.order,
                    ),
                )
            else:
                victim = max(candidates, key=lambda item: (item.deliver_at, item.order))
            if victim in self._pending:
                self._pending.remove(victim)
                heapq.heapify(self._pending)
            else:
                self._ready.remove(victim)
            victim.dropped = True
            victim.drop_reason = "queue_overflow"
            self.counters.note_drop("queue_overflow")

    def _is_duplicate(
        self, message: NetworkMessage, recipient: str, grade: MessageClass, now: float
    ) -> bool:
        """Suppress a byte-identical retransmission inside a short window."""

        digest = sha1(
            json.dumps(message.payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        key = (recipient, f"{message.sender_id}|{message.type.value}|{digest}")
        window = self.config.deduplication_window_seconds
        last = self._digest_index.get(key)
        if last is not None and now - last < window:
            return True
        self._digest_index[key] = now
        self._recent_digests.append((key[0], key[1], now))
        while len(self._recent_digests) >= self._recent_digests.maxlen and self._recent_digests:
            stale = self._recent_digests.popleft()
            if self._digest_index.get((stale[0], stale[1])) == stale[2]:
                del self._digest_index[(stale[0], stale[1])]
        return False

    def _drop(self, message: NetworkMessage, recipient: str, now: float, reason: str) -> None:
        noisy_control_plane = message.type.value in {"HEARTBEAT", "STATUS", "MISSION_SYNC", "TASK_BID", "WORLD_UPDATE"}
        noisy_renewal = message.type.value == "TASK_AWARD" and message.payload.get("renewal")
        if not noisy_control_plane and not noisy_renewal:
            self.events.emit(
                now, EventCategory.NETWORK, EventType.MESSAGE_DROPPED, message.sender_id,
                f"{message.type} message to {recipient} dropped ({reason})",
                [message.sender_id, recipient], {"message_id": message.message_id, "reason": reason},
            )

    def receive(self, endpoint_id: str) -> list[NetworkMessage]:
        messages = list(self._inboxes[endpoint_id])
        self._inboxes[endpoint_id].clear()
        return messages

    def _is_partitioned(self, source: str, target: str, now: float) -> bool:
        return self._partitions.get(frozenset((source, target)), 0.0) > now

    def link_state(self, source: str, target: str, now: float) -> LinkState:
        partitioned = self._is_partitioned(source, target, now)
        online = self._online.get(source, False) and self._online.get(target, False)
        severity = max(self.interference.network_severity(source), self.interference.network_severity(target))
        distance = 0.0
        obstructed = False
        distance_quality = 1.0
        if self.CONTROL_ID not in {source, target} and self._position_provider is not None:
            first = self._position_provider(source)
            second = self._position_provider(target)
            if first is None or second is None:
                online = False
            else:
                distance = first.distance_to(second)
                ratio = distance / self.config.reference_range_m
                distance_quality = 1.0 / (1.0 + ratio ** self.config.distance_falloff_power)
                if distance >= self.config.hard_range_m:
                    distance_quality = 0.0
                obstructed = self._obstructed(first, second)
        quality = max(0.0, min(1.0, distance_quality * (1.0 - severity) * (self.config.obstruction_penalty if obstructed else 1.0)))
        available = online and not partitioned and quality >= self.config.minimum_usable_quality
        packet_loss = min(1.0, self.config.base_packet_loss + (1.0 - quality) ** 2 * 0.95)
        latency = self.config.base_latency + (1.0 - quality) * self.config.poor_link_latency_seconds
        return LinkState(
            source_id=source, target_id=target, available=available,
            quality=0.0 if partitioned or not online else quality,
            latency_seconds=latency, partitioned=partitioned, distance_m=distance,
            obstructed=obstructed, packet_loss=packet_loss,
        )

    def links(self, now: float) -> list[LinkState]:
        nodes = sorted(endpoint for endpoint in self._endpoints if endpoint != self.CONTROL_ID)
        return [self.link_state(source, target, now) for index, source in enumerate(nodes) for target in nodes[index + 1 :]]

    def communication_profile(self, node_id: str, now: float) -> dict[str, float | int | bool]:
        peers = sorted(endpoint for endpoint in self._endpoints if endpoint not in {self.CONTROL_ID, node_id})
        links = [self.link_state(node_id, peer, now) for peer in peers]
        usable = [link for link in links if link.available]
        return {
            "mean_quality": sum(link.quality for link in usable) / len(usable) if usable else 0.0,
            "reachable_peers": len(usable), "total_peers": len(peers),
            "nearest_peer_distance": min((link.distance_m for link in usable), default=self.config.hard_range_m),
            "articulation_point": node_id in self.articulation_points(now),
        }

    def components(self, now: float) -> list[list[str]]:
        nodes = sorted(endpoint for endpoint in self._endpoints if endpoint != self.CONTROL_ID and self._online.get(endpoint, False))
        adjacency = {node: set() for node in nodes}
        for link in self.links(now):
            if link.available and link.source_id in adjacency and link.target_id in adjacency:
                adjacency[link.source_id].add(link.target_id)
                adjacency[link.target_id].add(link.source_id)
        result: list[list[str]] = []
        unseen = set(nodes)
        while unseen:
            root = min(unseen)
            stack, component = [root], []
            unseen.remove(root)
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor in sorted(adjacency[current] & unseen, reverse=True):
                    unseen.remove(neighbor)
                    stack.append(neighbor)
            result.append(sorted(component))
        return sorted(result, key=lambda item: (-len(item), item))

    def articulation_points(self, now: float) -> set[str]:
        nodes = [node for component in self.components(now) for node in component]
        adjacency = {node: set() for node in nodes}
        for link in self.links(now):
            if link.available:
                adjacency[link.source_id].add(link.target_id)
                adjacency[link.target_id].add(link.source_id)
        baseline = len(self.components(now))
        points: set[str] = set()
        for removed in nodes:
            remaining = set(nodes) - {removed}
            count = 0
            while remaining:
                count += 1
                stack = [remaining.pop()]
                while stack:
                    current = stack.pop()
                    for neighbor in adjacency[current] & remaining:
                        remaining.remove(neighbor)
                        stack.append(neighbor)
            if count > baseline:
                points.add(removed)
        return points

    def metrics(self, now: float) -> NetworkMetrics:
        active = [node for node in self._endpoints if node != self.CONTROL_ID and self._online.get(node, False)]
        components = self.components(now)
        links = [link for link in self.links(now) if self._online.get(link.source_id) and self._online.get(link.target_id)]
        mean_quality = sum(link.quality for link in links) / len(links) if links else (1.0 if len(active) <= 1 else 0.0)
        largest = len(components[0]) / len(active) if active and components else 1.0
        health = max(0.0, min(1.0, 0.55 * largest + 0.45 * mean_quality))
        loss = 1.0 - sum(self._recent_outcomes) / len(self._recent_outcomes) if self._recent_outcomes else 0.0
        return NetworkMetrics(
            network_health=health, connected_components=components,
            largest_component_fraction=largest, mean_link_quality=mean_quality,
            packet_loss_recent=loss, active_nodes=len(active),
            messaging=self.messaging_metrics(),
        )

    def messaging_metrics(self) -> MessagingMetrics:
        counters = self.counters
        return MessagingMetrics(
            adaptive=self.config.adaptive_messaging,
            messages_attempted=counters.attempted,
            messages_transmitted=counters.transmitted,
            messages_delivered=counters.delivered,
            bytes_attempted=counters.bytes_attempted,
            bytes_delivered=counters.bytes_delivered,
            expired=counters.expired,
            coalesced=counters.coalesced,
            deduplicated=counters.deduplicated,
            bandwidth_deferred=counters.bandwidth_deferred,
            saturated_ticks=counters.saturated_ticks,
            queue_depth=len(self._ready) + len(self._pending),
            attempted_by_priority=dict(sorted(counters.attempted_by_priority.items())),
            delivered_by_priority=dict(sorted(counters.delivered_by_priority.items())),
            dropped_by_reason=dict(sorted(counters.dropped_by_reason.items())),
            critical_delivery_ratio=round(counters.delivery_ratio(MessagePriority.CRITICAL), 6),
            low_delivery_ratio=round(counters.delivery_ratio(MessagePriority.LOW), 6),
        )

    def _obstructed(self, start: Vector3, end: Vector3) -> bool:
        definition = self._world_definition
        if definition is None:
            return False
        if any(self._segment_box(start, end, box.minimum, box.maximum) for box in definition.boxes):
            return True
        for circle in definition.circles:
            if min(start.z, end.z) > circle.height:
                continue
            dx, dy = end.x - start.x, end.y - start.y
            length_sq = dx * dx + dy * dy
            t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((circle.center.x - start.x) * dx + (circle.center.y - start.y) * dy) / length_sq))
            x, y = start.x + t * dx, start.y + t * dy
            if sqrt((x - circle.center.x) ** 2 + (y - circle.center.y) ** 2) <= circle.radius:
                return True
        return False

    @staticmethod
    def _segment_box(start: Vector3, end: Vector3, low: Vector3, high: Vector3) -> bool:
        enter, leave = 0.0, 1.0
        for origin, target, minimum, maximum in ((start.x, end.x, low.x, high.x), (start.y, end.y, low.y, high.y), (start.z, end.z, low.z, high.z)):
            delta = target - origin
            if abs(delta) < 1e-12:
                if origin < minimum or origin > maximum:
                    return False
                continue
            first, second = (minimum - origin) / delta, (maximum - origin) / delta
            if first > second:
                first, second = second, first
            enter, leave = max(enter, first), min(leave, second)
            if enter > leave:
                return False
        return True
