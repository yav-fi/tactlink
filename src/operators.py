"""Operators - the people giving gestures.

Each runs a MediaPipe model (on a phone, in the real system) and can command the
drone. In the sim they wander around a shared area, each with a position and a
facing; direction commands like "forward" are taken relative to the operator who
gave them. For now one webcam feeds the ``active`` operator; the pool is built
for five.
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
            self.operators.append(Operator(f"op{i + 1}", pos, a + math.pi))  # face inward
        self.active = 0
        self.wander_enabled = True

    @property
    def active_op(self) -> Operator:
        return self.operators[self.active]

    def set_active_by_proximity(self, xy) -> int:
        """Control goes to the operator nearest the drone (horizontal distance)."""
        xy = np.asarray(xy, dtype=float)
        self.active = int(np.argmin([np.linalg.norm(op.pos - xy) for op in self.operators]))
        return self.active

    def step(self, dt: float) -> None:
        if self.wander_enabled:
            for op in self.operators:
                op.wander(dt)

    def resolve_forward_bearing(self) -> float:
        """World bearing (radians) for a 'forward' command from the active operator."""
        return self.active_op.heading
