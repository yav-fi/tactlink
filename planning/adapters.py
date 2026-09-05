"""Bridges between the simulator's contracts and the planner's contracts.

Everything here is structural: it accepts pydantic models, dataclasses, or plain
dicts. ``planning`` therefore never imports ``simulation``, and the simulator can
adopt the planner without a dependency cycle.
"""

from __future__ import annotations

from typing import Any

from .environment import SimpleEnvironment, environment_from_dict
from .models import (
    MotionIntentLike,
    PeerState,
    PlannerResult,
    PlanningConfig,
    PlanningContext,
    TaskSpec,
    TaskType,
    TrackedEntity,
    Vector3,
)


def _field(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def to_vector(value: Any, default: Vector3 | None = None) -> Vector3 | None:
    if value is None:
        return default
    if isinstance(value, Vector3):
        return value
    if isinstance(value, dict):
        return Vector3(x=float(value.get("x", 0.0)), y=float(value.get("y", 0.0)), z=float(value.get("z", 0.0)))
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return Vector3(x=float(value[0]), y=float(value[1]), z=float(value[2]) if len(value) > 2 else 0.0)
    return Vector3(
        x=float(_field(value, "x", 0.0)),
        y=float(_field(value, "y", 0.0)),
        z=float(_field(value, "z", 0.0)),
    )


def task_spec_from_mission_task(task: Any, assigned_drones: list[str] | None = None) -> TaskSpec:
    """Convert a ``simulation.models.MissionTask`` (or dict) into a ``TaskSpec``.

    Region geometry, altitude, and standoff are read from ``task.metadata`` so
    the simulator's canonical contract does not have to change.
    """

    target = _field(task, "target", {}) or {}
    metadata = dict(_field(task, "metadata", {}) or {})
    region = None
    if "region" in metadata:
        from .models import Region

        raw = metadata["region"]
        if isinstance(raw, dict) and "polygon" in raw:
            region = Region(id=str(raw.get("id", "region")), polygon=[to_vector(p) for p in raw["polygon"]])
        elif isinstance(raw, dict) and "center" in raw:
            region = Region.circle(
                str(raw.get("id", "region")), to_vector(raw["center"]), float(raw.get("radius", 100.0))
            )

    return TaskSpec(
        task_id=str(_field(task, "id", _field(task, "task_id", "task"))),
        type=TaskType(str(_field(task, "type", "HOLD"))),
        point=to_vector(_field(target, "point")),
        waypoints=[to_vector(point) for point in (_field(target, "waypoints", []) or [])],
        region=region,
        region_id=_field(target, "region_id"),
        entity_id=_field(target, "entity_id"),
        assigned_drones=list(assigned_drones or _field(task, "assigned_nodes", []) or []),
        priority=int(_field(task, "priority", 50)),
        desired_altitude=metadata.get("desired_altitude"),
        standoff=metadata.get("standoff"),
        metadata=metadata,
    )


def peer_state_from_knowledge(node_id: str, knowledge: Any, priority: int = 50) -> PeerState:
    """Convert ``simulation.models.PeerKnowledge`` (or dict) into a ``PeerState``."""

    return PeerState(
        drone_id=str(_field(knowledge, "node_id", node_id)),
        position=to_vector(_field(knowledge, "last_position")),
        last_seen=float(_field(knowledge, "last_seen", 0.0)),
        available=bool(_field(knowledge, "available", True)),
        priority=priority,
    )


def context_from_node(
    node: Any,
    now: float,
    environment: Any = None,
    task: Any = None,
    home_position: Any = None,
    tracked_entities: list[TrackedEntity] | None = None,
    assigned_drones: list[str] | None = None,
    config: PlanningConfig | None = None,
    cruise_speed: float | None = None,
) -> PlanningContext:
    """Build a ``PlanningContext`` from a ``simulation.drone.DroneNode``.

    Only local belief is read - estimated state, peer knowledge, current task -
    so the planner inherits the simulator's no-global-truth discipline.
    """

    estimated = _field(node, "estimated")
    identity = _field(node, "identity")
    drone_id = str(_field(identity, "node_id", _field(node, "node_id", "drone")))
    mission_task = task if task is not None else _field(node, "current_task")
    peers_raw = _field(node, "peers", {}) or {}
    peers = [
        peer_state_from_knowledge(node_id, knowledge)
        for node_id, knowledge in sorted(peers_raw.items())
    ]

    return PlanningContext(
        drone_id=drone_id,
        estimated_position=to_vector(_field(estimated, "position"), Vector3()) or Vector3(),
        environment=environment,
        estimated_velocity=to_vector(_field(estimated, "velocity"), Vector3()) or Vector3(),
        localization_uncertainty=float(_field(estimated, "position_uncertainty", 1.5)),
        battery=float(_field(estimated, "battery_estimate", 1.0)),
        current_task=task_spec_from_mission_task(mission_task, assigned_drones) if mission_task else None,
        peers=peers,
        tracked_entities=list(tracked_entities or []),
        home_position=to_vector(home_position),
        now=now,
        cruise_speed=cruise_speed,
        config=config or PlanningConfig(),
    )


def environment_from_world_definition(definition: Any) -> SimpleEnvironment:
    """Build an environment from ``simulation.world.WorldDefinition`` or a dict."""

    if isinstance(definition, dict):
        return environment_from_dict(definition)
    if hasattr(definition, "model_dump"):
        return environment_from_dict(definition.model_dump(mode="python"))
    raise TypeError(f"unsupported world definition: {type(definition)!r}")


def motion_intent_from_result(result: PlannerResult) -> MotionIntentLike:
    """Shape a ``PlannerResult`` into the simulator's ``MotionIntent`` fields."""

    target = result.next_waypoint
    return MotionIntentLike(
        target=target.with_z(result.desired_altitude) if target else None,
        maximum_speed=result.desired_speed,
        hold=result.hold or result.desired_speed <= 0.0,
    )
