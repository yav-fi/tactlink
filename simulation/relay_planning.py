"""Multi-relay placement: distinct, non-redundant stations for weak clusters.

The single-relay scoring curve is unchanged (connectivity bottleneck first,
then balance, then travel), so an existing two-cluster split still resolves to
the same station.  What is new is what happens with *more than one* relay-capable
vehicle: each additional station has to justify itself with marginal
contribution the already-chosen stations do not provide, and the vehicles are
matched to stations by travel, battery, and the priority of whatever they would
have to abandon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Vector3

# Fractions along the centroid-to-centroid line that stations are sampled at.
_FRACTIONS: tuple[float, ...] = (0.30, 0.40, 0.50, 0.60, 0.70)
# Two stations closer than this are considered the same station.
MINIMUM_STATION_SEPARATION_M = 15.0


@dataclass(frozen=True)
class ClusterPair:
    """Two groups of nodes that can no longer talk at usable quality."""

    primary: tuple[str, ...]
    secondary: tuple[str, ...]
    primary_centroid: Vector3
    secondary_centroid: Vector3

    @property
    def key(self) -> str:
        return f"{'+'.join(self.primary)}|{'+'.join(self.secondary)}"


@dataclass
class RelayStation:
    """A scored candidate station bound to the pair it would reconnect."""

    pair_key: str
    point: Vector3
    bottleneck_quality: float
    mean_quality: float
    travel_m: float
    score: float
    marginal_gain: float = 0.0
    redundancy_penalty: float = 0.0
    assigned_to: str | None = None
    assignment_cost: float = 0.0
    rationale: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "pair": self.pair_key,
            "point": self.point.model_dump(mode="json"),
            "bottleneck_quality": round(self.bottleneck_quality, 5),
            "mean_quality": round(self.mean_quality, 5),
            "travel_m": round(self.travel_m, 2),
            "score": round(self.score, 5),
            "marginal_gain": round(self.marginal_gain, 5),
            "redundancy_penalty": round(self.redundancy_penalty, 5),
            "assigned_to": self.assigned_to,
            "assignment_cost": round(self.assignment_cost, 4),
            "rationale": self.rationale,
        }


@dataclass
class RelayVehicle:
    """A relay-capable vehicle and what taking the job would cost it."""

    node_id: str
    position: Vector3
    battery: float = 1.0
    current_task_priority: int = 0

    def cost_for(self, station: RelayStation) -> float:
        travel = self.position.distance_to(station.point)
        battery_penalty = (1.0 - max(0.0, min(1.0, self.battery))) * 250.0
        opportunity = self.current_task_priority * 1.5
        return travel + battery_penalty + opportunity


@dataclass
class RelayPlan:
    """The chosen set of stations plus everything needed to explain it."""

    stations: list[RelayStation] = field(default_factory=list)
    considered: list[RelayStation] = field(default_factory=list)
    rejected_redundant: int = 0

    @property
    def targets(self) -> list[Vector3]:
        return [station.point for station in self.stations]

    @property
    def assignments(self) -> dict[str, dict[str, Any]]:
        return {
            station.assigned_to: station.point.model_dump(mode="json")
            for station in self.stations
            if station.assigned_to
        }

    def to_records(self) -> list[dict[str, Any]]:
        return [station.to_record() for station in self.stations]


class RelayPlanner:
    """Deterministic candidate generation, scoring, and vehicle matching."""

    def __init__(
        self,
        reference_range_m: float,
        falloff_power: float,
        hard_range_m: float,
        free_point: Any = None,
    ) -> None:
        self.reference_range_m = reference_range_m
        self.falloff_power = falloff_power
        self.hard_range_m = hard_range_m
        # Callable that lifts a station above known static obstacles.
        self._free_point = free_point or (lambda point: point)

    def quality(self, distance: float) -> float:
        if distance >= self.hard_range_m:
            return 0.0
        return 1.0 / (1.0 + (distance / self.reference_range_m) ** self.falloff_power)

    def candidates_for(self, pair: ClusterPair, vehicles: list[RelayVehicle]) -> list[RelayStation]:
        left, right = pair.primary_centroid, pair.secondary_centroid
        points = [
            self._free_point(
                Vector3(
                    x=left.x + (right.x - left.x) * fraction,
                    y=left.y + (right.y - left.y) * fraction,
                    z=max(30.0, left.z + (right.z - left.z) * fraction),
                )
            )
            for fraction in _FRACTIONS
        ]
        stations: list[RelayStation] = []
        for point in points:
            left_quality = self.quality(point.distance_to(left))
            right_quality = self.quality(point.distance_to(right))
            travel = min(
                (vehicle.position.distance_to(point) for vehicle in vehicles),
                default=0.0,
            )
            bottleneck = min(left_quality, right_quality)
            score = 2.0 * bottleneck + left_quality + right_quality - travel / 1000.0
            stations.append(
                RelayStation(
                    pair_key=pair.key,
                    point=point,
                    bottleneck_quality=bottleneck,
                    mean_quality=(left_quality + right_quality) / 2.0,
                    travel_m=travel,
                    score=score,
                )
            )
        # Same ordering the single-relay policy has always used.
        stations.sort(key=lambda item: (-item.score, item.travel_m, -item.point.x, -item.point.y))
        return stations

    def plan(
        self,
        pairs: list[ClusterPair],
        vehicles: list[RelayVehicle],
        maximum_stations: int | None = None,
    ) -> RelayPlan:
        """Choose at most one useful, non-redundant station per weak pair."""

        plan = RelayPlan()
        if not pairs or not vehicles:
            return plan
        limit = min(len(vehicles), maximum_stations if maximum_stations is not None else len(pairs))
        best_by_pair: list[RelayStation] = []
        for pair in pairs:
            options = self.candidates_for(pair, vehicles)
            if options:
                plan.considered.extend(options[:1])
                best_by_pair.append(options[0])
        # Bridge the most-broken pair first; a station that helps a cluster that
        # is already nearly reachable is worth less than one that is not.
        best_by_pair.sort(key=lambda item: (-item.score, item.pair_key))

        chosen: list[RelayStation] = []
        for station in best_by_pair:
            if len(chosen) >= limit:
                break
            overlap = [
                existing
                for existing in chosen
                if existing.point.distance_to(station.point) < MINIMUM_STATION_SEPARATION_M
            ]
            if overlap:
                plan.rejected_redundant += 1
                continue
            nearest = min(
                (existing.point.distance_to(station.point) for existing in chosen),
                default=None,
            )
            # Coverage this station adds beyond what is already planned.
            redundancy = 0.0 if nearest is None else self.quality(nearest)
            station.redundancy_penalty = round(redundancy, 5)
            station.marginal_gain = round(max(0.0, station.bottleneck_quality - redundancy * 0.5), 5)
            if chosen and station.marginal_gain <= 0.0:
                plan.rejected_redundant += 1
                continue
            station.rationale = (
                f"bridges {station.pair_key} at bottleneck quality "
                f"{station.bottleneck_quality:.2f}; marginal gain {station.marginal_gain:.2f}"
            )
            chosen.append(station)

        plan.stations = self._assign(chosen, vehicles)
        return plan

    @staticmethod
    def _assign(stations: list[RelayStation], vehicles: list[RelayVehicle]) -> list[RelayStation]:
        """Greedy, deterministic vehicle-to-station matching on total cost."""

        available = {vehicle.node_id: vehicle for vehicle in vehicles}
        pairs = sorted(
            (
                (vehicle.cost_for(station), station.pair_key, vehicle.node_id, index)
                for index, station in enumerate(stations)
                for vehicle in vehicles
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
        taken_station: set[int] = set()
        for cost, _, node_id, index in pairs:
            if index in taken_station or node_id not in available:
                continue
            stations[index].assigned_to = node_id
            stations[index].assignment_cost = cost
            taken_station.add(index)
            del available[node_id]
        return [station for station in stations if station.assigned_to is not None]


def centroid(points: list[Vector3]) -> Vector3:
    return Vector3(
        x=sum(point.x for point in points) / len(points),
        y=sum(point.y for point in points) / len(points),
        z=sum(point.z for point in points) / len(points),
    )
