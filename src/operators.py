"""Operators - the people giving gestures.

Each runs a MediaPipe model (on a phone, in the real system) and can command the
drone. In the sim they wander around a shared area, each with a position and a
facing; direction commands like "forward" are taken relative to the operator who
gave them. Positions come either from the built-in simulation (a ring, optional
wander) or from live iPhone datagrams via ``sync_from_reports`` (see
``phone_feed``). For now one webcam feeds whichever operator has control; the
pool is built for five.
"""

from __future__ import annotations

import math

import numpy as np

_AREA = 12.0          # operators wander within +/- this many metres
_WALK_SPEED = 0.6     # m/s
_TURN_RATE = 0.5      # rad/s of heading drift


class Operator:
    def __init__(self, name: str, pos, heading: float):
        self.name = name
        self.pos = np.array(pos, dtype=float)
        self.heading = float(heading)          # radians, world frame (0 = +x / east)
        self.op_id = ""                        # phone id when driven by a live feed
        self._goal = self.pos.copy()
        self._rng = np.random.default_rng(abs(hash(name)) % (2**32))

    def facing_vec(self) -> np.ndarray:
        return np.array([math.cos(self.heading), math.sin(self.heading)])

    def wander(self, dt: float) -> None:
        if float(np.linalg.norm(self._goal - self.pos)) < 0.5:
            self._goal = self._rng.uniform(-_AREA, _AREA, size=2)
        step = self._goal - self.pos
        step = step / (np.linalg.norm(step) + 1e-6) * _WALK_SPEED * dt
        self.pos += step
        # Face roughly the way of travel, with a little drift.
        want = math.atan2(step[1], step[0])
        err = (want - self.heading + math.pi) % (2 * math.pi) - math.pi
        self.heading += float(np.clip(err, -_TURN_RATE * dt, _TURN_RATE * dt))


class OperatorPool:
    def __init__(self, n: int = 5):
        self.n = max(1, int(n))
        self.operators = []
        for i in range(self.n):
            a = 2 * math.pi * i / self.n
            pos = (7.0 * math.cos(a), 7.0 * math.sin(a))
            self.operators.append(Operator(f"op{i + 1}", pos, math.pi / 2))  # face north (+y)
        self.active = 0
        self.wander_enabled = False   # off until real phone positions drive it
        self._sync_origin = None      # frame offset locked on the first live sync

    @property
    def active_op(self) -> Operator:
        return self.operators[self.active]

    def sync_from_reports(self, reports, rot: float = 0.0, recenter: bool = True) -> None:
        """Replace the operator list with live phone reports (see phone_feed).

        ``reports`` is any iterable of objects with ``op_id``, ``name``, ``pos``
        (x, y) and ``heading`` (radians). ``rot`` rotates the phones' arbitrary
        UWB frame into the sim frame (radians); ``recenter`` subtracts the group
        centroid, fixed on the first non-empty sync so the geometry stays stable.
        """
        reports = list(reports)
        if not reports:
            self.operators = []
            self.n = 0
            self.active = 0
            self._sync_origin = None
            return

        c, s = math.cos(rot), math.sin(rot)
        pts = {r.op_id: np.array([c * r.pos[0] - s * r.pos[1],
                                  s * r.pos[0] + c * r.pos[1]], dtype=float)
               for r in reports}
        if recenter:
            if self._sync_origin is None:
                self._sync_origin = np.mean(list(pts.values()), axis=0)
            pts = {k: v - self._sync_origin for k, v in pts.items()}

        by_id = {op.op_id: op for op in self.operators if op.op_id}
        kept = []
        for r in reports:
            op = by_id.get(r.op_id)
            if op is None:
                op = Operator(r.name or r.op_id, pts[r.op_id], float(r.heading) + rot)
                op.op_id = r.op_id
            else:
                op.pos[:] = pts[r.op_id]
                op.heading = float(r.heading) + rot
                op.name = r.name or op.name
            kept.append(op)
        self.operators = kept
        self.n = len(kept)
        if self.active >= self.n:
            self.active = 0

    def set_active_by_proximity(self, xy, hysteresis: float = 0.72) -> int:
        """Control goes to the operator nearest the drone, with hysteresis so it
        doesn't flicker when the drone passes between two people."""
        if not self.operators:
            return 0
        xy = np.asarray(xy, dtype=float)
        dists = [float(np.linalg.norm(op.pos - xy)) for op in self.operators]
        nearest = int(np.argmin(dists))
        if dists[nearest] < dists[self.active] * hysteresis:
            self.active = nearest
        return self.active

    def step(self, dt: float) -> None:
        if self.wander_enabled:
            for op in self.operators:
                op.wander(dt)

    def resolve_forward_bearing(self) -> float:
        """World bearing (radians) for a 'forward' command from the active operator."""
        if not self.operators:
            return 0.0
        return self.active_op.heading
