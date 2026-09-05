"""Squad formation: one gesture drives the whole swarm, formation is kept.

    python tests/test_swarm.py
    pytest tests/test_swarm.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np  # noqa: E402

from control_types import GestureState, HandState  # noqa: E402
from controls import _TAKEOFF_ALT  # noqa: E402
from swarm import Swarm, formation_slots  # noqa: E402


def _run(sw, events_by_step, steps):
    for i in range(steps):
        sw.step(HandState(present=False),
                GestureState(events=list(events_by_step.get(i, []))), 1 / 60)
    return sw


def test_formation_slots_shape_and_balance():
    for n in (1, 2, 3, 5, 8):
        s = formation_slots(n)
        assert s.shape == (n, 2)
        assert np.allclose(s[0], [0, 0])              # leader at centre
    for n in (3, 5, 8):                               # symmetric ring -> centroid ~ leader
        assert np.linalg.norm(formation_slots(n).mean(axis=0)) < 0.6


def test_one_drone_matches_single_flight():
    sw = _run(Swarm(1), {0: ["takeoff"]}, 600)
    assert abs(sw.states()[0].pos[2] - _TAKEOFF_ALT) < 0.4
    assert sw.states()[0].armed


def test_whole_swarm_takes_off_together():
    sw = _run(Swarm(5), {0: ["takeoff"]}, 600)
    alts = [s.pos[2] for s in sw.states()]
    assert all(abs(a - _TAKEOFF_ALT) < 0.5 for a in alts), alts
    assert all(s.armed for s in sw.states())


def test_formation_held_during_dash():
    sw = _run(Swarm(5), {0: ["takeoff"], 240: ["fly_east"]}, 900)
    pos = np.array([s.pos[:2] for s in sw.states()])
    rel = pos - pos[0]                              # offsets from the leader
    assert np.allclose(rel, sw.offsets, atol=1.2), rel
    assert pos[0][0] > 3.0                          # leader actually went east


def test_return_home_recentres_the_swarm():
    sw = _run(Swarm(5), {0: ["takeoff"], 240: ["fly_east"], 700: ["return_home"]}, 1600)
    centre = np.mean([s.pos[:2] for s in sw.states()], axis=0)
    assert np.linalg.norm(centre) < 1.5, centre


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
