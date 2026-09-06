"""Phones on the ground: anchoring, nearest-operator control, gesture routing."""

from __future__ import annotations

import json
import math

import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from simulation.models import OperatorFrame, TaskType, Vector3
from simulation.operators import (
    HOLD_SECONDS,
    OperatorRegistry,
    gesture_mission,
    parse_phone_datagram,
    report_from_payload,
)
from simulation.simulation import SimulationEngine


def sample(operator_id: str, x: float, y: float, *, gesture: str = "None",
           confidence: float = 0.9, compass: float | None = None, name: str | None = None) -> dict:
    """One `RoomBridge` datagram body."""
    payload = {
        "v": 1, "id": operator_id, "name": name or operator_id, "room": "r",
        "t": 1.0, "cycle": 3, "pos": [x, y], "z": 0.0, "heading": 0.0,
        "moving": False, "speed": 0.0, "headingReady": True,
        "gesture": gesture, "gestureConfidence": confidence,
        "gestureSource": "vision", "flat": False,
    }
    if compass is not None:
        payload["compass"] = compass
        payload["compassValid"] = True
    return payload


def feed(registry: OperatorRegistry, now: float, *samples: dict) -> None:
    for payload in samples:
        report = report_from_payload(payload)
        assert report is not None
        registry.ingest(report, now=now)


def by_id(registry: OperatorRegistry) -> dict:
    return {state.operator_id: state for state in registry.states()}


# -- parsing -----------------------------------------------------------------

def test_parses_a_real_roombridge_datagram():
    report = parse_phone_datagram(json.dumps(sample("ian", 1.5, -2.0, gesture="Thumb_Up")).encode())
    assert report is not None
    assert report.operator_id == "ian"
    assert report.group_pos == (1.5, -2.0)
    assert report.gesture == "Thumb_Up"


@pytest.mark.parametrize("payload", [
    b"not json", b"[]", b'{"id": ""}', b'{"id": "a"}',
    b'{"id": "a", "pos": [1]}', b'{"id": "a", "pos": ["x", 2]}',
    json.dumps({"id": "a", "pos": [float("nan"), 0]}).encode(),
])
def test_malformed_datagrams_are_dropped_not_raised(payload):
    assert parse_phone_datagram(payload) is None


def test_compass_beats_motion_heading_and_maps_north_to_plus_y():
    report = report_from_payload(sample("a", 0, 0, compass=0.0))
    assert report.enu_heading(0.0) == pytest.approx(math.pi / 2)   # north = +y
    east = report_from_payload(sample("a", 0, 0, compass=90.0))
    assert east.enu_heading(0.0) == pytest.approx(0.0)             # east = +x


# -- anchoring ---------------------------------------------------------------

def test_anchor_phone_lands_on_its_configured_point_and_others_stay_relative():
    registry = OperatorRegistry(OperatorFrame(
        anchor_id="ian", anchor_east=10.0, anchor_north=-45.0))
    feed(registry, 100.0,
         sample("ian", 5.0, 5.0), sample("alvan", 8.0, 5.0), sample("yavin", 5.0, 9.0))
    placed = {s.operator_id: s for s in registry.place(registry.fresh_reports(100.0), 100.0)}

    assert (placed["ian"].position.x, placed["ian"].position.y) == (10.0, -45.0)
    assert placed["ian"].is_anchor
    # Relative geometry that UWB actually measured is preserved.
    assert (placed["alvan"].position.x, placed["alvan"].position.y) == (13.0, -45.0)
    assert (placed["yavin"].position.x, placed["yavin"].position.y) == (10.0, -41.0)


def test_frame_rotation_turns_the_whole_group_about_the_anchor():
    registry = OperatorRegistry(OperatorFrame(
        anchor_id="ian", anchor_east=0.0, anchor_north=0.0, rotation_deg=90.0))
    feed(registry, 100.0, sample("ian", 0.0, 0.0), sample("alvan", 4.0, 0.0))
    placed = {s.operator_id: s for s in registry.place(registry.fresh_reports(100.0), 100.0)}
    assert placed["alvan"].position.x == pytest.approx(0.0, abs=1e-9)
    assert placed["alvan"].position.y == pytest.approx(4.0)


def test_missing_anchor_falls_back_to_the_first_phone_by_id():
    registry = OperatorRegistry(OperatorFrame(anchor_id="absent", anchor_east=0, anchor_north=0))
    feed(registry, 100.0, sample("zeta", 3.0, 3.0), sample("alpha", 1.0, 1.0))
    placed = {s.operator_id: s for s in registry.place(registry.fresh_reports(100.0), 100.0)}
    assert placed["alpha"].is_anchor
    assert (placed["alpha"].position.x, placed["alpha"].position.y) == (0.0, 0.0)


