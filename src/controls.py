"""Translate a HandState into a normalized ControlInput.

Mapping (right hand, palm toward the camera):

* palm left / right in the frame -> yaw left / right
* palm up / down in the frame    -> climb / descend (throttle)
* tilt the hand left / right      -> roll (bank and slide sideways)
* pinch thumb + index            -> pitch forward (fly forward)
* open palm (>=4 fingers)         -> ARM / take off
* closed fist (0 fingers)         -> DISARM / land

A centered dead zone keeps the drone steady when the hand is roughly neutral.
"""

import math

from control_types import ControlInput, HandState

_DEAD_ZONE = 0.12          # normalized frame distance treated as "centered"
_ROLL_DEAD_ZONE = 0.18     # radians of hand tilt ignored around level
_ROLL_FULL_SCALE = 0.7     # hand tilt (radians) that maps to full roll
_SMOOTHING = 0.35          # 0..1, higher reacts faster


def _axis(value: float, center: float, dead: float) -> float:
    """Map ``value`` around ``center`` to -1..1 with a dead zone."""
    delta = value - center
    if abs(delta) <= dead:
        return 0.0
    span = max(center, 1.0 - center) - dead
    return max(-1.0, min(1.0, (delta - math.copysign(dead, delta)) / span))


class GestureController:
    def __init__(self) -> None:
        self._cmd = ControlInput()
        self._armed = False

    def update(self, hand: HandState) -> ControlInput:
        if not hand.present:
            # Hold altitude, stop translating, keep the current arm state.
            target = ControlInput(armed=self._armed, event="no hand - holding")
            self._blend(target)
            return self._cmd

        event = self._cmd.event
        if hand.fingers_up >= 4 and not self._armed:
            self._armed = True
            event = "ARMED (open palm)"
        elif hand.fingers_up == 0 and self._armed:
            self._armed = False
            event = "DISARMED (fist)"

        yaw = _axis(hand.palm_x, 0.5, _DEAD_ZONE)
        # Frame y grows downward, so invert: hand high -> positive throttle.
        throttle = -_axis(hand.palm_y, 0.5, _DEAD_ZONE)

        roll_raw = hand.roll_angle
        if abs(roll_raw) > math.pi / 2:  # hand read upside down; fold into -pi/2..pi/2
            roll_raw -= math.copysign(math.pi, roll_raw)
        if abs(roll_raw) <= _ROLL_DEAD_ZONE:
            roll = 0.0
        else:
            roll = (roll_raw - math.copysign(_ROLL_DEAD_ZONE, roll_raw)) / _ROLL_FULL_SCALE
            roll = max(-1.0, min(1.0, roll))

        # Only treat a pinch as a command when the hand is open enough that the
        # thumb-index gap is deliberate (a fist also closes that gap).
        pinch = hand.pinch if hand.fingers_up >= 2 else 0.0
        pitch = max(0.0, min(1.0, (pinch - 0.2) / 0.8))

        target = ControlInput(
            throttle=throttle,
            yaw_rate=yaw,
            roll=roll,
            pitch=pitch,
            armed=self._armed,
            event=event,
        )
        self._blend(target)
        return self._cmd

    def _blend(self, target: ControlInput) -> None:
        a = _SMOOTHING
        c = self._cmd
        c.throttle += (target.throttle - c.throttle) * a
        c.yaw_rate += (target.yaw_rate - c.yaw_rate) * a
        c.roll += (target.roll - c.roll) * a
        c.pitch += (target.pitch - c.pitch) * a
        c.armed = target.armed
        c.event = target.event
