"""Shared data types passed between the tracker, controller, and simulator."""

from dataclasses import dataclass


@dataclass
class HandState:
    """Result of reading one webcam frame with the hand tracker.

    All positions are normalized to the frame: x, y in 0..1 with the origin at
    the top-left. ``present`` is False when no hand is visible; every other field
    should be treated as stale in that case.
    """

    present: bool = False
    palm_x: float = 0.5
    palm_y: float = 0.5
    roll_angle: float = 0.0  # radians, hand tilt about the view axis (+ = right side down)
    pinch: float = 0.0       # 0 = fingers apart, 1 = thumb and index touching
    fingers_up: int = 0      # count of extended fingers, 0..5


@dataclass
class ControlInput:
    """Normalized flight command produced from a HandState.

    Each axis is clamped to -1..1 (throttle too: -1 = descend, +1 = climb).
    """

    throttle: float = 0.0
    yaw_rate: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    armed: bool = False
    # Human-readable note about the most recent gesture event, for the HUD.
    event: str = ""
