# planning — autonomous behaviour and motion/mission planning engine

Converts a structured mission task plus one drone's *local* world knowledge into
safe, executable actions. Fully local and deterministic: no network, no model,
no cloud, no runtime dependency beyond `pydantic`.

```
LLM / gesture / UI → MissionTask → simulation allocator → planning → waypoints → DroneNode
```

## 1. Files

| File | Purpose |
| --- | --- |
| `models.py` | All typed contracts: `Vector3`, `TaskSpec`, `PlanningContext`, `PlannerResult`, `PlanningConfig`, obstacles, regions, enums |
| `geometry.py` | Pure 2.5D helpers (segment/AABB, polygon clipping, centroids) |
| `environment.py` | `EnvironmentQuery` protocol + `SimpleEnvironment` (boxes, circles, polygons) + `environment_from_dict` |
| `pathfinding.py` | Deterministic grid A* with line-of-sight smoothing and a full-world fallback |
| `coordination.py` | Active-team roster, contiguous work splitting, ring slots, altitude layering |
| `deconfliction.py` | Separation checks, right-of-way, lateral detours, altitude stratification |
| `energy.py` | Battery feasibility with a reserve that must survive the trip home |
| `behaviors/` | One module per mission primitive: `goto`, `search`, `watch`, `trace`, `follow`, `regroup` (+ `base` helpers) |
| `planner.py` | `MissionPlanner.plan()` / `.should_replan()` — the orchestration pipeline |
| `adapters.py` | Structural bridges to/from the simulator's contracts |
| `demo.py` | `python -m planning.demo` — eight standalone scenarios, no UI |

## 2. Core API

```python
from planning import MissionPlanner, PlanningContext, TaskSpec, TaskType, SimpleEnvironment

planner = MissionPlanner(environment)          # environment: EnvironmentQuery
plan    = planner.plan(context)                # context: PlanningContext -> PlannerResult
decision = planner.should_replan(context, plan)  # -> ReplanDecision(should_replan, reasons)
planner.mark_lane_complete(drone_id, task_id, lane_id)   # SEARCH progress feedback
planner.forget(drone_id, task_id=None)                   # drop behaviour memory
```

**`PlanningContext`** (dataclass): `drone_id`, `estimated_position`, `estimated_velocity`,
`localization_uncertainty`, `battery`, `current_task`, `peers`, `tracked_entities`,
`home_position`, `environment`, `now`, `cruise_speed`, `config`.

**`PlannerResult`** (pydantic, JSON-serializable): `mode`, `phase`, `waypoints`,
`desired_velocity`, `desired_speed`, `desired_altitude`, `hold`,
`completion_condition`, `confidence`, `warnings`, `metadata`.

Pipeline inside `plan()`: **behaviour → obstacle routing → energy → deconfliction → motion output.**
Behaviours only decide *where*; every cross-cutting concern is applied uniformly afterwards.

## 3. Example input / output

```python
task = TaskSpec(task_id="search-alpha", type=TaskType.SEARCH, region_id="alpha",
                assigned_drones=["drone-1", "drone-2", "drone-3"])
context = PlanningContext(drone_id="drone-2", estimated_position=Vector3(x=0, y=-60, z=60),
                          environment=environment, current_task=task, peers=peers,
                          home_position=Vector3(x=-450, y=-450), battery=0.95, now=10.0)
plan = planner.plan(context)
```

```
mode=COVER  phase=STARTING  altitude=60.0  speed=12.0  confidence=0.90
waypoints  = [(-390,128), (390,128), (390,212), (240,132), ...]
metadata   = {"region_id": "alpha", "team": ["drone-1","drone-2","drone-3"], "team_index": 1,
              "lane_count": 6, "assigned_lane_ids": ["alpha:L2.0", "alpha:L3.0"],
              "coverage_fraction": 0.0, "energy": {...}, "deconfliction": {...}}
```

Warnings emitted: `LOCALIZATION_UNCERTAINTY_HIGH/SEVERE`, `PATH_BLOCKED`, `PATH_DEGRADED`,
`ROUTE_ADJUSTED`, `BATTERY_LOW/INSUFFICIENT/LIMITED_RANGE`, `PEER_STATE_STALE`,
`DECONFLICTION_APPLIED/YIELDING`, `TARGET_UNKNOWN/LOST`, `REGION_UNKNOWN`,
`OUT_OF_BOUNDS`, `COVERAGE_COMPLETE`, `NO_TASK`, `NO_HOME_POSITION`.

## 4. How `simulation/` calls the planner

`planning` never imports `simulation`; `adapters.py` reads simulator objects
structurally (pydantic model, dataclass, or plain dict all work).

