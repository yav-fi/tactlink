"""Typed planning contracts.

These models are intentionally independent of ``simulation`` so the planner can
be exercised, tested, and reused without importing the simulator. Field names
and value spellings mirror the simulator's canonical contracts, so plain
``model_dump()`` dictionaries round-trip through :mod:`planning.adapters`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import cos, pi, sin, sqrt
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .comms import CommunicationField
    from .environment import EnvironmentQuery


class Vector3(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def distance_to(self, other: "Vector3") -> float:
        return sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2)

    def distance_xy(self, other: "Vector3") -> float:
        return sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def moved(self, velocity: "Vector3", dt: float) -> "Vector3":
        return Vector3(x=self.x + velocity.x * dt, y=self.y + velocity.y * dt, z=self.z + velocity.z * dt)

    def with_z(self, z: float) -> "Vector3":
        return Vector3(x=self.x, y=self.y, z=z)

    def magnitude(self) -> float:
        return sqrt(self.x**2 + self.y**2 + self.z**2)


class TaskType(StrEnum):
    """Mirrors ``simulation.models.TaskType``."""

    GOTO = "GOTO"
    WATCH = "WATCH"
    SEARCH = "SEARCH"
    TRACE = "TRACE"
    FOLLOW = "FOLLOW"
    HOLD = "HOLD"
    RETURN = "RETURN"
    REGROUP = "REGROUP"
    RELAY = "RELAY"


class PlanMode(StrEnum):
    """What the drone is actually doing as a result of this plan."""

    TRANSIT = "TRANSIT"
    TRACE = "TRACE"
    OBSERVE = "OBSERVE"
    COVER = "COVER"
    TRACK = "TRACK"
    FORMATION = "FORMATION"
    HOLD = "HOLD"
    RETURN = "RETURN"
    ABORT = "ABORT"


class BehaviorPhase(StrEnum):
    """Per-task persistent phase; deliberately small."""

    STARTING = "STARTING"
    EN_ROUTE = "EN_ROUTE"
    ARRIVED = "ARRIVED"
    COVERING = "COVERING"
    REPOSITIONING = "REPOSITIONING"
    COMPLETE = "COMPLETE"
    MOVING_TO_POSITION = "MOVING_TO_POSITION"
    OBSERVING = "OBSERVING"
    ACQUIRING = "ACQUIRING"
    TRACKING = "TRACKING"
    LOST = "LOST"
    REACQUIRING = "REACQUIRING"
    HOLDING = "HOLDING"
    RETURNING = "RETURNING"


class PlanWarning(StrEnum):
    LOCALIZATION_UNCERTAINTY_HIGH = "LOCALIZATION_UNCERTAINTY_HIGH"
    LOCALIZATION_UNCERTAINTY_SEVERE = "LOCALIZATION_UNCERTAINTY_SEVERE"
    PATH_BLOCKED = "PATH_BLOCKED"
    PATH_DEGRADED = "PATH_DEGRADED"
    ROUTE_ADJUSTED = "ROUTE_ADJUSTED"
    BATTERY_LOW = "BATTERY_LOW"
    BATTERY_INSUFFICIENT = "BATTERY_INSUFFICIENT"
    BATTERY_LIMITED_RANGE = "BATTERY_LIMITED_RANGE"
    PEER_STATE_STALE = "PEER_STATE_STALE"
    DECONFLICTION_APPLIED = "DECONFLICTION_APPLIED"
    DECONFLICTION_YIELDING = "DECONFLICTION_YIELDING"
    TARGET_UNKNOWN = "TARGET_UNKNOWN"
    TARGET_LOST = "TARGET_LOST"
    REGION_UNKNOWN = "REGION_UNKNOWN"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    NO_TASK = "NO_TASK"
    COVERAGE_COMPLETE = "COVERAGE_COMPLETE"
    NO_HOME_POSITION = "NO_HOME_POSITION"


class CompletionKind(StrEnum):
    WITHIN_RADIUS = "WITHIN_RADIUS"
    WAYPOINTS_CONSUMED = "WAYPOINTS_CONSUMED"
    DWELL = "DWELL"
    CONTINUOUS = "CONTINUOUS"
    NONE = "NONE"


class CompletionCondition(BaseModel):
    kind: CompletionKind = CompletionKind.NONE
    point: Vector3 | None = None
    radius: float = 2.0
    seconds: float = 0.0
    description: str = ""


# ---------------------------------------------------------------------------
# Environment geometry
# ---------------------------------------------------------------------------


class BoxObstacle(BaseModel):
    kind: Literal["box"] = "box"
    id: str
    minimum: Vector3
    maximum: Vector3


class CircleObstacle(BaseModel):
    kind: Literal["circle"] = "circle"
    id: str
    center: Vector3
    radius: float
    height: float = 1_000.0


class PolygonObstacle(BaseModel):
    kind: Literal["polygon"] = "polygon"
    id: str
    points: list[Vector3]
    minimum_z: float = 0.0
    maximum_z: float = 1_000.0


Obstacle = BoxObstacle | CircleObstacle | PolygonObstacle


class WorldBounds(BaseModel):
    minimum: Vector3 = Vector3(x=-500.0, y=-500.0, z=0.0)
    maximum: Vector3 = Vector3(x=500.0, y=500.0, z=150.0)

    def contains_xy(self, point: Vector3, margin: float = 0.0) -> bool:
        return (
            self.minimum.x + margin <= point.x <= self.maximum.x - margin
            and self.minimum.y + margin <= point.y <= self.maximum.y - margin
        )

    def clamped(self, point: Vector3, margin: float = 0.0) -> Vector3:
        low, high = self.minimum, self.maximum
        return Vector3(
            x=min(high.x - margin, max(low.x + margin, point.x)),
            y=min(high.y - margin, max(low.y + margin, point.y)),
            z=min(high.z, max(low.z, point.z)),
        )


class Region(BaseModel):
    """A mission region expressed as an xy polygon (optionally from a circle)."""

    id: str
    polygon: list[Vector3] = Field(default_factory=list)

    @classmethod
    def rectangle(cls, region_id: str, min_x: float, min_y: float, max_x: float, max_y: float) -> "Region":
        return cls(
            id=region_id,
            polygon=[
                Vector3(x=min_x, y=min_y),
                Vector3(x=max_x, y=min_y),
                Vector3(x=max_x, y=max_y),
                Vector3(x=min_x, y=max_y),
            ],
        )

    @classmethod
    def circle(cls, region_id: str, center: Vector3, radius: float, segments: int = 16) -> "Region":
        return cls(
            id=region_id,
            polygon=[
                Vector3(
                    x=center.x + cos(2.0 * pi * index / segments) * radius,
                    y=center.y + sin(2.0 * pi * index / segments) * radius,
                    z=center.z,
                )
                for index in range(segments)
            ],
        )

    def is_valid(self) -> bool:
        return len(self.polygon) >= 3


# ---------------------------------------------------------------------------
# Planner inputs
# ---------------------------------------------------------------------------


class PeerState(BaseModel):
    """What this drone believes about a teammate. May be stale or missing."""

    drone_id: str
    position: Vector3 | None = None
    velocity: Vector3 = Vector3()
    last_seen: float = 0.0
    available: bool = True
    task_id: str | None = None
    priority: int = 50
    altitude: float | None = None


class TrackedEntity(BaseModel):
    """Last known state of a non-drone entity being observed (FOLLOW)."""

    entity_id: str
    position: Vector3
    velocity: Vector3 = Vector3()
    last_seen: float = 0.0


class TaskSpec(BaseModel):
    """Structured mission task handed to the planner.

    Compatible with ``simulation.models.MissionTask``; use
    :func:`planning.adapters.task_spec_from_mission_task` to convert.
    """

    task_id: str
    type: TaskType
    point: Vector3 | None = None
    waypoints: list[Vector3] = Field(default_factory=list)
    region: Region | None = None
    region_id: str | None = None
    entity_id: str | None = None
    assigned_drones: list[str] = Field(default_factory=list)
    priority: int = 50
    desired_altitude: float | None = None
    standoff: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanningConfig(BaseModel):
    """Every threshold the behaviour engine uses, in one place."""

    # motion envelope
    cruise_speed: float = 12.0
    minimum_speed: float = 1.5
    default_altitude: float = 60.0
    altitude_layer: float = 8.0

    # obstacle clearance
    base_clearance: float = 6.0
    uncertainty_clearance_gain: float = 0.6
    maximum_clearance: float = 45.0
    grid_cell_size: float = 10.0
    grid_padding: float = 80.0
    bounds_margin: float = 5.0

    # uncertainty thresholds (metres of 1-sigma position error)
    uncertainty_elevated: float = 8.0
    uncertainty_high: float = 15.0
    uncertainty_severe: float = 40.0
    uncertainty_tight_corridor_ratio: float = 1.2
    uncertainty_speed_floor: float = 0.3

    # battery
    battery_per_meter: float = 0.00008
    battery_reserve: float = 0.10
    battery_low_warning: float = 0.25
    battery_partial_progress_minimum: float = 0.35

    # peers
    peer_stale_seconds: float = 4.0

    # deconfliction
    minimum_horizontal_separation: float = 25.0
    minimum_vertical_separation: float = 6.0
    lateral_avoidance_offset: float = 30.0

    # search
    search_lane_spacing: float = 80.0
    search_lane_margin: float = 10.0

    # watch
    watch_standoff: float = 90.0
    watch_minimum_standoff: float = 25.0
    watch_arrival_radius: float = 8.0

    # follow
    follow_standoff: float = 40.0
    follow_lost_seconds: float = 5.0
    follow_reacquire_seconds: float = 15.0

    # regroup
    regroup_radius: float = 45.0

    # communication-aware routing
    communication_routing: bool = True
    communication_route_margin: float = 0.02
    communication_detour_radius: float = 160.0
    communication_detour_candidates: int = 3
    communication_maximum_detour_ratio: float = 1.45

    # replanning
    replan_position_drift: float = 40.0
    replan_uncertainty_delta: float = 10.0
    replan_battery_delta: float = 0.08
    replan_max_age: float = 20.0


@dataclass
class PlanningContext:
    """Everything a single drone knows when it needs an action.

    ``environment`` is any object implementing
    :class:`planning.environment.EnvironmentQuery`; the planner never reaches
    past that interface.
    """

    drone_id: str
    estimated_position: Vector3
    environment: "EnvironmentQuery | None" = None
    estimated_velocity: Vector3 = field(default_factory=Vector3)
    localization_uncertainty: float = 1.5
    battery: float = 1.0
    current_task: TaskSpec | None = None
    peers: list[PeerState] = field(default_factory=list)
    tracked_entities: list[TrackedEntity] = field(default_factory=list)
    home_position: Vector3 | None = None
    now: float = 0.0
    cruise_speed: float | None = None
    config: PlanningConfig = field(default_factory=PlanningConfig)
    # Learned connectivity terrain; ``None`` disables communication-aware routing.
    communication: "CommunicationField | None" = None
    connectivity_priority: float | None = None

    def peer(self, drone_id: str) -> PeerState | None:
        for state in self.peers:
            if state.drone_id == drone_id:
                return state
        return None

    def entity(self, entity_id: str) -> TrackedEntity | None:
        for tracked in self.tracked_entities:
            if tracked.entity_id == entity_id:
                return tracked
        return None

    @property
    def speed(self) -> float:
        return self.cruise_speed if self.cruise_speed is not None else self.config.cruise_speed

    def world_bounds(self) -> WorldBounds:
        if self.environment is None:
            return WorldBounds()
        return self.environment.world_bounds()


# ---------------------------------------------------------------------------
# Planner outputs
# ---------------------------------------------------------------------------


class PlannerResult(BaseModel):
    """JSON-serializable action recommendation for one drone."""

    drone_id: str
    task_id: str | None = None
    mode: PlanMode = PlanMode.HOLD
    phase: BehaviorPhase = BehaviorPhase.HOLDING
    waypoints: list[Vector3] = Field(default_factory=list)
    desired_velocity: Vector3 = Vector3()
    desired_speed: float = 0.0
    desired_altitude: float = 60.0
    hold: bool = False
    completion_condition: CompletionCondition = CompletionCondition()
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    warnings: list[PlanWarning] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def next_waypoint(self) -> Vector3 | None:
        return self.waypoints[0] if self.waypoints else None

    def path_length(self) -> float:
        return sum(
            self.waypoints[index].distance_to(self.waypoints[index + 1])
            for index in range(len(self.waypoints) - 1)
        )


class ReplanDecision(BaseModel):
    should_replan: bool = False
    reasons: list[str] = Field(default_factory=list)


@dataclass
class BehaviorState:
    """Persistent per (drone, task) memory kept by :class:`MissionPlanner`."""

    drone_id: str
    task_id: str
    phase: BehaviorPhase = BehaviorPhase.STARTING
    completed_lane_ids: list[str] = field(default_factory=list)
    assigned_lane_ids: list[str] = field(default_factory=list)
    observation_point: Vector3 | None = None
    last_target: Vector3 | None = None
    last_entity_seen: float | None = None
    last_plan_time: float = -1.0
    last_team: list[str] = field(default_factory=list)
    dwell_started: float | None = None


class MotionIntentLike(BaseModel):
    """Structural mirror of ``simulation.models.MotionIntent``.

    ``MotionIntent(**motion_intent_from_result(plan).model_dump())`` round-trips.
    """

    target: Vector3 | None = None
    maximum_speed: float = 0.0
    hold: bool = False
