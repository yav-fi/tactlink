"""Deterministic grid A* with line-of-sight smoothing.

Chosen over sampling planners because it is reproducible tick to tick, which
matters for a simulator that must replay identically.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from math import ceil, hypot

from .environment import EnvironmentQuery
from .geometry import clamp
from .models import Vector3

_NEIGHBORS: tuple[tuple[int, int], ...] = (
    (1, 0),
    (0, 1),
    (-1, 0),
    (0, -1),
    (1, 1),
    (1, -1),
    (-1, 1),
    (-1, -1),
)


@dataclass
class PathResult:
    points: list[Vector3] = field(default_factory=list)
    blocked: bool = False
    degraded: bool = False
    expanded: int = 0
    detoured: bool = False


class GridPathPlanner:
    """A* over a locally generated occupancy grid.

    The grid is built over the bounding box of start and goal (padded), so cost
    stays low even in a large world.
    """

    def __init__(
        self,
        environment: EnvironmentQuery,
        cell_size: float = 10.0,
        padding: float = 80.0,
        maximum_expansions: int = 40_000,
    ) -> None:
        self.environment = environment
        self.cell_size = max(1.0, cell_size)
        self.padding = padding
        self.maximum_expansions = maximum_expansions

    def plan(self, start: Vector3, goal: Vector3, clearance: float, altitude: float | None = None) -> PathResult:
        z = altitude if altitude is not None else goal.z
        start = start.with_z(z)
        goal = goal.with_z(z)
        if not self.environment.segment_intersects_obstacle(start, goal, clearance):
            return PathResult(points=[goal])

        ladder = self._clearance_ladder(clearance)
        # First pass uses a padded window around start/goal (cheap). If the
        # detour has to leave that window, widen to the whole world once.
        for padding in (self.padding, None):
            for attempt_clearance in ladder:
                result = self._search(start, goal, attempt_clearance, z, padding)
                if not result.blocked:
                    result.degraded = attempt_clearance < clearance
                    result.detoured = True
                    return result
        # Nothing worked: report blocked but still offer the direct line so the
        # caller can degrade gracefully instead of freezing.
        return PathResult(points=[goal], blocked=True, detoured=False)

    def _clearance_ladder(self, clearance: float) -> list[float]:
        ladder = [clearance]
        if clearance > 2.0:
            ladder.append(clearance * 0.5)
        if clearance > 0.5:
            ladder.append(0.5)
        return ladder

    def _search(
        self,
        start: Vector3,
        goal: Vector3,
        clearance: float,
        z: float,
        padding: float | None,
    ) -> PathResult:
        bounds = self.environment.world_bounds()
        if padding is None:
            min_x, max_x = bounds.minimum.x, bounds.maximum.x
            min_y, max_y = bounds.minimum.y, bounds.maximum.y
        else:
            min_x = clamp(min(start.x, goal.x) - padding, bounds.minimum.x, bounds.maximum.x)
            max_x = clamp(max(start.x, goal.x) + padding, bounds.minimum.x, bounds.maximum.x)
            min_y = clamp(min(start.y, goal.y) - padding, bounds.minimum.y, bounds.maximum.y)
            max_y = clamp(max(start.y, goal.y) + padding, bounds.minimum.y, bounds.maximum.y)
        columns = max(2, int(ceil((max_x - min_x) / self.cell_size)) + 1)
        rows = max(2, int(ceil((max_y - min_y) / self.cell_size)) + 1)

        def to_point(cell: tuple[int, int]) -> Vector3:
            return Vector3(x=min_x + cell[0] * self.cell_size, y=min_y + cell[1] * self.cell_size, z=z)

        def to_cell(point: Vector3) -> tuple[int, int]:
            return (
                int(round(clamp((point.x - min_x) / self.cell_size, 0, columns - 1))),
                int(round(clamp((point.y - min_y) / self.cell_size, 0, rows - 1))),
            )

        free_cache: dict[tuple[int, int], bool] = {}

        def is_free(cell: tuple[int, int]) -> bool:
            cached = free_cache.get(cell)
            if cached is None:
                cached = self.environment.is_free(to_point(cell), clearance)
                free_cache[cell] = cached
            return cached

        start_cell = self._nearest_free(to_cell(start), columns, rows, is_free)
        goal_cell = self._nearest_free(to_cell(goal), columns, rows, is_free)
        if start_cell is None or goal_cell is None:
            return PathResult(blocked=True)
        if start_cell == goal_cell:
            return PathResult(points=[goal])

        def heuristic(cell: tuple[int, int]) -> float:
            return hypot(cell[0] - goal_cell[0], cell[1] - goal_cell[1]) * self.cell_size

        open_heap: list[tuple[float, float, tuple[int, int]]] = [(heuristic(start_cell), 0.0, start_cell)]
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        cost_so_far: dict[tuple[int, int], float] = {start_cell: 0.0}
        expanded = 0
        found = False

        while open_heap:
            _, cost, cell = heapq.heappop(open_heap)
            if cost > cost_so_far.get(cell, float("inf")) + 1e-9:
                continue
            if cell == goal_cell:
                found = True
                break
            expanded += 1
            if expanded > self.maximum_expansions:
                break
            for dx, dy in _NEIGHBORS:
                neighbor = (cell[0] + dx, cell[1] + dy)
                if not (0 <= neighbor[0] < columns and 0 <= neighbor[1] < rows):
                    continue
                if not is_free(neighbor):
                    continue
                if dx and dy:
                    # never cut a corner between two blocked orthogonal cells
                    if not is_free((cell[0] + dx, cell[1])) or not is_free((cell[0], cell[1] + dy)):
                        continue
                step = self.cell_size * (1.41421356 if dx and dy else 1.0)
                candidate = cost + step
                if candidate < cost_so_far.get(neighbor, float("inf")) - 1e-9:
                    cost_so_far[neighbor] = candidate
                    came_from[neighbor] = cell
                    heapq.heappush(open_heap, (candidate + heuristic(neighbor), candidate, neighbor))

        if not found:
            return PathResult(blocked=True, expanded=expanded)

        cells = [goal_cell]
        while cells[-1] != start_cell:
            cells.append(came_from[cells[-1]])
        cells.reverse()
        raw = [to_point(cell) for cell in cells]
        raw[0] = start
        raw[-1] = goal
        return PathResult(points=self.smooth(raw, clearance)[1:], expanded=expanded)

    @staticmethod
    def _nearest_free(
        cell: tuple[int, int],
        columns: int,
        rows: int,
        is_free,
        maximum_ring: int = 8,
    ) -> tuple[int, int] | None:
        if is_free(cell):
            return cell
        for ring in range(1, maximum_ring + 1):
            candidates: list[tuple[int, int]] = []
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if max(abs(dx), abs(dy)) != ring:
                        continue
                    candidate = (cell[0] + dx, cell[1] + dy)
                    if 0 <= candidate[0] < columns and 0 <= candidate[1] < rows and is_free(candidate):
                        candidates.append(candidate)
            if candidates:
                return sorted(candidates, key=lambda item: (abs(item[0] - cell[0]) + abs(item[1] - cell[1]), item))[0]
        return None

    def smooth(self, points: list[Vector3], clearance: float) -> list[Vector3]:
        """Line-of-sight string pulling; keeps the waypoint list short."""

        if len(points) <= 2:
            return points
        smoothed = [points[0]]
        anchor = 0
        while anchor < len(points) - 1:
            best = anchor + 1
            for candidate in range(len(points) - 1, anchor, -1):
                if not self.environment.segment_intersects_obstacle(points[anchor], points[candidate], clearance):
                    best = candidate
                    break
            smoothed.append(points[best])
            anchor = best
        return smoothed


def route_through(
    planner: GridPathPlanner,
    start: Vector3,
    targets: list[Vector3],
    clearance: float,
) -> PathResult:
    """Chain A* through an ordered list of targets, keeping them in order."""

    points: list[Vector3] = []
    cursor = start
    blocked = False
    degraded = False
    detoured = False
    expanded = 0
    for target in targets:
        leg = planner.plan(cursor, target, clearance, altitude=target.z)
        blocked = blocked or leg.blocked
        degraded = degraded or leg.degraded
        detoured = detoured or leg.detoured
        expanded += leg.expanded
        points.extend(leg.points)
        cursor = leg.points[-1] if leg.points else cursor
    return PathResult(points=points, blocked=blocked, degraded=degraded, expanded=expanded, detoured=detoured)
