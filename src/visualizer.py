"""Render the simulated quadcopter and a control HUD to a BGR image."""

from __future__ import annotations

import math

import cv2
import numpy as np

from control_types import ControlInput
from controls import HAND_FLIGHT_ENABLED
from simulator import QuadState

_BG = (28, 26, 24)
_GRID = (70, 62, 55)
_GRID_AXIS = (120, 110, 95)
_DRONE = (240, 240, 245)
_ARM = (90, 200, 255)
_ROTOR_ARMED = (80, 240, 140)
_ROTOR_IDLE = (120, 120, 130)
_SHADOW = (18, 17, 16)
_TRAIL = (200, 160, 90)
_HUD = (235, 235, 235)
_HUD_DIM = (150, 150, 150)
_SELECTED = (90, 220, 255)
_WARN = (90, 90, 240)
_OK = (90, 220, 130)


class Camera:
    """Minimal look-at pinhole camera."""

    def __init__(self, size, position, target, fov_deg=55.0):
        self.w, self.h = size
        self.pos = np.asarray(position, dtype=float)
        f = np.asarray(target, dtype=float) - self.pos
        f /= np.linalg.norm(f)
        up = np.array([0.0, 0.0, 1.0])
        r = np.cross(f, up)
        r /= np.linalg.norm(r)
        u = np.cross(r, f)
        self._basis = np.stack([r, u, f])  # rows map world -> camera axes
        self._focal = 0.5 * self.w / math.tan(math.radians(fov_deg) / 2)

    def project(self, point):
        cam = self._basis @ (np.asarray(point, dtype=float) - self.pos)
        if cam[2] <= 0.05:
            return None
        u = self.w / 2 + self._focal * cam[0] / cam[2]
        v = self.h / 2 - self._focal * cam[1] / cam[2]
        return int(round(u)), int(round(v))


def _rot(yaw, roll, pitch):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    ry = np.array([[cr, 0, sr], [0, 1, 0], [-sr, 0, cr]])
    return rz @ rx @ ry


_CAM_OFFSET = np.array([0.0, -11.0, 6.0])  # chase-cam position relative to the drone


