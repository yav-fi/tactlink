"""Deterministic tests for gesture combos and the single-gesture delay/cancel.

Runs with no camera, no simulator, no drone, no extra dependencies:

    python tests/test_sequences.py     # standalone
    pytest tests/test_sequences.py     # if pytest is installed

Everything is driven by a fake clock so timing is exact and reproducible.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from gestures import GestureInterpreter  # noqa: E402
from sequences import SequenceMatcher  # noqa: E402

RETURN_HOME = [{"pattern": ["Open_Palm", "Closed_Fist", "Open_Palm"],
                "window": 3.0, "action": "return_home"}]
DOUBLE_FIST = [{"pattern": ["Closed_Fist", "Closed_Fist"],
                "window": 1.2, "action": "estop"}]
WIPER = [{"pattern": ["Victory", "v_flat", "Victory", "v_flat"],
          "window": 5.0, "action": "fly_pointed"}]


class _Hand:
    def __init__(self, point_dir=(0.0, 0.0)):
        self.present = True
        self.point_dir = point_dir


class Clock:
    """Feeds a gesture into the interpreter frame by frame and collects events."""

    def __init__(self, interp: GestureInterpreter, hand=None):
        self.interp = interp
        self.hand = hand
        self.t = 0.0
        self.events: list[str] = []

    def hold(self, gesture: str, seconds: float, dt: float = 0.05):
        for _ in range(max(1, round(seconds / dt))):
            self.t += dt
            state = self.interp.update(gesture, "canned", now=self.t, hand=self.hand)
            self.events.extend(state.events)
        return self

    def drain(self):
        out, self.events = self.events, []
        return out


def _interp(sequences, single_delay):
    return GestureInterpreter(SequenceMatcher(sequences), single_delay=single_delay)


def test_no_config_fires_single_immediately():
    c = Clock(_interp([], 0.0))
    c.hold("Open_Palm", 0.6)
    assert c.drain() == ["takeoff"]


def test_lone_combo_member_still_fires_after_delay():
    c = Clock(_interp(RETURN_HOME, 0.6))
    c.hold("Open_Palm", 0.6).hold("None", 2.5)
    assert c.drain() == ["takeoff"]  # held back, then released once no combo formed


def test_full_combo_fires_action_and_suppresses_singles():
    c = Clock(_interp(RETURN_HOME, 0.6))
    c.hold("Open_Palm", 0.6).hold("None", 0.3)
    c.hold("Closed_Fist", 0.6).hold("None", 0.3)
    c.hold("Open_Palm", 0.6).hold("None", 0.6)
    fired = c.drain()
    assert fired == ["return_home"], fired
    assert "takeoff" not in fired and "land" not in fired


def test_combo_with_realistic_gaps_suppresses_singles():
    # ~0.7 s poses with ~0.4 s no-hand gaps - the shape a person actually makes.
    c = Clock(_interp(RETURN_HOME, 0.6))
    c.hold("Open_Palm", 0.7).hold("None", 0.4)
    c.hold("Closed_Fist", 0.7).hold("None", 0.4)
    c.hold("Open_Palm", 0.7).hold("None", 1.0)
    fired = c.drain()
    assert fired == ["return_home"], fired


def test_combo_outside_window_does_not_fire():
    c = Clock(_interp(RETURN_HOME, 0.6))
    c.hold("Open_Palm", 0.6).hold("None", 1.6)
    c.hold("Closed_Fist", 0.6).hold("None", 1.6)
    c.hold("Open_Palm", 0.6).hold("None", 0.6)
    fired = c.drain()
    assert "return_home" not in fired, fired
    # the poses fall back to their own single-gesture commands instead
    assert "takeoff" in fired and "land" in fired


def test_non_combo_gesture_is_not_delayed():
    c = Clock(_interp(RETURN_HOME, 0.6))
    c.hold("Victory", 0.5)
    assert c.drain() == ["mode: position"]  # HEADING -> POSITION, no wait


def test_double_token_needs_a_gap():
    # Holding one long fist is a single token: the [fist, fist] combo must NOT fire.
    c = Clock(_interp(DOUBLE_FIST, 0.6))
    c.hold("Closed_Fist", 1.5).hold("None", 0.6)
    assert "estop" not in c.drain()

    # fist, release, fist -> two tokens -> estop.
    c = Clock(_interp(DOUBLE_FIST, 0.6))
    c.hold("Closed_Fist", 0.5).hold("None", 0.3)
    c.hold("Closed_Fist", 0.5).hold("None", 0.5)
    assert "estop" in c.drain()


def test_wiper_combo_resolves_fly_direction():
    for point_dir, expected in [((0.9, 0.1), "fly_east"),
                                ((-0.9, 0.1), "fly_west"),
                                ((0.1, -0.9), "fly_north")]:
        c = Clock(_interp(WIPER, 0.6), hand=_Hand(point_dir))
        c.hold("Victory", 0.6).hold("None", 0.3)
        c.hold("v_flat", 0.6).hold("None", 0.3)
        c.hold("Victory", 0.6).hold("None", 0.3)
        c.hold("v_flat", 0.6).hold("None", 0.4)
        fired = c.drain()
        assert fired == [expected], (point_dir, fired)
        assert "mode: heading" not in fired and "mode: position" not in fired


def test_sequence_hint_reports_partial_combo():
    interp = _interp(RETURN_HOME, 0.6)
    t = 0.0
    for _ in range(12):
        t += 0.05
        s = interp.update("Open_Palm", "canned", now=t)
    assert s.sequence_hint.startswith("Open_Palm>")


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
