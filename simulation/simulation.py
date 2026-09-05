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
from .interference import InterferenceEngine
from .mission import MissionManager
from .models import (
    DronePublicState,
    EventCategory,
    EventType,
    GPSMeasurement,
    InterferenceConfig,
    LocalizationMode,
    MissionCommand,
    MissionTask,
    MissionTarget,
    NodeIdentity,
    ScenarioEvent,
    SimulationSnapshot,
    TaskType,
    Vector3,
)
from .network import NetworkSimulator
from .scenarios import PRESET_INTERFERENCE, ScenarioEngine, ScenarioPreset
from .world import BoxObstacle, MovingEntity, Region, World, WorldDefinition


class SimulationEngine:
    def __init__(
        self,
        config: SimulationConfig | None = None,
        world_definition: WorldDefinition | None = None,
        scenario: ScenarioPreset | str = ScenarioPreset.NORMAL,
        drone_count: int = 3,
    ) -> None:
        self.config = config or SimulationConfig()
        self.scenario = ScenarioPreset(scenario)
        self.time = 0.0
        self.running = True
        self.events = EventBus(self.config.recent_event_limit)
        definition = world_definition or self.default_world_definition()
        self.world = World(definition, self.config.battery_drain_per_meter)
        # Built once from the STATIC world definition; nodes never see World truth.
        self.autonomy = PlanningAutonomy.from_world_definition(definition)
        self.interference = InterferenceEngine(PRESET_INTERFERENCE[self.scenario].model_copy(deep=True))
        self.network = NetworkSimulator(
            self.config.network,
            random.Random(self.config.seed + 101),
            self.interference,
            self.events,
        )
        self.scenario_engine = ScenarioEngine(self.scenario, random.Random(self.config.seed + 202))
        self._measurement_rng = random.Random(self.config.seed + 303)
        self._control_rng = random.Random(self.config.seed + 404)
        self._failure_rng = random.Random(self.config.seed + 505)
        self._failure_counter = 0
        self.allocator = TaskAllocator(self.config.allocator)
        self.missions = MissionManager(self.allocator, self.events, self.config.peer_timeout)
        self.drones: dict[str, DroneNode] = {}
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
                    metadata={"airframe": "simulated-multirotor"},
                ),
                position,
                self.config.localization,
                self.config.default_speed_mps,
                self.config.heartbeat_interval,
                self.config.status_interval,
                self.config.peer_timeout,
                autonomy=self.autonomy,
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
        return self.missions.submit_mission(command, self.time)

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
            for message in self.network.receive(node_id):
                for reply in node.handle_message(message, self.time):
                    self.network.send(reply, self.time)
            old_mode = node.estimated.localization_mode
            measurement = self._gps_measurement(node_id)
            _, new_mode = node.update_localization(measurement, step)
            node.update_battery_measurement(self.world.truth(node_id).actual_battery)
            sensor_noise = self.interference.sensor_severity(node_id)
            node.update_sensor_confidence(max(0.05, 1.0 - sensor_noise))
            for entity in self.world.definition.entities:
                noise = sensor_noise * 12.0
                node.update_entity_measurement(
                    entity.id,
                    Vector3(
                        x=entity.position.x + self._measurement_rng.gauss(0, noise),
                        y=entity.position.y + self._measurement_rng.gauss(0, noise),
                        z=entity.position.z + self._measurement_rng.gauss(0, noise * 0.25),
                    ),
                )
            self._emit_localization_transition(node_id, old_mode, new_mode)
            intent, outgoing, timed_out = node.tick(self.time, step)
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
            self.missions.handle_message(message, self.time)

        # Replans already pending run first. New timeout-triggered replans wait one tick,
        # preserving an observable degraded-capability state.
        for assignment in self.missions.allocate(self.time):
            self.network.send(assignment, self.time)
        self.missions.detect_timeouts(self.time)
        self.missions.update_capability(self.time)
        return self.snapshot()

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
        self.__init__(config=config, scenario=scenario, drone_count=count)
        self.events.emit(
            self.time,
            EventCategory.SIMULATION,
            EventType.SIMULATION_RESET,
            "operator",
            "Simulation reset to deterministic initial state",
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
                )
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
