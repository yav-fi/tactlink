"""MissionPlanner - turns a PlanningContext into a PlannerResult.

Pipeline, in order:

    behaviour -> obstacle routing -> energy -> deconfliction -> motion output

Behaviours stay simple because every cross-cutting concern (clearance from
localization uncertainty, battery feasibility, separation from peers) is applied
here, uniformly, to whatever targets the behaviour produced.
"""

from __future__ import annotations

from .behaviors.follow import plan_follow
from .behaviors.goto import plan_goto, plan_hold, plan_return
from .behaviors.regroup import plan_regroup
from .behaviors.search import plan_search
from .behaviors.trace import plan_trace
from .behaviors.watch import plan_watch
from .behaviors.base import BehaviorOutcome
from .comms import choose_route, connectivity_priority_for, measure_route
from .coordination import active_team, layered_altitude, shard_index
from .deconfliction import deconflict, relevant_peers
from .energy import assess_route
from .environment import EnvironmentQuery, SimpleEnvironment
from .geometry import clamp, distance_xy
from .models import (
    BehaviorPhase,
    BehaviorState,
    CompletionCondition,
    CompletionKind,
    PlanMode,
    PlannerResult,
    PlanningConfig,
    PlanningContext,
    PlanWarning,
    ReplanDecision,
    TaskType,
    Vector3,
)
from .pathfinding import GridPathPlanner, route_through

_BEHAVIORS = {
    TaskType.GOTO: plan_goto,
    TaskType.SEARCH: plan_search,
    TaskType.WATCH: plan_watch,
    TaskType.TRACE: plan_trace,
    TaskType.FOLLOW: plan_follow,
    TaskType.REGROUP: plan_regroup,
    TaskType.HOLD: plan_hold,
    TaskType.RETURN: plan_return,
    TaskType.RELAY: plan_goto,
}


