"""Structured, renderable explanations for every adaptive decision.

Each adaptive subsystem produces one of these instead of only an event string,
so the frontend can show *why* something happened without re-deriving it.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from pydantic import BaseModel, Field


class DecisionFactor(BaseModel):
    """One measured quantity that pushed a decision one way or the other."""

    name: str
    value: float
    baseline: float | None = None
    delta: float | None = None
    unit: str = ""
    detail: str = ""


class DecisionOption(BaseModel):
    """A candidate that was scored, whether or not it won."""

    option_id: str
    summary: str
    score: float
    selected: bool = False
    metrics: dict[str, float] = Field(default_factory=dict)


class DecisionExplanation(BaseModel):
    """Why the runtime did something, in a shape a UI can render directly."""

    decision_id: str
    timestamp: float
    kind: str
    subject: str
    headline: str
    rationale: list[str] = Field(default_factory=list)
    factors: list[DecisionFactor] = Field(default_factory=list)
    options: list[DecisionOption] = Field(default_factory=list)
    selected_option: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_text(self) -> str:
        lines = [self.headline, *self.rationale]
        for option in self.options:
            mark = "→" if option.selected else " "
            lines.append(f"{mark} {option.option_id}: {option.score:.2f} predicted effectiveness")
        return "\n".join(lines)


class ExplanationLog:
    """Bounded ring buffer of the most recent adaptive decisions."""

    def __init__(self, limit: int = 60) -> None:
        self._entries: deque[DecisionExplanation] = deque(maxlen=limit)
        self._sequence = 0

    def add(self, explanation: DecisionExplanation) -> DecisionExplanation:
        self._entries.append(explanation)
        return explanation

    def next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence:05d}"

    def recent(self, limit: int = 20, kinds: Iterable[str] | None = None) -> list[DecisionExplanation]:
        wanted = set(kinds) if kinds else None
        matching = [item for item in self._entries if wanted is None or item.kind in wanted]
        return matching[-limit:]

    def clear(self) -> None:
        self._entries.clear()
        self._sequence = 0