def test_quiet_phones_stop_being_drawn():
    registry = OperatorRegistry(stale_seconds=2.0)
    feed(registry, 100.0, sample("ian", 0, 0), sample("alvan", 3, 0))
    feed(registry, 103.0, sample("alvan", 3, 0))
    assert [s.operator_id for s in registry.place(registry.fresh_reports(103.0), 103.0)] == ["alvan"]


# -- control -----------------------------------------------------------------

def test_each_drone_listens_to_the_operator_nearest_to_it():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0))
    feed(registry, 100.0, sample("ian", 0, 0), sample("alvan", 100, 0))
    registry.update({"drone-1": Vector3(x=5, y=0, z=20),
                     "drone-2": Vector3(x=95, y=0, z=20)}, now=100.0)
    by_id = {s.operator_id: s for s in registry.states()}
    assert by_id["ian"].controls == ["drone-1"]
    assert by_id["alvan"].controls == ["drone-2"]


def test_control_holds_through_a_near_tie_then_hands_off_when_clearly_closer():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0))
    feed(registry, 100.0, sample("ian", 0, 0), sample("alvan", 20, 0))
    registry.update({"drone-1": Vector3(x=1, y=0, z=20)}, now=100.0)
    assert by_id(registry)["ian"].controls == ["drone-1"]    # ian is closest

    # A drone drifting just past the midpoint must not flip control.
    registry.update({"drone-1": Vector3(x=11, y=0, z=20)}, now=100.5)
    holder = next(s for s in registry.states() if s.controls)
    assert holder.operator_id == "ian"

    # Standing right next to Alvan does hand it over.
    registry.update({"drone-1": Vector3(x=19, y=0, z=20)}, now=101.0)
    holder = next(s for s in registry.states() if s.controls)
    assert holder.operator_id == "alvan"


# -- gestures ----------------------------------------------------------------

def test_a_gesture_must_be_held_then_fires_exactly_once():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0))
    drones = {"drone-1": Vector3(x=1, y=0, z=20)}

    feed(registry, 100.0, sample("ian", 0, 0, gesture="Thumb_Up"))
    assert registry.update(drones, now=100.0) == []                     # not held yet

    feed(registry, 100.0 + HOLD_SECONDS, sample("ian", 0, 0, gesture="Thumb_Up"))
    fired = registry.update(drones, now=100.0 + HOLD_SECONDS)
    assert [c.action for c in fired] == ["takeoff"]

    feed(registry, 101.0, sample("ian", 0, 0, gesture="Thumb_Up"))
    assert registry.update(drones, now=101.0) == []                     # held, not repeated


def test_a_noncontrolling_operator_cannot_move_the_drone():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0))
    drones = {"drone-1": Vector3(x=1, y=0, z=20)}
    # Alvan is far away and gesturing; Ian is next to the drone and is not.
    for now in (100.0, 100.0 + HOLD_SECONDS):
        feed(registry, now, sample("ian", 0, 0), sample("alvan", 100, 0, gesture="Thumb_Up"))
        fired = registry.update(drones, now=now)
    assert fired == []


def test_a_low_confidence_label_is_treated_as_no_gesture():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0))
    drones = {"drone-1": Vector3(x=1, y=0, z=20)}
    for now in (100.0, 100.0 + HOLD_SECONDS):
        feed(registry, now, sample("ian", 0, 0, gesture="Thumb_Up", confidence=0.2))
        fired = registry.update(drones, now=now)
    assert fired == []
    assert by_id(registry)["ian"].gesture == "None"


def test_losing_a_phone_does_not_leave_its_gesture_latched():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0),
                                stale_seconds=1.0)
    drones = {"drone-1": Vector3(x=1, y=0, z=20)}
    feed(registry, 100.0, sample("ian", 0, 0, gesture="Thumb_Up"))
    registry.update(drones, now=100.0)
    registry.update(drones, now=102.0)          # phone went quiet
    assert registry.states() == []
    assert registry.update(drones, now=102.5) == []


def test_dashes_are_resolved_against_the_operators_own_facing():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0))
    drones = {"drone-1": Vector3(x=0, y=0, z=20)}
    # Facing east (compass 90), so "forward" is +x and "left" is +y.
    for now in (100.0, 100.0 + HOLD_SECONDS):
        feed(registry, now, sample("ian", 0, 0, gesture="Three_Finger_Forward", compass=90.0))
        fired = registry.update(drones, now=now)
    mission = gesture_mission(fired[0], drones["drone-1"])
    assert mission.type == TaskType.GOTO
    assert mission.target.point.x == pytest.approx(25.0)
    assert mission.target.point.y == pytest.approx(0.0, abs=1e-9)
    assert mission.target.point.z == pytest.approx(20.0)   # a dash holds altitude


