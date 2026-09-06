"""Tests for the SignalMap phone ingest layer.

No camera, no simulator, no extra dependencies:

    python tests/test_phone_feed.py     # standalone
    pytest tests/test_phone_feed.py     # if pytest is installed
"""

import json
import math
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from operators import OperatorPool  # noqa: E402
from phone_feed import PhoneFeed, PhoneReport, parse_datagram  # noqa: E402


def _packet(**over):
    msg = {"v": 1, "id": "phone-1", "name": "Ian", "pos": [1.0, 2.0],
           "heading": 0.5, "moving": True, "headingReady": True,
           "gesture": "None", "cycle": 3}
    msg.update(over)
    return json.dumps(msg).encode("utf-8")


def test_parse_valid():
    r = parse_datagram(_packet(), now=10.0)
    assert isinstance(r, PhoneReport)
    assert r.op_id == "phone-1" and r.name == "Ian"
    assert r.pos == (1.0, 2.0) and abs(r.heading - 0.5) < 1e-9
    assert r.moving is True and r.cycle == 3 and r.recv_time == 10.0


def test_parse_rejects_junk():
    assert parse_datagram(b"not json") is None
    assert parse_datagram(b"[1, 2, 3]") is None                     # not an object
    assert parse_datagram(json.dumps({"id": "x"}).encode()) is None  # no pos
    assert parse_datagram(_packet(id="")) is None                    # empty id
    assert parse_datagram(_packet(pos=[1.0])) is None                # short pos
    assert parse_datagram(_packet(pos=["a", "b"])) is None           # non-numeric
    assert parse_datagram(_packet(pos=[float("nan"), 0.0])) is None  # not finite


def test_parse_tolerates_missing_optionals():
    r = parse_datagram(json.dumps({"id": "p", "pos": [0, 0]}).encode(), now=1.0)
    assert r.name == "p" and r.heading == 0.0 and r.moving is False
    assert r.gesture == "None" and r.cycle == 0
    assert r.compass_valid is False


def test_parse_compass():
    r = parse_datagram(_packet(compass=270.0, compassValid=True), now=1.0)
    assert r.compass == 270.0 and r.compass_valid is True
    r = parse_datagram(_packet(compass=270.0), now=1.0)   # compassValid absent
    assert r.compass_valid is False


def test_sim_heading_prefers_compass_then_falls_back():
    # compass 0 deg = facing north = sim +y = pi/2, regardless of rot.
    north = PhoneReport("a", "a", (0.0, 0.0), heading=1.234, compass=0.0, compass_valid=True)
    assert abs(north.sim_heading(rot=0.9) - math.pi / 2) < 1e-9
    # compass 90 deg (east) -> sim 0
    east = PhoneReport("a", "a", (0.0, 0.0), heading=0.0, compass=90.0, compass_valid=True)
    assert abs(east.sim_heading() - 0.0) < 1e-9
    # no compass -> motion heading + rot
    walk = PhoneReport("a", "a", (0.0, 0.0), heading=0.5, compass_valid=False)
    assert abs(walk.sim_heading(rot=0.25) - 0.75) < 1e-9


def test_pool_sync_uses_compass_for_facing():
    from operators import OperatorPool
    pool = OperatorPool(1)
    pool.sync_from_reports(
        [PhoneReport("A", "A", (-1.0, 0.0), heading=9.9, compass=0.0, compass_valid=True),
         PhoneReport("B", "B", (1.0, 0.0), heading=9.9, compass=0.0, compass_valid=True)],
        rot=1.3, recenter=False)
    # both report compass north -> both face sim +y (pi/2), rot ignored for facing
    for op in pool.operators:
        assert abs(op.heading - math.pi / 2) < 1e-9


def test_feed_receives_over_udp():
    feed = PhoneFeed(port=0, host="127.0.0.1", stale_sec=5.0)
    feed.start()
    try:
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(_packet(id="phone-7", name="Sam"), ("127.0.0.1", feed.port))
        got = []
        for _ in range(100):
            got = feed.latest()
            if got:
                break
            time.sleep(0.02)
        assert len(got) == 1 and got[0].op_id == "phone-7" and got[0].name == "Sam"
        assert feed.packets == 1
    finally:
        feed.stop()


