"""Small deterministic 2.5D geometry helpers used by the planning package.

Everything here is pure and dependency free so behaviours stay easy to reason
about and test.
"""

from __future__ import annotations

from math import atan2, cos, hypot, inf, sin

from .models import Vector3

EPSILON = 1e-9


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def distance_xy(a: Vector3, b: Vector3) -> float:
    return hypot(a.x - b.x, a.y - b.y)


def path_length(points: list[Vector3]) -> float:
    return sum(points[index].distance_to(points[index + 1]) for index in range(len(points) - 1))


def unit_xy(dx: float, dy: float) -> tuple[float, float]:
    length = hypot(dx, dy)
    if length <= EPSILON:
        return 1.0, 0.0
    return dx / length, dy / length


def perpendicular_xy(dx: float, dy: float) -> tuple[float, float]:
    ux, uy = unit_xy(dx, dy)
    return -uy, ux


def offset_point(origin: Vector3, angle: float, distance: float, z: float | None = None) -> Vector3:
    return Vector3(
        x=origin.x + cos(angle) * distance,
        y=origin.y + sin(angle) * distance,
        z=origin.z if z is None else z,
    )


def bearing(origin: Vector3, target: Vector3) -> float:
    return atan2(target.y - origin.y, target.x - origin.x)


def segment_point_distance_2d(a: Vector3, b: Vector3, point: Vector3) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    squared = dx * dx + dy * dy
    if squared <= EPSILON:
        return hypot(point.x - a.x, point.y - a.y)
    t = clamp(((point.x - a.x) * dx + (point.y - a.y) * dy) / squared, 0.0, 1.0)
    return hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy))


def segment_aabb_intersects_2d(
    a: Vector3,
    b: Vector3,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> bool:
    """Slab test; treats a degenerate segment as a point containment test."""

    if min_x <= a.x <= max_x and min_y <= a.y <= max_y:
        return True
    if min_x <= b.x <= max_x and min_y <= b.y <= max_y:
        return True
    enter, exit_ = 0.0, 1.0
    for delta, start, low, high in (
        (b.x - a.x, a.x, min_x, max_x),
        (b.y - a.y, a.y, min_y, max_y),
    ):
        if abs(delta) <= EPSILON:
            if start < low or start > high:
                return False
            continue
        t0, t1 = (low - start) / delta, (high - start) / delta
        if t0 > t1:
            t0, t1 = t1, t0
        enter = max(enter, t0)
        exit_ = min(exit_, t1)
        if enter > exit_:
            return False
    return True


def point_in_polygon(point: Vector3, polygon: list[Vector3]) -> bool:
    """Even-odd ray cast in the xy plane."""

    inside = False
    count = len(polygon)
    for index in range(count):
        a, b = polygon[index], polygon[(index + 1) % count]
        if (a.y > point.y) != (b.y > point.y):
            crossing = a.x + (point.y - a.y) / (b.y - a.y) * (b.x - a.x)
            if point.x < crossing:
                inside = not inside
    return inside


def polygon_edge_distance(point: Vector3, polygon: list[Vector3]) -> float:
    count = len(polygon)
    return min(
        segment_point_distance_2d(polygon[index], polygon[(index + 1) % count], point)
        for index in range(count)
    )


def polygon_bounds(polygon: list[Vector3]) -> tuple[float, float, float, float]:
    xs = [point.x for point in polygon]
    ys = [point.y for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def polygon_centroid(polygon: list[Vector3]) -> Vector3:
    area = 0.0
    cx = cy = 0.0
    count = len(polygon)
    for index in range(count):
        a, b = polygon[index], polygon[(index + 1) % count]
        cross = a.x * b.y - b.x * a.y
        area += cross
        cx += (a.x + b.x) * cross
        cy += (a.y + b.y) * cross
    if abs(area) <= EPSILON:
        return Vector3(
            x=sum(point.x for point in polygon) / count,
            y=sum(point.y for point in polygon) / count,
            z=polygon[0].z,
        )
    area *= 0.5
    return Vector3(x=cx / (6.0 * area), y=cy / (6.0 * area), z=polygon[0].z)


def polygon_radius(polygon: list[Vector3]) -> float:
    centroid = polygon_centroid(polygon)
    return max(distance_xy(centroid, point) for point in polygon)


def segment_segment_parameter(a: Vector3, b: Vector3, c: Vector3, d: Vector3) -> float | None:
    """Return t along a->b where it crosses c->d, or None."""

    r_x, r_y = b.x - a.x, b.y - a.y
    s_x, s_y = d.x - c.x, d.y - c.y
    denominator = r_x * s_y - r_y * s_x
    if abs(denominator) <= EPSILON:
        return None
    t = ((c.x - a.x) * s_y - (c.y - a.y) * s_x) / denominator
    u = ((c.x - a.x) * r_y - (c.y - a.y) * r_x) / denominator
    if -EPSILON <= t <= 1 + EPSILON and -EPSILON <= u <= 1 + EPSILON:
        return clamp(t, 0.0, 1.0)
    return None


def segment_polygon_intersects_2d(a: Vector3, b: Vector3, polygon: list[Vector3]) -> bool:
    if point_in_polygon(a, polygon) or point_in_polygon(b, polygon):
        return True
    count = len(polygon)
    for index in range(count):
        c, d = polygon[index], polygon[(index + 1) % count]
        if segment_segment_parameter(a, b, c, d) is not None:
            return True
    return False


def clip_segment_to_polygon(a: Vector3, b: Vector3, polygon: list[Vector3]) -> list[tuple[float, float]]:
    """Return the [t0, t1] intervals of segment a->b that lie inside polygon."""

    cuts = {0.0, 1.0}
    count = len(polygon)
    for index in range(count):
        c, d = polygon[index], polygon[(index + 1) % count]
        parameter = segment_segment_parameter(a, b, c, d)
        if parameter is not None:
            cuts.add(parameter)
    ordered = sorted(cuts)
    intervals: list[tuple[float, float]] = []
    for index in range(len(ordered) - 1):
        t0, t1 = ordered[index], ordered[index + 1]
        if t1 - t0 <= 1e-6:
            continue
        middle = (t0 + t1) * 0.5
        probe = Vector3(x=a.x + (b.x - a.x) * middle, y=a.y + (b.y - a.y) * middle, z=a.z)
        if point_in_polygon(probe, polygon):
            if intervals and abs(intervals[-1][1] - t0) <= 1e-6:
                intervals[-1] = (intervals[-1][0], t1)
            else:
                intervals.append((t0, t1))
    return intervals


def interpolate(a: Vector3, b: Vector3, t: float) -> Vector3:
    return Vector3(x=a.x + (b.x - a.x) * t, y=a.y + (b.y - a.y) * t, z=a.z + (b.z - a.z) * t)
