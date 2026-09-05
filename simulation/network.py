"""Seeded, message-level peer network with delay, loss, and partitions."""

from __future__ import annotations

import heapq
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field

from .config import NetworkConfig
from .events import EventBus
from .interference import InterferenceEngine
from .models import EventCategory, EventType, LinkState, NetworkMessage


@dataclass(order=True)
class _Delivery:
    deliver_at: float
    order: int
    recipient: str = field(compare=False)
    message: NetworkMessage = field(compare=False)


class NetworkSimulator:
    CONTROL_ID = "mission-control"

    def __init__(
        self,
        config: NetworkConfig,
        rng: random.Random,
        interference: InterferenceEngine,
        events: EventBus,
    ) -> None:
        self.config = config
        self.rng = rng
        self.interference = interference
        self.events = events
        self._endpoints: set[str] = {self.CONTROL_ID}
        self._online: dict[str, bool] = {self.CONTROL_ID: True}
        self._pending: list[_Delivery] = []
        self._inboxes: dict[str, deque[NetworkMessage]] = defaultdict(deque)
        self._partitions: dict[frozenset[str], float] = {}
        self._counter = 0

    def register(self, endpoint_id: str) -> None:
        self._endpoints.add(endpoint_id)
        self._online[endpoint_id] = True

    def set_online(self, endpoint_id: str, online: bool) -> None:
        self._online[endpoint_id] = online
        if not online:
            self._inboxes[endpoint_id].clear()

    def partition(self, first: set[str], second: set[str], until: float, now: float) -> None:
        for source in first:
            for target in second:
                if source != target:
                    self._partitions[frozenset((source, target))] = until
        self.events.emit(
            now,
            EventCategory.NETWORK,
            EventType.NETWORK_PARTITION,
            "network",
            f"Network partition separated {sorted(first)} from {sorted(second)}",
            first | second,
            {"until": until},
        )

    def reconnect_all(self, now: float) -> None:
        if self._partitions:
            affected = set().union(*self._partitions.keys())
            self._partitions.clear()
            self.events.emit(
                now,
                EventCategory.NETWORK,
                EventType.NETWORK_RECONNECTED,
                "network",
                "All network partitions were cleared",
                affected,
            )

    def update(self, now: float) -> None:
        expired = [pair for pair, until in self._partitions.items() if until <= now]
        if expired:
            affected = set().union(*expired)
            for pair in expired:
                del self._partitions[pair]
            self.events.emit(
                now,
                EventCategory.NETWORK,
                EventType.NETWORK_RECONNECTED,
                "network",
                "A temporary network partition ended",
                affected,
            )
        delivered = 0
        while self._pending and self._pending[0].deliver_at <= now:
            item = heapq.heappop(self._pending)
            if self._online.get(item.recipient, False) and not self._is_partitioned(item.message.sender_id, item.recipient, now):
                self._inboxes[item.recipient].append(item.message)
            delivered += 1
            if delivered >= self.config.bandwidth_messages_per_tick:
                break

    def send(self, message: NetworkMessage, now: float) -> bool:
        if not self._online.get(message.sender_id, False):
            return False
        recipients = (
            sorted(self._endpoints - {message.sender_id})
            if message.recipient_id is None
            else [message.recipient_id]
        )
        accepted = False
        for recipient in recipients:
            if recipient not in self._endpoints or not self._online.get(recipient, False):
                continue
            if self._is_partitioned(message.sender_id, recipient, now):
                self._drop(message, recipient, now, "partition")
                continue
            severity = max(
                self.interference.network_severity(message.sender_id),
                self.interference.network_severity(recipient),
            )
            loss = min(1.0, self.config.base_packet_loss + 0.9 * severity)
            if self.rng.random() < loss:
                self._drop(message, recipient, now, "packet_loss")
                continue
            latency = max(0.0, self.config.base_latency + self.rng.uniform(-self.config.jitter, self.config.jitter))
            latency += severity * self.rng.uniform(0.05, 0.8)
            self._queue(message, recipient, now + latency)
            accepted = True
            if self.config.duplication_probability and self.rng.random() < self.config.duplication_probability:
                self._queue(message, recipient, now + latency + 0.01)
        return accepted

    def _queue(self, message: NetworkMessage, recipient: str, deliver_at: float) -> None:
        self._counter += 1
        heapq.heappush(
            self._pending,
            _Delivery(deliver_at, self._counter, recipient, message.model_copy(deep=True)),
        )

    def _drop(self, message: NetworkMessage, recipient: str, now: float, reason: str) -> None:
        self.events.emit(
            now,
            EventCategory.NETWORK,
            EventType.MESSAGE_DROPPED,
            message.sender_id,
            f"{message.type} message to {recipient} dropped ({reason})",
            [message.sender_id, recipient],
            {"message_id": message.message_id, "reason": reason},
        )

    def receive(self, endpoint_id: str) -> list[NetworkMessage]:
        messages = list(self._inboxes[endpoint_id])
        self._inboxes[endpoint_id].clear()
        return messages

    def _is_partitioned(self, source: str, target: str, now: float) -> bool:
        return self._partitions.get(frozenset((source, target)), 0.0) > now

    def links(self, now: float) -> list[LinkState]:
        nodes = sorted(endpoint for endpoint in self._endpoints if endpoint != self.CONTROL_ID)
        links: list[LinkState] = []
        for index, source in enumerate(nodes):
            for target in nodes[index + 1 :]:
                severity = max(
                    self.interference.network_severity(source),
                    self.interference.network_severity(target),
                )
                partitioned = self._is_partitioned(source, target, now)
                links.append(
                    LinkState(
                        source_id=source,
                        target_id=target,
                        available=self._online.get(source, False) and self._online.get(target, False) and not partitioned,
                        quality=0.0 if partitioned else max(0.0, 1.0 - severity),
                        latency_seconds=self.config.base_latency + severity * 0.425,
                        partitioned=partitioned,
                    )
                )
        return links

