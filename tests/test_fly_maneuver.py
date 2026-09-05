"""The fly_<compass> autopilot dash: direction, bounded distance, altitude hold.

    python tests/test_fly_maneuver.py
    pytest tests/test_fly_maneuver.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np  # noqa: E402

from control_types import GestureState, HandState  # noqa: E402
from controls import _DASH_DISTANCE, GestureController  # noqa: E402
from simulator import QuadSimulator  # noqa: E402


def _fly(direction: str, seconds: float = 6.0):
    ctl = GestureController()
    sim = QuadSimulator()
    ctl._armed = True
    sim.state.armed = True
    sim.state.pos[2] = 2.0
    steps = int(seconds / (1 / 60))
    for i in range(steps):
        events = [direction] if i == 0 else []
        cmd = ctl.update(HandState(present=False), GestureState(events=events), sim.state)
        sim.step(cmd, 1 / 60)
    return sim.state, ctl


def test_fly_east_moves_positive_x_and_stops():
    state, ctl = _fly("fly_east")
    assert state.pos[0] > 3.0, state.pos          # went east
    assert abs(state.pos[1]) < 1.0, state.pos     # not north/south
    assert ctl.maneuver == "", "dash should finish"
    moved = float(np.linalg.norm(state.pos[:2]))
    assert moved <= _DASH_DISTANCE + 1.5, moved   # bounded


def test_fly_north_moves_positive_y():
    state, _ = _fly("fly_north")
    assert state.pos[1] > 3.0 and abs(state.pos[0]) < 1.0, state.pos


def test_fly_west_holds_altitude():
    state, _ = _fly("fly_west")
    assert state.pos[0] < -3.0, state.pos
    assert state.pos[2] > 1.0, f"altitude collapsed: {state.pos[2]}"


def test_fly_dash_ignored_when_disarmed():
    ctl = GestureController()
    sim = QuadSimulator()
    for i in range(120):
        events = ["fly_east"] if i == 0 else []
        cmd = ctl.update(HandState(present=False), GestureState(events=events), sim.state)
        sim.step(cmd, 1 / 60)
    assert ctl.maneuver == "" and abs(sim.state.pos[0]) < 0.5, sim.state.pos


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
