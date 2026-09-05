"""Deterministic trend estimation and threshold-crossing prediction.

No learning, no models, no randomness: an ordinary least-squares fit over a
short time window, plus an R-squared-derived confidence so the runtime can tell
"this is a real trend" from "this is noise".  Every consumer asks the same
question - *when* does this series cross a threshold, and how much should I
believe the answer.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class Prediction(BaseModel):
    """One explainable forecast about a single observable series."""

    kind: str
    subject: str
    metric: str
    current: float
    slope_per_second: float
    threshold: float
    seconds_to_threshold: float | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    samples: int = 0
    trend: list[float] = Field(default_factory=list)
    explanation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class TrendEstimator:
    """Least-squares slope over a bounded, time-windowed sample buffer."""

    window_seconds: float = 8.0
    maximum_samples: int = 64
    points: deque[tuple[float, float]] = field(default_factory=deque)

    def observe(self, now: float, value: float) -> None:
        if self.points and now <= self.points[-1][0]:
            # Same tick (or a reset clock): replace instead of duplicating.
            self.points[-1] = (now, value)
        else:
            self.points.append((now, value))
        while len(self.points) > self.maximum_samples:
            self.points.popleft()
        while len(self.points) > 2 and now - self.points[0][0] > self.window_seconds:
            self.points.popleft()

    def reset(self) -> None:
        self.points.clear()

    @property
    def samples(self) -> int:
        return len(self.points)

    @property
    def current(self) -> float:
        return self.points[-1][1] if self.points else 0.0

    @property
    def span(self) -> float:
        return self.points[-1][0] - self.points[0][0] if len(self.points) > 1 else 0.0

    def fit(self) -> tuple[float, float, float]:
        """Return ``(slope_per_second, intercept, r_squared)``."""

        count = len(self.points)
        if count < 2:
            return (0.0, self.current, 0.0)
        mean_t = sum(point[0] for point in self.points) / count
        mean_v = sum(point[1] for point in self.points) / count
        numerator = sum((t - mean_t) * (v - mean_v) for t, v in self.points)
        denominator = sum((t - mean_t) ** 2 for t, _ in self.points)
        if denominator <= 1e-12:
            return (0.0, mean_v, 0.0)
        slope = numerator / denominator
        intercept = mean_v - slope * mean_t
        total = sum((v - mean_v) ** 2 for _, v in self.points)
        if total <= 1e-12:
            return (slope, intercept, 1.0)
        residual = sum((v - (slope * t + intercept)) ** 2 for t, v in self.points)
        return (slope, intercept, max(0.0, min(1.0, 1.0 - residual / total)))

    def confidence(self, minimum_samples: int = 4) -> float:
        """Blend fit quality with how much evidence produced it."""

        if len(self.points) < minimum_samples:
            return 0.0
        _, _, r_squared = self.fit()
        coverage = min(1.0, len(self.points) / max(minimum_samples * 2, 1))
        return round(max(0.0, min(1.0, 0.7 * r_squared + 0.3 * coverage)), 4)

    def seconds_to(self, threshold: float, falling: bool = True) -> float | None:
        """Seconds until the fitted line crosses ``threshold``.

        ``None`` when the series is not moving toward the threshold at all, or
        is already past it (callers treat that as "already true", not "soon").
        """

        slope, _, _ = self.fit()
        current = self.current
        if falling:
            if current <= threshold or slope >= -1e-9:
                return None
            return (current - threshold) / (-slope)
        if current >= threshold or slope <= 1e-9:
            return None
        return (threshold - current) / slope

    def recent(self, count: int = 3) -> list[float]:
        values = [round(value, 4) for _, value in self.points]
        return values[-count:]


class PredictiveMonitor:
    """Named trend estimators plus rate-limited, explainable alert emission."""

    def __init__(
        self,
        window_seconds: float = 8.0,
        minimum_samples: int = 4,
        minimum_confidence: float = 0.55,
        cooldown_seconds: float = 5.0,
        minimum_span_seconds: float | None = None,
    ) -> None:
        self.window_seconds = window_seconds
        self.minimum_samples = minimum_samples
        self.minimum_confidence = minimum_confidence
        self.cooldown_seconds = cooldown_seconds
        self.minimum_span_seconds = (
            window_seconds * 0.5 if minimum_span_seconds is None else minimum_span_seconds
        )
        self.series: dict[str, TrendEstimator] = {}
        self._last_alert: dict[str, float] = {}

    def observe(self, name: str, now: float, value: float) -> TrendEstimator:
        estimator = self.series.get(name)
        if estimator is None:
            estimator = TrendEstimator(window_seconds=self.window_seconds)
            self.series[name] = estimator
        estimator.observe(now, value)
        return estimator

    def forget(self, name: str) -> None:
        self.series.pop(name, None)
        self._last_alert.pop(name, None)

    def predict(
        self,
        name: str,
        kind: str,
        subject: str,
        metric: str,
        threshold: float,
        horizon: float,
        falling: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Prediction | None:
        """Produce a prediction only when it is confident and inside horizon."""

        estimator = self.series.get(name)
        if estimator is None or estimator.samples < self.minimum_samples:
            return None
        if estimator.span < self.minimum_span_seconds:
            return None  # too short a baseline to distinguish trend from transient
        confidence = estimator.confidence(self.minimum_samples)
        if confidence < self.minimum_confidence:
            return None
        seconds = estimator.seconds_to(threshold, falling=falling)
        if seconds is None or seconds > horizon:
            return None
        slope, _, _ = estimator.fit()
        trend = estimator.recent(3)
        direction = "falling" if falling else "rising"
        return Prediction(
            kind=kind,
            subject=subject,
            metric=metric,
            current=round(estimator.current, 4),
            slope_per_second=round(slope, 6),
            threshold=threshold,
            seconds_to_threshold=round(seconds, 2),
            confidence=confidence,
            samples=estimator.samples,
            trend=trend,
            explanation=(
                f"{metric} {direction} {' → '.join(f'{value:.2f}' for value in trend)}"
                f" ({slope:+.4f}/s); reaches {threshold:.2f} in {seconds:.1f}s"
                f" at {confidence:.0%} confidence"
            ),
            metadata=metadata or {},
        )

    def should_alert(self, name: str, now: float) -> bool:
        last = self._last_alert.get(name)
        if last is not None and now - last < self.cooldown_seconds:
            return False
        self._last_alert[name] = now
        return True
