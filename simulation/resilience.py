"""Topology observer that turns network degradation into physical relay tasks.

Three things were added to the original reactive policy without replacing it:

* **prediction** - a trend on observed network health lets the manager act on a
  split that is *coming*, using a stricter link-quality threshold to find the
  bridge that is about to break;
* **multi-relay planning** - :mod:`simulation.relay_planning` assigns distinct,
  non-redundant stations to distinct vehicles instead of aiming them all at one
  midpoint;
* **counterfactual selection** - before committing, competing relay placements
  (including "do nothing") are rolled forward and compared.

The single-cluster-pair behaviour and its scoring curve are unchanged.
"""

from __future__ import annotations

from typing import Any

from .config import CounterfactualConfig, PredictionConfig
from .counterfactual import (
    CandidateAction,
    ForecastLinkModel,
    ForecastNode,
    ForecastRegion,
    ForecastState,
    ShadowEvaluator,
    forecast_payload,
)
from .events import EventBus
from .explain import DecisionExplanation, DecisionFactor, DecisionOption, ExplanationLog
from .models import (
    EventCategory,
    EventType,
    MissionCommand,
    MissionTarget,
    MissionTask,
    TaskStatus,
    TaskType,
    Vector3,
)
from .network import NetworkSimulator
from .prediction import Prediction, PredictiveMonitor
from .relay_planning import ClusterPair, RelayPlan, RelayPlanner, RelayVehicle, centroid
from .workunits import EdgeComputeBroker, WorkUnitKind
from .world import World


