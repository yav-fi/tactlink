"""Translate hand pose + gesture state into a normalized ControlInput.

Continuous flying comes from the hand pose; the mapping depends on the current
:class:`FlightMode`:

* HEADING  - palm x -> yaw,  palm y -> throttle, hand tilt -> roll
* POSITION - palm x -> roll, palm y -> throttle, hand tilt -> yaw
* HOVER    - translation locked, only palm y -> throttle

In every mode a pinch adds forward pitch. Discrete gesture events (takeoff,
land, spin360, return_home) run as short autopilot routines that override the
hand until they finish.
"""

import math

import numpy as np

from control_types import ControlInput, FlightMode, GestureState, HandState

_DEAD_ZONE = 0.12
_ROLL_DEAD_ZONE = 0.18
_ROLL_FULL_SCALE = 0.7
_SMOOTHING = 0.35

_TAKEOFF_ALT = 1.3
_SPIN_RATE = 1.0
_HOME_RADIUS = 0.4


def _axis(value: float, center: float, dead: float) -> float:
    delta = value - center
    if abs(delta) <= dead:
        return 0.0
    span = max(center, 1.0 - center) - dead
    return max(-1.0, min(1.0, (delta - math.copysign(dead, delta)) / span))


class GestureController:
    def __init__(self) -> None:
        self._cmd = ControlInput()
        self._armed = False
        self.maneuver = ""          # active autopilot routine, "" when hand-flown
        self._spin_start_yaw = 0.0

    def update(self, hand: HandState, gstate: GestureState, state) -> ControlInput:
        for event in gstate.events:
            self._start_maneuver(event, state)

        if self.maneuver:
            target = self._run_maneuver(state)
        elif not hand.present:
            target = ControlInput(armed=self._armed, event="no hand - holding")
        else:
            target = self._fly(hand, gstate)

        if gstate.events:
            target.event = " · ".join(gstate.events)
        self._blend(target)
        gstate.maneuver = self.maneuver
        return self._cmd

    # -- hand flying ---------------------------------------------------
    def _fly(self, hand: HandState, gstate: GestureState) -> ControlInput:
        x = _axis(hand.palm_x, 0.5, _DEAD_ZONE)
        y = -_axis(hand.palm_y, 0.5, _DEAD_ZONE)

        tilt = hand.roll_angle
        if abs(tilt) > math.pi / 2:            # hand read upside down
            tilt -= math.copysign(math.pi, tilt)
        if abs(tilt) <= _ROLL_DEAD_ZONE:
            tilt = 0.0
        else:
            tilt = max(-1.0, min(1.0,
                       (tilt - math.copysign(_ROLL_DEAD_ZONE, tilt)) / _ROLL_FULL_SCALE))

        pinch = hand.pinch if hand.fingers_up >= 2 else 0.0
        fwd = max(0.0, min(1.0, (pinch - 0.2) / 0.8))

        yaw = roll = pitch = 0.0
        throttle = y
        if gstate.mode is FlightMode.HEADING:
            yaw, roll, pitch = x, tilt, fwd
        elif gstate.mode is FlightMode.POSITION:
            roll, yaw, pitch = x, tilt, fwd
        # HOVER: leave yaw/roll/pitch at 0

        s = gstate.speed_scale
        return ControlInput(throttle=throttle, yaw_rate=yaw * s, roll=roll * s,
                            pitch=pitch * s, armed=self._armed)

    # -- autopilot routines ------------------------------------------
    def _start_maneuver(self, event: str, state) -> None:
        if event == "takeoff":
            self._armed = True
            self.maneuver = "takeoff"
        elif event == "land":
            self.maneuver = "land"
        elif event == "spin360" and self._armed:
            self._spin_start_yaw = state.yaw
            self.maneuver = "spin360"
        elif event == "return_home" and self._armed:
            self.maneuver = "return_home"

    def _run_maneuver(self, state) -> ControlInput:
        cmd = ControlInput(armed=self._armed, event=self.maneuver)

        if self.maneuver == "takeoff":
            cmd.throttle = 0.8
            if state.pos[2] >= _TAKEOFF_ALT:
                self.maneuver = ""
        elif self.maneuver == "land":
            cmd.throttle = -0.8
            if state.on_ground:
                self._armed = False
                cmd.armed = False
                self.maneuver = ""
        elif self.maneuver == "spin360":
            cmd.yaw_rate = _SPIN_RATE
            if abs(state.yaw - self._spin_start_yaw) >= 2 * math.pi - 0.2:
                self.maneuver = ""
        elif self.maneuver == "return_home":
            home_vec = -state.pos[:2]
            dist = float(np.linalg.norm(home_vec))
            if dist <= _HOME_RADIUS:
                self.maneuver = ""
            else:
                d = home_vec / dist
                cy, sy = math.cos(-state.yaw), math.sin(-state.yaw)
                body = np.array([cy * d[0] - sy * d[1], sy * d[0] + cy * d[1]])
                gain = min(1.0, dist / 3.0) * 0.8
                cmd.roll = float(body[0]) * gain
                cmd.pitch = float(body[1]) * gain
        return cmd

    def _blend(self, target: ControlInput) -> None:
        a = _SMOOTHING
        c = self._cmd
        c.throttle += (target.throttle - c.throttle) * a
        c.yaw_rate += (target.yaw_rate - c.yaw_rate) * a
        c.roll += (target.roll - c.roll) * a
        c.pitch += (target.pitch - c.pitch) * a
        c.armed = target.armed
        c.event = target.event
