"""A squad of drones flown as one formation.

Drone 0 is the leader - it runs the full :class:`GestureController`, so every
gesture command drives it exactly as the single-drone app does. The other drones
hold a fixed world-frame offset from the leader (a small centred grid), so the
whole formation takes off, climbs, orbits, dashes and returns home together.

``n = 1`` collapses to the original single-drone behaviour.
"""

from __future__ import annotations

import math

import numpy as np

from control_types import ControlInput
from controls import GestureController
from simulator import QuadSimulator


def formation_slots(n: int, radius: float = 2.8) -> np.ndarray:
    """``n`` world-frame (dx, dy) offsets: leader at the centre, the rest evenly
    spaced on a ring around it (so the formation centroid stays on the leader)."""
    n = max(1, int(n))
    slots = [(0.0, 0.0)]
    for i in range(n - 1):
        a = 2 * math.pi * i / (n - 1) - math.pi / 2
        slots.append((radius * math.cos(a), radius * math.sin(a)))
    return np.array(slots, dtype=float)


class Swarm:
    def __init__(self, n: int = 5):
        self.n = max(1, int(n))
        self.slots = formation_slots(self.n)
        self.offsets = self.slots - self.slots[0]     # relative to the leader
        self.ctl = GestureController()
        self.drones = [QuadSimulator() for _ in range(self.n)]
        for drone, off in zip(self.drones, self.offsets):
            drone.state.pos[:2] = off
        self.selected = 0                             # highlighted drone (single-out later)

    @property
    def maneuver(self) -> str:
        return self.ctl.maneuver

    def reset(self) -> None:
        self.__init__(self.n)

    def step(self, hand, gstate, dt: float):
        leader = self.drones[0]
        cmd = self.ctl.update(hand, gstate, leader.state)
        leader.step(cmd, dt)
        lead = leader.state
        for drone, off in zip(self.drones[1:], self.offsets[1:]):
            drone.step(self._follow(drone.state, lead, off), dt)
        return lead, cmd

    @staticmethod
    def _follow(s, lead, offset) -> ControlInput:
        target = np.asarray(lead.pos[:2]) + offset
        err = target - np.asarray(s.pos[:2])
        cy, sy = math.cos(-s.yaw), math.sin(-s.yaw)
        body = np.array([cy * err[0] - sy * err[1], sy * err[0] + cy * err[1]])
        cmd = ControlInput(armed=lead.armed)
        cmd.roll = float(np.clip(body[0] * 0.9, -1.0, 1.0))
        cmd.pitch = float(np.clip(body[1] * 0.9, -1.0, 1.0))
        cmd.throttle = float(np.clip((lead.pos[2] - s.pos[2]) * 1.4 - s.vel[2] * 0.45,
                                     -0.45, 1.0))
        yaw_err = (lead.yaw - s.yaw + math.pi) % (2 * math.pi) - math.pi
        cmd.yaw_rate = float(np.clip(yaw_err * 1.5, -1.0, 1.0))
        return cmd

    def states(self):
        return [d.state for d in self.drones]

    def trails(self):
        return [d.trail for d in self.drones]