def test_feed_keeps_newest_and_prunes_stale():
    feed = PhoneFeed(port=0, stale_sec=2.0)
    feed._table["a"] = parse_datagram(_packet(id="a"), now=100.0)
    feed._table["b"] = parse_datagram(_packet(id="b"), now=99.0)
    assert {r.op_id for r in feed.latest(now=101.0)} == {"a", "b"}   # b exactly 2 s old
    assert {r.op_id for r in feed.latest(now=101.5)} == {"a"}        # b now 2.5 s old
    assert feed.latest(now=200.0) == []


def test_from_arg_parsing():
    assert PhoneFeed.from_arg("9870").port == 9870
    assert PhoneFeed.from_arg("9870").host == "0.0.0.0"
    f = PhoneFeed.from_arg("192.168.1.5:9999")
    assert f.host == "192.168.1.5" and f.port == 9999


def test_pool_sync_add_update_drop():
    pool = OperatorPool(1)
    pool.sync_from_reports([PhoneReport("A", "Al", (0.0, 0.0), 0.0),
                            PhoneReport("B", "Bo", (2.0, 0.0), 0.0)], recenter=False)
    assert [op.name for op in pool.operators] == ["Al", "Bo"]
    assert pool.n == 2

    a_before = pool.operators[0]
    pool.sync_from_reports([PhoneReport("A", "Al", (5.0, 1.0), 1.2)], recenter=False)
    assert len(pool.operators) == 1
    assert pool.operators[0] is a_before                       # same object, updated in place
    assert tuple(pool.operators[0].pos) == (5.0, 1.0)
    assert abs(pool.operators[0].heading - 1.2) < 1e-9

    pool.sync_from_reports([], recenter=False)
    assert pool.operators == [] and pool.n == 0
    assert pool.resolve_forward_bearing() == 0.0
    assert pool.set_active_by_proximity((0.0, 0.0)) == 0       # no crash when empty


def test_pool_sync_rotation_and_recenter():
    pool = OperatorPool(1)
    # Two phones 2 m apart on the feed's x axis; rotate the frame 90 deg.
    pool.sync_from_reports([PhoneReport("A", "A", (-1.0, 0.0), 0.0),
                            PhoneReport("B", "B", (1.0, 0.0), 0.0)],
                           rot=math.pi / 2, recenter=True)
    pa, pb = pool.operators[0].pos, pool.operators[1].pos
    # centroid removed -> symmetric about origin; 90 deg rot maps +x onto +y
    assert abs(pa[0]) < 1e-9 and abs(pb[0]) < 1e-9
    assert abs(pa[1] + 1.0) < 1e-9 and abs(pb[1] - 1.0) < 1e-9
    # heading rotates with the frame
    assert abs(pool.operators[0].heading - math.pi / 2) < 1e-9


def test_pool_sync_origin_is_fixed_after_first_sync():
    pool = OperatorPool(1)
    pool.sync_from_reports([PhoneReport("A", "A", (10.0, 10.0), 0.0),
                            PhoneReport("B", "B", (12.0, 10.0), 0.0)], recenter=True)
    first_a = tuple(pool.operators[0].pos)
    # B walks away: A's rendered position must not move (origin stays put).
    pool.sync_from_reports([PhoneReport("A", "A", (10.0, 10.0), 0.0),
                            PhoneReport("B", "B", (40.0, 10.0), 0.0)], recenter=True)
    assert tuple(pool.operators[0].pos) == first_a


def test_forward_bearing_follows_synced_heading():
    pool = OperatorPool(1)
    pool.sync_from_reports([PhoneReport("A", "A", (0.0, 0.0), 1.0),
                            PhoneReport("B", "B", (3.0, 0.0), 2.0)], recenter=False)
    pool.active = 1
    assert abs(pool.resolve_forward_bearing() - 2.0) < 1e-9


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
