"""A lightweight arcade quadcopter model.

World frame: x = right, y = forward, z = up, all in meters. The model is not
aerodynamically accurate; it is tuned to feel responsive and stay stable for a
demo. Commands come in as a normalized :class:`ControlInput`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from control_types import ControlInput

MAX_SPEED = 4.0        # m/s horizontal at full stick
MAX_CLIMB = 2.5        # m/s vertical at full throttle
MAX_YAW_RATE = 2.2     # rad/s at full yaw stick
ACCEL = 2.5            # 1/s first-order response toward the target velocity
MAX_BANK = math.radians(28)  # visual tilt at full stick
GRAVITY = 6.0         # m/s^2 sink rate when disarmed


@dataclass
class QuadState:
    pos: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0]))
    vel: np.ndarray = field(default_factory=lambda: np.zeros(3))
    yaw: float = 0.0
    roll: float = 0.0      # visual only
    pitch: float = 0.0     # visual only
    armed: bool = False
    on_ground: bool = True
    rotor_phase: float = 0.0


class QuadSimulator:
    def __init__(self) -> None:
        self.state = QuadState()
        self.trail: list[np.ndarray] = []
        self._trail_max = 240

    def reset(self) -> None:
        self.state = QuadState()
        self.trail.clear()

    def step(self, cmd: ControlInput, dt: float) -> QuadState:
        dt = float(np.clip(dt, 1e-3, 0.1))
        s = self.state
        s.armed = cmd.armed

        s.yaw += cmd.yaw_rate * MAX_YAW_RATE * dt

        if cmd.armed:
            # Target horizontal velocity in body frame, rotated into the world.
            body = np.array([cmd.roll * MAX_SPEED, cmd.pitch * MAX_SPEED])
            cy, sy = math.cos(s.yaw), math.sin(s.yaw)
            world_xy = np.array([cy * body[0] - sy * body[1],
                                 sy * body[0] + cy * body[1]])
            target_v = np.array([world_xy[0], world_xy[1], cmd.throttle * MAX_CLIMB])
            s.on_ground = False
        else:
            # No lift: settle horizontally and sink toward the ground.
            target_v = np.array([0.0, 0.0, -GRAVITY])

        s.vel += (target_v - s.vel) * min(1.0, ACCEL * dt)
        s.pos = s.pos + s.vel * dt

        if s.pos[2] <= 0.0:
            s.pos[2] = 0.0
            s.vel[2] = max(0.0, s.vel[2])
            if not cmd.armed:
                s.vel[:] = 0.0
                s.on_ground = True

        # Visual attitude lags toward the commanded bank.
        target_roll = -cmd.roll * MAX_BANK
        target_pitch = cmd.pitch * MAX_BANK
        s.roll += (target_roll - s.roll) * min(1.0, 8.0 * dt)
        s.pitch += (target_pitch - s.pitch) * min(1.0, 8.0 * dt)

        spin = 55.0 if s.armed else (12.0 if not s.on_ground else 0.0)
        s.rotor_phase = (s.rotor_phase + spin * dt) % (2 * math.pi)

        self.trail.append(s.pos.copy())
        if len(self.trail) > self._trail_max:
            self.trail.pop(0)

        return s
