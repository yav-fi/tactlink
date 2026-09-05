"""Central clock coordinating truth, measurements, messages, and autonomy."""

from __future__ import annotations

import random
from math import exp
from collections.abc import Callable

from .allocator import TaskAllocator
from .autonomy import PlanningAutonomy
from .config import SimulationConfig
from .drone import DroneNode
from .events import EventBus
from .explain import DecisionExplanation, ExplanationLog
from .interference import InterferenceEngine
from .mission import MissionManager
from .models import (
    AdaptiveRuntimeState,
    CommunicationCellState,
    DronePublicState,
    EdgeComputeState,
    NodeAdaptiveState,
    EventCategory,
    EventType,
    GPSMeasurement,
    InterferenceConfig,
    LocalizationMode,
    MissionCommand,
    MissionTask,
    MissionTarget,
    NodeIdentity,
    NodeCapabilities,
    ObservationType,
    MessageType,
    ScenarioEvent,
    SimulationSnapshot,
    TaskType,
    Vector3,
    WorldKnowledgeMetrics,
)
from .network import NetworkSimulator
from .resilience import NetworkResilienceManager
from .scenarios import PRESET_INTERFERENCE, ScenarioEngine, ScenarioPreset
from .workunits import EdgeComputeBroker, EdgeResourceProfile, WorkExecutor
from .world import BoxObstacle, MovingEntity, Region, World, WorldDefinition
from .world_model import CoverageGrid, WorldBelief
from .authentication import MessageAuthenticator, deterministic_private_key, public_key_text