class NetworkResilienceManager:
    """Small, explainable relay policy; it never edits link quality directly."""

    def __init__(
        self,
        events: EventBus,
        health_threshold: float,
        evaluation_seconds: float,
        prediction: PredictionConfig | None = None,
        counterfactual: CounterfactualConfig | None = None,
        explanations: ExplanationLog | None = None,
        broker: EdgeComputeBroker | None = None,
        multi_relay: bool = True,
    ) -> None:
        self.events = events
        self.health_threshold = health_threshold
        self.evaluation_seconds = evaluation_seconds
        self.active_task_id: str | None = None
        self._last_evaluation = -evaluation_seconds
        self._state = "HEALTHY"
        self._baseline_health = 1.0
        self._relay_established = False
        self._relay_repositioning = False
        self.prediction_config = prediction or PredictionConfig()
        self.counterfactual_config = counterfactual or CounterfactualConfig()
        self.explanations = explanations
        self.broker = broker
        self.multi_relay = multi_relay
        self.monitor = PredictiveMonitor(
            window_seconds=self.prediction_config.window_seconds,
            minimum_samples=self.prediction_config.minimum_samples,
            minimum_confidence=self.prediction_config.minimum_confidence,
            cooldown_seconds=self.prediction_config.alert_cooldown_seconds,
        )
        self.last_plan: RelayPlan | None = None
        self.last_prediction: Prediction | None = None
        self.last_decision: DecisionExplanation | None = None
        self.counterfactual_runs = 0
        self.preemptive_triggers = 0

    def evaluate(
        self,
        now: float,
        network: NetworkSimulator,
        world: World,
        tasks: list[MissionTask],
        relay_nodes: list[str],
    ) -> MissionCommand | None:
        if now - self._last_evaluation < self.evaluation_seconds - 1e-9:
            return None
        self._last_evaluation = now
        metrics = network.metrics(now)
        self.monitor.observe("network_health", now, metrics.network_health)
        strong_components = self._components_at_quality(network, now, network.config.healthy_link_quality)
        unhealthy = len(strong_components) > 1 or metrics.network_health < self.health_threshold
        next_state = "DISCONNECTED" if len(metrics.connected_components) > 1 else ("RISK" if unhealthy else "HEALTHY")
        if next_state != self._state:
            if next_state == "DISCONNECTED":
                event_type, summary = EventType.NETWORK_PARTITION, "Physical network split into disconnected components"
            elif next_state == "RISK":
                event_type, summary = EventType.NETWORK_PARTITION_RISK, "Physical network has weak bridge links"
            else:
                event_type, summary = EventType.NETWORK_HEALED, "Physical network topology recovered"
            self.events.emit(
                now, EventCategory.NETWORK, event_type, "network-resilience", summary,
                [node for component in strong_components for node in component],
                {"network_health": metrics.network_health, "components": strong_components},
            )
            self._state = next_state

        active = next((task for task in tasks if task.id == self.active_task_id), None)
        if active is not None and active.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            self._track_active_relay(now, active, world, metrics.network_health)
            return None

        eligible = sorted(node for node in relay_nodes if world.is_online(node))
        if not eligible:
            return None

        prediction = self._partition_prediction(now)
        preemptive = False
        if not unhealthy or len(strong_components) < 2:
            # Nothing is broken yet. Act only if a split is confidently coming,
            # and only where a stricter threshold shows the bridge that is going.
            if prediction is None:
                return None
            predictive_quality = min(0.95, network.config.healthy_link_quality * 1.8)
            strong_components = self._components_at_quality(network, now, predictive_quality)
            if len(strong_components) < 2:
                return None
            preemptive = True

        plan = self._build_plan(now, network, world, tasks, eligible, strong_components)
        if plan is None or not plan.stations:
            return None

        decision = self._choose(now, world, tasks, plan, metrics.network_health, prediction, preemptive)
        if decision is not None and decision.selected_option == "A":
            return None  # forecasting says holding the current posture is better

        if preemptive:
            self.preemptive_triggers += 1
            assert prediction is not None
            self.events.emit(
                now, EventCategory.NETWORK, EventType.PREEMPTIVE_RELAY, "network-resilience",
                f"Repositioning relays before the split: {prediction.explanation}",
                [station.assigned_to or "" for station in plan.stations],
                {"prediction": prediction.model_dump(mode="json"), "stations": plan.to_records()},
            )

        threatened_priority = max(
            (task.priority for task in tasks if task.type != TaskType.RELAY and task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}),
            default=70,
        )
        self._baseline_health = metrics.network_health
        self._relay_established = False
        self._relay_repositioning = False
        self.last_plan = plan
        targets = plan.targets
        metadata: dict[str, Any] = {
            "network_support": True,
            "components": strong_components,
            "relay_targets": [target.model_dump(mode="json") for target in targets],
            "relay_assignments": plan.assignments,
            "relay_stations": plan.to_records(),
            "redundant_candidates_rejected": plan.rejected_redundant,
            "baseline_network_health": round(metrics.network_health, 4),
            "preemptive": preemptive,
            "policy": "deterministic-candidate-connectivity-score",
        }
        if prediction is not None:
            metadata["prediction"] = prediction.model_dump(mode="json")
        if decision is not None:
            metadata["decision"] = decision.model_dump(mode="json")
        return MissionCommand(
            type=TaskType.RELAY,
            target=MissionTarget(point=targets[0]),
            priority=min(100, threatened_priority + 1),
            required_capabilities={"relay"},
            desired_units=len(targets),
            minimum_units=1,
            metadata=metadata,
        )

    # -- prediction ----------------------------------------------------------

    def _partition_prediction(self, now: float) -> Prediction | None:
        if not self.prediction_config.enabled:
            return None
        prediction = self.monitor.predict(
            "network_health",
            kind="PARTITION_RISK",
            subject="network",
            metric="network health",
            threshold=self.health_threshold,
            horizon=self.prediction_config.partition_horizon_seconds,
        )
        if prediction is None:
            return None
        self.last_prediction = prediction
        if self.monitor.should_alert("network_health", now):
            self.events.emit(
                now, EventCategory.NETWORK, EventType.PREDICTION_EMITTED, "network-resilience",
                f"Projected partition risk: {prediction.explanation}",
                ["network"], prediction.model_dump(mode="json"),
            )
        return prediction

    # -- planning ------------------------------------------------------------

    def _build_plan(
        self,
        now: float,
        network: NetworkSimulator,
        world: World,
        tasks: list[MissionTask],
        eligible: list[str],
        strong_components: list[list[str]],
    ) -> RelayPlan | None:
        planner = RelayPlanner(
            network.config.reference_range_m,
            network.config.distance_falloff_power,
            network.config.hard_range_m,
            free_point=lambda point: self._free_relay_target(world, point),
        )
        first = strong_components[0]
        primary_centroid = centroid([world.truth(node).position for node in first])
        pairs = [
            ClusterPair(
                primary=tuple(first),
                secondary=tuple(component),
                primary_centroid=primary_centroid,
                secondary_centroid=centroid([world.truth(node).position for node in component]),
            )
            for component in strong_components[1:]
        ]
        if not pairs:
            return None
        priority_by_node = {
            node: task.priority
            for task in tasks
            if task.type != TaskType.RELAY and task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            for node in task.assigned_nodes
        }
        vehicles = [
            RelayVehicle(
                node_id=node_id,
                position=world.truth(node_id).position,
                battery=world.truth(node_id).actual_battery,
                current_task_priority=priority_by_node.get(node_id, 0),
            )
            for node_id in eligible
        ]
        maximum = len(vehicles) if self.multi_relay else 1
        plan = planner.plan(pairs, vehicles, maximum_stations=maximum)
        if plan.stations and self.broker is not None:
            # A real, bounded job: rescore the chosen stations, off-box when an
            # edge node is present. Recorded for provenance, never for control.
            self.broker.run(
                WorkUnitKind.RELAY_CANDIDATES,
                {
                    "reference_range_m": network.config.reference_range_m,
                    "falloff_power": network.config.distance_falloff_power,
                    "hard_range_m": network.config.hard_range_m,
                    "clusters": [
                        [world.truth(node).position.model_dump(mode="json") for node in component]
                        for component in strong_components
                    ],
                    "relay_positions": {
                        vehicle.node_id: vehicle.position.model_dump(mode="json") for vehicle in vehicles
                    },
                    "candidates": [station.point.model_dump(mode="json") for station in plan.stations],
                },
                now=now,
            )
        if plan.stations and self.explanations is not None:
            self.events.emit(
                now, EventCategory.NETWORK, EventType.RELAY_PLAN_UPDATED, "network-resilience",
                f"Planned {len(plan.stations)} distinct relay station(s); "
                f"rejected {plan.rejected_redundant} redundant candidate(s)",
                [station.assigned_to or "" for station in plan.stations],
                {"stations": plan.to_records()},
            )
        return plan

    # -- counterfactual ------------------------------------------------------

    def _choose(
        self,
        now: float,
        world: World,
        tasks: list[MissionTask],
        plan: RelayPlan,
        network_health: float,
        prediction: Prediction | None,
        preemptive: bool,
    ) -> DecisionExplanation | None:
        if not self.counterfactual_config.enabled or not plan.stations:
            return None
        nodes = [
            ForecastNode(
                node_id=node_id,
                position=world.truth(node_id).position,
                battery=world.truth(node_id).actual_battery,
                relay_capable=True,
                role="MISSION",
            )
            for node_id in world.node_ids
            if world.is_online(node_id)
        ]
        if len(nodes) < 2:
            return None
        regions = [
            ForecastRegion(region_id=region.id, center=region.center, radius=region.radius)
            for region in world.definition.regions
        ]
        state = ForecastState(
            now=now,
            nodes=nodes,
            regions=regions,
            link_model=ForecastLinkModel(),
        )
        candidates = [CandidateAction(option_id="A", summary="hold current posture", assignments={})]
        assignments: dict[str, tuple[str, Vector3 | None]] = {}
        for station in plan.stations[: self.counterfactual_config.maximum_candidates - 1]:
            if station.assigned_to is None:
                continue
            assignments[station.assigned_to] = ("RELAY", station.point)
            candidates.append(
                CandidateAction(
                    option_id=chr(ord("A") + len(candidates)),
                    summary=f"{station.assigned_to} relays at ({station.point.x:.0f}, {station.point.y:.0f})",
                    assignments=dict(assignments),
                )
            )
        if len(candidates) < 2:
            return None

        payload = forecast_payload(state, candidates)
        payload.update(
            {
                "horizon_seconds": self.counterfactual_config.horizon_seconds,
                "step_seconds": self.counterfactual_config.step_seconds,
                "minimum_margin": self.counterfactual_config.minimum_margin,
            }
        )
        self.counterfactual_runs += 1
        if self.broker is not None:
            result = self.broker.run(WorkUnitKind.COUNTERFACTUAL_FORECAST, payload, now=now).result
        else:
            evaluator = ShadowEvaluator(
                horizon_seconds=self.counterfactual_config.horizon_seconds,
                step_seconds=self.counterfactual_config.step_seconds,
                minimum_margin=self.counterfactual_config.minimum_margin,
            )
            forecast = evaluator.evaluate(state, candidates)
            if forecast is None:
                return None
            result = {
                "outcomes": [
                    {
                        "option_id": item.option_id,
                        "summary": item.summary,
                        "predicted_effectiveness": item.predicted_effectiveness,
                        "predicted_network_health": item.predicted_network_health,
                        "predicted_coverage": item.predicted_coverage,
                        "predicted_battery_cost": item.predicted_battery_cost,
                        "connected_fraction": item.connected_fraction,
                    }
                    for item in forecast.outcomes
                ],
                "selected": forecast.selected.option_id,
                "margin": forecast.margin,
                "decisive": forecast.decisive,
                "steps": forecast.steps,
            }

        outcomes = result.get("outcomes", [])
        selected = str(result.get("selected", "A"))
        margin = float(result.get("margin", 0.0))
        if not bool(result.get("decisive", False)):
            # An indecisive forecast never overrides the reactive policy.
            selected = candidates[1].option_id if selected == "A" else selected
        options = [
            DecisionOption(
                option_id=str(item["option_id"]),
                summary=str(item.get("summary", "")),
                score=float(item.get("predicted_effectiveness", 0.0)),
                selected=str(item["option_id"]) == selected,
                metrics={
                    "network_health": float(item.get("predicted_network_health", 0.0)),
                    "coverage": float(item.get("predicted_coverage", 0.0)),
                    "battery_cost": float(item.get("predicted_battery_cost", 0.0)),
                },
            )
            for item in outcomes
        ]
        rationale = [
            f"network health {network_health:.2f} (threshold {self.health_threshold:.2f})",
            f"{len(plan.stations)} distinct station(s), {plan.rejected_redundant} redundant candidate(s) rejected",
        ]
        if prediction is not None:
            rationale.insert(0, prediction.explanation)
        explanation = DecisionExplanation(
            decision_id=(self.explanations.next_id("relay") if self.explanations else f"relay-{now:.1f}"),
            timestamp=now,
            kind="RELAY_SELECTION",
            subject="network",
            headline=(
                "WHY RELAY? projected partition"
                if preemptive
                else "WHY RELAY? measured network degradation"
            ),
            rationale=rationale,
            factors=[
                DecisionFactor(name="network_health", value=round(network_health, 4), baseline=self.health_threshold,
                               delta=round(network_health - self.health_threshold, 4), unit="ratio"),
                DecisionFactor(name="forecast_margin", value=round(margin, 4), unit="effectiveness"),
            ],
            options=options,
            selected_option=selected,
            confidence=prediction.confidence if prediction else 0.8,
            metadata={"horizon_seconds": self.counterfactual_config.horizon_seconds,
                      "steps": result.get("steps", 0),
                      "preemptive": preemptive},
        )
        self.last_decision = explanation
        if self.explanations is not None:
            self.explanations.add(explanation)
        self.events.emit(
            now, EventCategory.NETWORK, EventType.COUNTERFACTUAL_EVALUATED, "network-resilience",
            f"Forecast {len(options)} relay options over {self.counterfactual_config.horizon_seconds:.0f}s; "
            f"selected {selected} (margin {margin:+.2f})",
            [option.option_id for option in options], explanation.model_dump(mode="json"),
        )
        return explanation

    # -- active relay tracking ----------------------------------------------

    def _track_active_relay(self, now: float, active: MissionTask, world: World, network_health: float) -> None:
        if self._relay_established or not active.assigned_nodes:
            return
        relay_id = active.assigned_nodes[0]
        target = active.target.point
        assignments = active.metadata.get("relay_assignments") or {}
        if relay_id in assignments:
            target = Vector3.model_validate(assignments[relay_id])
        if target and not self._relay_repositioning:
            self._relay_repositioning = True
            self.events.emit(
                now, EventCategory.NETWORK, EventType.RELAY_REPOSITIONING, relay_id,
                f"{relay_id} is physically repositioning to repair the network",
                [relay_id, active.id],
                {"target": target.model_dump(mode="json"), "network_health": network_health},
            )
        if target and world.is_online(relay_id) and world.truth(relay_id).position.distance_to(target) <= 10.0:
            self._relay_established = True
            self.events.emit(
                now, EventCategory.NETWORK, EventType.RELAY_ESTABLISHED, relay_id,
                f"{relay_id} reached the relay station; link health is now {network_health:.0%}",
                [relay_id, active.id],
                {"network_health": network_health, "baseline_health": self._baseline_health},
            )

    def task_created(self, task: MissionTask, now: float) -> None:
        self.active_task_id = task.id
        self.events.emit(
            now, EventCategory.NETWORK, EventType.RELAY_TASK_CREATED, "network-resilience",
            f"Created relay task {task.id} with {task.desired_units} scored relay station(s)",
            [task.id], {"target": task.target.model_dump(mode="json"), **task.metadata},
        )

    @staticmethod
    def _centroid(world: World, nodes: list[str]) -> Vector3:
        return centroid([world.truth(node).position for node in nodes])

    @staticmethod
    def _free_relay_target(world: World, target: Vector3) -> Vector3:
        altitude = target.z
        for box in world.definition.boxes:
            if box.minimum.x <= target.x <= box.maximum.x and box.minimum.y <= target.y <= box.maximum.y:
                altitude = max(altitude, box.maximum.z + 10.0)
        for circle in world.definition.circles:
            if target.distance_to(Vector3(x=circle.center.x, y=circle.center.y, z=target.z)) <= circle.radius:
                altitude = max(altitude, circle.height + 10.0)
        return Vector3(
            x=min(world.definition.maximum.x, max(world.definition.minimum.x, target.x)),
            y=min(world.definition.maximum.y, max(world.definition.minimum.y, target.y)),
            z=min(world.definition.maximum.z, max(world.definition.minimum.z, altitude)),
        )

    @classmethod
    def _score_relay_target(
        cls,
        network: NetworkSimulator,
        world: World,
        first: list[str],
        second: list[str],
        relay_nodes: list[str],
    ) -> Vector3:
        """Retained single-pair scoring entry point used by tests and tools."""

        planner = RelayPlanner(
            network.config.reference_range_m,
            network.config.distance_falloff_power,
            network.config.hard_range_m,
            free_point=lambda point: cls._free_relay_target(world, point),
        )
        pair = ClusterPair(
            primary=tuple(first),
            secondary=tuple(second),
            primary_centroid=cls._centroid(world, first),
            secondary_centroid=cls._centroid(world, second),
        )
        vehicles = [
            RelayVehicle(node_id=node_id, position=world.truth(node_id).position)
            for node_id in relay_nodes
        ]
        return planner.candidates_for(pair, vehicles)[0].point

    @staticmethod
    def _components_at_quality(network: NetworkSimulator, now: float, quality: float) -> list[list[str]]:
        nodes = sorted(
            node for component in network.components(now) for node in component
        )
        adjacency = {node: set() for node in nodes}
        for link in network.links(now):
            if link.available and link.quality >= quality:
                adjacency[link.source_id].add(link.target_id)
                adjacency[link.target_id].add(link.source_id)
        unseen, result = set(nodes), []
        while unseen:
            root = min(unseen)
            unseen.remove(root)
            stack, component = [root], []
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor in sorted(adjacency[current] & unseen, reverse=True):
                    unseen.remove(neighbor)
                    stack.append(neighbor)
            result.append(sorted(component))
        return sorted(result, key=lambda item: (-len(item), item))
