"""Translate deliberate discrete gesture events into canonical missions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simulation.models import MissionCommand, MissionTarget, TaskType, Vector3


@dataclass
class GestureMissionContext:
    point: Vector3 | None = None
    waypoints: list[Vector3] = field(default_factory=list)
    region_id: str | None = None
    entity_id: str | None = None
    priority: int = 80
    required_capabilities: set[str] = field(default_factory=set)
    desired_units: int = 1
    minimum_units: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


class GestureMissionAdapter:
    """Maps gesture labels or interpreted action events; analog axes stay local."""

    DEFAULT_ACTIONS = {
        "Closed_Fist": "land",
        "ILoveYou": "return_home",
    }
    ACTION_TYPES = {
        "land": TaskType.HOLD,
        "estop": TaskType.HOLD,
        "hold": TaskType.HOLD,
        "return_home": TaskType.RETURN,
        "return": TaskType.RETURN,
        "regroup": TaskType.REGROUP,
        "goto": TaskType.GOTO,
        "watch": TaskType.WATCH,
        "search": TaskType.SEARCH,
        "trace": TaskType.TRACE,
        "follow": TaskType.FOLLOW,
    }

    def __init__(self, action_file: str | Path | None = None) -> None:
        default_file = Path(__file__).resolve().parents[1] / "config" / "gesture_actions.json"
        self.gesture_actions = dict(self.DEFAULT_ACTIONS)
        path = Path(action_file) if action_file else default_file
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.gesture_actions.update(
                {str(key): str(value) for key, value in payload.items() if not str(key).startswith("_")}
            )
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, ValueError, TypeError):
            # GestureInterpreter reports malformed config; the mission bridge simply
            # keeps its safe built-in mappings instead of creating a command.
            self.gesture_actions = dict(self.DEFAULT_ACTIONS)

    def event_to_command(
        self,
        event: str,
        context: GestureMissionContext | None = None,
    ) -> MissionCommand | None:
        context = context or GestureMissionContext()
        action = self.gesture_actions.get(event, event)
        if action.lower().startswith("mission:"):
            action = action.split(":", 1)[1]
        task_type = self.ACTION_TYPES.get(action.lower())
        if task_type is None:
            return None
        target = MissionTarget(
            point=context.point,
            waypoints=context.waypoints,
            region_id=context.region_id,
            entity_id=context.entity_id,
        )
        if task_type in {TaskType.GOTO, TaskType.WATCH, TaskType.REGROUP} and not (
            target.point or target.region_id
        ):
            return None
        if task_type == TaskType.SEARCH and not (target.region_id or target.waypoints):
            return None
        if task_type == TaskType.TRACE and not target.waypoints:
            return None
        if task_type == TaskType.FOLLOW and not target.entity_id:
            return None
        metadata = {"input_source": "gesture", "gesture_event": event, **context.metadata}
        return MissionCommand(
            type=task_type,
            target=target,
            priority=context.priority,
            required_capabilities=context.required_capabilities,
            desired_units=context.desired_units,
            minimum_units=context.minimum_units,
            metadata=metadata,
        )
