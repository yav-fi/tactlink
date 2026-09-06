import math
import unittest

import numpy as np

from flight import Autopilot, Operators, Quad
from gesture_demo import StableLabel
from gestures import FingerSwingDetector, GestureGate, Hand, hand_from_landmarks


def _row(*xs):
    """An Operators with a fixed left-to-right layout, for deterministic tests."""
    ops = Operators(len(xs), seed=0)
    ops.pos = [np.array([float(x), 0.0]) for x in xs]
    ops.n = len(xs)
    return ops


def _point(dx, dy):
    n = math.hypot(dx, dy)
    return Hand(present=True, fingers=(False, True, True, False, False),
                point_dir=(dx / n, dy / n))


class SettlingTests(unittest.TestCase):
    def test_pose_must_be_held_and_loss_clears_immediately(self):
        state = StableLabel(.35)
        self.assertEqual(state.update('Open palm', .9, 1)[0], 'Settling...')
        self.assertEqual(state.update('Open palm', .9, 1.4)[0], 'Open palm')
        self.assertEqual(state.update('Unknown', 0, 1.41), ('Unknown', 0, 0))


class GestureGateTests(unittest.TestCase):
    def test_flight_gesture_fires_after_short_hold(self):
        gate = GestureGate(hold=0.4)
        self.assertIsNone(gate.update('Thumb_Up', 0.0)[0])
        self.assertEqual(gate.update('Thumb_Up', 0.45)[0], 'takeoff')
        self.assertIsNone(gate.update('Thumb_Up', 0.9)[0])

    def test_victory_needs_a_long_hold(self):
        gate = GestureGate(holds={'Victory': 2.0})
        t = 0.0
        while t < 1.9:
            self.assertIsNone(gate.update('Victory', t)[0])
            t += 0.1
        self.assertEqual(gate.update('Victory', 2.05)[0], 'handoff_random')

    def test_brief_recognizer_dropout_does_not_reset_the_hold(self):
        gate = GestureGate(holds={'Victory': 2.0}, gap=0.45)
        fired, t = None, 0.0
        while fired is None and t < 3.0:
            label = 'None' if (round(t, 1) % 0.5 == 0.0 and t > 0) else 'Victory'
            fired, _, _ = gate.update(label, t)
            t += 0.1
        self.assertEqual(fired, 'handoff_random')
        self.assertLess(t, 2.8)

    def test_fist_does_nothing(self):
        gate = GestureGate()
        for t in range(20):
            self.assertIsNone(gate.update('Closed_Fist', t * 0.2)[0])


class WiperTests(unittest.TestCase):
    def _swing(self, order):
        sw = FingerSwingDetector()
        t, fired = 0.0, None
        for phase in order:
            pd = (0.05, -1.0) if phase == 'V' else (1.0, 0.05) if phase == 'Hr' else (-1.0, 0.05)
            for _ in range(6):
                t += 0.05
                fired = sw.update(_point(*pd), t) or fired
        return fired

    def test_full_swing_dashes_the_way_the_fingers_point(self):
        self.assertEqual(self._swing(['V', 'Hr', 'V', 'Hr']), 'dash_east')
        self.assertEqual(self._swing(['V', 'Hl', 'V', 'Hl']), 'dash_west')

    def test_partial_swing_does_not_fire(self):
        self.assertIsNone(self._swing(['V', 'Hr', 'V']))

    def test_one_finger_point_is_ignored(self):
        sw = FingerSwingDetector()
        one = Hand(present=True, fingers=(False, True, False, False, False), point_dir=(1.0, 0.0))
        for k in range(40):
            self.assertIsNone(sw.update(one, k * 0.05))


class DashTests(unittest.TestCase):
    def test_dash_nudges_sideways_then_holds(self):
        from flight import DASH_DISTANCE
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        quad = Quad(ops.anchor_pos())
        pilot = Autopilot(ops)
        pilot.command('takeoff', quad, 0.0)
        for _ in range(200):
            pilot.update(quad, 1 / 30, 0.0)
        x0 = quad.pos[0]
        pilot.command('dash_east', quad, 5.0)
        for k in range(400):
            pilot.update(quad, 1 / 30, 5.0 + k / 30)
        self.assertAlmostEqual(quad.pos[0] - x0, DASH_DISTANCE, delta=0.6)
        self.assertEqual(pilot.mode, 'halt')      # holds, does not spring back


class AimHandoffTests(unittest.TestCase):
    def test_point_selects_the_operator_that_way_and_holds_to_send(self):
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        drone = np.array([0.0, 4.0])
        ops.anchor = 1
        self.assertEqual(ops.aim_from_point((1.0, 0.0), drone), 3)     # point right
        self.assertEqual(ops.aim_from_point((-1.0, 0.0), drone), 0)    # point left
        self.assertIsNone(ops.aim_from_point((0.1, 0.0), drone))       # limp hand

        for k in range(60):
            ops.aim(3, k * 0.05)
        self.assertGreaterEqual(ops.aim_progress(59 * 0.05), 1.0)
        self.assertEqual(ops.commit_aim(), 3)
        self.assertEqual(ops.anchor, 3)

    def test_random_handoff_never_picks_the_holder(self):
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        ops.rng = np.random.default_rng(1)
        for _ in range(40):
            before = ops.anchor
            self.assertNotEqual(before, ops.random_handoff())


class NoseTrackingTests(unittest.TestCase):
    def test_nose_turns_to_face_the_nearest_operator(self):
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        quad = Quad((1.5, 3.0))
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
        self.assertGreater(pos[:, 0].std(), 0.3)
        self.assertGreater(pos[:, 1].std(), 0.3)
        for i in range(4):
            for j in range(i + 1, 4):
                self.assertGreater(np.linalg.norm(a.pos[i] - a.pos[j]), 2.0)


class LandmarkTests(unittest.TestCase):
    def test_two_finger_pose_detected_from_landmarks(self):
        pts = [(0.5, 0.9)] * 21
        pts[0] = (0.5, 0.9)                          # wrist
        pts[5], pts[6], pts[8] = (0.5, 0.7), (0.5, 0.55), (0.5, 0.3)    # index out (up)
        pts[9], pts[10], pts[12] = (0.55, 0.7), (0.55, 0.55), (0.55, 0.3)  # middle out
        pts[13], pts[14] = (0.6, 0.7), (0.6, 0.72)   # ring curled
        pts[17], pts[18] = (0.65, 0.7), (0.65, 0.72)  # pinky curled
        hand = hand_from_landmarks(pts)
        self.assertTrue(hand.present)
        self.assertTrue(hand.two_fingers)


if __name__ == '__main__':
    unittest.main()
