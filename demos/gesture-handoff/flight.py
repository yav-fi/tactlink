"""Toy quadcopter, N static operators, and an autopilot for the hand-off demo.

Pure numpy - no camera, no MediaPipe. Operator 1 (the webcam operator) always
flies the drone; a hand-off just moves which operator the drone is *anchored* to
(orbits, returns to, is sent to). Operators are scattered at random through the
scene each run and never move.
"""

from __future__ import annotations

import math

import numpy as np

CRUISE_ALT = 3.0
CLIMB_STEP = 1.5
MAX_ALT = 9.0
ORBIT_RADIUS = 3.0
ORBIT_SPEED = 2.2         # m/s tangential
ARRIVE_RADIUS = 1.0       # within this of the anchor, a return/hand-off is "done"
MAX_SPEED = 4.5           # m/s horizontal cruise cap
DRAG = 1.6                # 1/s, frame-rate independent
DASH_DISTANCE = 5.0       # metres the wiper nudges the drone sideways
AIM_DWELL = 1.4           # seconds of steady pointing before a targeted hand-off
_AIM_LOST = 0.5
_AIM_MIN_POINT = 0.55     # |finger vector| that counts as a deliberate point
_AIM_MIN_ALIGN = 0.35     # dot with an operator's on-screen direction


def _ang_to(cur: float, target: float) -> float:
    return math.atan2(math.sin(target - cur), math.cos(target - cur))


class Operators:
    """N people scattered through the scene, plus which one holds the drone."""

    def __init__(self, n: int = 4, radius: float = 4.6, min_sep: float = 2.4, seed=None):
        self.n = max(2, int(n))
        self.rng = np.random.default_rng(seed)
        self.pos = _scatter(self.n, radius, min_sep, self.rng)
        self.names = [f"OP{i + 1}" for i in range(self.n)]
        self.anchor = 0               # operator the drone belongs to
        self.target = None            # operator being pointed at for a targeted hand-off
        self._aim_since = None
        self._aim_seen = 0.0

    def anchor_pos(self) -> np.ndarray:
        return self.pos[self.anchor]

    def random_handoff(self):
        """Move the anchor to a random *other* operator. Returns the new anchor."""
        choices = [i for i in range(self.n) if i != self.anchor]
        self.anchor = int(self.rng.choice(choices))
        return self.anchor

    def aim_from_point(self, point_dir, drone_xy):
        """Finger direction (image space) -> the operator most in that direction
        from the drone, or None. Point the way you want the drone to go."""
        dx, dy = point_dir
        if (dx * dx + dy * dy) ** 0.5 < _AIM_MIN_POINT:
            return None
        want = np.array([dx, -dy])                    # screen up (-y) == world +y
        want = want / (np.linalg.norm(want) + 1e-9)
        drone_xy = np.asarray(drone_xy, float)
        best, best_score = None, _AIM_MIN_ALIGN
        for i in range(self.n):
            if i == self.anchor:
                continue
            v = self.pos[i] - drone_xy
            d = np.linalg.norm(v)
            if d < 0.5:
                continue
            score = float(np.dot(v / d, want))
            if score > best_score:
                best, best_score = i, score
        return best

    def aim(self, target, now: float) -> None:
        """Feed the aimed operator each frame; hold-to-send progress is tracked."""
        if target is not None and target != self.anchor:
            if target != self.target:
                self.target = int(target)
                self._aim_since = now
            self._aim_seen = now
        elif self.target is not None and now - self._aim_seen > _AIM_LOST:
            self.target = None
            self._aim_since = None

    def aim_progress(self, now: float) -> float:
        if self.target is None or self._aim_since is None:
            return 0.0
        return min(1.0, (now - self._aim_since) / AIM_DWELL)

    def commit_aim(self):
        """Move the anchor to the aimed operator. Returns the new anchor, or None."""
        if self.target is not None and self.target != self.anchor:
            self.anchor = self.target
            self.target = None
            self._aim_since = None
            return self.anchor
        return None


class Quad:
    def __init__(self, start_xy):
        self.pos = np.array([float(start_xy[0]), float(start_xy[1]), 0.0])
        self.vel = np.zeros(3)
        self.yaw = math.pi / 2
        self.armed = False
        self.trail: list[np.ndarray] = []

    def step(self, accel_xy, climb_rate: float, yaw_rate: float, dt: float) -> None:
        self.vel[:2] += np.asarray(accel_xy, dtype=float) * dt
        self.vel[:2] *= max(0.0, 1.0 - DRAG * dt)
        speed = float(np.linalg.norm(self.vel[:2]))
        if speed > MAX_SPEED:
            self.vel[:2] *= MAX_SPEED / speed
        self.vel[2] = climb_rate
        self.yaw = math.atan2(math.sin(self.yaw + yaw_rate * dt),
                              math.cos(self.yaw + yaw_rate * dt))
        self.pos += self.vel * dt
        self.pos[2] = max(0.0, self.pos[2])
        self.trail.append(self.pos[:2].copy())
        if len(self.trail) > 140:
            self.trail.pop(0)


