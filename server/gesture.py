"""Small in-memory hand-gesture relay shared by the webcam and browser UI."""

from __future__ import annotations

import time
from collections import deque

from pydantic import BaseModel, Field


class GestureUpdate(BaseModel):
    present: bool = False
    gesture: str = Field(default="None", max_length=64)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = Field(default="none", max_length=32)
    hold_progress: float = Field(default=0.0, ge=0.0, le=1.0)
    sequence_hint: str = Field(default="", max_length=120)
    events: list[str] = Field(default_factory=list, max_length=16)


class GestureEvent(BaseModel):
    sequence: int
    action: str


class GestureSnapshot(BaseModel):
    connected: bool
    present: bool
    gesture: str
    score: float
    source: str
    hold_progress: float
    sequence_hint: str
    events: list[GestureEvent]


class GestureStore:
    """Retain the latest pose plus a short, numbered action history."""

    def __init__(self, disconnect_seconds: float = 2.0) -> None:
        self.disconnect_seconds = disconnect_seconds
        self._latest = GestureUpdate()
        self._last_update: float | None = None
        self._sequence = 0
        self._events: deque[GestureEvent] = deque(maxlen=32)

    def update(self, update: GestureUpdate, now: float | None = None) -> GestureSnapshot:
        self._latest = update
        self._last_update = time.monotonic() if now is None else now
        for action in update.events:
            action = action.strip()
            if not action:
                continue
            self._sequence += 1
            self._events.append(GestureEvent(sequence=self._sequence, action=action[:120]))
        return self.snapshot(now=self._last_update)

    def snapshot(self, now: float | None = None) -> GestureSnapshot:
        current = time.monotonic() if now is None else now
        connected = self._last_update is not None and current - self._last_update <= self.disconnect_seconds
        latest = self._latest if connected else GestureUpdate()
        return GestureSnapshot(
            connected=connected,
            present=latest.present,
            gesture=latest.gesture,
            score=latest.score,
            source=latest.source,
            hold_progress=latest.hold_progress,
            sequence_hint=latest.sequence_hint,
            events=list(self._events),
        )
