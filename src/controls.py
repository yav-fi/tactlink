"""Translate hand pose + gesture state into a normalized ControlInput.

The drone is flown entirely by discrete gesture commands and the autopilot
routines they trigger (takeoff, land, spin360, return_home,
fly_north/south/east/west, orbit). ``orbit`` and ``return_home`` are relative to
the current **anchor** - the controlling operator's position, passed in via
``GestureState.follow_pos`` (falls back to the origin). When no routine is
running an armed drone trails the anchor and holds its target altitude. A
`takeoff` while already airborne steps the target altitude up by ``_CLIMB_STEP``.

Set ``HAND_FLIGHT_ENABLED = True`` to also fly continuously from the hand pose
(palm position -> yaw/throttle, hand tilt -> roll, pinch -> forward pitch,
mapped per :class:`FlightMode`).
"""

import math

import numpy as np

from control_types import ControlInput, FlightMode, GestureState, HandState

HAND_FLIGHT_ENABLED = False   # continuous palm/tilt/pinch flying (off: gestures only)

_DEAD_ZONE = 0.12
_ROLL_DEAD_ZONE = 0.18
_ROLL_FULL_SCALE = 0.7
_SMOOTHING = 0.35

_TAKEOFF_ALT = 3.0     # altitude the first takeoff climbs to (above the operators)
_CLIMB_STEP = 1.5      # extra metres per thumbs-up once airborne
_MAX_ALT = 9.0
_SPIN_RATE = 1.0
_HOME_RADIUS = 0.4
_DASH_DISTANCE = 5.0    # metres a fly_<compass> dash covers before hovering
_ORBIT_RADIUS = 6.0     # metres from the anchor (operator) for the orbit maneuver
_ORBIT_SPEED = 3.0      # m/s tangential while orbiting

