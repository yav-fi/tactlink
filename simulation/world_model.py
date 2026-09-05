"""Node-local observations, deterministic reconciliation, and coverage metrics.

The classes in this module contain no simulator ``World`` reference.  A belief
only changes when its owning node senses something or receives a WORLD_UPDATE
through the simulated network.
"""

from __future__ import annotations

from math import ceil
from typing import Iterable

from .models import CoverageCell, Observation, ObservationType, RegionCoverage, Vector3


class CoverageGrid:
    """Static circular-region tessellation plus locally observed cell state."""

    def __init__(self, region_id: str, center: Vector3, radius: float, cell_size: float) -> None:
        self.region_id = region_id
        self.center = center.model_copy(deep=True)
        self.radius = radius
        self.cell_size = cell_size
        self.cells: dict[str, CoverageCell] = {}
        extent = ceil(radius / cell_size)
        for iy in range(-extent, extent + 1):
            for ix in range(-extent, extent + 1):
                point = Vector3(
                    x=center.x + ix * cell_size,
                    y=center.y + iy * cell_size,
                    z=center.z,
                )
                if point.distance_to(center) <= radius:
                    cell_id = f"{region_id}:{ix:+d}:{iy:+d}"
                    self.cells[cell_id] = CoverageCell(
                        cell_id=cell_id,
                        region_id=region_id,
                        center=point,
                        size_m=cell_size,
                    )

    def cells_within(self, position: Vector3, radius: float) -> list[CoverageCell]:
        return [
            cell for cell in self.cells.values()
            if cell.center.distance_to(position) <= radius
        ]

    def apply(self, observation: Observation) -> bool:
        cell_id = str(observation.geometry.get("cell_id", ""))
        cell = self.cells.get(cell_id)
        if cell is None:
            return False
        current_key = (cell.last_observed or -1.0, cell.confidence, cell.observed_by or "")
        candidate_key = (observation.timestamp, observation.confidence, observation.source_node_id)
        if candidate_key <= current_key:
            return False
        cell.last_observed = observation.timestamp
        cell.confidence = observation.confidence
        cell.observed_by = observation.source_node_id
        cell.observation_count += 1
        return True

    def metrics(self, now: float, freshness_half_life: float) -> RegionCoverage:
        observed = [cell for cell in self.cells.values() if cell.last_observed is not None]
        fresh = [
            cell for cell in observed
            if self.freshness(now - float(cell.last_observed), freshness_half_life) >= 0.5
        ]
        total = len(self.cells)
        rendered_cells: list[CoverageCell] = []
        for cell in self.cells.values():
            copy = cell.model_copy(deep=True)
            copy.freshness = (
                self.freshness(now - float(cell.last_observed), freshness_half_life)
                if cell.last_observed is not None else 0.0
            )
            rendered_cells.append(copy)
        return RegionCoverage(
            region_id=self.region_id,
            total_cells=total,
            observed_cells=len(observed),
            fresh_cells=len(fresh),
            coverage=len(observed) / total if total else 1.0,
            fresh_coverage=len(fresh) / total if total else 1.0,
            mean_confidence=(sum(cell.confidence for cell in observed) / len(observed) if observed else 0.0),
            cells=rendered_cells,
        )

    @staticmethod
    def freshness(age: float, half_life: float) -> float:
        return 0.5 ** (max(0.0, age) / max(half_life, 1e-6))


class WorldBelief:
    """One node's independently evolving, mergeable view of the world."""

    def __init__(self, grids: Iterable[CoverageGrid] = ()) -> None:
        self.observations: dict[str, Observation] = {}
        self.domain_latest: dict[str, Observation] = {}
        self.grids = {
            grid.region_id: CoverageGrid(grid.region_id, grid.center, grid.radius, grid.cell_size)
            for grid in grids
        }
        self.known_entities: dict[str, Observation] = {}
        self.known_obstacles: dict[str, Observation] = {}
        self.last_reconciled_at: float | None = None

    def incorporate(self, observation: Observation, reconciled_at: float | None = None) -> bool:
        if observation.observation_id in self.observations:
            return False
        current = self.domain_latest.get(observation.domain_key)
        if current is not None:
            # Deterministic last-writer merge. Confidence and source break ties.
            if abs(observation.timestamp - current.timestamp) <= 0.25:
                old_key = (current.confidence, current.timestamp, current.source_node_id, current.observation_id)
                new_key = (observation.confidence, observation.timestamp, observation.source_node_id, observation.observation_id)
            else:
                old_key = (current.timestamp, current.confidence, current.source_node_id, current.observation_id)
                new_key = (observation.timestamp, observation.confidence, observation.source_node_id, observation.observation_id)
            if new_key <= old_key:
                self.observations[observation.observation_id] = observation
                return False
        self.observations[observation.observation_id] = observation
        self.domain_latest[observation.domain_key] = observation
        if observation.observation_type == ObservationType.CELL_OBSERVED:
            region_id = str(observation.geometry.get("region_id", ""))
            grid = self.grids.get(region_id)
            if grid is not None:
                grid.apply(observation)
        elif observation.observation_type == ObservationType.ENTITY_OBSERVED:
            entity_id = str(observation.metadata.get("entity_id", ""))
            if entity_id:
                self.known_entities[entity_id] = observation
        elif observation.observation_type == ObservationType.OBSTACLE_OBSERVED:
            obstacle_id = str(observation.metadata.get("obstacle_id", ""))
            if obstacle_id:
                self.known_obstacles[obstacle_id] = observation
        if reconciled_at is not None:
            self.last_reconciled_at = reconciled_at
        return True

    def merge(self, observations: Iterable[Observation], now: float) -> list[Observation]:
        changed = [item for item in observations if self.incorporate(item, reconciled_at=now)]
        return changed

    def coverage(self, region_id: str, now: float, freshness_half_life: float) -> RegionCoverage | None:
        grid = self.grids.get(region_id)
        return grid.metrics(now, freshness_half_life) if grid else None

    def latest_region_observation(self, region_id: str) -> Observation | None:
        prefix = f"cell:{region_id}:"
        candidates = [item for key, item in self.domain_latest.items() if key.startswith(prefix)]
        return max(candidates, key=lambda item: (item.timestamp, item.confidence), default=None)
