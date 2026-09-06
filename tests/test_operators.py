"""Operator pool: wandering people, one active, forward-bearing lookup.

    python tests/test_operators.py
    pytest tests/test_operators.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np  # noqa: E402

from operators import Operator, OperatorPool  # noqa: E402


def test_pool_size_and_active():
    pool = OperatorPool(5)
    assert len(pool.operators) == 5
    assert pool.active_op is pool.operators[0]
    pool.active = 3
    assert pool.active_op.name == "op4"


def test_active_is_nearest_operator_to_the_drone():
    pool = OperatorPool(5)
    pool.wander_enabled = False
    pool.operators[0].pos[:] = [10.0, 0.0]
    pool.operators[3].pos[:] = [0.5, 0.5]
    assert pool.set_active_by_proximity([0.0, 0.0]) == 3
    assert pool.set_active_by_proximity([11.0, 0.0]) == 0


def test_proximity_has_hysteresis():
    pool = OperatorPool(2)
    pool.wander_enabled = False
    pool.operators[0].pos[:] = [-5.0, 0.0]
    pool.operators[1].pos[:] = [5.0, 0.0]
    assert pool.set_active_by_proximity([-5.0, 0.0]) == 0
    # just barely past the midpoint toward op1 - should NOT flip yet
    assert pool.set_active_by_proximity([0.3, 0.0]) == 0
    # clearly closer to op1 now
    assert pool.set_active_by_proximity([4.0, 0.0]) == 1


def test_forward_bearing_is_active_operator_heading():
    pool = OperatorPool(3)
    pool.wander_enabled = False
    pool.operators[1].heading = math.radians(90)
    pool.active = 1
    assert abs(pool.resolve_forward_bearing() - math.radians(90)) < 1e-6


def test_phone_relative_dashes_follow_active_operator_facing():
    pool = OperatorPool(1)
    pool.active_op.heading = math.radians(90)  # facing north
    forward = float(pool.resolve_relative_event("fly_forward").split(":")[1])
    left = float(pool.resolve_relative_event("fly_left").split(":")[1])
    right = float(pool.resolve_relative_event("fly_right").split(":")[1])
    assert abs(forward - math.pi / 2) < 1e-4
    assert abs(left - math.pi) < 1e-4
    assert abs(right) < 1e-4
    assert pool.resolve_relative_event("halt") == "halt"


def test_wander_moves_and_stays_bounded():
    op = Operator("t", (0.0, 0.0), 0.0)
    for _ in range(600):
        op.wander(1 / 60)
    assert np.linalg.norm(op.pos) > 0.5          # actually moved
    assert np.all(np.abs(op.pos) < 16.0)         # stayed in the area


def test_pool_step_no_wander_is_static():
    pool = OperatorPool(4)
    pool.wander_enabled = False
    before = [op.pos.copy() for op in pool.operators]
    for _ in range(120):
        pool.step(1 / 60)
    for a, op in zip(before, pool.operators):
        assert np.allclose(a, op.pos)


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