# World-frame compass directions (x = east, y = north).
_COMPASS = {
    "fly_north": np.array([0.0, 1.0]),
    "fly_south": np.array([0.0, -1.0]),
    "fly_east": np.array([1.0, 0.0]),
    "fly_west": np.array([-1.0, 0.0]),
}


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
        self._alt_target = 0.0      # metres; the altitude the drone seeks when armed
        self._spin_start_yaw = 0.0
        self._dash_dir = np.zeros(2)
        self._dash_start = np.zeros(2)
        self._anchor = np.zeros(2)   # point orbit / return-home are relative to
        self._leashed = False        # trail the operator when idle? (off after a dash)

    def update(self, hand: HandState, gstate: GestureState, state) -> ControlInput:
        if gstate.follow_pos is not None:
            self._anchor = np.array(gstate.follow_pos, dtype=float)
        for event in gstate.events:
            self._start_maneuver(event, state)

        if self.maneuver:
            target = self._run_maneuver(state)
        elif HAND_FLIGHT_ENABLED and hand.present:
            target = self._fly(hand, gstate)
        elif self._armed and self._leashed and gstate.follow_pos is not None:
            target = self._follow_point(state, gstate.follow_pos)
        else:
            # Gestures-only, no target: hold position, seek the target altitude.
            target = ControlInput(armed=self._armed,
                                  throttle=self._alt_throttle(state) if self._armed else 0.0)

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

    @staticmethod
    def _world_to_body(d, yaw: float):
        """Rotate a world-frame xy direction into the drone's body frame."""
        cy, sy = math.cos(-yaw), math.sin(-yaw)
        return np.array([cy * d[0] - sy * d[1], sy * d[0] + cy * d[1]])

    def _alt_throttle(self, state) -> float:
        """Throttle command to climb toward / hold ``self._alt_target`` (with damping)."""
        err = self._alt_target - state.pos[2]
        return float(np.clip(err * 1.3 - state.vel[2] * 0.45, -0.45, 1.0))

    def _follow_point(self, state, xy) -> ControlInput:
        """Gently trail a point on the ground (the nearest operator), staying above."""
        err = np.asarray(xy, dtype=float) - np.asarray(state.pos[:2])
        dist = float(np.linalg.norm(err))
        cmd = ControlInput(armed=self._armed, throttle=self._alt_throttle(state))
        if dist > 0.6:                          # dead zone so it settles overhead
            body = self._world_to_body(err / dist, state.yaw)
            gain = min(1.0, dist / 4.0) * 0.6   # easy-going, not a chase
            cmd.roll = float(body[0]) * gain
            cmd.pitch = float(body[1]) * gain
        return cmd

    # -- autopilot routines ------------------------------------------
    def _start_maneuver(self, event: str, state) -> None:
        if event == "estop":
            self._armed = False
            self._alt_target = 0.0
            self.maneuver = ""
        elif event == "takeoff":
            if not self._armed:
                self._armed = True
                self._alt_target = _TAKEOFF_ALT
            else:                       # already airborne: step up
                self._alt_target = min(self._alt_target + _CLIMB_STEP, _MAX_ALT)
            self.maneuver = "climb"
            self._leashed = True        # after takeoff, sit with the operator
        elif event == "land":
            self._alt_target = 0.0
            self.maneuver = "land"
        elif event == "spin360" and self._armed:
            self._spin_start_yaw = state.yaw
            self.maneuver = "spin360"
        elif event == "return_home" and self._armed:
            self.maneuver = "return_home"
            self._leashed = True        # come back and stay with the operator
        elif event == "orbit" and self._armed:
            # Repeat the gesture to stop orbiting and hover in place.
            self.maneuver = "" if self.maneuver == "orbit" else "orbit"
            self._leashed = False
        elif (event in _COMPASS or event.startswith("fly_bearing:")) and self._armed:
            if event in _COMPASS:
                self._dash_dir = _COMPASS[event]
            else:
                bearing = float(event.split(":", 1)[1])
                self._dash_dir = np.array([math.cos(bearing), math.sin(bearing)])
            self._dash_start = np.array(state.pos[:2], dtype=float)
            self.maneuver = "fly_bearing" if event.startswith("fly_bearing:") else event
            self._leashed = False       # hold position after the dash, don't drift back

    def _run_maneuver(self, state) -> ControlInput:
        cmd = ControlInput(armed=self._armed, event=self.maneuver)

        if self.maneuver == "climb":
            cmd.throttle = self._alt_throttle(state)
            if abs(state.pos[2] - self._alt_target) < 0.25:
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
            home_vec = self._anchor - np.asarray(state.pos[:2])
            dist = float(np.linalg.norm(home_vec))
            if dist <= _HOME_RADIUS:
                self.maneuver = ""
            else:
                body = self._world_to_body(home_vec / dist, state.yaw)
                gain = min(1.0, dist / 3.0) * 0.8
                cmd.roll = float(body[0]) * gain
                cmd.pitch = float(body[1]) * gain
                cmd.throttle = self._alt_throttle(state)
        elif self.maneuver in _COMPASS or self.maneuver == "fly_bearing":
            travelled = float(np.linalg.norm(np.array(state.pos[:2]) - self._dash_start))
            if travelled >= _DASH_DISTANCE:
                self.maneuver = ""
            else:
                body = self._world_to_body(self._dash_dir, state.yaw)
                ease = min(1.0, (_DASH_DISTANCE - travelled) / 1.5)  # slow into the stop
                cmd.roll = float(body[0]) * 0.8 * ease
                cmd.pitch = float(body[1]) * 0.8 * ease
                cmd.throttle = self._alt_throttle(state)
        elif self.maneuver == "orbit":
            # Fly out to the ring around the anchor (the controlling operator),
            # then circle it forever until a new command or a repeat gesture.
            rel = np.asarray(state.pos[:2]) - self._anchor
            r = float(np.linalg.norm(rel))
            radial = rel / r if r > 0.3 else np.array([1.0, 0.0])
            tangent = np.array([-radial[1], radial[0]])          # counter-clockwise
            v_radial = float(np.clip(-(r - _ORBIT_RADIUS) * 1.3, -_ORBIT_SPEED, _ORBIT_SPEED))
            world_v = v_radial * radial + _ORBIT_SPEED * tangent
            speed = float(np.linalg.norm(world_v)) + 1e-6
            body = self._world_to_body(world_v / speed, state.yaw)
            mag = min(1.0, speed / 4.0) * 0.8
            cmd.roll = float(body[0]) * mag
            cmd.pitch = float(body[1]) * mag
            cmd.throttle = self._alt_throttle(state)
            # Keep the nose pointed inward at the anchor while circling.
            inward = -radial
            desired_yaw = math.atan2(-inward[0], inward[1])
            err = (desired_yaw - state.yaw + math.pi) % (2 * math.pi) - math.pi
            cmd.yaw_rate = float(np.clip(err * 1.5, -1.0, 1.0))
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
