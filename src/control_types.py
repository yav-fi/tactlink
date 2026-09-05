"""Shared data types passed between the tracker, controller, and simulator."""

from dataclasses import dataclass, field
from enum import Enum


class FlightMode(Enum):
    """What the moving hand controls. Cycled with the Victory sign."""

    HEADING = "heading"    # palm x -> yaw, palm y -> throttle, tilt -> roll
    POSITION = "position"  # palm x -> roll, palm y -> throttle, tilt -> yaw
    HOVER = "hover"        # translation locked; only palm y -> throttle


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
    # Direction the index + middle fingers point, as an image-space unit vector
    # (x right, y down). (0, 0) when it can't be measured.
    point_dir: tuple = (0.0, 0.0)
    gesture: str = "None"    # active gesture name (custom model or MediaPipe canned), or "None"
    gesture_score: float = 0.0
    gesture_source: str = "none"  # "custom", "canned", or "none"


@dataclass
class GestureState:
    """Discrete decisions distilled from the gesture stream for one frame."""

    mode: FlightMode = FlightMode.HEADING
    speed_name: str = "normal"
    speed_scale: float = 1.0
    active_gesture: str = "None"   # gesture currently being held (may not have fired yet)
    gesture_source: str = "none"   # "custom", "canned", or "none"
    hold_progress: float = 0.0     # 0..1 toward firing the held gesture
    sequence_hint: str = ""        # partial combo in progress, e.g. "Open_Palm>Closed_Fist>…"
    events: list = field(default_factory=list)  # command strings fired this frame
    maneuver: str = ""             # autopilot routine currently running, if any


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
