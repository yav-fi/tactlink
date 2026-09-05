"""Standalone demonstration of the planning package.

Run with::

    python -m planning.demo

No UI, no server, no simulator: just the behaviour engine printing what it
decides. Every scenario below is deterministic.
"""

from __future__ import annotations

from .environment import SimpleEnvironment
from .models import (
    PeerState,
    PlannerResult,
    PlanningConfig,
    PlanningContext,
    Region,
    TaskSpec,
    TaskType,
    TrackedEntity,
    Vector3,
    WorldBounds,
)
from .planner import MissionPlanner

HOME = Vector3(x=-450.0, y=-450.0, z=0.0)


def build_world() -> SimpleEnvironment:
    """1000m x 1000m world with rectangular obstacles and one search region."""

    environment = SimpleEnvironment(
        bounds=WorldBounds(minimum=Vector3(x=-500, y=-500, z=0), maximum=Vector3(x=500, y=500, z=180))
    )
    environment.add_box("warehouse", Vector3(x=-160, y=-120, z=0), Vector3(x=-40, y=90, z=95))
    environment.add_box("hangar", Vector3(x=60, y=-260, z=0), Vector3(x=250, y=-140, z=70))
    environment.add_box("tower-block", Vector3(x=120, y=140, z=0), Vector3(x=230, y=300, z=140))
    environment.add_circle("stack", Vector3(x=-260, y=250, z=0), radius=55.0, height=120.0)
    environment.add_region(Region.rectangle("alpha", -400.0, -80.0, 400.0, 420.0))
    environment.add_region(Region.circle("bravo", Vector3(x=250.0, y=-380.0), 120.0))
    return environment


def make_context(
    drone_id: str,
    position: Vector3,
    environment: SimpleEnvironment,
    task: TaskSpec | None,
    peers: list[PeerState],
    now: float = 10.0,
    battery: float = 0.95,
    uncertainty: float = 2.0,
    entities: list[TrackedEntity] | None = None,
) -> PlanningContext:
    return PlanningContext(
        drone_id=drone_id,
        estimated_position=position,
        environment=environment,
        localization_uncertainty=uncertainty,
        battery=battery,
        current_task=task,
        peers=peers,
        tracked_entities=list(entities or []),
        home_position=HOME,
        now=now,
        config=PlanningConfig(),
    )


def peers_for(
    drone_id: str, positions: dict[str, Vector3], now: float, unavailable: set[str] | None = None
) -> list[PeerState]:
    unavailable = unavailable or set()
    return [
        PeerState(
            drone_id=other,
            position=position,
            last_seen=now if other not in unavailable else now - 30.0,
            available=other not in unavailable,
        )
        for other, position in sorted(positions.items())
        if other != drone_id
    ]


def describe(plan: PlannerResult, extra_keys: tuple[str, ...] = ()) -> str:
    head = (
        f"  {plan.drone_id:<9} {plan.mode.value:<9} {plan.phase.value:<18} "
        f"alt={plan.desired_altitude:6.1f}  speed={plan.desired_speed:5.1f}  "
        f"wpts={len(plan.waypoints):<3} conf={plan.confidence:.2f}"
    )
    lines = [head]
    if plan.warnings:
        lines.append(f"            warnings: {', '.join(sorted({w.value for w in plan.warnings}))}")
    for key in extra_keys:
        if key in plan.metadata:
            lines.append(f"            {key}: {plan.metadata[key]}")
    preview = ", ".join(f"({point.x:.0f},{point.y:.0f})" for point in plan.waypoints[:5])
    if preview:
        suffix = " ..." if len(plan.waypoints) > 5 else ""
        lines.append(f"            path: {preview}{suffix}")
    return "\n".join(lines)


def section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------------------


