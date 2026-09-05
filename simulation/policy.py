"""Small deterministic node-local safety and mission-constraint policy."""

from __future__ import annotations

from .models import LocalEstimatedState, MissionTask, PolicyResult, TaskType, Vector3


class LocalPolicyEngine:
    def __init__(self, minimum_battery_reserve: float = 0.15, maximum_motion_uncertainty_m: float = 35.0) -> None:
        self.minimum_battery_reserve = minimum_battery_reserve
        self.maximum_motion_uncertainty_m = maximum_motion_uncertainty_m

    def evaluate(self, state: LocalEstimatedState, task: MissionTask, home: Vector3) -> PolicyResult:
        reasons: list[str] = []
        warnings: list[str] = []
        moving = task.type not in {TaskType.HOLD, TaskType.WATCH}
        if moving and state.position_uncertainty > self.maximum_motion_uncertainty_m:
            reasons.append("POSITION_UNCERTAINTY_TOO_HIGH")
        if task.type != TaskType.RETURN and state.battery_estimate <= self.minimum_battery_reserve:
            reasons.append("BATTERY_RESERVE_REQUIRED")
        elif task.type != TaskType.RETURN and state.battery_estimate <= self.minimum_battery_reserve + 0.10:
            warnings.append("BATTERY_RESERVE_APPROACHING")
        return PolicyResult(
            allowed=not reasons,
            reason_codes=reasons,
            warnings=warnings,
            recommended_action="RETURN" if "BATTERY_RESERVE_REQUIRED" in reasons else "HOLD" if reasons else None,
        )
