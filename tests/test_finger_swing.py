"""Two-finger V<->H wiper detector - fires fly_<compass>, ignores other poses.

    python tests/test_finger_swing.py
    pytest tests/test_finger_swing.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from control_types import HandState  # noqa: E402
from finger_swing import FingerSwingDetector, ThreeFingerForward  # noqa: E402


def _hand(dx, dy, two=True, present=True):
    return HandState(present=present, fingers=(False, two, two, False, False),
                     point_dir=(dx, dy))


def _three_hand(dx, dy, three=True):
    return HandState(present=True, fingers=(False, True, True, three, False),
                     point_dir=(dx, dy))


class Runner:
    def __init__(self):
        self.d = FingerSwingDetector()
        self.t = 0.0
        self.fired = []

    def feed(self, dx, dy, seconds=0.4, two=True, present=True, dt=0.05):
        for _ in range(max(1, round(seconds / dt))):
            self.t += dt
            self.fired += self.d.update(_hand(dx, dy, two, present), self.t)
        return self

    def wiper(self, h_dx, h_dy):
        return (self.feed(0.03, -0.99).feed(h_dx, h_dy)
                    .feed(0.03, -0.99).feed(h_dx, h_dy))


def test_wiper_right_fires_east():
    assert Runner().wiper(0.98, 0.05).fired == ["fly_east"]


def test_wiper_left_fires_west():
    assert Runner().wiper(-0.98, 0.05).fired == ["fly_west"]


def test_half_swing_does_not_fire():
    assert Runner().feed(0.0, -1.0).feed(0.9, 0.1).fired == []


def test_needs_two_fingers():
    assert Runner().wiper(0.98, 0.05).__class__  # sanity
    r = Runner()
    r.feed(0.0, -1.0, two=False).feed(0.9, 0.1, two=False)
    r.feed(0.0, -1.0, two=False).feed(0.9, 0.1, two=False)
    assert r.fired == []


def test_swing_resets_if_pose_lost():
    r = Runner()
    r.feed(0.0, -1.0).feed(0.9, 0.1)          # V, H
    r.feed(0.0, 0.0, seconds=1.0, present=False)  # hand gone > LOST_SEC
    r.feed(0.0, -1.0).feed(0.9, 0.1)          # V, H again - only 2 now
    assert r.fired == []


def test_three_fingers_sideways_fires_forward_once():
    d = ThreeFingerForward()
    t, fired = 0.0, []
    for _ in range(16):                       # ~0.8 s held sideways
        t += 0.05
        fired += d.update(_three_hand(0.98, 0.05), t)
    assert fired == ["fly_forward"]
    for _ in range(6):                        # still held -> no repeat
        t += 0.05
        fired += d.update(_three_hand(0.98, 0.05), t)
    assert fired == ["fly_forward"]


def test_three_fingers_vertical_does_not_fire():
    d = ThreeFingerForward()
    t, fired = 0.0, []
    for _ in range(20):
        t += 0.05
        fired += d.update(_three_hand(0.02, -0.99), t)
    assert fired == []


def test_two_fingers_do_not_fire_forward():
    d = ThreeFingerForward()
    t, fired = 0.0, []
    for _ in range(20):
        t += 0.05
        fired += d.update(_three_hand(0.98, 0.05, three=False), t)
    assert fired == []


def _run_standalone():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