def scenario_search(environment: SimpleEnvironment) -> None:
    section("SCENARIO 1 - three drones SEARCH region alpha")
    planner = MissionPlanner(environment)
    positions = {
        "drone-1": Vector3(x=-380.0, y=-60.0, z=60.0),
        "drone-2": Vector3(x=0.0, y=-60.0, z=60.0),
        "drone-3": Vector3(x=360.0, y=-60.0, z=60.0),
    }
    team = sorted(positions)
    task = TaskSpec(
        task_id="task-search-alpha",
        type=TaskType.SEARCH,
        region_id="alpha",
        assigned_drones=team,
        metadata={"lane_spacing": 90.0},
    )
    plans: dict[str, PlannerResult] = {}
    for drone_id in team:
        context = make_context(
            drone_id, positions[drone_id], environment, task, peers_for(drone_id, positions, 10.0)
        )
        plan = planner.plan(context)
        plans[drone_id] = plan
        print(describe(plan, ("assigned_lane_ids", "lane_count", "coverage_fraction")))

    lanes = [set(plan.metadata["assigned_lane_ids"]) for plan in plans.values()]
    overlap = lanes[0] & lanes[1] | lanes[0] & lanes[2] | lanes[1] & lanes[2]
    print(f"\n  lane overlap between drones: {sorted(overlap) or 'none'}")
    print(f"  distinct altitudes: {sorted({round(plan.desired_altitude, 1) for plan in plans.values()})}")

    section("SCENARIO 2 - drone-3 is lost mid-search, survivors redistribute")
    # drone-1 and drone-2 have already flown their first lanes; drone-3 flew none.
    covered = sorted(set(lanes[0]) | set(lanes[1]))[:2]
    task_after = task.model_copy(
        update={
            "assigned_drones": ["drone-1", "drone-2"],
            "metadata": {"lane_spacing": 90.0, "completed_lane_ids": covered},
        }
    )
    print(f"  lanes already covered: {covered}")
    print(f"  lanes drone-3 had been holding: {sorted(lanes[2])}")
    after: dict[str, PlannerResult] = {}
    for drone_id in ("drone-1", "drone-2"):
        context = make_context(
            drone_id,
            positions[drone_id],
            environment,
            task_after,
            peers_for(drone_id, positions, 45.0, unavailable={"drone-3"}),
            now=45.0,
        )
        decision = planner.should_replan(context, plans[drone_id])
        plan = planner.plan(context)
        after[drone_id] = plan
        print(f"\n  should_replan({drone_id}): {decision.should_replan} -> {decision.reasons}")
        print(describe(plan, ("assigned_lane_ids", "coverage_fraction")))

    recovered = set().union(*(set(plan.metadata["assigned_lane_ids"]) for plan in after.values()))
    orphaned = set(lanes[2]) - set(covered)
    print(f"\n  drone-3's uncovered lanes now picked up: {sorted(orphaned & recovered) == sorted(orphaned)}")


def scenario_watch(environment: SimpleEnvironment) -> None:
    section("SCENARIO 3 - three drones WATCH region bravo (no stacking)")
    planner = MissionPlanner(environment)
    positions = {
        "drone-1": Vector3(x=100.0, y=-450.0, z=60.0),
        "drone-2": Vector3(x=250.0, y=-500.0, z=60.0),
        "drone-3": Vector3(x=420.0, y=-360.0, z=60.0),
    }
    team = sorted(positions)
    task = TaskSpec(task_id="task-watch-bravo", type=TaskType.WATCH, region_id="bravo", assigned_drones=team)
    points: list[Vector3] = []
    for drone_id in team:
        context = make_context(
            drone_id, positions[drone_id], environment, task, peers_for(drone_id, positions, 10.0)
        )
        plan = planner.plan(context)
        points.append(plan.waypoints[-1])
        print(describe(plan, ("observation_point", "standoff", "spread_quality")))
    spread = [
        round(points[a].distance_xy(points[b]), 1) for a, b in ((0, 1), (0, 2), (1, 2))
    ]
    print(f"\n  pairwise separation of observation points: {spread}")


def scenario_goto_obstacle(environment: SimpleEnvironment) -> None:
    section("SCENARIO 4 - GOTO straight through the warehouse")
    planner = MissionPlanner(environment)
    task = TaskSpec(
        task_id="task-goto", type=TaskType.GOTO, point=Vector3(x=120.0, y=0.0), assigned_drones=["drone-1"]
    )
    context = make_context("drone-1", Vector3(x=-320.0, y=0.0, z=60.0), environment, task, [])
    plan = planner.plan(context)
    print(describe(plan, ("path_length",)))
    legs = [context.estimated_position, *plan.waypoints]
    clear = all(
        not environment.segment_intersects_obstacle(legs[i], legs[i + 1], 0.0) for i in range(len(legs) - 1)
    )
    print(f"\n  every leg clear of obstacles: {clear}")