def test_halt_becomes_a_hold_with_no_destination():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0))
    drones = {"drone-1": Vector3(x=0, y=0, z=20)}
    for now in (100.0, 100.0 + HOLD_SECONDS):
        feed(registry, now, sample("ian", 0, 0, gesture="Open_Palm"))
        fired = registry.update(drones, now=now)
    mission = gesture_mission(fired[0], drones["drone-1"])
    assert mission.type == TaskType.HOLD
    assert mission.target.point is None


def test_disabled_labels_never_produce_a_command():
    registry = OperatorRegistry(OperatorFrame(anchor_id="ian", anchor_east=0, anchor_north=0))
    drones = {"drone-1": Vector3(x=0, y=0, z=20)}
    for now in (100.0, 100.0 + HOLD_SECONDS):
        feed(registry, now, sample("ian", 0, 0, gesture="Closed_Fist"))
        fired = registry.update(drones, now=now)
    assert fired == []


# -- engine and API ----------------------------------------------------------

def test_engine_stays_deterministic_when_no_phone_is_publishing():
    first = SimulationEngine(drone_count=3)
    second = SimulationEngine(drone_count=3)
    for _ in range(5):
        first.tick()
        second.tick()
    assert first.snapshot().operators == []
    assert [d.truth.position for d in first.snapshot().drones] \
        == [d.truth.position for d in second.snapshot().drones]


def test_three_phones_reach_the_snapshot_the_frontend_reads():
    engine = SimulationEngine(drone_count=3)
    app = create_app(engine=engine, start_runner=False, phone_feed_port=0)
    with TestClient(app) as client:
        client.post("/api/operators/frame", json={
            "anchor_id": "ian", "anchor_east": 0.0, "anchor_north": -45.0}).raise_for_status()
        for payload in (sample("ian", 0, 0, name="Ian"),
                        sample("alvan", 4, 0, name="Alvan"),
                        sample("yavin", 2, 3, name="Yavin")):
            assert client.post("/api/operators", json=payload).status_code == 202

        engine.tick()
        snapshot = client.get("/api/state").json()

    people = {item["operator_id"]: item for item in snapshot["operators"]}
    assert set(people) == {"ian", "alvan", "yavin"}
    assert (people["ian"]["position"]["x"], people["ian"]["position"]["y"]) == (0.0, -45.0)
    assert people["ian"]["is_anchor"] is True
    assert people["yavin"]["position"]["z"] == 0.0            # standing on the ground
    # Every drone is being listened to by exactly one person.
    assigned = [node for item in snapshot["operators"] for node in item["controls"]]
    assert sorted(assigned) == ["drone-1", "drone-2", "drone-3"]


def test_a_held_gesture_creates_a_runtime_mission():
    engine = SimulationEngine(drone_count=2)
    # Gesture holds are wall-clock, so drive the registry's clock instead of
    # sleeping through HOLD_SECONDS.
    clock = {"now": 100.0}
    engine.operators.clock = lambda: clock["now"]
    app = create_app(engine=engine, start_runner=False, phone_feed_port=0)

    with TestClient(app) as client:
        client.post("/api/operators/frame", json={
            "anchor_id": "ian", "anchor_east": -40.0, "anchor_north": -30.0}).raise_for_status()
        assert client.get("/api/missions").json() == []
        for _ in range(4):
            client.post("/api/operators", json=sample("ian", 0, 0, gesture="ILoveYou"))
            engine.tick()
            clock["now"] += 0.2
        missions = client.get("/api/missions").json()

    gestured = [task for task in missions
                if task["metadata"].get("source") == "operator-gesture"]
    # One person alone in the field is the nearest operator to both drones, so
    # one held gesture commands both - once each, not once per tick.
    assert len(gestured) == 2
    assert sorted(task["metadata"]["addressed_to"] for task in gestured) == ["drone-1", "drone-2"]
    for task in gestured:
        assert task["type"] == "GOTO"
        assert task["metadata"]["operator_id"] == "ian"
        assert task["metadata"]["action"] == "return_home"
        # It sends the drone to where that person is standing.
        assert (task["target"]["point"]["x"], task["target"]["point"]["y"]) == (-40.0, -30.0)
