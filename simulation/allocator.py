"""Explainable deterministic task allocation."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AllocatorWeights
from .models import DroneStatusReport, MissionTask, Vector3


@dataclass(frozen=True)
class AllocationScore:
    node_id: str
    cost: float
    components: dict[str, float]


class TaskAllocator:
    def __init__(self, weights: AllocatorWeights) -> None:
        self.weights = weights

    def score(self, report: DroneStatusReport, task: MissionTask) -> AllocationScore:
        target = task.target.point or (task.target.waypoints[0] if task.target.waypoints else report.estimated.position)
        distance = report.estimated.position.distance_to(target)
        mismatch = len(task.required_capabilities - report.capabilities)
        components = {
            "distance": distance * self.weights.distance,
            "battery": (1.0 - report.estimated.battery_estimate) * self.weights.battery,
            "workload": report.workload * self.weights.workload,
            "communication": 0.0,
            "uncertainty": report.estimated.position_uncertainty * self.weights.uncertainty,
            "capability_mismatch": mismatch * self.weights.capability_mismatch,
        }
        return AllocationScore(report.node_id, sum(components.values()), components)

    def select(
        self,
        task: MissionTask,
        reports: list[DroneStatusReport],
        excluded: set[str],
        count: int,
    ) -> list[AllocationScore]:
        scores = [self.score(report, task) for report in reports if report.node_id not in excluded]
        eligible = [score for score in scores if score.components["capability_mismatch"] == 0.0]
        return sorted(eligible, key=lambda score: (score.cost, score.node_id))[:count]

