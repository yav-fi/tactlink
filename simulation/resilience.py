"""Topology observer that turns network degradation into a physical relay task."""

from __future__ import annotations

from .events import EventBus
from .models import EventCategory, EventType, MissionCommand, MissionTarget, MissionTask, TaskStatus, TaskType, Vector3
from .network import NetworkSimulator
from .world import World


class NetworkResilienceManager:
    """Small, explainable relay policy; it never edits link quality directly."""

    def __init__(self, events: EventBus, health_threshold: float, evaluation_seconds: float) -> None:
        self.events = events
        self.health_threshold = health_threshold
        self.evaluation_seconds = evaluation_seconds
        self.active_task_id: str | None = None
        self._last_evaluation = -evaluation_seconds
        self._state = "HEALTHY"
        self._baseline_health = 1.0
        self._relay_established = False
        self._relay_repositioning = False

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
            if not self._relay_established and active.assigned_nodes:
                relay_id = active.assigned_nodes[0]
                target = active.target.point
                if target and not self._relay_repositioning:
                    self._relay_repositioning = True
                    self.events.emit(
                        now, EventCategory.NETWORK, EventType.RELAY_REPOSITIONING, relay_id,
                        f"{relay_id} is physically repositioning to repair the network",
                        [relay_id, active.id],
                        {"target": target.model_dump(mode="json"), "network_health": metrics.network_health},
                    )
                if target and world.is_online(relay_id) and world.truth(relay_id).position.distance_to(target) <= 10.0:
                    self._relay_established = True
                    self.events.emit(
                        now, EventCategory.NETWORK, EventType.RELAY_ESTABLISHED, relay_id,
                        f"{relay_id} reached the relay station; link health is now {metrics.network_health:.0%}",
                        [relay_id, active.id],
                        {"network_health": metrics.network_health, "baseline_health": self._baseline_health},
                    )
            return None

        eligible = sorted(node for node in relay_nodes if world.is_online(node))
        if not unhealthy or not eligible or len(strong_components) < 2:
            return None
        first, second = strong_components[0], strong_components[1]
        first_center = self._centroid(world, first)
        second_center = self._centroid(world, second)
        target = Vector3(
            x=(first_center.x + second_center.x) / 2.0,
            y=(first_center.y + second_center.y) / 2.0,
            z=max(30.0, (first_center.z + second_center.z) / 2.0),
        )
        target = self._free_relay_target(world, target)
        threatened_priority = max(
            (task.priority for task in tasks if task.type != TaskType.RELAY and task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}),
            default=70,
        )
        self._baseline_health = metrics.network_health
        self._relay_established = False
        self._relay_repositioning = False
        return MissionCommand(
            type=TaskType.RELAY,
            target=MissionTarget(point=target),
            priority=min(100, threatened_priority + 1),
            required_capabilities={"relay"},
            desired_units=1,
            minimum_units=1,
            metadata={
                "network_support": True,
                "components": strong_components,
                "baseline_network_health": round(metrics.network_health, 4),
                "policy": "midpoint-between-two-largest-weak-components",
            },
        )

    def task_created(self, task: MissionTask, now: float) -> None:
        self.active_task_id = task.id
        self.events.emit(
            now, EventCategory.NETWORK, EventType.RELAY_TASK_CREATED, "network-resilience",
            f"Created relay task {task.id} at the midpoint of weak network groups",
            [task.id], {"target": task.target.model_dump(mode="json"), **task.metadata},
        )

    @staticmethod
    def _centroid(world: World, nodes: list[str]) -> Vector3:
        positions = [world.truth(node).position for node in nodes]
        return Vector3(
            x=sum(point.x for point in positions) / len(positions),
            y=sum(point.y for point in positions) / len(positions),
            z=sum(point.z for point in positions) / len(positions),
        )

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
