"""Bounded shadow forecasting used on meaningful decision events only.

The evaluator forks a *reduced-fidelity* copy of the state - point-mass motion,
the same distance-falloff link model the transport uses, and the same coverage
accrual shape the sensing loop uses - then rolls a handful of competing actions
forward for a few seconds and compares the outcomes.

It is deliberately not a recursive re-entry into ``SimulationEngine``: the
forecast is a pure function of its inputs, runs in bounded time, and is called
only when the runtime is about to make a decision that is expensive to undo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Vector3


@dataclass(frozen=True)
class ForecastLinkModel:
    """The transport's own deterministic quality curve, reused for forecasts."""

    reference_range_m: float = 220.0
    hard_range_m: float = 650.0
    falloff_power: float = 2.0
    minimum_usable_quality: float = 0.08

    def quality(self, distance: float) -> float:
        if distance >= self.hard_range_m:
            return 0.0
        return 1.0 / (1.0 + (distance / self.reference_range_m) ** self.falloff_power)


@dataclass
class ForecastNode:
    """Minimal per-vehicle state the forecast needs to move things forward."""

    node_id: str
    position: Vector3
    speed: float = 12.0
    battery: float = 1.0
    relay_capable: bool = False
    role: str = "MISSION"
    target: Vector3 | None = None

    def clone(self) -> "ForecastNode":
        return ForecastNode(
            node_id=self.node_id,
            position=Vector3(x=self.position.x, y=self.position.y, z=self.position.z),
            speed=self.speed,
            battery=self.battery,
            relay_capable=self.relay_capable,
            role=self.role,
            target=None if self.target is None else Vector3(x=self.target.x, y=self.target.y, z=self.target.z),
        )


@dataclass
class ForecastRegion:
    """A search objective: where it is and how much of it is already known."""

    region_id: str
    center: Vector3
    radius: float
    coverage: float = 0.0
    weight: float = 1.0


@dataclass
class ForecastState:
    """The fork point. Everything here is copied before a candidate is rolled."""

    now: float
    nodes: list[ForecastNode]
    regions: list[ForecastRegion] = field(default_factory=list)
    link_model: ForecastLinkModel = field(default_factory=ForecastLinkModel)
    battery_drain_per_meter: float = 0.00008
    sensing_radius_m: float = 32.0

    def clone(self) -> "ForecastState":
        return ForecastState(
            now=self.now,
            nodes=[node.clone() for node in self.nodes],
            regions=[
                ForecastRegion(
                    region_id=region.region_id,
                    center=Vector3(x=region.center.x, y=region.center.y, z=region.center.z),
                    radius=region.radius,
                    coverage=region.coverage,
                    weight=region.weight,
                )
                for region in self.regions
            ],
            link_model=self.link_model,
            battery_drain_per_meter=self.battery_drain_per_meter,
            sensing_radius_m=self.sensing_radius_m,
        )


