"""Ground-truth world and simple point-mass motion.

DroneNode never receives a World reference. This module is the sole owner of true
positions, health, and battery values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2

from pydantic import BaseModel, Field

from .models import DroneTruthState, MotionIntent, Vector3


class BoxObstacle(BaseModel):
    id: str
    minimum: Vector3
    maximum: Vector3


class CircleObstacle(BaseModel):
    id: str
    center: Vector3
    radius: float
    height: float


class Region(BaseModel):
    id: str
    center: Vector3
    radius: float


class MovingEntity(BaseModel):
    id: str
    position: Vector3
    velocity: Vector3 = Vector3()


class WorldDefinition(BaseModel):
    minimum: Vector3 = Vector3(x=-500, y=-500, z=0)
    maximum: Vector3 = Vector3(x=500, y=500, z=150)
    boxes: list[BoxObstacle] = Field(default_factory=list)
    circles: list[CircleObstacle] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)
    entities: list[MovingEntity] = Field(default_factory=list)


@dataclass
class _WorldDrone:
    node_id: str
    position: Vector3
    home: Vector3
    velocity: Vector3 = field(default_factory=Vector3)
    heading: float = 0.0
    health: float = 1.0
    battery: float = 1.0
    online: bool = True
    maximum_speed: float = 12.0
    intent: MotionIntent = field(default_factory=MotionIntent)


class World:
    def __init__(self, definition: WorldDefinition, battery_drain_per_meter: float = 0.00008) -> None:
        self.definition = definition
        self._drones: dict[str, _WorldDrone] = {}
        self._battery_drain_per_meter = battery_drain_per_meter

    def add_drone(
        self,
        node_id: str,
        position: Vector3,
        battery: float = 1.0,
        maximum_speed: float = 12.0,
    ) -> None:
        self._drones[node_id] = _WorldDrone(
            node_id=node_id,
            position=position.model_copy(deep=True),
            home=position.model_copy(deep=True),
            battery=battery,
            maximum_speed=maximum_speed,
        )

    def set_motion_intent(self, node_id: str, intent: MotionIntent) -> None:
        self._drones[node_id].intent = intent

    def update(self, dt: float) -> None:
        for drone in self._drones.values():
            if not drone.online:
                drone.velocity = Vector3()
                continue
            target = drone.intent.target
            if drone.intent.hold or target is None:
                drone.velocity = Vector3()
                continue
            distance = drone.position.distance_to(target)
            if distance <= 1e-9:
                drone.velocity = Vector3()
                continue
            speed = min(drone.maximum_speed, drone.intent.maximum_speed, distance / dt)
            drone.velocity = Vector3(
                x=(target.x - drone.position.x) / distance * speed,
                y=(target.y - drone.position.y) / distance * speed,
                z=(target.z - drone.position.z) / distance * speed,
            )
            drone.position = self._clamp(drone.position.moved(drone.velocity, dt))
            drone.heading = atan2(drone.velocity.y, drone.velocity.x)
            drone.battery = max(0.0, drone.battery - speed * dt * self._battery_drain_per_meter)
            if drone.battery == 0.0:
                drone.online = False

        for entity in self.definition.entities:
            entity.position = self._clamp(entity.position.moved(entity.velocity, dt))

    def _clamp(self, point: Vector3) -> Vector3:
        low, high = self.definition.minimum, self.definition.maximum
        return Vector3(
            x=min(high.x, max(low.x, point.x)),
            y=min(high.y, max(low.y, point.y)),
            z=min(high.z, max(low.z, point.z)),
        )

    def truth(self, node_id: str) -> DroneTruthState:
        drone = self._drones[node_id]
        return DroneTruthState(
            node_id=node_id,
            position=drone.position.model_copy(deep=True),
            velocity=drone.velocity.model_copy(deep=True),
            heading=drone.heading,
            actual_health=drone.health,
            actual_battery=drone.battery,
            online=drone.online,
        )

    def all_truth(self) -> list[DroneTruthState]:
        return [self.truth(node_id) for node_id in sorted(self._drones)]

    def home(self, node_id: str) -> Vector3:
        return self._drones[node_id].home.model_copy(deep=True)

    def entity_position(self, entity_id: str) -> Vector3 | None:
        entity = next((item for item in self.definition.entities if item.id == entity_id), None)
        return entity.position.model_copy(deep=True) if entity else None

    def fail(self, node_id: str) -> None:
        drone = self._drones[node_id]
        drone.online = False
        drone.velocity = Vector3()

    def recover(self, node_id: str) -> None:
        self._drones[node_id].online = True

    def is_online(self, node_id: str) -> bool:
        return self._drones[node_id].online

    @property
    def node_ids(self) -> list[str]:
        return sorted(self._drones)
