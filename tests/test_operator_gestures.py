"""Tests for per-operator gesture interpreters.

    python tests/test_operator_gestures.py
    pytest tests/test_operator_gestures.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from operator_gestures import OperatorGestures, operator_key  # noqa: E402
from operators import OperatorPool  # noqa: E402
from phone_feed import PhoneReport  # noqa: E402


def _pool(*specs):
    pool = OperatorPool(1)
    pool.sync_from_reports(
        [PhoneReport(op_id=i, name=i, pos=(x, 0.0), heading=0.0, gesture=g)
         for i, x, g in specs],
        recenter=False,
    )
    return pool


def _drain(og, pool, active, seconds=1.2, **kw):
    fired = []
    for k in range(int(seconds / 0.05)):
        fired += og.update(pool, active, now=k * 0.05, **kw).events
    return fired


def test_active_operator_fires_its_own_gesture():
    pool = _pool(("A", 0.0, "Thumb_Up"), ("B", 3.0, "None"))
    assert _drain(OperatorGestures(), pool, "A") == ["takeoff"]


def test_non_active_operator_is_ignored():
    pool = _pool(("A", 0.0, "Thumb_Up"), ("B", 3.0, "Thumb_Up"))
    # B is also holding Thumb_Up, but only A has control.
    assert _drain(OperatorGestures(), pool, "A") == ["takeoff"]
    assert _drain(OperatorGestures(), pool, "B") == ["takeoff"]
    assert _drain(OperatorGestures(), pool, None) == []


def test_webcam_fills_in_when_active_operator_is_idle():
    pool = _pool(("A", 0.0, "None"), ("B", 3.0, "None"))
    fired = _drain(OperatorGestures(), pool, "A",
                   webcam_gesture="Thumb_Up", webcam_source="canned")
    assert fired == ["takeoff"]


def test_phone_gesture_wins_over_webcam():
    pool = _pool(("A", 0.0, "Thumb_Down"), ("B", 3.0, "None"))
    fired = _drain(OperatorGestures(), pool, "A",
                   webcam_gesture="Thumb_Up", webcam_source="canned")
    assert fired == ["land"]          # phone's Thumb_Down, not the webcam's Thumb_Up


def test_phone_geometric_labels_reach_only_active_operator():
    pool = _pool(("A", 0.0, "Dash_Left"), ("B", 3.0, "Three_Finger_Forward"))
    assert _drain(OperatorGestures(), pool, "A") == ["fly_left"]
    assert _drain(OperatorGestures(), pool, "B") == ["fly_forward"]
    pool = _pool(("A", 0.0, "Dash_Right"), ("B", 3.0, "None"))
    assert _drain(OperatorGestures(), pool, "A") == ["fly_right"]


def test_interpreters_are_pruned_when_operators_leave():
    og = OperatorGestures()
    pool = _pool(("A", 0.0, "None"), ("B", 3.0, "None"))
    og.update(pool, "A", now=0.0)
    assert set(og._by_key) == {"A", "B"}
    pool.sync_from_reports([PhoneReport(op_id="A", name="A", pos=(0.0, 0.0), heading=0.0)],
                           recenter=False)
    og.update(pool, "A", now=0.1)
    assert set(og._by_key) == {"A"}


def test_handoff_gives_the_new_operator_a_fresh_interpreter():
    og = OperatorGestures()
    pool = _pool(("A", 0.0, "Pointing_Up"), ("B", 3.0, "Pointing_Up"))
    # A holds long enough to fire; then control passes to B mid-hold.
    fired = []
    for k in range(20):
        fired += og.update(pool, "A", now=k * 0.05).events
    assert fired == ["orbit"]
    # B has been holding too, but its interpreter only starts counting now.
    more = []
    for k in range(4):
        more += og.update(pool, "B", now=1.0 + k * 0.05).events
    assert more == []                 # not enough hold time yet on B's own clock


def test_simulated_pool_still_works_without_a_feed():
    # No phone feed: operators have gesture "None", webcam drives the active one.
    pool = OperatorPool(3)
    active = operator_key(pool.operators[0])
    fired = _drain(OperatorGestures(), pool, active,
                   webcam_gesture="Thumb_Up", webcam_source="canned")
    assert fired == ["takeoff"]


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
