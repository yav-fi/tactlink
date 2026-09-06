"""Hand-rolled 3D view of the drone and the operators.

A pinhole camera projects world points (x = right, y = away from the viewer,
z = up) to pixels; everything is drawn with OpenCV 2D primitives onto a numpy
image. No game engine, no meshes.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

_BG = (24, 20, 17)
_GRID = (44, 40, 36)
_GRID_AXIS = (70, 64, 58)
_DRONE = (90, 230, 90)
_DRONE_IDLE = (150, 150, 150)
_TRAIL = (150, 110, 60)
_ANCHOR = (255, 210, 90)      # operator holding the drone (BGR: cyan-ish gold)
_OP = (150, 150, 150)
_TEXT = (225, 225, 225)


class _Camera:
    def __init__(self, size, eye, target, fov_deg=58.0):
        self.w, self.h = size
        self.eye = np.array(eye, float)
        fwd = np.array(target, float) - self.eye
        fwd /= np.linalg.norm(fwd)
        right = np.cross(fwd, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        self.rot = np.array([right, up, fwd])
        self.focal = (self.h / 2) / math.tan(math.radians(fov_deg) / 2)

    def project(self, p):
        c = self.rot @ (np.array(p, float) - self.eye)
        if c[2] <= 0.05:
            return None
        return (int(self.w / 2 + self.focal * c[0] / c[2]),
                int(self.h / 2 - self.focal * c[1] / c[2]))


class Scene:
    def __init__(self, width=640, height=480):
        self.w, self.h = width, height
        self.cam = _Camera((width, height), eye=(0.0, -18.0, 9.0),
                           target=(0.0, 0.5, 1.2), fov_deg=66.0)

    def _line(self, img, a, b, color, thick=1):
        pa, pb = self.cam.project(a), self.cam.project(b)
        if pa and pb:
            cv2.line(img, pa, pb, color, thick, cv2.LINE_AA)

    def render(self, quad, ops, hud) -> np.ndarray:
        img = np.full((self.h, self.w, 3), _BG, dtype=np.uint8)
        self._grid(img)

        # operators, far to near
        for i in sorted(range(ops.n), key=lambda k: -ops.pos[k][1]):
            self._operator(img, ops, i)

        self._trail(img, quad.trail)
        self._shadow(img, quad)
        self._gaze(img, quad, ops, hud)
        self._drone(img, quad)
        self._hud(img, quad, ops, hud)
        return img

    def _gaze(self, img, quad, ops, hud):
        """Faint line from the drone to the operator its nose is facing."""
        if hud.get("mode") == "orbit":
            i = ops.anchor
        else:
            i = min(range(ops.n),
                    key=lambda k: float(np.linalg.norm(ops.pos[k] - quad.pos[:2])))
        head = (float(ops.pos[i][0]), float(ops.pos[i][1]), 1.6)
        self._line(img, (quad.pos[0], quad.pos[1], quad.pos[2]), head, (66, 62, 56), 1)

    def _grid(self, img, extent=12, step=2.0):
        rng = np.arange(-extent, extent + step, step)
        for x in rng:
            self._line(img, (x, -extent, 0), (x, extent, 0),
                       _GRID_AXIS if abs(x) < 1e-6 else _GRID, 2 if abs(x) < 1e-6 else 1)
        for y in rng:
            self._line(img, (-extent, y, 0), (extent, y, 0),
                       _GRID_AXIS if abs(y) < 1e-6 else _GRID, 2 if abs(y) < 1e-6 else 1)

    def _operator(self, img, ops, i):
        base = (float(ops.pos[i][0]), float(ops.pos[i][1]), 0.0)
        head = (base[0], base[1], 1.75)
        anchor = i == ops.anchor
        col, thick = (_ANCHOR, 3) if anchor else (_OP, 2)
        self._line(img, base, head, col, thick)
        p = self.cam.project(head)
        if p:
            cv2.circle(img, p, 6 if anchor else 5, col, -1, cv2.LINE_AA)
            cv2.putText(img, ops.names[i], (p[0] + 9, p[1] + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        if anchor:
            self._ground_ring(img, base, 0.7, _ANCHOR)

    def _ground_ring(self, img, centre, r, col, thick=1, arc=1.0):
        pts = []
        for a in np.linspace(-math.pi / 2, -math.pi / 2 + 2 * math.pi * arc, 30):
            pts.append(self.cam.project((centre[0] + r * math.cos(a),
                                         centre[1] + r * math.sin(a), 0.02)))
        pts = [q for q in pts if q]
        if len(pts) > 1:
            cv2.polylines(img, [np.array(pts, np.int32)], arc >= 0.999, col, thick,
                          cv2.LINE_AA)

    def _trail(self, img, trail):
        pts = [self.cam.project((p[0], p[1], 0.0)) for p in trail]
        for a, b in zip(pts, pts[1:]):
            if a and b:
                cv2.line(img, a, b, _TRAIL, 1, cv2.LINE_AA)

    def _shadow(self, img, quad):
        self._ground_ring(img, (quad.pos[0], quad.pos[1], 0.0), 0.5, (34, 30, 27))

    def _drone(self, img, quad):
        col = _DRONE if quad.armed else _DRONE_IDLE
        c = quad.pos
        cy, sy = math.cos(quad.yaw), math.sin(quad.yaw)
        arm = 0.55
        offs = [(arm, arm), (arm, -arm), (-arm, -arm), (-arm, arm)]
        rotors = []
        for ox, oy in offs:
            wx = c[0] + ox * cy - oy * sy
            wy = c[1] + ox * sy + oy * cy
            rotors.append((wx, wy, c[2]))
        for r in rotors:
            self._line(img, (c[0], c[1], c[2]), r, col, 2)
            p = self.cam.project(r)
            if p:
                cv2.circle(img, p, 5, col, 1, cv2.LINE_AA)
        nose = (c[0] + 1.3 * cy, c[1] + 1.3 * sy, c[2])
        self._line(img, (c[0], c[1], c[2]), nose, (255, 255, 255), 2)
        p = self.cam.project(nose)
        if p:
            cv2.circle(img, p, 3, (255, 255, 255), -1, cv2.LINE_AA)

    def _hud(self, img, quad, ops, hud):
        f = cv2.FONT_HERSHEY_SIMPLEX
        arm = "ARMED" if quad.armed else "DISARMED"
        cv2.putText(img, arm, (14, 26), f, 0.6,
                    (90, 230, 90) if quad.armed else (120, 120, 120), 2, cv2.LINE_AA)
        cv2.putText(img, f"alt {quad.pos[2]:.1f} m", (14, 48), f, 0.5, _TEXT, 1, cv2.LINE_AA)
        mode = hud.get("mode", "idle").upper()
        cv2.putText(img, f"MODE {mode}", (14, 70), f, 0.55,
                    (80, 210, 255) if mode not in ("IDLE",) else _TEXT, 2, cv2.LINE_AA)
        cv2.putText(img, f"CTRL -> {ops.names[ops.anchor]}", (self.w - 150, 26), f, 0.5,
                    _ANCHOR, 1, cv2.LINE_AA)
        pose = float(hud.get("pose", 0.0))
        if pose > 0.01:
            plabel = hud.get("pose_label", "")
            cv2.putText(img, plabel, (self.w - 230, 48), f, 0.45, (150, 255, 255), 1, cv2.LINE_AA)
            cv2.rectangle(img, (self.w - 230, 54), (self.w - 70, 60), (60, 60, 60), -1)
            cv2.rectangle(img, (self.w - 230, 54),
                          (self.w - 230 + int(160 * pose), 60), (150, 255, 255), -1)

        note = hud.get("note", "")
        if note:
            cv2.putText(img, note, (14, self.h - 58), f, 0.5, (90, 230, 90), 1, cv2.LINE_AA)
        g = hud.get("gesture", "None")
        if g not in ("None", ""):
            cv2.putText(img, g, (14, self.h - 34), f, 0.55, _TEXT, 1, cv2.LINE_AA)
            prog = hud.get("progress", 0.0)
            cv2.rectangle(img, (170, self.h - 44), (170 + 150, self.h - 34), (60, 60, 60), -1)
            cv2.rectangle(img, (170, self.h - 44),
                          (170 + int(150 * prog), self.h - 34), (90, 230, 90), -1)
        cv2.putText(img, "closed fist = hand off  |  3 fingers = dash forward  |  index L/R = dash  |  hold each",
                    (14, self.h - 14), f, 0.34, (140, 140, 140), 1, cv2.LINE_AA)
