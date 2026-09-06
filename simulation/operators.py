"""Live human operators on the ground, as seen by the mission runtime.

Each iPhone in the SignalMap UWB room already streams its own position, facing
and locally recognized hand gesture (``ios/SignalMap/RoomBridge.swift``). Those
datagrams reach this module either over UDP (``server.phone_listener``) or as
``POST /api/operators``. Here they become operator markers standing on real
ground beside the drones, and they decide which person each drone listens to.

Two problems sit between a phone report and a marker on the map:

*Anchoring.* The UWB room frame is arbitrary: its origin is wherever the mesh
happened to initialise and it has no north. :class:`OperatorFrame` pins one
nominated phone - the anchor - to a chosen point on the ground and rotates the
rest of the group about it, so the formation lands on the lawn facing the right
way while preserving the relative geometry UWB actually measured. Pairwise
ranging can observe the group's *shape* but never its absolute translation or
rotation, so that pinning is a declared convention, not a measurement.

*Control.* "The drone obeys whoever is nearest to it" is decided per drone and
needs hysteresis, or control flickers every time a drone passes between two
people. :class:`OperatorRegistry` keeps the current holder until someone else is
meaningfully closer.

Gestures are held, not tapped: a label must stay steady for ``HOLD_SECONDS``
before it fires once, and it cannot fire again until the hand relaxes. That is
the same contract as the standalone webcam simulator, applied per operator so
one person's timing never bleeds into another's.

This module is simulation-only and never commands real aircraft.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Callable

from .models import (
    MissionCommand,
    MissionTarget,
    OperatorFrame,
    OperatorState,
    TaskType,
    Vector3,
)

STALE_SECONDS = 3.0        # a phone that goes quiet this long stops being drawn
HOLD_SECONDS = 0.4         # how long a gesture must be held before it fires
HANDOFF_RATIO = 0.72       # a challenger must be this much closer to take control
MIN_CONFIDENCE = 0.55      # below this the phone's label is treated as no gesture

# Gesture label -> action, mirroring ``config/gesture_actions.json`` so the
# phones need no separate vocabulary for runtime mode. Labels mapped to None
# are deliberately inert: a misread of them must cost nothing.
GESTURE_ACTIONS: dict[str, str | None] = {
    "Thumb_Up": "takeoff",
    "Thumb_Down": "land",
    "Open_Palm": "halt",
    "Pointing_Up": "watch_me",
    "ILoveYou": "return_home",
    "Three_Finger_Forward": "fly_forward",
    "Dash_Left": "fly_left",
    "Dash_Right": "fly_right",
    "Closed_Fist": None,
    "Victory": None,
    "None": None,
}


@dataclass
class OperatorReport:
    """One phone's newest sample, parsed and clamped but not yet placed."""

    operator_id: str
    name: str
    group_pos: tuple[float, float]      # metres in the room's arbitrary UWB frame
    group_z: float = 0.0
    heading: float = 0.0                # radians in that same frame (walking only)
    compass: float = 0.0                # degrees clockwise from magnetic north
    compass_valid: bool = False
    gesture: str = "None"
    gesture_confidence: float = 0.0
    gesture_source: str = "phone"
    cycle: int = 0
    received_at: float = 0.0

    def enu_heading(self, rotation: float) -> float:
        """Facing in the scene frame (radians, 0 = +east, counter-clockwise).

        The absolute magnetic compass is preferred because it is meaningful
        while standing still; the UWB motion heading only exists while walking,
        and needs the same rotation as the positions.
        """
        if self.compass_valid:
            return math.radians(90.0 - self.compass)
        return self.heading + rotation


def parse_phone_datagram(data: bytes, now: float | None = None) -> OperatorReport | None:
    """Parse one ``RoomBridge`` UDP payload. Returns None for anything malformed.

    Deliberately total: a phone on a flaky link should never be able to raise
    inside the listener, so every field is validated and clamped here.
    """
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return report_from_payload(obj, now)


def report_from_payload(obj: dict, now: float | None = None) -> OperatorReport | None:
    """Build a report from an already-decoded ``RoomBridge`` sample."""
    operator_id = obj.get("id")
    if not isinstance(operator_id, str) or not operator_id:
        return None

    pos = obj.get("pos")
    if (not isinstance(pos, (list, tuple)) or len(pos) < 2
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pos[:2])):
        return None

    def number(key: str, default: float = 0.0) -> float:
        try:
            value = float(obj.get(key, default) or default)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    compass = number("compass")
    confidence = number("gestureConfidence")
    gesture = obj.get("gesture")
    cycle = obj.get("cycle", 0)
    return OperatorReport(
        operator_id=operator_id[:64],
        name=(str(obj.get("name"))[:32] if obj.get("name") else operator_id[:32]),
        group_pos=(float(pos[0]), float(pos[1])),
        group_z=number("z"),
        heading=number("heading"),
        compass=compass,
        compass_valid=bool(obj.get("compassValid", False)),
        gesture=(str(gesture)[:32] if gesture else "None"),
        gesture_confidence=max(0.0, min(1.0, confidence)),
        gesture_source=(str(obj.get("gestureSource"))[:16] if obj.get("gestureSource") else "phone"),
        cycle=int(cycle) if isinstance(cycle, (int, float)) and math.isfinite(cycle) else 0,
        received_at=time.monotonic() if now is None else now,
    )


