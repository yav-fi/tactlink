"""Bounded, relocatable compute jobs and the broker that places them.

A ``WorkUnit`` is a pure function of its payload.  That is what makes the edge
tier optional and honest: the same job produces byte-identical results whether
it ran in-process or on a laptop that joined over a WebSocket, so a run stays
deterministic and reproducible no matter who executed the work.

An edge node advertises a real resource profile (CPU score, GPU availability,
memory, tags).  The broker uses that profile for placement and records where
each unit actually ran, so an operator can see compute genuinely moving.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha1
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from .comms_belief import cell_center, quality_from
from .counterfactual import ShadowEvaluator, forecast_from_payload
from .models import Vector3


class WorkUnitKind(StrEnum):
    RELAY_CANDIDATES = "RELAY_CANDIDATES"
    COUNTERFACTUAL_FORECAST = "COUNTERFACTUAL_FORECAST"
    MAP_CHUNK_RECONCILE = "MAP_CHUNK_RECONCILE"
    PATH_ALTERNATIVES = "PATH_ALTERNATIVES"
    MISSION_METRICS = "MISSION_METRICS"


class WorkUnitStatus(StrEnum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EdgeResourceProfile(BaseModel):
    """What a non-mobile compute contributor advertises when it joins."""

    node_id: str
    cpu_score: float = Field(default=1.0, ge=0.0)
    gpu_available: bool = False
    memory_mb: int = Field(default=1024, ge=0)
    tags: set[str] = Field(default_factory=set)
    concurrency: int = Field(default=1, ge=1)

    def can_run(self, unit: "WorkUnit") -> bool:
        if unit.required_tags - self.tags:
            return False
        if unit.requires_gpu and not self.gpu_available:
            return False
        return self.memory_mb >= unit.memory_mb


class WorkUnit(BaseModel):
    """One bounded job. ``payload`` fully determines ``result``."""

    unit_id: str
    kind: WorkUnitKind
    payload: dict[str, Any] = Field(default_factory=dict)
    required_tags: set[str] = Field(default_factory=set)
    requires_gpu: bool = False
    memory_mb: int = Field(default=64, ge=0)
    created_at: float = 0.0
    status: WorkUnitStatus = WorkUnitStatus.PENDING
    assigned_to: str | None = None
    attempts: int = 0
    executed_by: str | None = None
    duration_ms: float = 0.0


class WorkUnitResult(BaseModel):
    unit_id: str
    kind: WorkUnitKind
    result: dict[str, Any] = Field(default_factory=dict)
    executed_by: str = "local"
    duration_ms: float = 0.0
    digest: str = ""


class WorkExecutor(Protocol):
    """Transport-agnostic handle to something that can run a work unit."""

    node_id: str

    def execute(self, unit: WorkUnit) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Compute kernels - real arithmetic, no placeholders
# ---------------------------------------------------------------------------


def _relay_candidates(payload: dict[str, Any]) -> dict[str, Any]:
    """Score relay stations by bottleneck link quality minus travel cost."""

    reference = float(payload.get("reference_range_m", 220.0))
    power = float(payload.get("falloff_power", 2.0))
    hard_range = float(payload.get("hard_range_m", 650.0))
    clusters = [
        [Vector3.model_validate(point) for point in cluster]
        for cluster in payload.get("clusters", [])
    ]
    relays = {
        str(node_id): Vector3.model_validate(point)
        for node_id, point in sorted((payload.get("relay_positions") or {}).items())
    }
    candidates = [Vector3.model_validate(point) for point in payload.get("candidates", [])]

    def quality(distance: float) -> float:
        if distance >= hard_range:
            return 0.0
        return 1.0 / (1.0 + (distance / reference) ** power)

    scored: list[dict[str, Any]] = []
    for index, point in enumerate(candidates):
        cluster_links = []
        for cluster in clusters:
            if not cluster:
                continue
            cluster_links.append(max(quality(point.distance_to(member)) for member in cluster))
        if not cluster_links:
            continue
        bottleneck = min(cluster_links)
        travel = min(
            (position.distance_to(point) for position in relays.values()),
            default=0.0,
        )
        contribution = 2.0 * bottleneck + sum(cluster_links) / len(cluster_links)
        score = contribution - travel / 1000.0
        scored.append(
            {
                "index": index,
                "point": point.model_dump(mode="json"),
                "bottleneck_quality": round(bottleneck, 5),
                "mean_quality": round(sum(cluster_links) / len(cluster_links), 5),
                "travel_m": round(travel, 3),
                "score": round(score, 6),
            }
        )
    scored.sort(key=lambda item: (-item["score"], item["index"]))
    return {"candidates": scored, "best": scored[0] if scored else None}


def _counterfactual_forecast(payload: dict[str, Any]) -> dict[str, Any]:
    state, candidates = forecast_from_payload(payload)
    evaluator = ShadowEvaluator(
        horizon_seconds=float(payload.get("horizon_seconds", 12.0)),
        step_seconds=float(payload.get("step_seconds", 2.0)),
        minimum_margin=float(payload.get("minimum_margin", 0.02)),
    )
    result = evaluator.evaluate(state, candidates)
    if result is None:
        return {"outcomes": [], "selected": None, "margin": 0.0, "decisive": False}
    return {
        "outcomes": [
            {
                "option_id": item.option_id,
                "summary": item.summary,
                "predicted_effectiveness": item.predicted_effectiveness,
                "predicted_network_health": item.predicted_network_health,
                "predicted_coverage": item.predicted_coverage,
                "predicted_battery_cost": item.predicted_battery_cost,
                "connected_fraction": item.connected_fraction,
            }
            for item in result.outcomes
        ],
        "selected": result.selected.option_id,
        "margin": result.margin,
        "decisive": result.decisive,
        "steps": result.steps,
    }


def _map_chunk_reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    """Last-writer-wins merge of observation chunks, matching WorldBelief order."""

    latest: dict[str, dict[str, Any]] = {}
    duplicates = 0
    seen_ids: set[str] = set()
    for chunk in payload.get("chunks", []):
        for record in chunk:
            observation_id = str(record.get("observation_id", ""))
            if observation_id in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(observation_id)
            key = str(record.get("domain_key", ""))
            candidate = (
                float(record.get("timestamp", 0.0)),
                float(record.get("confidence", 0.0)),
                str(record.get("source_node_id", "")),
                observation_id,
            )
            current = latest.get(key)
            if current is None:
                latest[key] = {"key": candidate, "record": record}
                continue
            if candidate > current["key"]:
                latest[key] = {"key": candidate, "record": record}
    merged = [latest[key]["record"] for key in sorted(latest)]
    return {
        "merged": merged,
        "domain_count": len(merged),
        "duplicate_count": duplicates,
        "observation_count": len(seen_ids),
    }


def _path_alternatives(payload: dict[str, Any]) -> dict[str, Any]:
    """Score candidate polylines on length versus learned connectivity."""

    cell_size = float(payload.get("cell_size_m", 40.0))
    connectivity_weight = float(payload.get("connectivity_weight", 1.0))
    distance_weight = float(payload.get("distance_weight", 1.0))
    cells = {
        str(item["cell"]): (float(item.get("quality", 0.0)), float(item.get("weight", 0.0)))
        for item in payload.get("cells", [])
    }
    centers = {key: cell_center(key, cell_size) for key in cells}
    radius = cell_size * 2.5

    def predicted(point: Vector3) -> float | None:
        numerator = 0.0
        denominator = 0.0
        for key, (quality, weight) in cells.items():
            center = centers[key]
            distance = ((center.x - point.x) ** 2 + (center.y - point.y) ** 2) ** 0.5
            if distance > radius or weight <= 0.0:
                continue
            scaled = weight / (1.0 + distance / cell_size)
            numerator += quality * scaled
            denominator += scaled
        return numerator / denominator if denominator > 1e-9 else None

    routes = []
    for index, route in enumerate(payload.get("routes", [])):
        points = [Vector3.model_validate(point) for point in route.get("points", [])]
        if not points:
            continue
        length = sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1))
        start = Vector3.model_validate(route["start"]) if route.get("start") else points[0]
        length += start.distance_to(points[0])
        estimates = [value for value in (predicted(point) for point in points) if value is not None]
        bottleneck = min(estimates) if estimates else 0.0
        mean = sum(estimates) / len(estimates) if estimates else 0.0
        routes.append(
            {
                "index": index,
                "route_id": str(route.get("route_id", f"route-{index}")),
                "length_m": round(length, 3),
                "bottleneck_quality": round(bottleneck, 5),
                "mean_quality": round(mean, 5),
                "samples": len(estimates),
            }
        )
    if not routes:
        return {"routes": [], "best": None}
    shortest = min(item["length_m"] for item in routes)
    for item in routes:
        excess = (item["length_m"] - shortest) / max(shortest, 1.0)
        item["distance_penalty"] = round(distance_weight * excess, 6)
        item["connectivity_value"] = round(
            connectivity_weight * (0.65 * item["bottleneck_quality"] + 0.35 * item["mean_quality"]), 6
        )
        item["score"] = round(item["connectivity_value"] - item["distance_penalty"], 6)
    routes.sort(key=lambda item: (-item["score"], item["index"]))
    return {"routes": routes, "best": routes[0], "shortest_length_m": round(shortest, 3)}


def _mission_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Priority-weighted effectiveness roll-up over supplied task records."""

    tasks = payload.get("tasks", [])
    weighted = 0.0
    weights = 0.0
    per_task = []
    for record in tasks:
        priority = max(1, int(record.get("priority", 50)))
        effectiveness = max(0.0, min(1.0, float(record.get("effectiveness", 0.0))))
        weighted += effectiveness * priority
        weights += priority
        per_task.append(
            {
                "task_id": str(record.get("task_id", "")),
                "priority": priority,
                "effectiveness": round(effectiveness, 5),
                "weight": priority,
            }
        )
    per_task.sort(key=lambda item: (-item["priority"], item["task_id"]))
    overall = weighted / weights if weights else 1.0
    degraded = [item["task_id"] for item in per_task if item["effectiveness"] < 0.45]
    return {
        "overall_effectiveness": round(overall, 6),
        "tasks": per_task,
        "degraded_tasks": degraded,
        "task_count": len(per_task),
    }