@dataclass(frozen=True)
class CandidateAction:
    """One competing course of action, expressed as role/target overrides."""

    option_id: str
    summary: str
    assignments: dict[str, tuple[str, Vector3 | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastOutcome:
    option_id: str
    summary: str
    predicted_effectiveness: float
    predicted_network_health: float
    predicted_coverage: float
    predicted_battery_cost: float
    connected_fraction: float
    metrics: dict[str, float]


@dataclass(frozen=True)
class CounterfactualResult:
    outcomes: list[ForecastOutcome]
    selected: ForecastOutcome
    margin: float
    decisive: bool
    horizon_seconds: float
    steps: int

    def as_lines(self) -> list[str]:
        lines = [
            f"Option {item.option_id}: {item.predicted_effectiveness:.2f} predicted effectiveness"
            f" ({item.summary})"
            for item in self.outcomes
        ]
        lines.append(f"Selected {self.selected.option_id} (margin {self.margin:+.2f})")
        return lines


class ShadowEvaluator:
    """Rolls candidates forward at reduced fidelity and ranks them."""

    def __init__(
        self,
        horizon_seconds: float = 12.0,
        step_seconds: float = 2.0,
        minimum_margin: float = 0.02,
        effectiveness_weights: tuple[float, float, float] = (0.45, 0.40, 0.15),
    ) -> None:
        self.horizon_seconds = horizon_seconds
        self.step_seconds = step_seconds
        self.minimum_margin = minimum_margin
        self.effectiveness_weights = effectiveness_weights

    def evaluate(self, state: ForecastState, candidates: list[CandidateAction]) -> CounterfactualResult | None:
        if not candidates:
            return None
        outcomes = [self._forecast(state, candidate) for candidate in candidates]
        ranked = sorted(
            outcomes,
            key=lambda item: (-item.predicted_effectiveness, item.predicted_battery_cost, item.option_id),
        )
        best = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        margin = best.predicted_effectiveness - runner_up.predicted_effectiveness if runner_up else 1.0
        steps = max(1, int(round(self.horizon_seconds / self.step_seconds)))
        return CounterfactualResult(
            outcomes=outcomes,
            selected=best,
            margin=round(margin, 4),
            decisive=margin >= self.minimum_margin,
            horizon_seconds=self.horizon_seconds,
            steps=steps,
        )

    # -- forecasting ---------------------------------------------------------

    def _forecast(self, state: ForecastState, candidate: CandidateAction) -> ForecastOutcome:
        fork = state.clone()
        for node in fork.nodes:
            override = candidate.assignments.get(node.node_id)
            if override is not None:
                role, target = override
                node.role = role
                node.target = None if target is None else Vector3(x=target.x, y=target.y, z=target.z)

        starting_battery = sum(node.battery for node in fork.nodes)
        steps = max(1, int(round(self.horizon_seconds / self.step_seconds)))
        health_samples: list[float] = []
        connected_samples: list[float] = []
        for _ in range(steps):
            self._advance(fork, self.step_seconds)
            health, connected = self._network(fork)
            health_samples.append(health)
            connected_samples.append(connected)

        network_health = sum(health_samples) / len(health_samples)
        connected_fraction = sum(connected_samples) / len(connected_samples)
        coverage = self._coverage_score(fork)
        battery_cost = max(0.0, starting_battery - sum(node.battery for node in fork.nodes))
        coverage_weight, network_weight, battery_weight = self.effectiveness_weights
        effectiveness = max(
            0.0,
            min(
                1.0,
                coverage_weight * coverage
                + network_weight * network_health
                + battery_weight * max(0.0, 1.0 - battery_cost * 4.0),
            ),
        )
        return ForecastOutcome(
            option_id=candidate.option_id,
            summary=candidate.summary,
            predicted_effectiveness=round(effectiveness, 4),
            predicted_network_health=round(network_health, 4),
            predicted_coverage=round(coverage, 4),
            predicted_battery_cost=round(battery_cost, 6),
            connected_fraction=round(connected_fraction, 4),
            metrics={
                "effectiveness": round(effectiveness, 4),
                "network_health": round(network_health, 4),
                "coverage": round(coverage, 4),
                "battery_cost": round(battery_cost, 6),
                "connected_fraction": round(connected_fraction, 4),
            },
        )

    @staticmethod
    def _advance(state: ForecastState, dt: float) -> None:
        for node in state.nodes:
            if node.target is not None:
                distance = node.position.distance_to(node.target)
                if distance > 1e-6:
                    travelled = min(distance, node.speed * dt)
                    ratio = travelled / distance
                    node.position = Vector3(
                        x=node.position.x + (node.target.x - node.position.x) * ratio,
                        y=node.position.y + (node.target.y - node.position.y) * ratio,
                        z=node.position.z + (node.target.z - node.position.z) * ratio,
                    )
                    node.battery = max(0.0, node.battery - travelled * state.battery_drain_per_meter)
        for region in state.regions:
            searching = [
                node
                for node in state.nodes
                if node.role in {"SEARCH", "MISSION"}
                and node.battery > 0.0
                and node.position.distance_to(region.center) <= region.radius + state.sensing_radius_m
            ]
            if not searching:
                continue
            area = max(1.0, 3.14159 * region.radius * region.radius)
            swept = len(searching) * 2.0 * state.sensing_radius_m * min(
                node.speed for node in searching
            ) * dt
            region.coverage = min(1.0, region.coverage + swept / area * (1.0 - region.coverage))

    @staticmethod
    def _network(state: ForecastState) -> tuple[float, float]:
        live = [node for node in state.nodes if node.battery > 0.0]
        if len(live) <= 1:
            return (1.0, 1.0)
        model = state.link_model
        adjacency = {node.node_id: set() for node in live}
        qualities: list[float] = []
        for index, first in enumerate(live):
            for second in live[index + 1 :]:
                quality = model.quality(first.position.distance_to(second.position))
                qualities.append(quality)
                if quality >= model.minimum_usable_quality:
                    adjacency[first.node_id].add(second.node_id)
                    adjacency[second.node_id].add(first.node_id)
        unseen = {node.node_id for node in live}
        largest = 0
        while unseen:
            stack = [min(unseen)]
            unseen.discard(stack[0])
            size = 0
            while stack:
                current = stack.pop()
                size += 1
                for neighbor in sorted(adjacency[current] & unseen):
                    unseen.discard(neighbor)
                    stack.append(neighbor)
            largest = max(largest, size)
        connected = largest / len(live)
        mean_quality = sum(qualities) / len(qualities) if qualities else 1.0
        return (max(0.0, min(1.0, 0.55 * connected + 0.45 * mean_quality)), connected)

    @staticmethod
    def _coverage_score(state: ForecastState) -> float:
        if not state.regions:
            return 1.0
        weights = sum(region.weight for region in state.regions) or 1.0
        return sum(region.coverage * region.weight for region in state.regions) / weights


def forecast_payload(state: ForecastState, candidates: list[CandidateAction]) -> dict[str, Any]:
    """Serialize a forecast request so it can run on an external edge node."""

    return {
        "now": state.now,
        "battery_drain_per_meter": state.battery_drain_per_meter,
        "sensing_radius_m": state.sensing_radius_m,
        "link_model": {
            "reference_range_m": state.link_model.reference_range_m,
            "hard_range_m": state.link_model.hard_range_m,
            "falloff_power": state.link_model.falloff_power,
            "minimum_usable_quality": state.link_model.minimum_usable_quality,
        },
        "nodes": [
            {
                "node_id": node.node_id,
                "position": node.position.model_dump(mode="json"),
                "speed": node.speed,
                "battery": node.battery,
                "relay_capable": node.relay_capable,
                "role": node.role,
                "target": node.target.model_dump(mode="json") if node.target else None,
            }
            for node in state.nodes
        ],
        "regions": [
            {
                "region_id": region.region_id,
                "center": region.center.model_dump(mode="json"),
                "radius": region.radius,
                "coverage": region.coverage,
                "weight": region.weight,
            }
            for region in state.regions
        ],
        "candidates": [
            {
                "option_id": candidate.option_id,
                "summary": candidate.summary,
                "assignments": {
                    node_id: {
                        "role": role,
                        "target": target.model_dump(mode="json") if target else None,
                    }
                    for node_id, (role, target) in sorted(candidate.assignments.items())
                },
            }
            for candidate in candidates
        ],
    }


def forecast_from_payload(payload: dict[str, Any]) -> tuple[ForecastState, list[CandidateAction]]:
    """Inverse of :func:`forecast_payload`; used by the edge worker runtime."""

    link = payload.get("link_model", {})
    state = ForecastState(
        now=float(payload.get("now", 0.0)),
        nodes=[
            ForecastNode(
                node_id=str(item["node_id"]),
                position=Vector3.model_validate(item["position"]),
                speed=float(item.get("speed", 12.0)),
                battery=float(item.get("battery", 1.0)),
                relay_capable=bool(item.get("relay_capable", False)),
                role=str(item.get("role", "MISSION")),
                target=Vector3.model_validate(item["target"]) if item.get("target") else None,
            )
            for item in payload.get("nodes", [])
        ],
        regions=[
            ForecastRegion(
                region_id=str(item["region_id"]),
                center=Vector3.model_validate(item["center"]),
                radius=float(item.get("radius", 50.0)),
                coverage=float(item.get("coverage", 0.0)),
                weight=float(item.get("weight", 1.0)),
            )
            for item in payload.get("regions", [])
        ],
        link_model=ForecastLinkModel(
            reference_range_m=float(link.get("reference_range_m", 220.0)),
            hard_range_m=float(link.get("hard_range_m", 650.0)),
            falloff_power=float(link.get("falloff_power", 2.0)),
            minimum_usable_quality=float(link.get("minimum_usable_quality", 0.08)),
        ),
        battery_drain_per_meter=float(payload.get("battery_drain_per_meter", 0.00008)),
        sensing_radius_m=float(payload.get("sensing_radius_m", 32.0)),
    )
    candidates = [
        CandidateAction(
            option_id=str(item["option_id"]),
            summary=str(item.get("summary", "")),
            assignments={
                node_id: (
                    str(value.get("role", "MISSION")),
                    Vector3.model_validate(value["target"]) if value.get("target") else None,
                )
                for node_id, value in sorted((item.get("assignments") or {}).items())
            },
        )
        for item in payload.get("candidates", [])
    ]
    return state, candidates
