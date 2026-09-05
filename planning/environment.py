"""Environment port plus a self-contained local implementation.

The ``environment/`` team only has to implement :class:`EnvironmentQuery`. The
planner never touches obstacle internals directly.
"""

from __future__ import annotations

from math import inf
from typing import Any, Protocol, runtime_checkable

from .geometry import (
    distance_xy,
    point_in_polygon,
    polygon_edge_distance,
    segment_aabb_intersects_2d,
    segment_point_distance_2d,
    segment_polygon_intersects_2d,
)
from .models import (
    BoxObstacle,
    CircleObstacle,
    Obstacle,
    PolygonObstacle,
    Region,
    Vector3,
    WorldBounds,
)


@runtime_checkable
class EnvironmentQuery(Protocol):
    """Minimal read-only view of the world the planner depends on."""

    def world_bounds(self) -> WorldBounds: ...

    def is_free(self, point: Vector3, clearance: float = 0.0) -> bool: ...

    def segment_intersects_obstacle(self, a: Vector3, b: Vector3, clearance: float = 0.0) -> bool: ...

    def nearest_obstacle_distance(self, point: Vector3) -> float: ...

    def region_geometry(self, region_id: str) -> Region | None: ...


class SimpleEnvironment:
    """Axis-aligned boxes, circles, and polygons with 2.5D height handling.

    An obstacle whose top is below the queried altitude does not block. That is
    enough fidelity for the demo while leaving room for real 3D geometry later.
    """

    def __init__(
        self,
        bounds: WorldBounds | None = None,
        obstacles: list[Obstacle] | None = None,
        regions: list[Region] | None = None,
    ) -> None:
        self.bounds = bounds or WorldBounds()
        self.obstacles: list[Obstacle] = list(obstacles or [])
        self.regions: dict[str, Region] = {region.id: region for region in (regions or [])}

    # -- construction helpers ------------------------------------------------

    def add_box(self, obstacle_id: str, minimum: Vector3, maximum: Vector3) -> "SimpleEnvironment":
        self.obstacles.append(BoxObstacle(id=obstacle_id, minimum=minimum, maximum=maximum))
        return self

    def add_circle(
        self, obstacle_id: str, center: Vector3, radius: float, height: float = 1_000.0
    ) -> "SimpleEnvironment":
        self.obstacles.append(CircleObstacle(id=obstacle_id, center=center, radius=radius, height=height))
        return self

    def add_polygon(
        self,
        obstacle_id: str,
        points: list[Vector3],
        minimum_z: float = 0.0,
        maximum_z: float = 1_000.0,
    ) -> "SimpleEnvironment":
        self.obstacles.append(
            PolygonObstacle(id=obstacle_id, points=points, minimum_z=minimum_z, maximum_z=maximum_z)
        )
        return self

    def add_region(self, region: Region) -> "SimpleEnvironment":
        self.regions[region.id] = region
        return self

    # -- EnvironmentQuery ----------------------------------------------------

    def world_bounds(self) -> WorldBounds:
        return self.bounds

    def region_geometry(self, region_id: str) -> Region | None:
        return self.regions.get(region_id)

    def is_free(self, point: Vector3, clearance: float = 0.0) -> bool:
        if not self.bounds.contains_xy(point):
            return False
        for obstacle in self.obstacles:
            if not self._altitude_overlaps(obstacle, point.z, point.z):
                continue
            if self._point_distance(obstacle, point) <= clearance:
                return False
        return True

    def segment_intersects_obstacle(self, a: Vector3, b: Vector3, clearance: float = 0.0) -> bool:
        low_z, high_z = min(a.z, b.z), max(a.z, b.z)
        for obstacle in self.obstacles:
            if not self._altitude_overlaps(obstacle, low_z, high_z):
                continue
            if self._segment_hits(obstacle, a, b, clearance):
                return True
        return False

    def nearest_obstacle_distance(self, point: Vector3) -> float:
        distances = [
            self._point_distance(obstacle, point)
            for obstacle in self.obstacles
            if self._altitude_overlaps(obstacle, point.z, point.z)
        ]
        edge = min(
            point.x - self.bounds.minimum.x,
            self.bounds.maximum.x - point.x,
            point.y - self.bounds.minimum.y,
            self.bounds.maximum.y - point.y,
        )
        distances.append(edge)
        return min(distances) if distances else inf

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _altitude_overlaps(obstacle: Obstacle, low_z: float, high_z: float) -> bool:
        if isinstance(obstacle, BoxObstacle):
            return not (high_z < obstacle.minimum.z or low_z > obstacle.maximum.z)
        if isinstance(obstacle, CircleObstacle):
            return not (low_z > obstacle.center.z + obstacle.height)
        return not (high_z < obstacle.minimum_z or low_z > obstacle.maximum_z)

    @staticmethod
    def _point_distance(obstacle: Obstacle, point: Vector3) -> float:
        """Signed-ish xy distance: 0.0 when inside the footprint."""

        if isinstance(obstacle, BoxObstacle):
            dx = max(obstacle.minimum.x - point.x, 0.0, point.x - obstacle.maximum.x)
            dy = max(obstacle.minimum.y - point.y, 0.0, point.y - obstacle.maximum.y)
            return (dx * dx + dy * dy) ** 0.5
        if isinstance(obstacle, CircleObstacle):
            return max(0.0, distance_xy(obstacle.center, point) - obstacle.radius)
        if point_in_polygon(point, obstacle.points):
            return 0.0
        return polygon_edge_distance(point, obstacle.points)

    @staticmethod
    def _segment_hits(obstacle: Obstacle, a: Vector3, b: Vector3, clearance: float) -> bool:
        if isinstance(obstacle, BoxObstacle):
            return segment_aabb_intersects_2d(
                a,
                b,
                obstacle.minimum.x - clearance,
                obstacle.minimum.y - clearance,
                obstacle.maximum.x + clearance,
                obstacle.maximum.y + clearance,
            )
        if isinstance(obstacle, CircleObstacle):
            return segment_point_distance_2d(a, b, obstacle.center) <= obstacle.radius + clearance
        if segment_polygon_intersects_2d(a, b, obstacle.points):
            return True
        if clearance <= 0.0:
            return False
        count = len(obstacle.points)
        for index in range(count):
            edge_a, edge_b = obstacle.points[index], obstacle.points[(index + 1) % count]
            if (
                segment_point_distance_2d(a, b, edge_a) <= clearance
                or segment_point_distance_2d(edge_a, edge_b, a) <= clearance
                or segment_point_distance_2d(edge_a, edge_b, b) <= clearance
            ):
                return True
        return False


