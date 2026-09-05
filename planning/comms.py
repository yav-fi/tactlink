"""Communication-aware routing: trade a little distance for connectivity.

The planner never sees radio truth.  It sees a *field* - a coarse grid of
"quality we have historically observed around here" that some other layer
learned from real packet outcomes.  This module turns that field into a
deterministic choice between a short route and a better-connected one, and
records the trade so the decision can be shown to an operator verbatim:

    WHY LONGER PATH?  +18m distance, +0.34 expected link quality
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from typing import Any, Iterable, Protocol

from .models import Vector3


class CommunicationField(Protocol):
    """Anything that can estimate connectivity at a point."""

    def predicted_quality(self, point: Vector3) -> float | None: ...

    def confidence(self, point: Vector3) -> float: ...


@dataclass
class GridCommunicationField:
    """Inverse-distance interpolation over learned connectivity cells.

    Constructed from plain dictionaries so ``planning`` keeps its independence
    from ``simulation``.
    """

    cell_size: float = 40.0
    cells: dict[str, tuple[float, float]] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: Iterable[dict[str, Any]], cell_size: float = 40.0) -> "GridCommunicationField":
        cells: dict[str, tuple[float, float]] = {}
        for record in records:
            key = str(record.get("cell", ""))
            if not key or ":" not in key:
                continue
            cells[key] = (float(record.get("quality", 0.0)), float(record.get("weight", 0.0)))
        return cls(cell_size=cell_size, cells=cells)

    def center(self, key: str) -> Vector3:
        ix, iy = (int(part) for part in key.split(":"))
        return Vector3(x=(ix + 0.5) * self.cell_size, y=(iy + 0.5) * self.cell_size, z=0.0)

    def key_for(self, point: Vector3) -> str:
        return f"{int(floor(point.x / self.cell_size))}:{int(floor(point.y / self.cell_size))}"

    @property
    def radius(self) -> float:
        return self.cell_size * 2.5

    def predicted_quality(self, point: Vector3) -> float | None:
        numerator = 0.0
        denominator = 0.0
        for key, (quality, weight) in self.cells.items():
            if weight <= 0.0:
                continue
            center = self.center(key)
            distance = center.distance_xy(point)
            if distance > self.radius:
                continue
            scaled = weight / (1.0 + distance / self.cell_size)
            numerator += quality * scaled
            denominator += scaled
        if denominator <= 1e-9:
            return None
        return max(0.0, min(1.0, numerator / denominator))

    def confidence(self, point: Vector3) -> float:
        total = sum(
            weight
            for key, (_, weight) in self.cells.items()
            if self.center(key).distance_xy(point) <= self.radius
        )
        return max(0.0, min(1.0, total / 6.0))

    def strong_points(self, near: Vector3, radius: float, minimum_quality: float = 0.4, limit: int = 4) -> list[Vector3]:
        """Best-known cell centres within ``radius`` of ``near``, best first."""

        ranked = sorted(
            (
                (quality, weight, key)
                for key, (quality, weight) in self.cells.items()
                if quality >= minimum_quality
                and weight > 0.0
                and self.center(key).distance_xy(near) <= radius
            ),
            key=lambda item: (-item[0], -item[1], item[2]),
        )
        return [self.center(key) for _, _, key in ranked[:limit]]


@dataclass
class RouteOption:
    """One candidate polyline plus the field's verdict on it."""

    route_id: str
    points: list[Vector3]
    length_m: float
    bottleneck_quality: float
    mean_quality: float
    samples: int = 0
    score: float = 0.0
    distance_penalty: float = 0.0
    connectivity_value: float = 0.0

    def to_record(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "length_m": round(self.length_m, 2),
            "bottleneck_quality": round(self.bottleneck_quality, 4),
            "mean_quality": round(self.mean_quality, 4),
            "samples": self.samples,
            "score": round(self.score, 5),
            "distance_penalty": round(self.distance_penalty, 5),
            "connectivity_value": round(self.connectivity_value, 5),
        }