class Autopilot:
    """Runs the drone. ``mode`` is one of idle / climb / land / orbit / return /
    handoff / halt and is also what the HUD shows."""

    def __init__(self, operators: Operators):
        self.ops = operators
        self.mode = "idle"
        self.alt_target = 0.0
        self._orbit_on = False
        self._orbit_phase = 0.0
        self._halt_xy = None
        self._dash_goal = None

    def command(self, cmd: str, quad: Quad, now: float) -> str:
        """Apply a fired gesture command. Returns a short note for the HUD."""
        if cmd == "takeoff":
            if not quad.armed:
                quad.armed = True
                self.alt_target = CRUISE_ALT
                note = "take off"
            else:
                self.alt_target = min(MAX_ALT, self.alt_target + CLIMB_STEP)
                note = f"climb -> {self.alt_target:.0f} m"
            self.mode = "climb"
            return note
        if not quad.armed:
            return ""
        if cmd == "land":
            self.alt_target = 0.0
            self.mode = "land"
            return "land"
        if cmd == "halt":
            self.mode = "halt"
            self._orbit_on = False
            self._halt_xy = quad.pos[:2].copy()
            self.alt_target = float(quad.pos[2])
            return "halt"
        if cmd == "orbit":
            self._orbit_on = not self._orbit_on
            self.mode = "orbit" if self._orbit_on else "idle"
            return "orbit on" if self._orbit_on else "orbit off"
        if cmd == "return":
            self.mode = "return"
            self._orbit_on = False
            return f"return to {self.ops.names[self.ops.anchor]}"
        if cmd in ("dash_east", "dash_west"):
            d = DASH_DISTANCE if cmd == "dash_east" else -DASH_DISTANCE
            self._dash_goal = quad.pos[:2] + np.array([d, 0.0])
            self._orbit_on = False
            self.mode = "dash"
            return f"dash {'right' if d > 0 else 'left'}"
        if cmd == "handoff_random":
            new_anchor = self.ops.random_handoff()
            self.mode = "handoff"
            self._orbit_on = False
            return f"hand off -> {self.ops.names[new_anchor]}"
        if cmd == "handoff_aim":
            new_anchor = self.ops.commit_aim()
            if new_anchor is None:
                return ""
            self.mode = "handoff"
            self._orbit_on = False
            return f"hand off -> {self.ops.names[new_anchor]}"
        return ""

    def update(self, quad: Quad, dt: float, now: float) -> None:
        if not quad.armed:
            quad.step((0.0, 0.0), -2.0 if quad.pos[2] > 0.02 else 0.0, 0.0, dt)
            return

        if self.mode == "land":
            if quad.pos[2] < 0.06:
                quad.armed = False
                self.mode = "idle"
                self.alt_target = 0.0
            quad.step((0.0, 0.0), -1.8, 0.0, dt)
            return

        anchor = self.ops.anchor_pos()
        goal = anchor.copy()

        if self.mode == "halt":
            goal = self._halt_xy
        elif self.mode == "dash":
            goal = self._dash_goal
            if np.linalg.norm(quad.pos[:2] - self._dash_goal) < ARRIVE_RADIUS:
                self.mode = "halt"                    # then hold at the new spot
                self._halt_xy = np.asarray(self._dash_goal, float).copy()
                self.alt_target = float(quad.pos[2])
        elif self.mode == "orbit":
            self._orbit_phase += (ORBIT_SPEED / ORBIT_RADIUS) * dt
            goal = anchor + ORBIT_RADIUS * np.array(
                [math.cos(self._orbit_phase), math.sin(self._orbit_phase)])
        elif self.mode in ("return", "handoff"):
            goal = anchor
            if np.linalg.norm(quad.pos[:2] - anchor) < ARRIVE_RADIUS:
                self.mode = "idle"
        elif self.mode == "climb":
            if abs(self.alt_target - quad.pos[2]) < 0.2:
                self.mode = "idle"

        # Nose points at whoever it is watching: the operator it circles while
        # orbiting, otherwise the nearest operator - so it "looks at" people as
        # it flies past them.
        focus = anchor if self.mode == "orbit" else self._nearest_op(quad)
        to_focus = np.asarray(focus, float) - quad.pos[:2]
        yaw_rate = 0.0
        if np.linalg.norm(to_focus) > 0.4:
            desired = math.atan2(to_focus[1], to_focus[0])
            yaw_rate = float(np.clip(_ang_to(quad.yaw, desired) * 3.0, -4.0, 4.0))

        err = np.asarray(goal, float) - quad.pos[:2]
        accel = np.clip(err * 3.0 - quad.vel[:2] * 2.2, -9.0, 9.0)
        climb = float(np.clip((self.alt_target - quad.pos[2]) * 1.8, -3.0, 3.0))
        quad.step(accel, climb, yaw_rate, dt)

    def _nearest_op(self, quad: Quad) -> np.ndarray:
        i = min(range(self.ops.n),
                key=lambda k: float(np.linalg.norm(self.ops.pos[k] - quad.pos[:2])))
        return self.ops.pos[i]


def _scatter(n: int, radius: float, min_sep: float, rng) -> list:
    """n points scattered anywhere in a disc of the given radius, no two closer
    than min_sep, recentred so the group's centroid is the origin."""
    pts: list[np.ndarray] = []
    for _ in range(8000):
        if len(pts) == n:
            break
        r = radius * math.sqrt(rng.random())
        a = rng.random() * 2 * math.pi
        p = np.array([r * math.cos(a), r * math.sin(a)])
        if all(np.linalg.norm(p - q) >= min_sep for q in pts):
            pts.append(p)
    while len(pts) < n:                            # fall back to a jittered ring
        a = 2 * math.pi * (len(pts) + rng.random()) / n
        pts.append(np.array([radius * 0.8 * math.cos(a), radius * 0.8 * math.sin(a)]))
    centroid = np.mean(pts, axis=0)
    return [p - centroid for p in pts]
