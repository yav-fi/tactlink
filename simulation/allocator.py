"""Explainable deterministic task allocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import AllocatorWeights
from .models import DroneStatusReport, MissionTask, Vector3


@dataclass(frozen=True)
class AllocationScore:
    node_id: str
    cost: float
    components: dict[str, float]
    details: dict[str, Any] = field(default_factory=dict)


class TaskAllocator:
    def __init__(self, weights: AllocatorWeights) -> None:
        self.weights = weights

    def score(
        self,
        report: DroneStatusReport,
        task: MissionTask,
        communication: dict[str, float | int | bool] | None = None,
    ) -> AllocationScore:
        target = task.target.point or (task.target.waypoints[0] if task.target.waypoints else report.estimated.position)
        distance = report.estimated.position.distance_to(target)
        mismatch = len(task.required_capabilities - report.capabilities)
        communication = communication or {
            "mean_quality": 1.0,
            "reachable_peers": 1,
            "total_peers": 1,
            "nearest_peer_distance": 0.0,
            "articulation_point": False,
        }
        total_peers = max(1, int(communication.get("total_peers", 1)))
        reachable_fraction = float(communication.get("reachable_peers", 0)) / total_peers
        quality_gap = 1.0 - float(communication.get("mean_quality", 0.0))
        nearest_distance = float(communication.get("nearest_peer_distance", 0.0))
        distance_isolation = min(1.0, nearest_distance / 650.0)
        bridge_penalty = 1.0 if communication.get("articulation_point", False) and task.type.value != "RELAY" else 0.0
        communication_risk = (
            0.50 * quality_gap
            + 0.25 * (1.0 - reachable_fraction)
            + 0.15 * distance_isolation
            + 0.10 * bridge_penalty
        )
        components = {
            "distance": distance * self.weights.distance,
            "battery": (1.0 - report.estimated.battery_estimate) * self.weights.battery,
            "workload": report.workload * self.weights.workload,
            "communication": communication_risk * self.weights.communication,
            "uncertainty": report.estimated.position_uncertainty * self.weights.uncertainty,
            "capability_mismatch": mismatch * self.weights.capability_mismatch,
        }
        return AllocationScore(
            report.node_id,
            sum(components.values()),
            components,
            {
                "communication": {
                    "mean_link_quality": round(1.0 - quality_gap, 4),
                    "reachable_peers": int(communication.get("reachable_peers", 0)),
                    "total_peers": total_peers,
                    "nearest_peer_distance_m": round(nearest_distance, 2),
                    "articulation_point": bool(communication.get("articulation_point", False)),
                    "risk": round(communication_risk, 4),
                }
            },
        )

    def select(
        self,
        task: MissionTask,
        reports: list[DroneStatusReport],
        excluded: set[str],
        count: int,
        communication: dict[str, dict[str, float | int | bool]] | None = None,
    ) -> list[AllocationScore]:
        scores = [
            self.score(report, task, (communication or {}).get(report.node_id))
            for report in reports if report.node_id not in excluded
        ]
        eligible = [score for score in scores if score.components["capability_mismatch"] == 0.0]
        return sorted(eligible, key=lambda score: (score.cost, score.node_id))[:count]
