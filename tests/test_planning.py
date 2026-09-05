"""Targeted checks for the planning package's core guarantees."""

from __future__ import annotations

import pytest

from planning import (
    MissionPlanner,
    PeerState,
    PlanMode,
    PlanningConfig,
    PlanningContext,
    PlanWarning,
    Region,
    SimpleEnvironment,
    TaskSpec,
    TaskType,
    Vector3,
    WorldBounds,
    adapters,
)

HOME = Vector3(x=-450.0, y=-450.0, z=0.0)


@pytest.fixture
def environment() -> SimpleEnvironment:
    world = SimpleEnvironment(
        bounds=WorldBounds(minimum=Vector3(x=-500, y=-500, z=0), maximum=Vector3(x=500, y=500, z=180))
    )
    world.add_box("warehouse", Vector3(x=-160, y=-120, z=0), Vector3(x=-40, y=90, z=95))
    world.add_region(Region.rectangle("alpha", -400.0, -80.0, 400.0, 420.0))
    world.add_region(Region.circle("bravo", Vector3(x=250.0, y=-380.0), 120.0))
    return world


def context(
    drone_id: str,
    position: Vector3,
    environment: SimpleEnvironment,
    task: TaskSpec | None,
    peers: list[PeerState] | None = None,
    **overrides,
) -> PlanningContext:
    defaults = {
        "localization_uncertainty": 2.0,
        "battery": 0.95,
        "now": 10.0,
        "home_position": HOME,
        "config": PlanningConfig(),
    }
    defaults.update(overrides)
    return PlanningContext(
        drone_id=drone_id,
        estimated_position=position,
        environment=environment,
        current_task=task,
        peers=list(peers or []),
        **defaults,
    )


def test_goto_path_avoids_obstacles(environment: SimpleEnvironment) -> None:
    planner = MissionPlanner(environment)
    task = TaskSpec(task_id="t", type=TaskType.GOTO, point=Vector3(x=120.0, y=0.0), assigned_drones=["d1"])
    start = Vector3(x=-320.0, y=0.0, z=60.0)
    plan = planner.plan(context("d1", start, environment, task))

    assert plan.mode is PlanMode.TRANSIT
    assert PlanWarning.PATH_BLOCKED not in plan.warnings
    assert len(plan.waypoints) > 1, "a detour around the warehouse is required"
    legs = [start.with_z(plan.desired_altitude), *plan.waypoints]
    for index in range(len(legs) - 1):
        assert not environment.segment_intersects_obstacle(legs[index], legs[index + 1], 0.0)
    assert plan.waypoints[-1].distance_xy(task.point) < 1.0


def test_search_divides_region_between_three_drones(environment: SimpleEnvironment) -> None:
    planner = MissionPlanner(environment)
    team = ["d1", "d2", "d3"]
    task = TaskSpec(
        task_id="search-alpha", type=TaskType.SEARCH, region_id="alpha", assigned_drones=team,
        metadata={"lane_spacing": 90.0},
    )
    positions = {
        "d1": Vector3(x=-380.0, y=-60.0, z=60.0),
        "d2": Vector3(x=0.0, y=-60.0, z=60.0),
        "d3": Vector3(x=360.0, y=-60.0, z=60.0),
    }
    assignments = {}
    for drone_id in team:
        peers = [PeerState(drone_id=other, position=positions[other], last_seen=10.0) for other in team if other != drone_id]
        plan = planner.plan(context(drone_id, positions[drone_id], environment, task, peers))
        assignments[drone_id] = set(plan.metadata["assigned_lane_ids"])
        assert plan.waypoints, "each drone gets a coverage path"

    assert all(assignments.values()), "no drone is left without lanes"
    assert not (assignments["d1"] & assignments["d2"]), "lanes must not be duplicated"
    assert not (assignments["d1"] & assignments["d3"])
    assert not (assignments["d2"] & assignments["d3"])
    total = set().union(*assignments.values())
    assert len(total) == plan.metadata["lane_count"], "the whole region is covered"


def test_search_redistributes_when_a_drone_is_lost(environment: SimpleEnvironment) -> None:
    planner = MissionPlanner(environment)
    team = ["d1", "d2", "d3"]
    position = Vector3(x=-380.0, y=-60.0, z=60.0)
    task = TaskSpec(
        task_id="search-alpha", type=TaskType.SEARCH, region_id="alpha", assigned_drones=team,
        metadata={"lane_spacing": 90.0},
    )
    peers = [PeerState(drone_id=other, position=position, last_seen=10.0) for other in ("d2", "d3")]
    before = planner.plan(context("d1", position, environment, task, peers))

    survivors = task.model_copy(update={"assigned_drones": ["d1", "d2"]})
    stale_peers = [
        PeerState(drone_id="d2", position=position, last_seen=40.0),
        PeerState(drone_id="d3", position=position, last_seen=5.0, available=False),
    ]
    after_context = context("d1", position, environment, survivors, stale_peers, now=40.0)
    decision = planner.should_replan(after_context, before)
    after = planner.plan(after_context)

    assert decision.should_replan and "team composition changed" in decision.reasons
    assert after.metadata["team"] == ["d1", "d2"]
    assert len(after.metadata["assigned_lane_ids"]) > len(before.metadata["assigned_lane_ids"])