class Visualizer:
    def __init__(self, width=640, height=720):
        self.w, self.h = width, height
        self._look = np.array([0.0, 0.0, 1.5])  # smoothed point the camera tracks
        self.cam = Camera((width, height), position=self._look + _CAM_OFFSET,
                          target=self._look)
        self._arm_len = 0.55

    def render(self, states, cmd: ControlInput, fps: float = 0.0,
              trails=None, gstate=None, selected=None) -> np.ndarray:
        if isinstance(states, QuadState):        # single-drone convenience
            states = [states]
        trails = trails or [[] for _ in states]
        img = np.full((self.h, self.w, 3), _BG, dtype=np.uint8)

        centre = np.mean([s.pos for s in states], axis=0)
        goal = np.array([centre[0], centre[1], max(1.0, centre[2]) * 0.5 + 1.0])
        self._look += (goal - self._look) * 0.08
        self.cam = Camera((self.w, self.h), position=self._look + _CAM_OFFSET,
                          target=self._look)

        self._draw_grid(img)
        # Far drones first so nearer ones draw on top.
        order = sorted(range(len(states)), key=lambda i: -float(
            np.linalg.norm(np.asarray(states[i].pos) - self.cam.pos)))
        for i in order:
            self._draw_trail(img, trails[i])
            self._draw_shadow(img, states[i])
            self._draw_drone(img, states[i], highlighted=(i == selected and len(states) > 1))
        self._draw_hud(img, states, cmd, fps, gstate, selected)
        return img

    # -- world -----------------------------------------------------------
    def _line(self, img, a, b, color, thickness=1):
        pa, pb = self.cam.project(a), self.cam.project(b)
        if pa is None or pb is None:
            return
        cv2.line(img, pa, pb, color, thickness, cv2.LINE_AA)

    def _draw_grid(self, img, extent=14, step=2):
        # Recenter the grid under the camera focus so there is always ground.
        cx = round(self._look[0] / step) * step
        cy = round(self._look[1] / step) * step
        for i in range(-extent, extent + 1, step):
            x, y = cx + i, cy + i
            self._line(img, (x, cy - extent, 0), (x, cy + extent, 0),
                       _GRID_AXIS if x == 0 else _GRID, 2 if x == 0 else 1)
            self._line(img, (cx - extent, y, 0), (cx + extent, y, 0),
                       _GRID_AXIS if y == 0 else _GRID, 2 if y == 0 else 1)

    def _draw_trail(self, img, trail):
        pts = [self.cam.project(p) for p in trail]
        for a, b in zip(pts, pts[1:]):
            if a and b:
                cv2.line(img, a, b, _TRAIL, 1, cv2.LINE_AA)

    def _motor_offsets(self):
        L = self._arm_len
        return [np.array([L, L, 0.0]), np.array([L, -L, 0.0]),
                np.array([-L, -L, 0.0]), np.array([-L, L, 0.0])]

    def _draw_shadow(self, img, state: QuadState):
        r = _rot(state.yaw, 0.0, 0.0)
        ground = []
        for off in self._motor_offsets():
            world = state.pos + r @ off
            p = self.cam.project((world[0], world[1], 0.0))
            if p is None:
                return
            ground.append(p)
        overlay = img.copy()
        cv2.fillConvexPoly(overlay, np.array(ground, dtype=np.int32), _SHADOW)
        cv2.addWeighted(overlay, 0.35, img, 0.65, 0, img)

    def _draw_drone(self, img, state: QuadState, highlighted: bool = False):
        r = _rot(state.yaw, state.roll, state.pitch)
        center = state.pos
        motors = [center + r @ off for off in self._motor_offsets()]

        arm_color = _SELECTED if highlighted else _ARM
        for m in motors:
            self._line(img, center, m, arm_color, 3)

        nose = center + r @ np.array([0.0, self._arm_len * 1.4, 0.0])
        self._line(img, center, nose, _DRONE, 2)

        if highlighted:
            pc = self.cam.project(center)
            if pc:
                cv2.circle(img, pc, 22, _SELECTED, 2, cv2.LINE_AA)

        armed = state.armed or not state.on_ground
        rotor_color = _ROTOR_ARMED if state.armed else _ROTOR_IDLE
        for idx, m in enumerate(motors):
            spin = state.rotor_phase + idx * math.pi / 2
            ring = []
            for k in range(12):
                ang = 2 * math.pi * k / 12 + spin
                local = np.array([0.28 * math.cos(ang), 0.28 * math.sin(ang), 0.0])
                ring.append(m + r @ local)
            proj = [self.cam.project(p) for p in ring]
            proj = [p for p in proj if p is not None]
            if len(proj) >= 3:
                cv2.polylines(img, [np.array(proj, dtype=np.int32)], True,
                              rotor_color, 2 if armed else 1, cv2.LINE_AA)
            blade = self.cam.project(m + r @ np.array([0.28 * math.cos(spin),
                                                      0.28 * math.sin(spin), 0.0]))
            pm = self.cam.project(m)
            if blade and pm:
                cv2.line(img, pm, blade, rotor_color, 1, cv2.LINE_AA)

        pc = self.cam.project(center)
        if pc:
            cv2.circle(img, pc, 4, _DRONE, -1, cv2.LINE_AA)

    # -- hud -----------------------------------------------------------
    def _draw_hud(self, img, states, cmd: ControlInput, fps: float,
                  gstate=None, selected=None):
        font = cv2.FONT_HERSHEY_SIMPLEX
        pad = 14
        state = states[0]
        centre = np.mean([s.pos for s in states], axis=0)

        status = "ARMED" if state.armed else "DISARMED"
        color = _OK if state.armed else _WARN
        suffix = f"  x{len(states)}" if len(states) > 1 else ""
        cv2.putText(img, status + suffix, (pad, 30), font, 0.8, color, 2, cv2.LINE_AA)
        if len(states) > 1 and selected is not None:
            cv2.putText(img, f"sel #{selected + 1}", (pad, self.h - 172), font, 0.5,
                        _SELECTED, 1, cv2.LINE_AA)

        speed = float(np.linalg.norm(state.vel[:2]))
        cv2.putText(img, f"alt {centre[2]:4.1f} m   spd {speed:4.1f} m/s   yaw {math.degrees(state.yaw):+4.0f}",
                    (pad, 54), font, 0.5, _HUD, 1, cv2.LINE_AA)
        if fps:
            cv2.putText(img, f"{fps:4.1f} fps", (self.w - 90, 30), font, 0.5,
                        _HUD_DIM, 1, cv2.LINE_AA)

        if gstate is not None:
            if gstate.maneuver:
                mode, mode_color = gstate.maneuver.upper(), _WARN
            elif HAND_FLIGHT_ENABLED:
                mode, mode_color = gstate.mode.value.upper(), _ARM
            else:
                mode, mode_color = "HOLD", _ARM
            cv2.putText(img, f"MODE {mode}", (pad, 80), font, 0.55, mode_color, 1, cv2.LINE_AA)
            if HAND_FLIGHT_ENABLED:
                cv2.putText(img, f"SPEED {gstate.speed_name}", (pad, 100), font, 0.5,
                            _HUD_DIM, 1, cv2.LINE_AA)
            g = gstate.active_gesture
            if g and g != "None":
                tag = "*" if gstate.gesture_source == "custom" else ""
                gcol = _OK if gstate.gesture_source == "custom" else _HUD
                cv2.putText(img, g + tag, (pad, 124), font, 0.5, gcol, 1, cv2.LINE_AA)
                if gstate.hold_progress > 0:
                    x0 = pad + 130
                    cv2.rectangle(img, (x0, 114), (x0 + 90, 124), (60, 58, 55), -1)
                    cv2.rectangle(img, (x0, 114),
                                  (x0 + int(90 * gstate.hold_progress), 124), _OK, -1)
            if gstate.sequence_hint:
                cv2.putText(img, f"combo: {gstate.sequence_hint}", (pad, 146),
                            font, 0.5, _WARN, 1, cv2.LINE_AA)

        hint = ("thumb up=takeoff/rise  thumb down=land  point=orbit  ILY=home"
                + ("  V=mode" if HAND_FLIGHT_ENABLED else "  (gestures only)"))
        cv2.putText(img, hint, (pad, self.h - 150), font, 0.42, _HUD_DIM, 1, cv2.LINE_AA)

        bars = [("THR", cmd.throttle, True), ("YAW", cmd.yaw_rate, True),
                ("ROLL", cmd.roll, True), ("PTCH", cmd.pitch, False)]
        y = self.h - 18 - len(bars) * 24
        if cmd.event:
            cv2.putText(img, cmd.event, (pad, y - 12), font, 0.5, _OK, 1, cv2.LINE_AA)
        for label, value, bipolar in bars:
            self._bar(img, pad, y, label, value, bipolar)
            y += 24

    def _bar(self, img, x, y, label, value, bipolar):
        font = cv2.FONT_HERSHEY_SIMPLEX
        w, h = 160, 12
        x0 = x + 52
        cv2.putText(img, label, (x, y + h), font, 0.45, _HUD, 1, cv2.LINE_AA)
        cv2.rectangle(img, (x0, y), (x0 + w, y + h), (60, 58, 55), -1)
        v = float(np.clip(value, -1.0, 1.0))
        if bipolar:
            mid = x0 + w // 2
            end = int(mid + v * (w // 2))
            cv2.rectangle(img, (min(mid, end), y), (max(mid, end), y + h), _ARM, -1)
            cv2.line(img, (mid, y - 2), (mid, y + h + 2), _HUD_DIM, 1)
        else:
            end = int(x0 + max(0.0, v) * w)
            cv2.rectangle(img, (x0, y), (end, y + h), _ROTOR_ARMED, -1)
        cv2.rectangle(img, (x0, y), (x0 + w, y + h), (110, 108, 105), 1)
