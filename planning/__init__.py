"""Autonomous behaviour and motion/mission planning engine.

Converts structured mission tasks plus local world knowledge into safe,
executable drone actions. Fully local, deterministic, no network or model
dependencies.

Typical use from the simulator::

    from planning import MissionPlanner, adapters

    environment = adapters.environment_from_world_definition(world.definition)
    planner = MissionPlanner(environment)
    context = adapters.context_from_node(node, now, environment, home_position=home)
    plan = planner.plan(context)
    intent = adapters.motion_intent_from_result(plan)
"""

from . import adapters
from .comms import (
    CommunicationField,
    GridCommunicationField,
    RouteChoice,
    RouteOption,
    choose_route,
    connectivity_priority_for,
    measure_route,
)
from .coordination import active_team, layered_altitude, ring_slot, split_contiguous
from .deconfliction import deconflict
from .energy import EnergyAssessment, assess_route
from .environment import EnvironmentQuery, SimpleEnvironment, environment_from_dict
from .models import (
    BehaviorPhase,
    BehaviorState,
    BoxObstacle,
    CircleObstacle,
    CompletionCondition,
    CompletionKind,
    MotionIntentLike,
    PeerState,
    PlanMode,
    PlannerResult,
    PlanningConfig,
    PlanningContext,
    PlanWarning,
    PolygonObstacle,
    Region,
    ReplanDecision,
    TaskSpec,
    TaskType,
    TrackedEntity,
    Vector3,
    WorldBounds,
)
from .pathfinding import GridPathPlanner
from .planner import MissionPlanner, default_planner

__all__ = [
    "BehaviorPhase",
    "CommunicationField",
    "GridCommunicationField",
    "RouteChoice",
    "RouteOption",
    "choose_route",
    "connectivity_priority_for",
    "measure_route",
    "BehaviorState",
    "BoxObstacle",
    "CircleObstacle",
    "CompletionCondition",
    "CompletionKind",
    "EnergyAssessment",
    "EnvironmentQuery",
    "GridPathPlanner",
    "MissionPlanner",
    "MotionIntentLike",
    "PeerState",
    "PlanMode",
    "PlanWarning",
    "PlannerResult",
    "PlanningConfig",
    "PlanningContext",
    "PolygonObstacle",
    "Region",
    "ReplanDecision",
    "SimpleEnvironment",
    "TaskSpec",
    "TaskType",
    "TrackedEntity",
    "Vector3",
    "WorldBounds",
    "active_team",
    "adapters",
    "assess_route",
    "deconflict",
    "default_planner",
    "environment_from_dict",
    "layered_altitude",
    "ring_slot",
    "split_contiguous",
]