def test_watch_spreads_observers_around_the_region(environment: SimpleEnvironment) -> None:
    planner = MissionPlanner(environment)
    team = ["d1", "d2", "d3"]
    task = TaskSpec(task_id="watch-bravo", type=TaskType.WATCH, region_id="bravo", assigned_drones=team)
    positions = {
        "d1": Vector3(x=100.0, y=-450.0, z=60.0),
        "d2": Vector3(x=250.0, y=-480.0, z=60.0),
        "d3": Vector3(x=420.0, y=-360.0, z=60.0),
    }
    center = Vector3(x=250.0, y=-380.0)
    observation_points = []
    for drone_id in team:
        peers = [PeerState(drone_id=other, position=positions[other], last_seen=10.0) for other in team if other != drone_id]
        plan = planner.plan(context(drone_id, positions[drone_id], environment, task, peers))
        assert plan.mode is PlanMode.OBSERVE
        point = plan.waypoints[-1]
        assert point.distance_xy(center) > 50.0, "observers must stand off, not sit on the target"
        observation_points.append(point)

    separations = [
        observation_points[a].distance_xy(observation_points[b]) for a, b in ((0, 1), (0, 2), (1, 2))
    ]
    assert min(separations) > PlanningConfig().minimum_horizontal_separation


def test_insufficient_battery_recommends_return(environment: SimpleEnvironment) -> None:
    planner = MissionPlanner(environment)
    task = TaskSpec(task_id="far", type=TaskType.GOTO, point=Vector3(x=430.0, y=430.0), assigned_drones=["d1"])
    plan = planner.plan(
        context("d1", Vector3(x=-300.0, y=-300.0, z=60.0), environment, task, battery=0.18)
    )

    assert plan.mode is PlanMode.RETURN
    assert PlanWarning.BATTERY_INSUFFICIENT in plan.warnings
    assert plan.metadata["battery_recommendation"] == "RETURN"
    assert plan.waypoints[-1].distance_xy(HOME) < 1.0


def test_converging_drones_deconflict(environment: SimpleEnvironment) -> None:
    planner = MissionPlanner(environment)
    meeting = Vector3(x=0.0, y=-400.0)
    positions = {"d1": Vector3(x=-20.0, y=-410.0, z=60.0), "d2": Vector3(x=-8.0, y=-408.0, z=60.0)}
    task = TaskSpec(task_id="shared", type=TaskType.GOTO, point=meeting, assigned_drones=["d1", "d2"])

    plans = {}
    for drone_id, position in positions.items():
        other = "d2" if drone_id == "d1" else "d1"
        peers = [PeerState(drone_id=other, position=positions[other], last_seen=10.0)]
        plans[drone_id] = planner.plan(context(drone_id, position, environment, task, peers))

    assert all(PlanWarning.DECONFLICTION_APPLIED in plan.warnings for plan in plans.values())
    yielding = [plan for plan in plans.values() if PlanWarning.DECONFLICTION_YIELDING in plan.warnings]
    assert len(yielding) == 1, "exactly one drone yields; the other holds its route"
    assert yielding[0].drone_id == "d2", "lower-ranked id yields on a priority tie"
    assert abs(plans["d1"].desired_altitude - plans["d2"].desired_altitude) >= PlanningConfig().minimum_vertical_separation
    assert yielding[0].waypoints[-1].distance_xy(meeting) < 1.0, "the yielding drone rejoins its route"


def test_uncertainty_widens_clearance_then_holds(environment: SimpleEnvironment) -> None:
    planner = MissionPlanner(environment)
    task = TaskSpec(task_id="tight", type=TaskType.GOTO, point=Vector3(x=60.0, y=60.0), assigned_drones=["d1"])
    position = Vector3(x=-25.0, y=-10.0, z=60.0)

    nominal = planner.plan(context("d1", position, environment, task, localization_uncertainty=2.0))
    planner.forget("d1")
    elevated = planner.plan(context("d1", position, environment, task, localization_uncertainty=15.0))
    planner.forget("d1")
    severe = planner.plan(context("d1", position, environment, task, localization_uncertainty=50.0))

    assert nominal.mode is PlanMode.TRANSIT and not nominal.warnings
    assert elevated.metadata["clearance"] > nominal.metadata["clearance"]
    assert elevated.desired_speed < nominal.desired_speed
    assert PlanWarning.LOCALIZATION_UNCERTAINTY_HIGH in elevated.warnings
    assert severe.mode is PlanMode.HOLD and severe.hold
    assert PlanWarning.LOCALIZATION_UNCERTAINTY_SEVERE in severe.warnings


def test_adapters_bridge_simulation_contracts(environment: SimpleEnvironment) -> None:
    """A simulator-shaped dict goes in, a MotionIntent-shaped object comes out."""

    mission_task = {
        "id": "task-42",
        "type": "GOTO",
        "priority": 60,
        "assigned_nodes": ["node-a", "node-b"],
        "target": {"point": {"x": 200.0, "y": 200.0, "z": 0.0}, "waypoints": []},
        "metadata": {"desired_altitude": 70.0},
    }
    node = {
        "identity": {"node_id": "node-a"},
        "estimated": {
            "position": {"x": -300.0, "y": -300.0, "z": 60.0},
            "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
            "position_uncertainty": 2.0,
            "battery_estimate": 0.9,
        },
        "peers": {
            "node-b": {"node_id": "node-b", "last_seen": 9.0, "last_position": {"x": -280.0, "y": -320.0, "z": 60.0}}
        },
        "current_task": mission_task,
    }

    planning_context = adapters.context_from_node(node, now=10.0, environment=environment, home_position=HOME)
    assert planning_context.drone_id == "node-a"
    assert planning_context.current_task is not None
    assert planning_context.current_task.assigned_drones == ["node-a", "node-b"]

    plan = MissionPlanner(environment).plan(planning_context)
    intent = adapters.motion_intent_from_result(plan)
    assert intent.target is not None
    assert intent.maximum_speed > 0.0
    assert not intent.hold