def environment_from_dict(data: dict[str, Any]) -> SimpleEnvironment:
    """Build an environment from a plain dict, e.g. a simulator world dump.

    Accepts ``{"minimum", "maximum", "boxes", "circles", "polygons", "regions"}``
    which matches ``simulation.world.WorldDefinition.model_dump()`` plus optional
    polygon obstacles.
    """

    bounds = WorldBounds(
        minimum=Vector3.model_validate(data.get("minimum", {"x": -500, "y": -500, "z": 0})),
        maximum=Vector3.model_validate(data.get("maximum", {"x": 500, "y": 500, "z": 150})),
    )
    obstacles: list[Obstacle] = []
    for box in data.get("boxes", []):
        obstacles.append(
            BoxObstacle(
                id=str(box["id"]),
                minimum=Vector3.model_validate(box["minimum"]),
                maximum=Vector3.model_validate(box["maximum"]),
            )
        )
    for circle in data.get("circles", []):
        obstacles.append(
            CircleObstacle(
                id=str(circle["id"]),
                center=Vector3.model_validate(circle["center"]),
                radius=float(circle["radius"]),
                height=float(circle.get("height", 1_000.0)),
            )
        )
    for polygon in data.get("polygons", []):
        obstacles.append(
            PolygonObstacle(
                id=str(polygon["id"]),
                points=[Vector3.model_validate(point) for point in polygon["points"]],
                minimum_z=float(polygon.get("minimum_z", 0.0)),
                maximum_z=float(polygon.get("maximum_z", 1_000.0)),
            )
        )
    regions: list[Region] = []
    for region in data.get("regions", []):
        if "polygon" in region:
            regions.append(
                Region(
                    id=str(region["id"]),
                    polygon=[Vector3.model_validate(point) for point in region["polygon"]],
                )
            )
        else:
            regions.append(
                Region.circle(
                    str(region["id"]),
                    Vector3.model_validate(region["center"]),
                    float(region["radius"]),
                )
            )
    return SimpleEnvironment(bounds=bounds, obstacles=obstacles, regions=regions)
