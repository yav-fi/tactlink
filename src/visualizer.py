"""Render the simulated quadcopter and a control HUD to a BGR image."""

from __future__ import annotations

import math

import cv2
import numpy as np

from control_types import ControlInput
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

    def render(self, state: QuadState, cmd: ControlInput, fps: float = 0.0,
              trail=None) -> np.ndarray:
        img = np.full((self.h, self.w, 3), _BG, dtype=np.uint8)
        # Ease the camera toward the drone so it stays framed while it flies.
        goal = np.array([state.pos[0], state.pos[1], max(1.0, state.pos[2]) * 0.5 + 1.0])
        self._look += (goal - self._look) * 0.08
        self.cam = Camera((self.w, self.h), position=self._look + _CAM_OFFSET,
                          target=self._look)
        self._draw_grid(img)
        self._draw_trail(img, trail or [])
        self._draw_shadow(img, state)
        self._draw_drone(img, state)
        self._draw_hud(img, state, cmd, fps)
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

    def _draw_drone(self, img, state: QuadState):
        r = _rot(state.yaw, state.roll, state.pitch)
        center = state.pos
        motors = [center + r @ off for off in self._motor_offsets()]

        for m in motors:
            self._line(img, center, m, _ARM, 3)

        nose = center + r @ np.array([0.0, self._arm_len * 1.4, 0.0])
        self._line(img, center, nose, _DRONE, 2)

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
    def _draw_hud(self, img, state: QuadState, cmd: ControlInput, fps: float):
        font = cv2.FONT_HERSHEY_SIMPLEX
        pad = 14

        status = "ARMED" if state.armed else "DISARMED"
        color = _OK if state.armed else _WARN
        cv2.putText(img, status, (pad, 30), font, 0.8, color, 2, cv2.LINE_AA)

        alt = state.pos[2]
        speed = float(np.linalg.norm(state.vel[:2]))
        cv2.putText(img, f"alt {alt:4.1f} m   spd {speed:4.1f} m/s   yaw {math.degrees(state.yaw):+4.0f}",
                    (pad, 54), font, 0.5, _HUD, 1, cv2.LINE_AA)
        if fps:
            cv2.putText(img, f"{fps:4.1f} fps", (self.w - 90, 30), font, 0.5,
                        _HUD_DIM, 1, cv2.LINE_AA)

        hint = "palm=yaw/throttle  tilt=roll  pinch=forward  open=arm  fist=land"
        cv2.putText(img, hint, (pad, 78), font, 0.44, _HUD_DIM, 1, cv2.LINE_AA)

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
