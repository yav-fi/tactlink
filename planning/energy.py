"""Deliberately simple energy feasibility model.

distance -> battery fraction, plus a reserve that must survive the trip home.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import PlanningConfig, Vector3


@dataclass
class EnergyAssessment:
    feasible: bool
    required: float
    return_cost: float
    reserve: float
    remaining_after: float
    recommend_return: bool = False
    truncated_at: int | None = None
    affordable_waypoints: list[Vector3] = field(default_factory=list)


def cost_for_distance(distance: float, config: PlanningConfig) -> float:
    return max(0.0, distance) * config.battery_per_meter


def assess_route(
    start: Vector3,
    waypoints: list[Vector3],
    home: Vector3 | None,
    battery: float,
    config: PlanningConfig,
) -> EnergyAssessment:
    """Walk the route and keep the longest prefix that still leaves reserve.

    Returning fewer waypoints is much more useful than a hard rejection: the
    drone still makes progress and the caller sees ``truncated_at``.
    """

    reserve = config.battery_reserve
    budget = battery - reserve
    home_reference = home if home is not None else start
    immediate_return = cost_for_distance(start.distance_to(home_reference), config)

    if budget <= 0.0 or budget < immediate_return:
        return EnergyAssessment(
            feasible=False,
            required=0.0,
            return_cost=immediate_return,
            reserve=reserve,
            remaining_after=battery - immediate_return,
            recommend_return=True,
            truncated_at=0,
            affordable_waypoints=[],
        )

    affordable: list[Vector3] = []
    travelled = 0.0
    cursor = start
    truncated_at: int | None = None
    for index, waypoint in enumerate(waypoints):
        leg = cursor.distance_to(waypoint)
        prospective = travelled + leg
        cost = cost_for_distance(prospective, config)
        home_cost = cost_for_distance(waypoint.distance_to(home_reference), config)
        if cost + home_cost > budget:
            truncated_at = index
            break
        affordable.append(waypoint)
        travelled = prospective
        cursor = waypoint

    required = cost_for_distance(travelled, config)
    return_cost = cost_for_distance(cursor.distance_to(home_reference), config)
    return EnergyAssessment(
        feasible=truncated_at is None,
        required=required,
        return_cost=return_cost,
        reserve=reserve,
        remaining_after=battery - required - return_cost,
        recommend_return=not affordable,
        truncated_at=truncated_at,
        affordable_waypoints=affordable,
    )