def _link_quality_probe(payload: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - helper
    return {
        "quality": round(
            quality_from(float(payload.get("success_rate", 1.0)), float(payload.get("latency", 0.0))), 6
        )
    }


KERNELS: dict[WorkUnitKind, Callable[[dict[str, Any]], dict[str, Any]]] = {
    WorkUnitKind.RELAY_CANDIDATES: _relay_candidates,
    WorkUnitKind.COUNTERFACTUAL_FORECAST: _counterfactual_forecast,
    WorkUnitKind.MAP_CHUNK_RECONCILE: _map_chunk_reconcile,
    WorkUnitKind.PATH_ALTERNATIVES: _path_alternatives,
    WorkUnitKind.MISSION_METRICS: _mission_metrics,
}


def execute_work_unit(unit: WorkUnit) -> dict[str, Any]:
    """Run a unit's kernel. Identical everywhere; that is the whole contract."""

    kernel = KERNELS.get(unit.kind)
    if kernel is None:
        raise KeyError(f"unknown work unit kind: {unit.kind}")
    return kernel(unit.payload)


def result_digest(result: dict[str, Any]) -> str:
    import json

    return sha1(
        json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------


@dataclass
class LocalExecutor:
    """In-process fallback so the mission never depends on extra laptops."""

    node_id: str = "local"

    def execute(self, unit: WorkUnit) -> dict[str, Any]:
        return execute_work_unit(unit)


@dataclass
class EdgeComputeBroker:
    """Places work units, records provenance, and retries orphaned work."""

    maximum_attempts: int = 3
    local: LocalExecutor = field(default_factory=LocalExecutor)
    profiles: dict[str, EdgeResourceProfile] = field(default_factory=dict)
    executors: dict[str, WorkExecutor] = field(default_factory=dict)
    completed: list[WorkUnitResult] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    reassigned: int = 0
    executed_remotely: int = 0
    executed_locally: int = 0
    failures: int = 0
    _sequence: int = 0
    _in_flight: dict[str, WorkUnit] = field(default_factory=dict)

    # -- membership ----------------------------------------------------------

    def join(self, profile: EdgeResourceProfile, executor: WorkExecutor) -> None:
        self.profiles[profile.node_id] = profile
        self.executors[profile.node_id] = executor

    def leave(self, node_id: str) -> list[str]:
        """Drop an edge node; any unit it held becomes retryable."""

        self.profiles.pop(node_id, None)
        self.executors.pop(node_id, None)
        orphans = [
            unit_id for unit_id, unit in sorted(self._in_flight.items()) if unit.assigned_to == node_id
        ]
        for unit_id in orphans:
            unit = self._in_flight.pop(unit_id)
            unit.status = WorkUnitStatus.PENDING
            unit.assigned_to = None
            self.orphaned.append(unit_id)
        return orphans

    @property
    def edge_nodes(self) -> list[str]:
        return sorted(self.profiles)

    def next_unit_id(self, kind: WorkUnitKind) -> str:
        self._sequence += 1
        return f"work-{kind.value.lower().replace('_', '-')}-{self._sequence:05d}"

    # -- placement -----------------------------------------------------------

    def select_executor(self, unit: WorkUnit) -> WorkExecutor:
        eligible = [
            self.profiles[node_id]
            for node_id in sorted(self.profiles)
            if self.profiles[node_id].can_run(unit) and node_id in self.executors
        ]
        if not eligible:
            return self.local
        best = max(eligible, key=lambda profile: (profile.cpu_score, profile.gpu_available, profile.node_id))
        return self.executors[best.node_id]

    def submit(self, unit: WorkUnit) -> WorkUnitResult:
        """Run ``unit`` on the best available executor, falling back locally.

        A remote failure is never fatal: the unit is retried, and the last
        attempt always runs in-process, so an operator can yank the laptop out
        mid-mission without losing the decision that depended on the result.
        """

        last_error: Exception | None = None
        for _ in range(max(1, self.maximum_attempts)):
            executor = self.select_executor(unit)
            unit.attempts += 1
            unit.assigned_to = executor.node_id
            unit.status = WorkUnitStatus.ASSIGNED
            self._in_flight[unit.unit_id] = unit
            started = time.perf_counter()
            try:
                payload = executor.execute(unit)
            except Exception as exc:  # a dead laptop must not stop the mission
                last_error = exc
                self.failures += 1
                if executor.node_id == self.local.node_id:
                    break
                self.leave(executor.node_id)
                self.reassigned += 1
                continue
            finally:
                self._in_flight.pop(unit.unit_id, None)
            duration = (time.perf_counter() - started) * 1000.0
            unit.status = WorkUnitStatus.COMPLETED
            unit.executed_by = executor.node_id
            unit.duration_ms = duration
            if executor.node_id == self.local.node_id:
                self.executed_locally += 1
            else:
                self.executed_remotely += 1
            result = WorkUnitResult(
                unit_id=unit.unit_id,
                kind=unit.kind,
                result=payload,
                executed_by=executor.node_id,
                duration_ms=round(duration, 4),
                digest=result_digest(payload),
            )
            self.completed.append(result)
            if len(self.completed) > 200:
                del self.completed[:-200]
            return result
        # ``maximum_attempts`` limits remote placement attempts, not mission
        # availability. Even a one-attempt policy still gets one local retry.
        if unit.assigned_to != self.local.node_id:
            unit.attempts += 1
            unit.assigned_to = self.local.node_id
            unit.status = WorkUnitStatus.ASSIGNED
            started = time.perf_counter()
            try:
                payload = self.local.execute(unit)
            except Exception as exc:
                last_error = exc
                self.failures += 1
            else:
                duration = (time.perf_counter() - started) * 1000.0
                unit.status = WorkUnitStatus.COMPLETED
                unit.executed_by = self.local.node_id
                unit.duration_ms = duration
                self.executed_locally += 1
                result = WorkUnitResult(
                    unit_id=unit.unit_id,
                    kind=unit.kind,
                    result=payload,
                    executed_by=self.local.node_id,
                    duration_ms=round(duration, 4),
                    digest=result_digest(payload),
                )
                self.completed.append(result)
                if len(self.completed) > 200:
                    del self.completed[:-200]
                return result
        unit.status = WorkUnitStatus.FAILED
        raise RuntimeError(f"work unit {unit.unit_id} failed after {unit.attempts} attempts: {last_error}")

    def run(
        self,
        kind: WorkUnitKind,
        payload: dict[str, Any],
        now: float = 0.0,
        required_tags: set[str] | None = None,
        requires_gpu: bool = False,
        memory_mb: int = 64,
    ) -> WorkUnitResult:
        unit = WorkUnit(
            unit_id=self.next_unit_id(kind),
            kind=kind,
            payload=payload,
            required_tags=set(required_tags or set()),
            requires_gpu=requires_gpu,
            memory_mb=memory_mb,
            created_at=now,
        )
        return self.submit(unit)

    def metrics(self) -> dict[str, Any]:
        return {
            "edge_nodes": self.edge_nodes,
            "units_completed": len(self.completed),
            "executed_remotely": self.executed_remotely,
            "executed_locally": self.executed_locally,
            "reassigned": self.reassigned,
            "orphaned": list(self.orphaned),
            "failures": self.failures,
        }