def scenario_deconfliction(environment: SimpleEnvironment) -> None:
    section("SCENARIO 5 - two drones converging on the same waypoint")
    planner = MissionPlanner(environment)
    meeting = Vector3(x=0.0, y=-400.0)
    positions = {"drone-1": Vector3(x=-20.0, y=-410.0, z=60.0), "drone-2": Vector3(x=-8.0, y=-408.0, z=60.0)}
    for drone_id in sorted(positions):
        task = TaskSpec(
            task_id="task-goto-shared",
            type=TaskType.GOTO,
            point=meeting,
            assigned_drones=sorted(positions),
        )
        context = make_context(
            drone_id, positions[drone_id], environment, task, peers_for(drone_id, positions, 10.0)
        )
        plan = planner.plan(context)
        print(describe(plan, ("deconfliction",)))


def scenario_battery(environment: SimpleEnvironment) -> None:
    section("SCENARIO 6 - battery 18%, reserve 10%, round trip unaffordable")
    planner = MissionPlanner(environment)
    task = TaskSpec(
        task_id="task-goto-far", type=TaskType.GOTO, point=Vector3(x=430.0, y=430.0), assigned_drones=["drone-1"]
    )
    context = make_context(
        "drone-1", Vector3(x=-300.0, y=-300.0, z=60.0), environment, task, [], battery=0.18
    )
    plan = planner.plan(context)
    print(describe(plan, ("energy", "battery_recommendation", "home")))


def scenario_uncertainty(environment: SimpleEnvironment) -> None:
    section("SCENARIO 7 - localization uncertainty 2m / 15m / 50m in tight geometry")
    planner = MissionPlanner(environment)
    task = TaskSpec(
        task_id="task-goto-tight", type=TaskType.GOTO, point=Vector3(x=60.0, y=60.0), assigned_drones=["drone-1"]
    )
    for uncertainty in (2.0, 15.0, 50.0):
        context = make_context(
            "drone-1",
            Vector3(x=-25.0, y=-10.0, z=60.0),
            environment,
            task,
            [],
            uncertainty=uncertainty,
        )
        planner.forget("drone-1", task.task_id)
        plan = planner.plan(context)
        print(f"\n  uncertainty = {uncertainty:.0f} m")
        print(describe(plan, ("clearance", "obstacle_margin")))


def scenario_follow_regroup(environment: SimpleEnvironment) -> None:
    section("SCENARIO 8 - FOLLOW standoff and REGROUP formation")
    planner = MissionPlanner(environment)
    positions = {
        "drone-1": Vector3(x=-100.0, y=-350.0, z=60.0),
        "drone-2": Vector3(x=-60.0, y=-380.0, z=60.0),
    }
    entity = TrackedEntity(
        entity_id="convoy-1", position=Vector3(x=0.0, y=-420.0), velocity=Vector3(x=6.0, y=0.0), last_seen=10.0
    )
    follow = TaskSpec(
        task_id="task-follow",
        type=TaskType.FOLLOW,
        entity_id="convoy-1",
        assigned_drones=sorted(positions),
        standoff=50.0,
    )
    for drone_id in sorted(positions):
        context = make_context(
            drone_id,
            positions[drone_id],
            environment,
            follow,
            peers_for(drone_id, positions, 10.0),
            entities=[entity],
        )
        print(describe(planner.plan(context), ("standoff", "observation_age")))

    regroup = TaskSpec(
        task_id="task-regroup",
        type=TaskType.REGROUP,
        point=Vector3(x=-400.0, y=100.0),
        assigned_drones=sorted(positions),
    )
    print()
    for drone_id in sorted(positions):
        context = make_context(
            drone_id, positions[drone_id], environment, regroup, peers_for(drone_id, positions, 10.0)
        )
        print(describe(planner.plan(context), ("formation_slot", "slot_index")))


def main() -> None:
    environment = build_world()
    scenario_search(environment)
    scenario_watch(environment)
    scenario_goto_obstacle(environment)
    scenario_deconfliction(environment)
    scenario_battery(environment)
    scenario_uncertainty(environment)
    scenario_follow_regroup(environment)
    print()


if __name__ == "__main__":
    main()