class MissionPlanner:
    """Stateless per call except for small per-(drone, task) behaviour memory."""

    def __init__(
        self,
        environment: EnvironmentQuery | None = None,
        config: PlanningConfig | None = None,
    ) -> None:
        self.environment = environment
        self.config = config or PlanningConfig()
        self._states: dict[tuple[str, str], BehaviorState] = {}

    # -- behaviour state -----------------------------------------------------

    def state_for(self, drone_id: str, task_id: str) -> BehaviorState:
        key = (drone_id, task_id)
        if key not in self._states:
            self._states[key] = BehaviorState(drone_id=drone_id, task_id=task_id)
        return self._states[key]

    def forget(self, drone_id: str, task_id: str | None = None) -> None:
        for key in [
            key for key in self._states if key[0] == drone_id and (task_id is None or key[1] == task_id)
        ]:
            del self._states[key]

    def mark_lane_complete(self, drone_id: str, task_id: str, lane_id: str) -> None:
        """Called by the simulator when a search lane has actually been flown."""

        state = self.state_for(drone_id, task_id)
        if lane_id not in state.completed_lane_ids:
            state.completed_lane_ids.append(lane_id)

    # -- planning ------------------------------------------------------------

    def plan(self, context: PlanningContext) -> PlannerResult:
        environment = context.environment or self.environment
        config = context.config or self.config
        task = context.current_task

        if task is None:
            return self._idle_result(context, config)

        state = self.state_for(context.drone_id, task.task_id)
        clearance, speed_scale, uncertainty_warnings = self._uncertainty_profile(context, config)

        hold_result = self._uncertainty_gate(context, config, environment, clearance, uncertainty_warnings)
        if hold_result is not None:
            return hold_result

        behavior = _BEHAVIORS[task.type](context, state, environment, clearance)
        warnings = [*uncertainty_warnings, *behavior.warnings]

        altitude = behavior.altitude if behavior.altitude is not None else config.default_altitude
        team, _ = active_team(context)
        altitude = layered_altitude(altitude, shard_index(team, context.drone_id), len(team), config.altitude_layer)
        bounds = context.world_bounds()
        altitude = clamp(altitude, bounds.minimum.z + 5.0, bounds.maximum.z)

        targets = [bounds.clamped(point.with_z(altitude), config.bounds_margin) for point in behavior.targets]
        if any(distance_xy(point, original) > 1e-6 for point, original in zip(targets, behavior.targets)):
            if PlanWarning.OUT_OF_BOUNDS not in warnings:
                warnings.append(PlanWarning.OUT_OF_BOUNDS)

        waypoints, routing_warnings = self._route(context, environment, targets, clearance, altitude, config)
        warnings.extend(routing_warnings)
        route_choice = self._communication_route(
            context, environment, targets, waypoints, clearance, altitude, config
        )
        if route_choice is not None and route_choice.changed:
            waypoints = route_choice.selected.points

        mode = behavior.mode
        phase = behavior.phase
        completion = behavior.completion
        confidence = behavior.confidence

        # -- energy ---------------------------------------------------------
        energy = assess_route(
            context.estimated_position,
            waypoints,
            context.home_position,
            context.battery,
            config,
        )
        if context.battery <= config.battery_low_warning:
            warnings.append(PlanWarning.BATTERY_LOW)
        if mode not in {PlanMode.RETURN, PlanMode.HOLD}:
            progress_fraction = self._progress_fraction(context, waypoints, energy.affordable_waypoints)
            abandon = energy.recommend_return or (
                energy.truncated_at is not None
                and progress_fraction < config.battery_partial_progress_minimum
            )
            if abandon:
                warnings.append(PlanWarning.BATTERY_INSUFFICIENT)
                fallback = plan_return(context, state, environment, clearance)
                mode, phase, completion = PlanMode.RETURN, BehaviorPhase.RETURNING, fallback.completion
                confidence = min(confidence, 0.6)
                targets = [bounds.clamped(point.with_z(altitude), config.bounds_margin) for point in fallback.targets]
                waypoints, extra = self._route(context, environment, targets, clearance, altitude, config)
                warnings.extend(extra)
                behavior = BehaviorOutcome(
                    mode=mode,
                    phase=phase,
                    targets=targets,
                    completion=completion,
                    metadata={**behavior.metadata, **fallback.metadata},
                )
                behavior.metadata["battery_recommendation"] = "RETURN"
            elif energy.truncated_at is not None:
                warnings.append(PlanWarning.BATTERY_LIMITED_RANGE)
                waypoints = energy.affordable_waypoints
                confidence = min(confidence, 0.7)
                behavior.metadata["battery_recommendation"] = "PARTIAL"
                behavior.metadata["battery_progress_fraction"] = round(progress_fraction, 3)

        # -- deconfliction ---------------------------------------------------
        outcome = deconflict(context, waypoints, altitude, clearance, environment)
        waypoints = [point.with_z(outcome.altitude) for point in outcome.waypoints]
        altitude = outcome.altitude
        for warning in outcome.warnings:
            if warning not in warnings:
                warnings.append(warning)
        speed_scale *= outcome.speed_scale * behavior.speed_scale

        # -- motion output ---------------------------------------------------
        speed = 0.0 if behavior.hold else max(config.minimum_speed, context.speed * speed_scale)
        desired_velocity = self._velocity(context, waypoints, speed)
        confidence = self._confidence(confidence, warnings)

        metadata = dict(behavior.metadata)
        metadata.update(
            {
                "plan_time": context.now,
                "battery": context.battery,
                "uncertainty": context.localization_uncertainty,
                "clearance": round(clearance, 3),
                "speed_scale": round(speed_scale, 3),
                "team": metadata.get("team", team),
                "path_length": round(
                    sum(
                        (waypoints[index]).distance_to(waypoints[index + 1])
                        for index in range(len(waypoints) - 1)
                    )
                    + (context.estimated_position.distance_to(waypoints[0]) if waypoints else 0.0),
                    2,
                ),
                "energy": {
                    "required": round(energy.required, 4),
                    "return_cost": round(energy.return_cost, 4),
                    "reserve": energy.reserve,
                    "feasible": energy.feasible,
                },
                "deconfliction": {
                    "conflicts": outcome.conflicts,
                    "yielding": outcome.yielding,
                },
            }
        )
        if route_choice is not None:
            metadata["route_choice"] = route_choice.to_record()

        state.last_target = waypoints[0] if waypoints else None
        state.last_plan_time = context.now
        state.phase = phase

        return PlannerResult(
            drone_id=context.drone_id,
            task_id=task.task_id,
            mode=mode,
            phase=phase,
            waypoints=waypoints,
            desired_velocity=desired_velocity,
            desired_speed=speed,
            desired_altitude=altitude,
            hold=behavior.hold and not outcome.yielding,
            completion_condition=completion,
            confidence=confidence,
            warnings=warnings,
            metadata=metadata,
        )

    # -- replanning ----------------------------------------------------------

    def should_replan(self, context: PlanningContext, previous: PlannerResult | None) -> ReplanDecision:
        config = context.config or self.config
        reasons: list[str] = []
        if previous is None:
            return ReplanDecision(should_replan=True, reasons=["no previous plan"])

        task = context.current_task
        if task is None:
            if previous.task_id is not None:
                reasons.append("task cleared")
            return ReplanDecision(should_replan=bool(reasons), reasons=reasons)
        if previous.task_id != task.task_id:
            reasons.append("task changed")

        metadata = previous.metadata
        if context.now - float(metadata.get("plan_time", context.now)) > config.replan_max_age:
            reasons.append("plan stale")
        if abs(float(metadata.get("battery", context.battery)) - context.battery) >= config.replan_battery_delta:
            reasons.append("battery changed")
        if (
            context.localization_uncertainty - float(metadata.get("uncertainty", context.localization_uncertainty))
            >= config.replan_uncertainty_delta
        ):
            reasons.append("localization uncertainty increased")

        team, _ = active_team(context)
        if list(metadata.get("team", team)) != team:
            reasons.append("team composition changed")

        environment = context.environment or self.environment
        if environment is not None and previous.waypoints:
            clearance, _, _ = self._uncertainty_profile(context, config)
            legs = [context.estimated_position, *previous.waypoints]
            for index in range(len(legs) - 1):
                if environment.segment_intersects_obstacle(legs[index], legs[index + 1], clearance):
                    reasons.append("path blocked")
                    break

        if previous.waypoints:
            drift = distance_xy(context.estimated_position, previous.waypoints[0])
            previous_drift = float(metadata.get("path_length", drift))
            if drift > previous_drift + config.replan_position_drift:
                reasons.append("drifted from route")

        for peer in relevant_peers(context):
            assert peer.position is not None
            if (
                distance_xy(context.estimated_position, peer.position) < config.minimum_horizontal_separation
                and abs(previous.desired_altitude - (peer.altitude or peer.position.z))
                < config.minimum_vertical_separation
            ):
                reasons.append(f"separation conflict with {peer.drone_id}")
                break

        if task.type == TaskType.FOLLOW and task.entity_id:
            tracked = context.entity(task.entity_id)
            previous_position = metadata.get("predicted_position")
            if tracked is not None and isinstance(previous_position, dict):
                moved = distance_xy(tracked.position, Vector3.model_validate(previous_position))
                if moved > (task.standoff or config.follow_standoff) * 0.5:
                    reasons.append("tracked entity moved")

        return ReplanDecision(should_replan=bool(reasons), reasons=reasons)

    # -- internals -----------------------------------------------------------

    def _uncertainty_profile(
        self, context: PlanningContext, config: PlanningConfig
    ) -> tuple[float, float, list[PlanWarning]]:
        uncertainty = max(0.0, context.localization_uncertainty)
        clearance = min(
            config.maximum_clearance,
            config.base_clearance + config.uncertainty_clearance_gain * uncertainty,
        )
        warnings: list[PlanWarning] = []
        speed_scale = 1.0
        if uncertainty > config.uncertainty_elevated:
            span = max(config.uncertainty_severe - config.uncertainty_elevated, 1e-6)
            ratio = min(1.0, (uncertainty - config.uncertainty_elevated) / span)
            speed_scale = 1.0 - ratio * (1.0 - config.uncertainty_speed_floor)
        if uncertainty >= config.uncertainty_severe:
            warnings.append(PlanWarning.LOCALIZATION_UNCERTAINTY_SEVERE)
        elif uncertainty >= config.uncertainty_high:
            warnings.append(PlanWarning.LOCALIZATION_UNCERTAINTY_HIGH)
        return clearance, speed_scale, warnings

    def _uncertainty_gate(
        self,
        context: PlanningContext,
        config: PlanningConfig,
        environment: EnvironmentQuery | None,
        clearance: float,
        warnings: list[PlanWarning],
    ) -> PlannerResult | None:
        """Stop moving through tight geometry when we barely know where we are."""

        uncertainty = context.localization_uncertainty
        if uncertainty < config.uncertainty_severe or environment is None:
            return None
        task = context.current_task
        if task is not None and task.type in {TaskType.RETURN, TaskType.HOLD}:
            return None
        margin = environment.nearest_obstacle_distance(context.estimated_position)
        if margin >= uncertainty * config.uncertainty_tight_corridor_ratio:
            return None
        return PlannerResult(
            drone_id=context.drone_id,
            task_id=task.task_id if task else None,
            mode=PlanMode.HOLD,
            phase=BehaviorPhase.HOLDING,
            waypoints=[context.estimated_position],
            desired_speed=0.0,
            desired_altitude=context.estimated_position.z,
            hold=True,
            completion_condition=CompletionCondition(
                kind=CompletionKind.NONE, description="hold until localization improves"
            ),
            confidence=0.2,
            warnings=[*warnings, PlanWarning.LOCALIZATION_UNCERTAINTY_SEVERE],
            metadata={
                "plan_time": context.now,
                "battery": context.battery,
                "uncertainty": uncertainty,
                "reason": "uncertainty exceeds obstacle margin",
                "obstacle_margin": round(margin, 2),
                "team": active_team(context)[0],
            },
        )

    def _route(
        self,
        context: PlanningContext,
        environment: EnvironmentQuery | None,
        targets: list[Vector3],
        clearance: float,
        altitude: float,
        config: PlanningConfig,
    ) -> tuple[list[Vector3], list[PlanWarning]]:
        if not targets:
            return [], []
        if environment is None:
            return targets, []
        grid = GridPathPlanner(environment, config.grid_cell_size, config.grid_padding)
        result = route_through(grid, context.estimated_position.with_z(altitude), targets, clearance)
        warnings: list[PlanWarning] = []
        if result.blocked:
            warnings.append(PlanWarning.PATH_BLOCKED)
        if result.degraded:
            warnings.append(PlanWarning.PATH_DEGRADED)
        if result.detoured and PlanWarning.ROUTE_ADJUSTED not in warnings:
            warnings.append(PlanWarning.ROUTE_ADJUSTED)
        return (result.points or targets), warnings

    def _communication_route(
        self,
        context: PlanningContext,
        environment: EnvironmentQuery | None,
        targets: list[Vector3],
        baseline: list[Vector3],
        clearance: float,
        altitude: float,
        config: PlanningConfig,
    ):
        """Offer the routed path a better-connected alternative, if one exists.

        Bounded on purpose: at most a few detour anchors drawn from the learned
        field, each routed once with the same A* the direct path uses. A task
        that does not need connectivity keeps the shortest route.
        """

        if not config.communication_routing or context.communication is None or not baseline or not targets:
            return None
        task = context.current_task
        priority = context.connectivity_priority
        if priority is None:
            priority = (
                connectivity_priority_for(str(task.type), task.priority, task.metadata) if task else 0.0
            )
        if priority <= 0.0:
            return None
        start = context.estimated_position.with_z(altitude)
        options = [measure_route("direct", start, baseline, context.communication)]
        goal = targets[-1]
        midpoint = Vector3(x=(start.x + goal.x) / 2.0, y=(start.y + goal.y) / 2.0, z=altitude)
        anchors = context.communication.strong_points(
            midpoint,
            config.communication_detour_radius,
            limit=config.communication_detour_candidates,
        ) if hasattr(context.communication, "strong_points") else []
        limit = options[0].length_m * config.communication_maximum_detour_ratio
        for index, anchor in enumerate(anchors):
            waypoint = anchor.with_z(altitude)
            if waypoint.distance_xy(start) < 1.0 or waypoint.distance_xy(goal) < 1.0:
                continue
            points, _ = self._route(
                context, environment, [waypoint, *targets], clearance, altitude, config
            )
            if not points:
                continue
            option = measure_route(f"via-{index}", start, points, context.communication)
            if option.length_m > limit:
                continue  # never accept an unbounded detour to hug the mesh
            options.append(option)
        if len(options) < 2:
            return None
        return choose_route(options, priority, minimum_margin=config.communication_route_margin)

    @staticmethod
    def _progress_fraction(
        context: PlanningContext, full: list[Vector3], affordable: list[Vector3]
    ) -> float:
        def length(points: list[Vector3]) -> float:
            if not points:
                return 0.0
            total = context.estimated_position.distance_to(points[0])
            return total + sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1))

        total = length(full)
        return 0.0 if total <= 1e-6 else length(affordable) / total

    @staticmethod
    def _velocity(context: PlanningContext, waypoints: list[Vector3], speed: float) -> Vector3:
        if not waypoints or speed <= 0.0:
            return Vector3()
        target = waypoints[0]
        delta = Vector3(
            x=target.x - context.estimated_position.x,
            y=target.y - context.estimated_position.y,
            z=target.z - context.estimated_position.z,
        )
        length = delta.magnitude()
        if length <= 1e-6:
            return Vector3()
        return Vector3(x=delta.x / length * speed, y=delta.y / length * speed, z=delta.z / length * speed)

    @staticmethod
    def _confidence(base: float, warnings: list[PlanWarning]) -> float:
        penalties = {
            PlanWarning.PATH_BLOCKED: 0.4,
            PlanWarning.PATH_DEGRADED: 0.15,
            PlanWarning.LOCALIZATION_UNCERTAINTY_SEVERE: 0.35,
            PlanWarning.LOCALIZATION_UNCERTAINTY_HIGH: 0.15,
            PlanWarning.BATTERY_INSUFFICIENT: 0.25,
            PlanWarning.BATTERY_LIMITED_RANGE: 0.1,
            PlanWarning.PEER_STATE_STALE: 0.1,
            PlanWarning.TARGET_LOST: 0.3,
            PlanWarning.TARGET_UNKNOWN: 0.3,
            PlanWarning.DECONFLICTION_YIELDING: 0.05,
        }
        value = base
        for warning in set(warnings):
            value -= penalties.get(warning, 0.0)
        return round(clamp(value, 0.0, 1.0), 3)

    def _idle_result(self, context: PlanningContext, config: PlanningConfig) -> PlannerResult:
        return PlannerResult(
            drone_id=context.drone_id,
            task_id=None,
            mode=PlanMode.HOLD,
            phase=BehaviorPhase.HOLDING,
            waypoints=[context.estimated_position],
            desired_speed=0.0,
            desired_altitude=context.estimated_position.z,
            hold=True,
            completion_condition=CompletionCondition(kind=CompletionKind.NONE, description="idle"),
            confidence=1.0,
            warnings=[PlanWarning.NO_TASK],
            metadata={
                "plan_time": context.now,
                "battery": context.battery,
                "uncertainty": context.localization_uncertainty,
                "team": [context.drone_id],
            },
        )


def default_planner(environment: EnvironmentQuery | None = None) -> MissionPlanner:
    return MissionPlanner(environment or SimpleEnvironment())