class SimulationEngine:
    def __init__(
        self,
        config: SimulationConfig | None = None,
        world_definition: WorldDefinition | None = None,
        scenario: ScenarioPreset | str = ScenarioPreset.NORMAL,
        drone_count: int = 3,
        mode: str = "fabric",
    ) -> None:
        self.config = config or SimulationConfig()
        self.scenario = ScenarioPreset(scenario)
        if mode not in {"fabric", "baseline"}:
            raise ValueError("mode must be 'fabric' or 'baseline'")
        self.mode = mode
        self._recorder: object | None = None
        self.time = 0.0
        self.running = True
        self.events = EventBus(self.config.recent_event_limit)
        definition = world_definition or self.default_world_definition()
        self.world = World(definition, self.config.battery_drain_per_meter)
        self.explanations = ExplanationLog()
        self.broker = EdgeComputeBroker(
            maximum_attempts=self.config.adaptive.edge_compute.maximum_attempts
        )
        # Built once from the STATIC world definition; nodes never see World truth.
        self.autonomy = PlanningAutonomy.from_world_definition(
            definition,
            communication_routing=self.config.adaptive.communication_aware_routing,
        )
        self.interference = InterferenceEngine(PRESET_INTERFERENCE[self.scenario].model_copy(deep=True))
        self.network = NetworkSimulator(
            self.config.network,
            random.Random(self.config.seed + 101),
            self.interference,
            self.events,
            position_provider=lambda node_id: self.world.truth(node_id).position if node_id in self.world.node_ids else None,
            world_definition=definition,
        )
        self.scenario_engine = ScenarioEngine(self.scenario, random.Random(self.config.seed + 202))
        self._measurement_rng = random.Random(self.config.seed + 303)
        self._control_rng = random.Random(self.config.seed + 404)
        self._failure_rng = random.Random(self.config.seed + 505)
        self._failure_counter = 0
        self.allocator = TaskAllocator(self.config.allocator)
        self.missions = MissionManager(self.allocator, self.events, self.config.peer_timeout)
        self.resilience = NetworkResilienceManager(
            self.events,
            self.config.relay_health_threshold,
            self.config.relay_evaluation_seconds,
            prediction=self.config.adaptive.prediction,
            counterfactual=self.config.adaptive.counterfactual,
            explanations=self.explanations,
            broker=self.broker if self.config.adaptive.edge_compute.enabled else None,
            multi_relay=self.config.adaptive.multi_relay_coordination,
        )
        self._preemptive_handoffs = 0
        self._route_changes = 0
        self._seen_handoffs: set[str] = set()
        self._seen_route_choices: set[str] = set()
        self.drones: dict[str, DroneNode] = {}
        self._coverage_templates = [
            CoverageGrid(region.id, region.center, region.radius, self.config.sensing.cell_size_m)
            for region in definition.regions
        ]
        self._last_sensed: dict[str, float] = {}
        self._coverage_event_bucket: dict[str, int] = {}
        self._fresh_event_bucket: dict[str, int] = {}
        self._last_reconcile_event: dict[str, float] = {}
        self._external_workers: dict[str, object] = {}
        self._initial_drone_count = drone_count
        self._create_default_drones(drone_count)
        self.events.emit(
            self.time,
            EventCategory.SIMULATION,
            EventType.SIMULATION_STARTED,
            "simulation",
            f"Simulation started with seed {self.config.seed}",
            self.drones.keys(),
            {"scenario": self.scenario, "seed": self.config.seed},
        )

    @staticmethod
    def default_world_definition() -> WorldDefinition:
        return WorldDefinition(
            boxes=[
                BoxObstacle(
                    id="block-east",
                    minimum=Vector3(x=10, y=10, z=0),
                    maximum=Vector3(x=60, y=60, z=60),
                ),
                BoxObstacle(
                    id="block-west",
                    minimum=Vector3(x=-90, y=20, z=0),
                    maximum=Vector3(x=-50, y=70, z=50),
                ),
            ],
            regions=[
                Region(id="ALPHA", center=Vector3(x=120, y=70, z=30), radius=35),
                Region(id="BRAVO", center=Vector3(x=-110, y=100, z=35), radius=55),
            ],
            entities=[
                MovingEntity(
                    id="vehicle-1",
                    position=Vector3(x=-150, y=-120, z=0),
                    velocity=Vector3(x=3, y=2, z=0),
                )
            ],
        )

    def _create_default_drones(self, count: int) -> None:
        positions = [
            Vector3(x=-40, y=-25, z=20),
            Vector3(x=-10, y=-30, z=20),
            Vector3(x=20, y=-25, z=20),
            Vector3(x=50, y=-30, z=20),
            Vector3(x=80, y=-25, z=20),
        ]
        batteries = [0.92, 0.81, 0.77, 0.69, 0.88]
        capabilities = [
            {"camera", "navigation"},
            {"camera", "thermal", "navigation"},
            {"camera", "mapping", "navigation"},
            {"camera", "mapping", "relay", "navigation"},
            {"camera", "relay", "navigation"},
        ]
        keys = {
            f"drone-{index + 1}": deterministic_private_key(self.config.seed, f"drone-{index + 1}")
            for index in range(count)
        }
        public_keys = {node_id: public_key_text(key) for node_id, key in keys.items()}
        self._message_verifier = MessageAuthenticator(public_keys) if self.config.security.enabled else None
        for index in range(count):
            node_id = f"drone-{index + 1}"
            position = positions[index % len(positions)].model_copy(deep=True)
            battery = batteries[index % len(batteries)]
            self.world.add_drone(node_id, position, battery, self.config.default_speed_mps)
            self.network.register(node_id)
            self.drones[node_id] = DroneNode(
                NodeIdentity(
                    node_id=node_id,
                    name=f"Drone {index + 1}",
                    capabilities=capabilities[index % len(capabilities)],
                    public_key=public_keys[node_id] if self.config.security.enabled else None,
                    resources=NodeCapabilities(
                        mobility=True,
                        sensors=capabilities[index % len(capabilities)] & {"camera", "thermal", "mapping"},
                        communication_roles={"peer", "relay"} if "relay" in capabilities[index % len(capabilities)] else {"peer"},
                        relay="relay" in capabilities[index % len(capabilities)],
                    ),
                    metadata={"airframe": "simulated-multirotor"},
                ),
                position,
                self.config.localization,
                self.config.default_speed_mps,
                self.config.heartbeat_interval,
                self.config.status_interval,
                self.config.peer_timeout,
                autonomy=self.autonomy,
                allocator=self.allocator,
                auction_window=self.config.auction_window_seconds,
                auction_rebroadcast=self.config.auction_rebroadcast_seconds,
                coverage_grids=self._coverage_templates,
                distributed_coordination=self.mode == "fabric",
                lease_seconds=self.config.task_lease_seconds,
                authenticator=(
                    MessageAuthenticator(public_keys, keys[node_id])
                    if self.config.security.enabled else None
                ),
                communication=self.config.adaptive.communication,
                prediction=self.config.adaptive.prediction,
            )
            self.events.emit(
                self.time,
                EventCategory.SIMULATION,
                EventType.NODE_STARTED,
                node_id,
                f"{node_id} joined the simulation",
                [node_id],
            )

    def submit_mission(self, command: MissionCommand) -> MissionTask:
        if not self.missions.control_available:
            raise RuntimeError("mission control is offline; existing replicated missions continue")
        if command.target.point is None and command.target.region_id:
            region = next(
                (region for region in self.world.definition.regions if region.id == command.target.region_id),
                None,
            )
            if region:
                command = command.model_copy(deep=True)
                command.target.point = region.center.model_copy(deep=True)
                if command.type == TaskType.SEARCH and not command.target.waypoints:
                    radius = region.radius * 0.7
                    command.target.waypoints = [
                        Vector3(x=region.center.x - radius, y=region.center.y - radius, z=region.center.z),
                        Vector3(x=region.center.x + radius, y=region.center.y - radius, z=region.center.z),
                        Vector3(x=region.center.x + radius, y=region.center.y + radius, z=region.center.z),
                        Vector3(x=region.center.x - radius, y=region.center.y + radius, z=region.center.z),
                    ]
        task = self.missions.submit_mission(command, self.time)
        if self._recorder is not None:
            self._recorder.record_mission(self.time, command)
        self.network.send(self.missions.announcement(task, self.time), self.time)
        return task

    def _submit_relay_mission(self, command: MissionCommand) -> MissionTask | None:
        online = self._online_node_ids()
        if not online:
            return None
        task = self.missions.submit_mission(command, self.time)
        if self.missions.control_available:
            self.network.send(self.missions.announcement(task, self.time), self.time)
        else:
            initiator = min(online)
            for message in self.drones[initiator].introduce_mission(task, self.time):
                self.network.send(message, self.time)
        self.resilience.task_created(task, self.time)
        return task

    def tick(self, dt: float | None = None) -> SimulationSnapshot:
        if not self.running:
            return self.snapshot()
        step = dt if dt is not None else self.config.tick_seconds
        self.time = round(self.time + step, 9)

        for event in self.scenario_engine.step(self.time, self._online_node_ids()):
            self.inject_event(event)
        self._apply_random_failure(step)
        self.interference.update(self.time)
        self.world.update(step)
        self.network.update(self.time)

        for node_id, node in sorted(self.drones.items()):
            if not self.world.is_online(node_id):
                continue
            delivered: list[object] = []
            for message in self.network.receive(node_id):
                if message.sender_id != NetworkSimulator.CONTROL_ID and message.signature is None:
                    message = message.model_copy(deep=True)
                    message.payload["link_quality"] = self.network.link_state(
                        message.sender_id, node_id, self.time
                    ).quality
                if node_id in self._external_workers:
                    delivered.append(message)
                    continue
                before_observations = len(node.world_belief.observations)
                rejected_before = node.rejected_signature_count
                for reply in node.handle_message(message, self.time):
                    self.network.send(reply, self.time)
                if node.rejected_signature_count > rejected_before:
                    self.events.emit(
                        self.time, EventCategory.NETWORK, EventType.SIGNATURE_REJECTED,
                        node_id, f"{node_id} rejected an invalid or unknown signed message",
                        [node_id, message.sender_id], {"message_id": message.message_id, "sender": message.sender_id},
                    )
                learned = len(node.world_belief.observations) - before_observations
                if (
                    learned > 0
                    and message.type == MessageType.WORLD_UPDATE
                    and self.time - self._last_reconcile_event.get(node_id, -99.0) >= 4.0
                ):
                    self._last_reconcile_event[node_id] = self.time
                    self.events.emit(
                        self.time, EventCategory.KNOWLEDGE, EventType.WORLD_MODEL_RECONCILED,
                        node_id, f"{node_id} reconciled {learned} remote observations",
                        [node_id, message.sender_id], {"learned": learned, "source": message.sender_id},
                    )
            old_mode = node.estimated.localization_mode
            measurement = self._gps_measurement(node_id)
            sensor_noise = self.interference.sensor_severity(node_id)
            if node_id in self._external_workers:
                before_ids = set(node.world_belief.observations)
                self._sense_node(node_id, node, sensor_noise)
                local_observations = [
                    observation for observation_id, observation in node.world_belief.observations.items()
                    if observation_id not in before_ids and observation.source_node_id == node_id
                ]
                try:
                    intent, outgoing, timed_out = self._external_workers[node_id].tick(
                        node, self.time, step, delivered, measurement,
                        self.world.truth(node_id).actual_battery,
                        max(0.05, 1.0 - sensor_noise),
                        local_observations,
                    )
                except Exception as exc:
                    intent, outgoing, timed_out = self._worker_failure(node_id, exc)
                new_mode = node.estimated.localization_mode
            else:
                _, new_mode = node.update_localization(measurement, step)
                node.update_battery_measurement(self.world.truth(node_id).actual_battery)
                node.update_sensor_confidence(max(0.05, 1.0 - sensor_noise))
                self._sense_node(node_id, node, sensor_noise)
                intent, outgoing, timed_out = node.tick(self.time, step)
            self._emit_localization_transition(node_id, old_mode, new_mode)
            self.world.set_motion_intent(node_id, intent)
            for peer_id in timed_out:
                self.events.emit(
                    self.time,
                    EventCategory.AUTONOMY,
                    EventType.HEARTBEAT_TIMEOUT,
                    node_id,
                    f"{node_id} marked {peer_id} unavailable from local peer data",
                    [node_id, peer_id],
                )
            for message in outgoing:
                self.network.send(message, self.time)

        for message in self.network.receive(NetworkSimulator.CONTROL_ID):
            if self._message_verifier is not None and not self._message_verifier.verify(message):
                self.events.emit(
                    self.time, EventCategory.NETWORK, EventType.SIGNATURE_REJECTED,
                    "mission-control", "Mission control rejected an invalid or unknown signed message",
                    [message.sender_id], {"message_id": message.message_id},
                )
            else:
                self.missions.handle_message(message, self.time)

        self.missions.detect_timeouts(self.time)
        if self.mode == "baseline" and self.missions.control_available:
            for message in self.missions.allocate(self.time):
                self.network.send(message, self.time)
        online_nodes = [self.drones[node_id] for node_id in self._online_node_ids()]
        self.missions.observe_node_states(online_nodes, self.time)
        self.missions.update_capability(self.time)
        aggregate = self._aggregate_belief()
        self._emit_knowledge_metrics(aggregate)
        network_metrics = self.network.metrics(self.time)
        self.missions.update_effectiveness(
            self.time,
            aggregate,
            self.config.sensing.freshness_half_life_seconds,
            network_metrics.network_health,
        )
        relay_command = self.resilience.evaluate(
            self.time,
            self.network,
            self.world,
            list(self.missions.tasks.values()),
            [node_id for node_id, node in self.drones.items() if "relay" in node.identity.capabilities],
        )
        if relay_command is not None and self.mode == "fabric":
            self._submit_relay_mission(relay_command)
        self._collect_adaptive_signals()
        snapshot = self.snapshot()
        if self._recorder is not None:
            self._recorder.record_snapshot(snapshot)
        return snapshot

    def attach_external_worker(self, node_id: str, uri: str) -> None:
        """Move one autonomy core into a WebSocket worker before the run starts."""
        if self.time != 0.0:
            raise RuntimeError("external workers must attach before the first simulation tick")
        if node_id not in self.drones:
            raise KeyError(node_id)
        from .worker import WebSocketWorkerClient
        self._external_workers[node_id] = WebSocketWorkerClient(
            uri, self.drones[node_id], self.config, self.world.definition, self.mode,
            self._message_verifier.public_keys if self._message_verifier is not None else None,
        )
        self.events.emit(
            self.time, EventCategory.AUTONOMY, EventType.WORKER_CONNECTED,
            node_id, f"{node_id} autonomy connected through external worker transport",
            [node_id], {"transport": "websocket", "uri": uri},
        )

    def _worker_failure(self, node_id: str, exc: Exception) -> tuple[object, list[object], list[str]]:
        worker = self._external_workers.pop(node_id, None)
        if worker is not None:
            worker.close()
        self.events.emit(
            self.time, EventCategory.FAILURE, EventType.WORKER_DISCONNECTED,
            node_id, f"{node_id} external autonomy worker disconnected; vehicle is holding",
            [node_id], {"error": str(exc)},
        )
        from .models import MotionIntent
        return MotionIntent(hold=True), [], []

    def attach_recorder(self, recorder: object) -> None:
        self._recorder = recorder
        recorder.write_header(
            seed=self.config.seed,
            scenario=str(self.scenario),
            mode=self.mode,
            config=self.config.model_dump(mode="json"),
        )
        recorder.record_snapshot(self.snapshot())

    def _sense_node(self, node_id: str, node: DroneNode, sensor_noise: float) -> None:
        if "camera" not in node.identity.capabilities and "thermal" not in node.identity.capabilities:
            return
        interval = self.config.sensing.update_interval_seconds
        if self.time - self._last_sensed.get(node_id, -interval) < interval - 1e-9:
            return
        self._last_sensed[node_id] = self.time
        truth_position = self.world.truth(node_id).position
        radius = self.config.sensing.observation_radius_m
        created = 0
        for grid in node.world_belief.grids.values():
            for cell in grid.cells_within(truth_position, radius):
                distance = cell.center.distance_to(truth_position)
                confidence = max(
                    0.05,
                    (1.0 - sensor_noise)
                    * (1.0 - self.config.sensing.confidence_falloff * distance / radius),
                )
                node.create_observation(
                    self.time,
                    ObservationType.CELL_OBSERVED,
                    f"cell:{grid.region_id}:{cell.cell_id}",
                    {
                        "region_id": grid.region_id,
                        "cell_id": cell.cell_id,
                        "center": cell.center.model_dump(mode="json"),
                    },
                    confidence,
                    node.estimated.position_uncertainty,
                    {"sensor": "camera"},
                )
                created += 1
        for entity in self.world.definition.entities:
            distance = truth_position.distance_to(entity.position)
            if distance > radius:
                continue
            noise = max(0.25, sensor_noise * 12.0)
            observed = Vector3(
                x=entity.position.x + self._measurement_rng.gauss(0, noise),
                y=entity.position.y + self._measurement_rng.gauss(0, noise),
                z=entity.position.z + self._measurement_rng.gauss(0, noise * 0.25),
            )
            node.create_observation(
                self.time,
                ObservationType.ENTITY_OBSERVED,
                f"entity:{entity.id}",
                {"position": observed.model_dump(mode="json")},
                max(0.05, (1.0 - sensor_noise) * (1.0 - 0.5 * distance / radius)),
                noise,
                {"entity_id": entity.id, "sensor": "camera"},
            )
            created += 1
        if created and len(node.world_belief.observations) == created:
            self.events.emit(
                self.time, EventCategory.KNOWLEDGE, EventType.OBSERVATION_CREATED,
                node_id, f"{node_id} began building its local world belief",
                [node_id], {"created": created, "sensor_radius_m": radius},
            )

    def _aggregate_belief(self) -> WorldBelief:
        aggregate = WorldBelief(self._coverage_templates)
        for node_id in self._online_node_ids():
            aggregate.merge(self.drones[node_id].world_belief.observations.values(), self.time)
        return aggregate

    def observed_entities(self) -> dict[str, float]:
        """Entity ids in the fused belief, with when each was last observed.

        Read-only projection for operator-facing consumers (the mission plan
        runtime evaluates ENTITY_OBSERVED triggers from it). Nodes are
        unaffected; this reads the same merged belief the snapshot reports.
        """
        return {
            entity_id: observation.timestamp
            for entity_id, observation in self._aggregate_belief().known_entities.items()
        }

    def _emit_knowledge_metrics(self, belief: WorldBelief) -> None:
        for region_id, grid in belief.grids.items():
            metrics = grid.metrics(self.time, self.config.sensing.freshness_half_life_seconds)
            coverage_bucket = int(metrics.coverage * 20 + 1e-9)
            previous_coverage = self._coverage_event_bucket.get(region_id, 0)
            if coverage_bucket > previous_coverage:
                self._coverage_event_bucket[region_id] = coverage_bucket
                self.events.emit(
                    self.time, EventCategory.KNOWLEDGE, EventType.COVERAGE_CHANGED,
                    "coverage-monitor", f"{region_id} sensed coverage reached {metrics.coverage:.0%}",
                    [region_id], metrics.model_dump(mode="json", exclude={"cells"}),
                )
            fresh_bucket = int(metrics.fresh_coverage * 10 + 1e-9)
            previous_fresh = self._fresh_event_bucket.get(region_id, fresh_bucket)
            if fresh_bucket < previous_fresh:
                self.events.emit(
                    self.time, EventCategory.KNOWLEDGE, EventType.INFORMATION_STALE,
                    "freshness-monitor", f"{region_id} fresh coverage fell to {metrics.fresh_coverage:.0%}",
                    [region_id], metrics.model_dump(mode="json", exclude={"cells"}),
                )
            self._fresh_event_bucket[region_id] = fresh_bucket

    def run_steps(self, count: int, dt: float | None = None) -> SimulationSnapshot:
        snapshot = self.snapshot()
        for _ in range(count):
            snapshot = self.tick(dt)
        return snapshot

    def _gps_measurement(self, node_id: str) -> GPSMeasurement | None:
        severity = self.interference.gps_severity(node_id)
        if severity >= 0.99 or (
            severity >= self.config.localization.gps_outage_threshold
            and self._measurement_rng.random() < severity * 0.55
        ):
            return None
        if self._measurement_rng.random() < severity * 0.15:
            return None
        truth = self.world.truth(node_id)
        accuracy = self.config.localization.gps_noise_meters + severity * self.config.localization.degraded_noise_meters
        return GPSMeasurement(
            timestamp=self.time,
            position=Vector3(
                x=truth.position.x + self._measurement_rng.gauss(0, accuracy),
                y=truth.position.y + self._measurement_rng.gauss(0, accuracy),
                z=truth.position.z + self._measurement_rng.gauss(0, accuracy * 0.4),
            ),
            accuracy_meters=accuracy,
        )

    def _apply_random_failure(self, dt: float) -> None:
        online = self._online_node_ids()
        rate = self.interference.config.node_failure_rate
        if not online or rate <= 0:
            return
        probability = 1.0 - exp(-rate * dt)
        if self._failure_rng.random() < probability:
            self._failure_counter += 1
            node_id = self._failure_rng.choice(online)
            self.inject_event(
                ScenarioEvent(
                    id=f"failure-{self._failure_counter:05d}",
                    timestamp=self.time,
                    type="NODE_FAILURE",
                    affected_nodes=[node_id],
                    severity=1.0,
                    metadata={"rate_per_second": rate, "preset": self.scenario},
                )
            )

    def _emit_localization_transition(
        self, node_id: str, previous: LocalizationMode, current: LocalizationMode
    ) -> None:
        if previous == current:
            return
        if current == LocalizationMode.DEAD_RECKONING:
            event_type, summary = EventType.GPS_LOST, f"{node_id} entered dead reckoning"
        elif previous == LocalizationMode.DEAD_RECKONING and current in {
            LocalizationMode.GPS,
            LocalizationMode.DEGRADED_GPS,
        }:
            event_type, summary = EventType.GPS_RECOVERED, f"{node_id} reacquired GPS measurements"
        else:
            event_type, summary = EventType.GPS_DEGRADED, f"{node_id} GPS accuracy degraded"
        self.events.emit(
            self.time,
            EventCategory.LOCALIZATION,
            event_type,
            node_id,
            summary,
            [node_id],
            {"from": previous, "to": current},
        )

    def fail_drone(self, node_id: str, source: str = "operator") -> None:
        if node_id not in self.drones:
            raise KeyError(node_id)
        if not self.world.is_online(node_id):
            return
        self.world.fail(node_id)
        self.drones[node_id].fail()
        self.network.set_online(node_id, False)
        self.events.emit(
            self.time,
            EventCategory.FAILURE,
            EventType.NODE_FAILED,
            source,
            f"{node_id} stopped operating; peers have not yet detected the loss",
            [node_id],
        )

    def recover_drone(self, node_id: str) -> None:
        if node_id not in self.drones:
            raise KeyError(node_id)
        self.world.recover(node_id)
        self.network.set_online(node_id, True)
        self.drones[node_id].recover(self.time)
        self.events.emit(
            self.time,
            EventCategory.FAILURE,
            EventType.NODE_RECOVERED,
            "operator",
            f"{node_id} restarted and must re-establish peer contact",
            [node_id],
        )

    def fail_control(self) -> None:
        if not self.missions.control_available:
            return
        self.missions.control_available = False
        self.network.set_online(NetworkSimulator.CONTROL_ID, False)
        self.events.emit(
            self.time, EventCategory.FAILURE, EventType.CONTROL_LOST, "operator",
            "Simulated mission control is offline; nodes continue from replicated mission state",
            list(self.drones),
        )

    def recover_control(self) -> None:
        if self.missions.control_available:
            return
        self.missions.control_available = True
        self.network.set_online(NetworkSimulator.CONTROL_ID, True)
        self.events.emit(
            self.time, EventCategory.SIMULATION, EventType.CONTROL_RECOVERED, "operator",
            "Simulated mission control rejoined as an observer and mission ingress",
            list(self.drones),
        )

    def fail_random_drone(self) -> str:
        online = self._online_node_ids()
        if not online:
            raise RuntimeError("no online drones")
        node_id = self._control_rng.choice(online)
        self.fail_drone(node_id, source="operator-random")
        return node_id

    def set_interference(self, config: InterferenceConfig) -> None:
        self.interference.configure(config)
        self.events.emit(
            self.time,
            EventCategory.SIMULATION,
            EventType.INTERFERENCE_CHANGED,
            "operator",
            "Interference controls updated",
            payload=config.model_dump(mode="json"),
        )

    def set_scenario(self, preset: ScenarioPreset | str) -> None:
        self.scenario = ScenarioPreset(preset)
        self.interference.configure(PRESET_INTERFERENCE[self.scenario].model_copy(deep=True))
        self.scenario_engine = ScenarioEngine(self.scenario, random.Random(self.config.seed + 202))

    def inject_event(self, event: ScenarioEvent) -> None:
        event = event.model_copy(update={"timestamp": self.time})
        if self._recorder is not None:
            self._recorder.record_scenario_event(event)
        kind = event.type.upper()
        if kind == "NODE_FAILURE":
            for node_id in event.affected_nodes:
                if node_id in self.drones:
                    self.fail_drone(node_id, source=event.id)
        elif kind == "NETWORK_PARTITION":
            first = set(event.affected_nodes)
            second = set(self.drones) - first
            self.network.partition(first, second, self.time + event.duration, self.time)
        elif kind == "NETWORK_RECONNECT":
            self.network.reconnect_all(self.time)
        else:
            self.interference.apply(event)
        self.events.emit(
            self.time,
            EventCategory.SIMULATION,
            EventType.RANDOM_EVENT,
            event.id,
            f"Applied {kind} at severity {event.severity:.0%}",
            event.affected_nodes,
            event.model_dump(mode="json"),
        )

    def pause(self) -> None:
        self.running = False
        self.events.emit(
            self.time,
            EventCategory.SIMULATION,
            EventType.SIMULATION_PAUSED,
            "operator",
            "Simulation paused",
        )

    def resume(self) -> None:
        self.running = True
        self.events.emit(
            self.time,
            EventCategory.SIMULATION,
            EventType.SIMULATION_RESUMED,
            "operator",
            "Simulation resumed",
        )

    def reset(self) -> None:
        config = self.config.model_copy(deep=True)
        scenario = self.scenario
        count = self._initial_drone_count
        recorder = self._recorder
        for worker in self._external_workers.values():
            worker.close()
        self.__init__(config=config, scenario=scenario, drone_count=count, mode=self.mode)
        self._recorder = recorder
        self.events.emit(
            self.time,
            EventCategory.SIMULATION,
            EventType.SIMULATION_RESET,
            "operator",
            "Simulation reset to deterministic initial state",
        )


    # -- adaptive fabric state ----------------------------------------------

    def _node_adaptive_state(self, node: DroneNode) -> NodeAdaptiveState:
        belief = node.comm_belief
        cells = belief.cells
        mean_quality = (
            sum(cell.quality for cell in cells.values()) / len(cells) if cells else 1.0
        )
        route_choice = None
        if node.plan is not None:
            candidate = node.plan.metadata.get("route_choice")
            if isinstance(candidate, dict):
                route_choice = candidate
        return NodeAdaptiveState(
            learned_cells=len(cells),
            learned_mean_quality=max(0.0, min(1.0, mean_quality)),
            observed_samples=belief.observed_samples,
            merged_samples=belief.merged_samples,
            peer_link_quality=dict(sorted(belief.peer_quality.items())),
            predictions=[item.model_dump(mode="json") for item in node.active_predictions],
            route_choice=route_choice,
            handoffs=[dict(item) for item in node.handoff_requests[-3:]],
            explanations=[item.model_dump(mode="json") for item in node.explanations.recent(3)],
        )

    def _adaptive_state(self) -> AdaptiveRuntimeState:
        adaptive = self.config.adaptive
        merged: dict[str, CommunicationCellState] = {}
        predictions: list[dict[str, object]] = []
        for node_id in sorted(self.drones):
            node = self.drones[node_id]
            for record in node.comm_belief.snapshot():
                state = CommunicationCellState.model_validate(record)
                current = merged.get(state.cell)
                if current is None or (state.updated_at, state.weight) > (current.updated_at, current.weight):
                    merged[state.cell] = state
            predictions.extend(item.model_dump(mode="json") for item in node.active_predictions)
        broker = self.broker
        edge = EdgeComputeState(
            enabled=adaptive.edge_compute.enabled,
            edge_nodes=broker.edge_nodes,
            profiles=[
                broker.profiles[node_id].model_dump(mode="json") for node_id in broker.edge_nodes
            ],
            units_completed=len(broker.completed),
            executed_remotely=broker.executed_remotely,
            executed_locally=broker.executed_locally,
            reassigned=broker.reassigned,
            orphaned=list(broker.orphaned),
            failures=broker.failures,
            recent_units=[
                {
                    "unit_id": item.unit_id,
                    "kind": item.kind.value,
                    "executed_by": item.executed_by,
                    "digest": item.digest,
                }
                for item in broker.completed[-5:]
            ],
        )
        return AdaptiveRuntimeState(
            adaptive_messaging=self.config.network.adaptive_messaging,
            communication_belief=adaptive.communication.enabled,
            communication_aware_routing=adaptive.communication_aware_routing,
            predictive_recovery=adaptive.prediction.enabled,
            counterfactual_selection=adaptive.counterfactual.enabled,
            multi_relay_coordination=adaptive.multi_relay_coordination,
            communication_map=[merged[key] for key in sorted(merged)],
            predictions=predictions,
            decisions=[item.model_dump(mode="json") for item in self.explanations.recent(8)],
            relay_stations=(
                self.resilience.last_plan.to_records() if self.resilience.last_plan else []
            ),
            counterfactual_runs=self.resilience.counterfactual_runs,
            preemptive_relay_triggers=self.resilience.preemptive_triggers,
            preemptive_handoffs=self._preemptive_handoffs,
            communication_route_changes=self._route_changes,
            edge_compute=edge,
        )

    def attach_edge_node(self, profile: EdgeResourceProfile, executor: WorkExecutor) -> None:
        """Register an optional compute contributor; core execution is unaffected."""

        self.broker.join(profile, executor)
        self.events.emit(
            self.time, EventCategory.AUTONOMY, EventType.EDGE_NODE_JOINED, profile.node_id,
            f"{profile.node_id} joined as an edge compute node "
            f"(cpu {profile.cpu_score:.1f}, gpu={'yes' if profile.gpu_available else 'no'})",
            [profile.node_id], profile.model_dump(mode="json"),
        )

    def detach_edge_node(self, node_id: str) -> list[str]:
        orphans = self.broker.leave(node_id)
        self.events.emit(
            self.time, EventCategory.FAILURE, EventType.EDGE_NODE_LEFT, node_id,
            f"{node_id} left the edge compute pool; {len(orphans)} work unit(s) are retryable",
            [node_id], {"orphaned": orphans},
        )
        return orphans

    def connect_edge_worker(self, uri: str, node_id: str | None = None) -> EdgeResourceProfile:
        """Attach a WebSocket edge worker announced by ``simulation.edge``."""

        from .worker import WebSocketEdgeClient

        client = WebSocketEdgeClient(uri, expected_node_id=node_id)
        self.attach_edge_node(client.profile, client)
        return client.profile

    def _collect_adaptive_signals(self) -> None:
        """Surface node-level adaptive decisions as engine events, once each."""

        for node_id in sorted(self.drones):
            node = self.drones[node_id]
            for request in node.handoff_requests:
                key = f"{node_id}:{request['task_id']}"
                if key in self._seen_handoffs:
                    continue
                self._seen_handoffs.add(key)
                self._preemptive_handoffs += 1
                explanation = request.get("explanation") or {}
                if explanation:
                    self.explanations.add(DecisionExplanation.model_validate(explanation))
                self.events.emit(
                    self.time, EventCategory.AUTONOMY, EventType.PREEMPTIVE_HANDOFF, node_id,
                    f"{node_id} released {request['task_id']} before hitting battery reserve",
                    [node_id, request["task_id"]], request,
                )
            for prediction in node.active_predictions:
                if node.predictions.should_alert(f"event:{prediction.kind}", self.time):
                    self.events.emit(
                        self.time, EventCategory.AUTONOMY, EventType.PREDICTION_EMITTED, node_id,
                        f"{node_id}: {prediction.explanation}",
                        [node_id], prediction.model_dump(mode="json"),
                    )
            plan = node.plan
            if plan is not None:
                choice = plan.metadata.get("route_choice")
                if isinstance(choice, dict) and choice.get("changed"):
                    signature = f"{node_id}:{plan.task_id}:{choice.get('selected')}"
                    if signature not in self._seen_route_choices:
                        self._seen_route_choices.add(signature)
                        self._route_changes += 1
                        self.events.emit(
                            self.time, EventCategory.AUTONOMY, EventType.COMMUNICATION_AWARE_ROUTE, node_id,
                            f"{node_id}: {choice.get('explanation', 'communication-aware route selected')}",
                            [node_id], choice,
                        )

    def snapshot(self, event_limit: int = 30) -> SimulationSnapshot:
        drones: list[DronePublicState] = []
        for node_id, node in sorted(self.drones.items()):
            truth = self.world.truth(node_id)
            public_state = node.state
            if not truth.online:
                public_state = node.state
            drones.append(
                DronePublicState(
                    identity=node.identity,
                    state=public_state,
                    estimated=node.estimated.model_copy(deep=True),
                    truth=truth,
                    current_task_id=node.current_task.id if node.current_task else None,
                    task_queue=[task.id for task in node.task_queue],
                    task_progress=node.task_progress,
                    peers={key: value.model_copy(deep=True) for key, value in node.peers.items()},
                    current_plan=[
                        Vector3(x=point.x, y=point.y, z=point.z)
                        for point in (node.plan.waypoints if node.plan else [])
                    ],
                    role="RELAY" if node.current_task and node.current_task.type == TaskType.RELAY else "MISSION",
                    local_mission_revision=node._mission_revision,
                    observation_count=len(node.world_belief.observations),
                    known_cells={
                        region_id: sum(1 for cell in grid.cells.values() if cell.last_observed is not None)
                        for region_id, grid in node.world_belief.grids.items()
                    },
                    last_world_reconciliation=node.world_belief.last_reconciled_at,
                    policy=node.last_policy_result,
                    adaptive=self._node_adaptive_state(node),
                )
            )
        network_metrics = self.network.metrics(self.time)
        network_metrics.degraded_nodes = sum(1 for drone in drones if drone.state == "DEGRADED")
        network_metrics.relay_nodes = [drone.identity.node_id for drone in drones if drone.role == "RELAY"]
        network_metrics.gps_degraded_count = sum(
            1 for drone in drones if drone.estimated.localization_mode != LocalizationMode.GPS
        )
        aggregate = self._aggregate_belief()
        regions = [
            grid.metrics(self.time, self.config.sensing.freshness_half_life_seconds)
            for grid in aggregate.grids.values()
        ]
        observations = list(aggregate.domain_latest.values())
        sync_lag = 0.0
        for node in self.drones.values():
            if not node.online:
                continue
            for domain_key, latest in aggregate.domain_latest.items():
                local = node.world_belief.domain_latest.get(domain_key)
                sync_lag = max(sync_lag, latest.timestamp - (local.timestamp if local else 0.0))
        duplicate_count = 0
        for task_id in self.missions.tasks:
            executing = sum(
                1 for node in self.drones.values()
                if node.online and node.current_task is not None and node.current_task.id == task_id
            )
            desired = self.missions.tasks[task_id].desired_units
            duplicate_count += max(0, executing - desired)
        world_knowledge = WorldKnowledgeMetrics(
            regions=regions,
            observation_count=len(aggregate.observations),
            mean_observation_confidence=(
                sum(item.confidence for item in observations) / len(observations) if observations else 0.0
            ),
            world_model_sync_lag=max(0.0, sync_lag),
            duplicate_task_execution_count=duplicate_count,
        )
        return SimulationSnapshot(
            simulation_time=self.time,
            running=self.running,
            scenario=self.scenario,
            drones=drones,
            missions=[task.model_copy(deep=True) for task in self.missions.tasks.values()],
            links=self.network.links(self.time),
            interference=self.interference.config.model_copy(deep=True),
            mission_capability=self.missions.overall_capability,
            mission_effectiveness=self.missions.overall_effectiveness,
            world_knowledge=world_knowledge,
            network=network_metrics,
            control_available=self.missions.control_available,
            adaptive=self._adaptive_state(),
            events=self.events.recent(event_limit),
        )

    def _online_node_ids(self) -> list[str]:
        return [node_id for node_id in self.world.node_ids if self.world.is_online(node_id)]


def default_watch_mission() -> MissionCommand:
    return MissionCommand(
        type=TaskType.WATCH,
        target=MissionTarget(region_id="ALPHA"),
        priority=80,
        required_capabilities={"camera"},
        desired_units=2,
        minimum_units=1,
    )


def run_until(engine: SimulationEngine, predicate: Callable[[], bool], max_steps: int = 1000) -> None:
    """Small test/demo helper retained here to use the same deterministic clock."""
    for _ in range(max_steps):
        if predicate():
            return
        engine.tick()
    raise TimeoutError("simulation condition was not reached")