@dataclass
class RouteChoice:
    selected: RouteOption
    baseline: RouteOption
    options: list[RouteOption]
    connectivity_priority: float
    changed: bool
    explanation: str

    def to_record(self) -> dict[str, Any]:
        return {
            "selected": self.selected.route_id,
            "baseline": self.baseline.route_id,
            "changed": self.changed,
            "connectivity_priority": round(self.connectivity_priority, 3),
            "extra_distance_m": round(self.selected.length_m - self.baseline.length_m, 2),
            "link_quality_delta": round(self.selected.bottleneck_quality - self.baseline.bottleneck_quality, 4),
            "explanation": self.explanation,
            "options": [option.to_record() for option in self.options],
        }


def measure_route(
    route_id: str,
    start: Vector3,
    points: list[Vector3],
    field_model: CommunicationField,
) -> RouteOption:
    length = start.distance_to(points[0]) if points else 0.0
    length += sum(points[index].distance_to(points[index + 1]) for index in range(len(points) - 1))
    estimates = [value for value in (field_model.predicted_quality(point) for point in points) if value is not None]
    return RouteOption(
        route_id=route_id,
        points=points,
        length_m=length,
        bottleneck_quality=min(estimates) if estimates else 0.0,
        mean_quality=sum(estimates) / len(estimates) if estimates else 0.0,
        samples=len(estimates),
    )


def choose_route(
    options: list[RouteOption],
    connectivity_priority: float,
    baseline_id: str = "direct",
    minimum_margin: float = 0.02,
) -> RouteChoice | None:
    """Pick between candidate routes; ties and low priority keep the short one.

    ``connectivity_priority`` is the caller's statement of how much this task
    actually needs the network.  At zero the shortest route always wins, which
    is what stops every vehicle from hugging the mesh.
    """

    if not options:
        return None
    baseline = next((option for option in options if option.route_id == baseline_id), options[0])
    shortest = min(option.length_m for option in options)
    for option in options:
        excess = (option.length_m - shortest) / max(shortest, 1.0)
        option.distance_penalty = round(excess, 6)
        option.connectivity_value = round(
            connectivity_priority * (0.65 * option.bottleneck_quality + 0.35 * option.mean_quality), 6
        )
        option.score = round(option.connectivity_value - option.distance_penalty, 6)
    ranked = sorted(options, key=lambda option: (-option.score, option.length_m, option.route_id))
    best = ranked[0]
    changed = best.route_id != baseline.route_id and best.score > baseline.score + minimum_margin
    if not changed:
        best = baseline
    extra = best.length_m - baseline.length_m
    delta = best.bottleneck_quality - baseline.bottleneck_quality
    if changed:
        explanation = (
            f"WHY LONGER PATH? +{extra:.0f}m distance, {delta:+.2f} expected link quality "
            f"(connectivity priority {connectivity_priority:.2f})"
        )
    else:
        explanation = (
            f"shortest route kept: no alternative beat it by {minimum_margin:.2f} "
            f"at connectivity priority {connectivity_priority:.2f}"
        )
    return RouteChoice(
        selected=best,
        baseline=baseline,
        options=ranked,
        connectivity_priority=connectivity_priority,
        changed=changed,
        explanation=explanation,
    )


def connectivity_priority_for(task_type: str, priority: int, metadata: dict[str, Any] | None = None) -> float:
    """How much this objective depends on staying reachable, in [0, 1].

    Explicit mission metadata wins; otherwise RELAY and REGROUP are inherently
    connectivity-critical, search-style work is mildly so, and a low-priority
    isolated task is not.
    """

    metadata = metadata or {}
    explicit = metadata.get("connectivity_priority")
    if isinstance(explicit, (int, float)):
        return max(0.0, min(1.0, float(explicit)))
    if metadata.get("connectivity_critical"):
        return 1.0
    base = {
        "RELAY": 1.0,
        "REGROUP": 0.8,
        "FOLLOW": 0.55,
        "WATCH": 0.5,
        "SEARCH": 0.45,
        "TRACE": 0.35,
        "GOTO": 0.3,
        "HOLD": 0.2,
        "RETURN": 0.1,
    }.get(str(task_type), 0.3)
    # A high-priority objective is worth more effort to keep reportable.
    return max(0.0, min(1.0, base * (0.6 + priority / 125.0)))
