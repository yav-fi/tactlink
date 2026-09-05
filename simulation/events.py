"""Bounded structured event stream."""

from collections import deque
from collections.abc import Iterable
from typing import Any

from .models import EventCategory, EventType, SimulationEvent


class EventBus:
    def __init__(self, history_limit: int = 500) -> None:
        self._history: deque[SimulationEvent] = deque(maxlen=history_limit)
        self._sequence = 0

    def emit(
        self,
        timestamp: float,
        category: EventCategory,
        event_type: EventType,
        source: str,
        summary: str,
        affected: Iterable[str] = (),
        payload: dict[str, Any] | None = None,
    ) -> SimulationEvent:
        self._sequence += 1
        event = SimulationEvent(
            sequence=self._sequence,
            timestamp=round(timestamp, 6),
            category=category,
            event_type=event_type,
            source=source,
            affected_entities=list(affected),
            human_readable_summary=summary,
            payload=payload or {},
        )
        self._history.append(event)
        return event

    def recent(self, limit: int = 100, after_sequence: int = 0) -> list[SimulationEvent]:
        matching = [event for event in self._history if event.sequence > after_sequence]
        return matching[-limit:]

    def clear(self) -> None:
        self._history.clear()
        self._sequence = 0

