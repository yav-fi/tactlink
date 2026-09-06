import math
import unittest

import numpy as np

from flight import Autopilot, Operators, Quad
from gesture_demo import StableLabel
from gestures import GestureGate


def _row(*xs):
    """An Operators with a fixed left-to-right layout, for deterministic tests."""
    ops = Operators(len(xs), seed=0)
    ops.pos = [np.array([float(x), 0.0]) for x in xs]
    ops.n = len(xs)
    return ops


class SettlingTests(unittest.TestCase):
    def test_pose_must_be_held_and_loss_clears_immediately(self):
        state = StableLabel(.35)
        self.assertEqual(state.update('Open palm', .9, 1)[0], 'Settling...')
        self.assertEqual(state.update('Open palm', .9, 1.4)[0], 'Open palm')
        self.assertEqual(state.update('Unknown', 0, 1.41), ('Unknown', 0, 0))

    def test_switching_pose_restarts_settling(self):
        state = StableLabel(.35)
        state.update('Open palm', .9, 1)
        state.update('Open palm', .9, 1.4)
        self.assertEqual(state.update('Closed fist', .8, 1.5)[0], 'Settling...')
        self.assertEqual(state.update('Closed fist', .8, 1.9)[0], 'Closed fist')


class GestureGateTests(unittest.TestCase):
    def test_flight_gesture_fires_after_short_hold(self):
        gate = GestureGate(hold=0.4)
        self.assertIsNone(gate.update('Thumb_Up', 0.0)[0])
        self.assertEqual(gate.update('Thumb_Up', 0.45)[0], 'takeoff')
        self.assertIsNone(gate.update('Thumb_Up', 0.9)[0])       # held, no repeat

    def test_victory_needs_a_long_hold(self):
        gate = GestureGate(holds={'Victory': 2.0})
        t = 0.0
        while t < 1.9:
            self.assertIsNone(gate.update('Victory', t)[0])
            t += 0.1
        self.assertEqual(gate.update('Victory', 2.05)[0], 'handoff')

    def test_brief_recognizer_dropout_does_not_reset_the_hold(self):
        gate = GestureGate(holds={'Victory': 2.0}, gap=0.45)
        fired = None
        t = 0.0
        while fired is None and t < 3.0:
            # lose the pose for one ~0.1 s frame every ~0.5 s
            label = 'None' if (round(t, 1) % 0.5 == 0.0 and t > 0) else 'Victory'
            fired, _, _ = gate.update(label, t)
            t += 0.1
        self.assertEqual(fired, 'handoff')
        self.assertLess(t, 2.8)                 # still finished near the 2 s mark

    def test_fist_does_nothing(self):
        gate = GestureGate()
        for t in range(20):
            self.assertIsNone(gate.update('Closed_Fist', t * 0.2)[0])


class RandomHandoffTests(unittest.TestCase):
    def test_handoff_is_random_and_returns_the_new_anchor(self):
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        ops.rng = np.random.default_rng(3)
        seen = set()
        for _ in range(12):
            new = ops.random_handoff()
            self.assertEqual(new, ops.anchor)
            seen.add(new)
        self.assertGreater(len(seen), 1)                              # actually random

    def test_never_hands_off_to_the_current_holder(self):
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        ops.rng = np.random.default_rng(1)
        for _ in range(50):
            before = ops.anchor
            after = ops.random_handoff()
            self.assertNotEqual(before, after)

    def test_victory_hold_flies_the_drone_to_the_new_operator(self):
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        ops.rng = np.random.default_rng(0)
        quad = Quad(ops.anchor_pos())
        pilot = Autopilot(ops)
        gate = GestureGate()
        pilot.command('takeoff', quad, 0.0)
        for _ in range(180):
            pilot.update(quad, 1 / 30, 0.0)

        t, fired = 1.0, None
        while fired is None and t < 6:                              # hold Victory
            fired = gate.update('Victory', t)[0]
            t += 1 / 30
        self.assertEqual(fired, 'handoff')
        pilot.command('handoff', quad, t)
        new = ops.anchor
        for k in range(500):
            pilot.update(quad, 1 / 30, t + k / 30)
        self.assertLess(np.linalg.norm(quad.pos[:2] - ops.pos[new]), 1.2)


class NoseTrackingTests(unittest.TestCase):
    def test_nose_turns_to_face_the_nearest_operator(self):
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        quad = Quad((1.5, 3.0))                  # hold in front of OP3
        quad.pos[2] = 3.0
        quad.armed = True
        pilot = Autopilot(ops)
        pilot.mode = "halt"
        pilot._halt_xy = quad.pos[:2].copy()
        pilot.alt_target = 3.0
        for _ in range(240):
            pilot.update(quad, 1 / 60, 0.0)
        to_op = ops.pos[2] - quad.pos[:2]
        want = math.atan2(to_op[1], to_op[0])
        err = math.atan2(math.sin(want - quad.yaw), math.cos(want - quad.yaw))
        self.assertLess(abs(err), 0.2)


class ScatterTests(unittest.TestCase):
    def test_operators_are_random_2d_and_spaced(self):
        a = Operators(4, seed=1)
        b = Operators(4, seed=2)
        self.assertFalse(np.allclose(np.array(a.pos), np.array(b.pos)))
        self.assertTrue(np.allclose(np.mean(a.pos, axis=0), 0.0, atol=1e-9))
        pos = np.array(a.pos)
        self.assertGreater(pos[:, 0].std(), 0.3)       # spread in x
        self.assertGreater(pos[:, 1].std(), 0.3)       # and in y (not a row)
        for i in range(4):
            for j in range(i + 1, 4):
                self.assertGreater(np.linalg.norm(a.pos[i] - a.pos[j]), 2.0)


if __name__ == '__main__':
    unittest.main()
