"""The fly_<compass> autopilot dash: direction, bounded distance, altitude hold.

    python tests/test_fly_maneuver.py
    pytest tests/test_fly_maneuver.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np  # noqa: E402

from control_types import GestureState, HandState  # noqa: E402
from controls import (  # noqa: E402
    _CLIMB_STEP, _DASH_DISTANCE, _ORBIT_RADIUS, _TAKEOFF_ALT, GestureController,
)
from simulator import QuadSimulator  # noqa: E402


def _drive(events_by_step, steps):
    ctl = GestureController()
    sim = QuadSimulator()
    for i in range(steps):
        cmd = ctl.update(HandState(present=False),
                         GestureState(events=list(events_by_step.get(i, []))), sim.state)
        sim.step(cmd, 1 / 60)
    return sim.state, ctl


def _armed_at(ctl, sim, alt=2.0):
    """Put the drone in the air the way a `takeoff` event would."""
    ctl._armed = True
    ctl._alt_target = alt
    sim.state.armed = True
    sim.state.pos[2] = alt


def _fly(direction: str, seconds: float = 6.0):
    ctl = GestureController()
    sim = QuadSimulator()
    _armed_at(ctl, sim)
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


def test_orbit_converges_to_ring_and_keeps_circling():
    ctl = GestureController()
    sim = QuadSimulator()
    _armed_at(ctl, sim)
    sim.state.pos[:2] = [1.0, 0.0]
    angles = []
    for i in range(int(30 / (1 / 60))):
        events = ["orbit"] if i == 0 else []
        cmd = ctl.update(HandState(present=False), GestureState(events=events), sim.state)
        sim.step(cmd, 1 / 60)
        if i % 120 == 0 and i > 1200:
            angles.append(np.arctan2(sim.state.pos[1], sim.state.pos[0]))
    r = float(np.linalg.norm(sim.state.pos[:2]))
    assert abs(r - _ORBIT_RADIUS) < 1.5, r
    assert sim.state.pos[2] > 1.0, sim.state.pos[2]
    # angle keeps advancing -> still circling
    assert len(set(np.round(angles, 1))) > 2, angles
    # nose points inward at the origin: forward vector (-sin yaw, cos yaw) ~ -pos
    fwd = np.array([-np.sin(sim.state.yaw), np.cos(sim.state.yaw)])
    inward = -sim.state.pos[:2] / r
    assert float(fwd @ inward) > 0.9, (fwd, inward)


def test_orbit_repeat_gesture_stops():
    ctl = GestureController()
    sim = QuadSimulator()
    _armed_at(ctl, sim)
    sim.state.pos[:2] = [1.0, 0.0]
    for i in range(600):
        cmd = ctl.update(HandState(present=False), GestureState(events=["orbit"] if i == 0 else []), sim.state)
        sim.step(cmd, 1 / 60)
    assert ctl.maneuver == "orbit"
    ctl.update(HandState(present=False), GestureState(events=["orbit"]), sim.state)
    assert ctl.maneuver == ""


def test_takeoff_then_thumbs_up_steps_altitude():
    # takeoff, let it settle, then two more "takeoff" events 3 s apart
    state, _ = _drive({0: ["takeoff"], 240: ["takeoff"], 540: ["takeoff"]}, 900)
    assert abs(state.pos[2] - (_TAKEOFF_ALT + 2 * _CLIMB_STEP)) < 0.4, state.pos[2]


def test_takeoff_holds_base_altitude():
    state, _ = _drive({0: ["takeoff"]}, 600)
    assert abs(state.pos[2] - _TAKEOFF_ALT) < 0.3, state.pos[2]


def test_fly_bearing_dashes_along_the_given_angle():
    import math
    ctl = GestureController()
    sim = QuadSimulator()
    _armed_at(ctl, sim)
    bearing = math.radians(30)               # east-north-east
    for i in range(360):
        ev = [f"fly_bearing:{bearing:.4f}"] if i == 0 else []
        cmd = ctl.update(HandState(present=False), GestureState(events=ev), sim.state)
        sim.step(cmd, 1 / 60)
    moved = np.array(sim.state.pos[:2])
    assert np.linalg.norm(moved) > 3.0
    # direction of travel ~ the commanded bearing
    ang = math.atan2(moved[1], moved[0])
    assert abs((ang - bearing + math.pi) % (2 * math.pi) - math.pi) < 0.3
    assert ctl.maneuver == ""


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