@dataclass
class _Latch:
    """Fires a held gesture once, then waits for the hand to change."""

    label: str = "None"
    since: float = 0.0
    fired: bool = False

    def update(self, label: str, now: float) -> str | None:
        if label != self.label:
            self.label, self.since, self.fired = label, now, False
        if self.fired or label == "None":
            return None
        if now - self.since < HOLD_SECONDS:
            return None
        self.fired = True
        return label


@dataclass
class _Assignment:
    """Which operator a drone is currently listening to, and how far away."""

    operator_id: str = ""
    distance: float = math.inf


@dataclass
class GestureCommand:
    """One fired gesture, already attributed to a drone and an operator."""

    node_id: str
    operator: OperatorState
    action: str


class OperatorRegistry:
    """Newest report per phone, placed on the ground and routed to drones."""

    def __init__(self, frame: OperatorFrame | None = None,
                 stale_seconds: float = STALE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        # Phone samples and gesture holds are wall-clock events, not simulated
        # ones, so this deliberately does not use the engine's clock. It is
        # injectable purely so tests can hold a gesture without sleeping.
        self.clock = clock
        self.frame = frame or OperatorFrame()
        self.stale_seconds = float(stale_seconds)
        self.packets = 0
        self._reports: dict[str, OperatorReport] = {}
        self._latches: dict[str, _Latch] = {}
        self._control: dict[str, _Assignment] = {}
        self._states: list[OperatorState] = []

    # -- input ---------------------------------------------------------------

    def ingest(self, report: OperatorReport, now: float | None = None) -> None:
        """Accept one phone sample, stamped on arrival by the registry's clock.

        Arrival time is decided here rather than by the parser so that staleness
        and gesture holds are always measured against the same clock.
        """
        report.received_at = self.clock() if now is None else now
        self._reports[report.operator_id] = report
        self.packets += 1

    def set_frame(self, frame: OperatorFrame) -> OperatorFrame:
        self.frame = frame
        return self.frame

    def clear(self) -> None:
        self._reports.clear()
        self._latches.clear()
        self._control.clear()
        self._states = []

    @property
    def connected(self) -> int:
        return len(self._reports)

    # -- placement -----------------------------------------------------------

    def fresh_reports(self, now: float) -> list[OperatorReport]:
        """Reports still inside the staleness window, oldest ids first."""
        fresh = [r for r in self._reports.values() if now - r.received_at <= self.stale_seconds]
        self._reports = {r.operator_id: r for r in fresh}
        fresh.sort(key=lambda r: r.operator_id)
        return fresh

    def _anchor(self, reports: list[OperatorReport]) -> OperatorReport:
        named = next((r for r in reports if r.operator_id == self.frame.anchor_id), None)
        return named or reports[0]

    def place(self, reports: list[OperatorReport], now: float) -> list[OperatorState]:
        """Turn fresh reports into scene-frame operator states."""
        if not reports:
            return []
        anchor = self._anchor(reports)
        ax, ay = anchor.group_pos
        rotation = math.radians(self.frame.rotation_deg)
        cos_r, sin_r = math.cos(rotation), math.sin(rotation)

        states: list[OperatorState] = []
        for report in reports:
            dx = report.group_pos[0] - ax
            dy = report.group_pos[1] - ay
            east = self.frame.anchor_east + cos_r * dx - sin_r * dy
            north = self.frame.anchor_north + sin_r * dx + cos_r * dy
            gesture = report.gesture if report.gesture_confidence >= MIN_CONFIDENCE else "None"
            states.append(OperatorState(
                operator_id=report.operator_id,
                name=report.name,
                position=Vector3(x=east, y=north, z=self.frame.ground_z),
                heading=report.enu_heading(rotation),
                gesture=gesture,
                gesture_confidence=report.gesture_confidence,
                gesture_source=report.gesture_source,
                action=GESTURE_ACTIONS.get(gesture),
                is_anchor=report.operator_id == anchor.operator_id,
                age=max(0.0, now - report.received_at),
            ))
        return states

    # -- control -------------------------------------------------------------

    def _assign(self, states: list[OperatorState], drones: dict[str, Vector3]) -> None:
        """Give each drone the nearest operator, holding on through near ties."""
        by_id = {state.operator_id: state for state in states}
        for node_id in list(self._control):
            if node_id not in drones:
                del self._control[node_id]

        for node_id, position in sorted(drones.items()):
            distances = {
                state.operator_id: math.dist(
                    (state.position.x, state.position.y), (position.x, position.y)
                )
                for state in states
            }
            if not distances:
                self._control.pop(node_id, None)
                continue
            nearest = min(distances, key=lambda key: (distances[key], key))
            held = self._control.get(node_id)
            keeper = held.operator_id if held and held.operator_id in distances else None
            if keeper is not None and distances[nearest] >= distances[keeper] * HANDOFF_RATIO:
                nearest = keeper
            self._control[node_id] = _Assignment(nearest, distances[nearest])
            by_id[nearest].controls.append(node_id)

        for state in states:
            held = [self._control[node].distance for node in state.controls]
            state.nearest_distance = min(held) if held else None

    # -- tick ----------------------------------------------------------------

    def update(self, drones: dict[str, Vector3], now: float | None = None) -> list[GestureCommand]:
        """Refresh placement and control, and return gestures that just fired.

        Only the controlling operator's gestures reach a drone; everyone else's
        are still latched (so their timing stays their own) and then dropped.
        That is what enforces "the person closest to the drone is the one it
        acts on".
        """
        now = self.clock() if now is None else now
        states = self.place(self.fresh_reports(now), now)
        self._assign(states, drones)
        self._states = states

        live = {state.operator_id for state in states}
        for stale in [key for key in self._latches if key not in live]:
            del self._latches[stale]

        commands: list[GestureCommand] = []
        for state in states:
            latch = self._latches.setdefault(state.operator_id, _Latch())
            fired = latch.update(state.gesture, now)
            action = GESTURE_ACTIONS.get(fired) if fired else None
            if action is None:
                continue
            for node_id in state.controls:
                commands.append(GestureCommand(node_id, state, action))
        return commands

    def states(self) -> list[OperatorState]:
        return [state.model_copy(deep=True) for state in self._states]


# How far a dash gesture throws the drone, and the altitudes each action asks
# for. These are the runtime analogues of the standalone simulator's autopilot
# routines: the runtime has no orbit primitive, so "point up" becomes a WATCH
# task aimed at the person who gave it.
DASH_METRES = 25.0
TAKEOFF_ALTITUDE = 25.0
RETURN_ALTITUDE = 15.0
WATCH_ALTITUDE = 18.0
LANDING_ALTITUDE = 2.0
GESTURE_PRIORITY = 90


def gesture_mission(command: GestureCommand, drone: Vector3) -> MissionCommand | None:
    """Translate one fired gesture into a runtime mission command.

    The task is submitted to the ordinary auction rather than pinned to
    ``command.node_id``. In practice the commanded drone wins it, because the
    target sits next to that drone and the allocator bids on distance - but a
    better-placed peer is allowed to take it, which is the behaviour the rest of
    the fabric already relies on. ``metadata`` records who asked and which drone
    they were addressing so the console can show the intent either way.
    """
    action = command.action
    operator = command.operator
    point: Vector3 | None
    task_type = TaskType.GOTO

    if action == "takeoff":
        point = Vector3(x=operator.position.x, y=operator.position.y, z=TAKEOFF_ALTITUDE)
    elif action == "land":
        point = Vector3(x=drone.x, y=drone.y, z=LANDING_ALTITUDE)
    elif action == "return_home":
        point = Vector3(x=operator.position.x, y=operator.position.y, z=RETURN_ALTITUDE)
    elif action == "watch_me":
        task_type = TaskType.WATCH
        point = Vector3(x=operator.position.x, y=operator.position.y, z=WATCH_ALTITUDE)
    elif action == "halt":
        task_type = TaskType.HOLD
        point = None
    elif action in {"fly_forward", "fly_left", "fly_right"}:
        # Dashes are relative to the person, not the map: "forward" is wherever
        # the operator who gave the command is facing.
        offsets = {"fly_forward": 0.0, "fly_left": math.pi / 2, "fly_right": -math.pi / 2}
        bearing = operator.heading + offsets[action]
        point = Vector3(
            x=drone.x + DASH_METRES * math.cos(bearing),
            y=drone.y + DASH_METRES * math.sin(bearing),
            z=drone.z,
        )
    else:
        return None

    return MissionCommand(
        type=task_type,
        target=MissionTarget(point=point),
        priority=GESTURE_PRIORITY,
        desired_units=1,
        minimum_units=1,
        metadata={
            "source": "operator-gesture",
            "operator_id": operator.operator_id,
            "operator_name": operator.name,
            "gesture": operator.gesture,
            "action": action,
            "addressed_to": command.node_id,
        },
    )
