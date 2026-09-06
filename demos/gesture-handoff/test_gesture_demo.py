import math
import unittest

import numpy as np

from flight import Autopilot, Operators, Quad
from gesture_demo import StableLabel
from gestures import GestureGate, Hand, HeldPose, classify_pose, hand_from_landmarks


def _row(*xs):
    """An Operators with a fixed left-to-right layout, for deterministic tests."""
    ops = Operators(len(xs), seed=0)
    ops.pos = [np.array([float(x), 0.0]) for x in xs]
    ops.n = len(xs)
    return ops


def _hand(fingers, dx=0.0, dy=0.0):
    n = math.hypot(dx, dy) or 1.0
    return Hand(present=True, fingers=fingers, point_dir=(dx / n, dy / n))


ONE = (False, True, False, False, False)
TWO = (False, True, True, False, False)
THREE = (False, True, True, True, False)


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

    def test_brief_dropout_does_not_reset_the_hold(self):
        gate = GestureGate(hold=1.0, gap=0.5)
        fired, t = None, 0.0
        while fired is None and t < 2.0:
            label = 'None' if (round(t, 1) % 0.5 == 0.0 and t > 0) else 'Pointing_Up'
            fired, _, _ = gate.update(label, t)
            t += 0.1
        self.assertEqual(fired, 'orbit')
        self.assertLess(t, 1.7)

    def test_victory_and_fist_do_nothing_in_the_gate(self):
        gate = GestureGate()
        for t in range(20):
            self.assertIsNone(gate.update('Victory', t * 0.2)[0])
            self.assertIsNone(gate.update('Closed_Fist', t * 0.2)[0])


FIST = (False, False, False, False, False)


class ClassifyPoseTests(unittest.TestCase):
    def test_finger_counts_and_directions(self):
        self.assertEqual(classify_pose(_hand(FIST)), 'handoff_random')         # fist
        self.assertEqual(classify_pose(_hand(THREE, dy=-1.0)), 'dash_forward')
        self.assertEqual(classify_pose(_hand(THREE, dx=1.0)), 'dash_forward')  # any orientation
        self.assertEqual(classify_pose(_hand(ONE, dx=1.0)), 'dash_east')
        self.assertEqual(classify_pose(_hand(ONE, dx=-1.0)), 'dash_west')
        self.assertEqual(classify_pose(_hand(TWO, dx=1.0)), 'dash_east')       # 2 fingers sideways

    def test_two_up_and_pointing_up_are_not_poses(self):
        self.assertIsNone(classify_pose(_hand(TWO, dy=-1.0)))     # peace sign does nothing now
        self.assertIsNone(classify_pose(_hand(ONE, dy=-1.0)))     # that's orbit (canned)
        self.assertIsNone(classify_pose(None))

    def test_a_canned_command_is_not_re_read_as_a_pose(self):
        self.assertIsNone(classify_pose(_hand(THREE, dy=-1.0), 'Open_Palm'))
        self.assertIsNone(classify_pose(_hand(FIST), 'Pointing_Up'))


class HeldPoseTests(unittest.TestCase):
    def _hold(self, pose, seconds=2.0):
        h = HeldPose()
        fired, t = None, 0.0
        while fired is None and t < seconds:
            t += 0.05
            fired, _, _ = h.update(pose, t)
        return fired, t

    def test_fires_after_its_hold_and_survives_a_blip(self):
        fired, t = self._hold('dash_east')
        self.assertEqual(fired, 'dash_east')
        self.assertLess(t, 0.8)                       # dash hold ~0.5 s

        h = HeldPose()
        fired = None
        t = 0.0
        while fired is None and t < 2.0:
            t += 0.05
            p = None if round(t, 2) == 0.35 else 'dash_forward'   # one-frame blip
            fired, _, _ = h.update(p, t)
        self.assertEqual(fired, 'dash_forward')
        self.assertLess(t, 1.0)

    def test_needs_a_release_before_re_firing(self):
        h = HeldPose()
        for k in range(20):
            h.update('dash_east', k * 0.05)
        self.assertIsNone(h.update('dash_east', 1.5)[0])   # still held, no repeat


class DashTests(unittest.TestCase):
    def test_dash_forward_moves_away_then_holds(self):
        from flight import DASH_DISTANCE
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        quad = Quad(ops.anchor_pos())
        pilot = Autopilot(ops)
        pilot.command('takeoff', quad, 0.0)
        for _ in range(200):
            pilot.update(quad, 1 / 30, 0.0)
        y0 = quad.pos[1]
        pilot.command('dash_forward', quad, 5.0)
        for k in range(400):
            pilot.update(quad, 1 / 30, 5.0 + k / 30)
        self.assertAlmostEqual(quad.pos[1] - y0, DASH_DISTANCE, delta=0.7)
        self.assertEqual(pilot.mode, 'halt')


class RandomHandoffTests(unittest.TestCase):
    def test_never_picks_the_current_holder_and_is_random(self):
        ops = _row(-4.5, -1.5, 1.5, 4.5)
        ops.rng = np.random.default_rng(1)
        seen = set()
        for _ in range(40):
            before = ops.anchor
            new = ops.random_handoff()
            self.assertEqual(new, ops.anchor)
            self.assertNotEqual(before, new)
            seen.add(new)
        self.assertGreater(len(seen), 1)


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
    def test_finger_counts_from_landmarks(self):
        pts = [(0.5, 0.9)] * 21                       # everything at the wrist = curled
        pts[5], pts[6], pts[8] = (0.5, 0.7), (0.5, 0.55), (0.5, 0.30)     # index out
        pts[9], pts[10], pts[12] = (0.55, 0.7), (0.55, 0.55), (0.55, 0.30)  # middle out
        pts[13], pts[14], pts[16] = (0.6, 0.7), (0.6, 0.55), (0.6, 0.30)  # ring out
        hand = hand_from_landmarks(pts)
        self.assertTrue(hand.present)
        self.assertEqual(hand.up_count, 3)


if __name__ == '__main__':
    unittest.main()