```python
from planning import MissionPlanner, adapters

environment = adapters.environment_from_world_definition(world.definition)   # once
planner = MissionPlanner(environment)                                        # once

# per node, per replan tick (the simulator decides the cadence)
context = adapters.context_from_node(
    node, now, environment,
    home_position=world.home(node.identity.node_id),
    assigned_drones=task.assigned_nodes,      # from MissionManager
)
if planner.should_replan(context, previous_plan).should_replan:
    previous_plan = planner.plan(context)

intent = adapters.motion_intent_from_result(previous_plan)
world.set_motion_intent(node_id, MotionIntent(**intent.model_dump()))
```

Recommended integration point: replace the body of `DroneNode.choose_action`
with a planner call, keeping `MotionIntent` as the output contract. Feed
`plan.metadata["assigned_lane_ids"]` back through `MessageType.MISSION_STATE` so
peers share coverage progress via `task.metadata["completed_lane_ids"]`.

Task fields the planner reads from `MissionTask.metadata`: `desired_altitude`,
`standoff`, `lane_spacing`, `region` (`{"id", "polygon"}` or `{"id","center","radius"}`),
`completed_lane_ids`, `stagger`.

## 5. How `environment/` implements the adapter

Implement five methods; nothing else is touched:

```python
class EnvironmentQuery(Protocol):
    def world_bounds(self) -> WorldBounds: ...
    def is_free(self, point: Vector3, clearance: float = 0.0) -> bool: ...
    def segment_intersects_obstacle(self, a: Vector3, b: Vector3, clearance: float = 0.0) -> bool: ...
    def nearest_obstacle_distance(self, point: Vector3) -> float: ...
    def region_geometry(self, region_id: str) -> Region | None: ...
```

`clearance` is an inflation radius in metres — the planner raises it as
localization uncertainty grows. `is_free` must also return `False` outside the
world bounds. `SimpleEnvironment` is a working reference implementation; swap it
out by passing any object satisfying the protocol to `MissionPlanner`.

## 6. Currently implemented

- **Primitives:** GOTO, TRACE, WATCH, SEARCH, FOLLOW, HOLD, RETURN, REGROUP.
- **Path planning:** 8-connected grid A* over inflated obstacles, corner-cut
  guarded, line-of-sight string pulling, clearance ladder + full-world fallback,
  2.5D (obstacles below the flight altitude do not block).
- **Coordination:** deterministic active-team roster from local peer belief;
  SEARCH splits lanes into contiguous blocks; WATCH/REGROUP/FOLLOW use ring
  slots; TRACE staggers drones along the route; the team is stratified into
  altitude layers.
- **Resilience:** losing a peer shrinks the roster and the survivors pick up the
  uncovered lanes on the next plan; stale peers raise `PEER_STATE_STALE` and are
  excluded; missing peer state is tolerated.
- **Deconfliction:** structural altitude layering plus reactive yielding
  (priority, then lexicographic id), lateral detour waypoint, route rejoin.
- **Uncertainty:** clearance grows with uncertainty, speed scales down, and the
  drone holds when uncertainty exceeds the local obstacle margin.
- **Energy:** reserve-aware feasibility; truncates a route to its affordable
  prefix, or switches to RETURN when partial progress is not worth it.
- **State/replanning:** per (drone, task) phase memory and `should_replan` over
  task change, team change, blocked path, drift, battery, uncertainty, staleness,
  and tracked-entity movement.

## 7. Known limitations

- Planning is 2.5D: A* searches x/y at a single assigned altitude. Vertical
  manoeuvring exists only as deconfliction layering.
- Deconfliction is reactive and pairwise; it has no time-parameterised trajectory
  model, so it prevents co-location, not every conceivable crossing conflict.
- The energy model is distance-only — no wind, hover, payload, or climb cost.
- SEARCH lane retirement is proximity-based unless the simulator calls
  `mark_lane_complete`; sensor footprint is not modelled.
- Grid A* resolution (default 10 m) limits gaps it can find; very narrow
  corridors need a smaller `grid_cell_size`.
- Peer state is trusted as reported. There is no cross-checking of a peer's
  claimed position.

## 8. Next three highest-value improvements

1. **Time-parameterised deconfliction.** Give each waypoint an ETA and check
   predicted separation over time instead of instantaneous distance — removes
   the remaining crossing-conflict blind spot.
2. **Coverage feedback loop.** Have the simulator report actually-sensed cells
   (with a sensor footprint) so SEARCH retires lanes on evidence and can resume a
   partially flown lane rather than a whole one.
3. **Layered 3D planning.** Plan A* on a small stack of altitude layers with
   transition costs, so the planner can fly *over* a low obstacle instead of
   always routing around it.
