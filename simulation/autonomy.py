"""Narrow seam between a node's local belief and the planning package.

``DroneNode`` gets a planner and a read-only environment abstraction built once
from the *static* ``WorldDefinition``. It never gains access to ``World`` truth:
every planning input still comes from the node's own estimated state, peer
knowledge, and assigned task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from planning import MissionPlanner, PlanningConfig, PlanningContext, TrackedEntity, adapters
from planning.environment import EnvironmentQuery
from planning.models import Vector3 as PlanningVector3

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .drone import DroneNode
    from .world import WorldDefinition


class PlanningAutonomy:
    """Factory + context builder shared by every node in one simulation."""

    def __init__(self, environment: EnvironmentQuery, config: PlanningConfig | None = None) -> None:
        self.environment = environment
        self.config = config or PlanningConfig(altitude_layer=4.0, base_clearance=5.0, grid_cell_size=8.0)

    @classmethod
    def from_world_definition(cls, definition: "WorldDefinition") -> "PlanningAutonomy":
        return cls(adapters.environment_from_world_definition(definition))

    def new_planner(self) -> MissionPlanner:
        """One planner per node keeps behaviour state naturally per-drone."""

        return MissionPlanner(self.environment, self.config)

    def build_context(self, node: "DroneNode", now: float) -> PlanningContext:
        task = node.current_task
        altitude = self._task_altitude(task, node.estimated.position.z)
        config = self.config.model_copy(
            update={"default_altitude": altitude, "cruise_speed": node.maximum_speed}
        )
        entities = [
            TrackedEntity(
                entity_id=entity_id,
                position=PlanningVector3(x=position.x, y=position.y, z=position.z),
                last_seen=now,
            )
            for entity_id, position in sorted(node.known_entities.items())
        ]
        return adapters.context_from_node(
            node,
            now,
            self.environment,
            home_position=node.home,
            tracked_entities=entities,
            config=config,
            cruise_speed=node.maximum_speed,
        )

    @staticmethod
    def _task_altitude(task, fallback: float) -> float:
        """Preserve the mission's intended altitude instead of a planner default."""

        if task is None:
            return fallback
        metadata_altitude = task.metadata.get("desired_altitude")
        if metadata_altitude is not None:
            return float(metadata_altitude)
        if task.target.point is not None:
            return task.target.point.z
        if task.target.waypoints:
            return task.target.waypoints[0].z
        return fallback
