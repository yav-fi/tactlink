"""Gesture debouncing and geometric hand-pose classification for the demo.

No camera or MediaPipe here.

* ``GestureGate`` fires a command when a canned MediaPipe label is held.
* ``hand_from_landmarks`` turns 21 landmarks into finger-extension states and an
  index-pointing vector.
* ``classify_pose`` + ``HeldPose`` recognise the poses MediaPipe is unreliable
  about by counting fingers: two up = hand off, three up = dash forward, index
  held sideways = dash left / right.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Canned MediaPipe gesture -> command. Only the five the model is reliable at;
# the hand-off (fist) and dashes are counted from finger positions instead.
ACTIONS = {
    "Thumb_Up": "takeoff",     # arm + take off; again while flying = climb a step
    "Thumb_Down": "land",      # descend + disarm
    "Open_Palm": "halt",       # cancel the routine, hover in place
    "Pointing_Up": "orbit",    # circle the controlling operator (toggle)
    "ILoveYou": "return",      # fly back to the controlling operator
}

HOLD_SEC = 0.40
RELEASE_SEC = 0.25
GAP_SEC = 0.5             # a recognizer dropout shorter than this does not break a hold
HOLDS = {}


class GestureGate:
    """Fires a command once when its gesture is held long enough. Brief gaps
    (the recognizer losing the pose for < ``gap`` s) do not reset the hold; the
    hand must rest for ``release`` s before the same gesture can fire again."""

    def __init__(self, hold=HOLD_SEC, release=RELEASE_SEC, gap=GAP_SEC, holds=None):
        self.hold = hold
        self.release = release
        self.gap = gap
        self.holds = HOLDS if holds is None else holds
        self._held = "None"
        self._since = 0.0
        self._seen = 0.0
        self._fired = ""
        self._rest_since = None

    def update(self, label: str, now: float):
        """Returns ``(command | None, hold_progress 0..1, held_label)``."""
        active = label if label not in ("None", "") else None

        if active is not None:
            if active != self._held:
                self._held = active
                self._since = now
            self._seen = now
            self._rest_since = None
        else:
            if self._held != "None" and now - self._seen > self.gap:
                self._held = "None"
            if self._held == "None":
                if self._rest_since is None:
                    self._rest_since = now
                if now - self._rest_since >= self.release:
                    self._fired = ""

        if self._held == "None" or self._held not in ACTIONS or self._held == self._fired:
            return None, 0.0, self._held

        hold = self.holds.get(self._held, self.hold)
        progress = min(1.0, (now - self._since) / hold)
        if progress >= 1.0:
            self._fired = self._held
            return ACTIONS[self._held], 1.0, self._held
        return None, progress, self._held


# --- hand landmarks -------------------------------------------------------

@dataclass
class Hand:
    present: bool = False
    fingers: tuple = (False,) * 5      # thumb, index, middle, ring, pinky extended?
    point_dir: tuple = (0.0, 0.0)      # index (+ middle) direction, image space (x right, y down)

    @property
    def up_count(self) -> int:
        return sum(self.fingers[1:])   # index..pinky extended


def _d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hand_from_landmarks(pts) -> Hand:
    """21 (x, y) normalized landmarks -> :class:`Hand`. A finger counts as
    extended when its tip is farther from the wrist than its middle joint."""
    if not pts or len(pts) < 21:
        return Hand(present=False)
    w = pts[0]

    def ext(tip, pip):
        return _d(pts[tip], w) > _d(pts[pip], w)      # tip farther from wrist than the joint

    thumb = _d(pts[4], w) > _d(pts[2], w)
    fingers = (thumb, ext(8, 6), ext(12, 10), ext(16, 14), ext(20, 18))

    dx = pts[8][0] - pts[5][0]
    dy = pts[8][1] - pts[5][1]
    if fingers[2]:                     # fold in the middle finger only if it's out too
        dx += pts[12][0] - pts[9][0]
        dy += pts[12][1] - pts[9][1]
    n = math.hypot(dx, dy)
    point_dir = (dx / n, dy / n) if n > 1e-6 else (0.0, 0.0)
    return Hand(present=True, fingers=fingers, point_dir=point_dir)


# --- geometric held poses ----------------------------------------------

_POSE_HOLD = {"handoff_random": 0.5, "dash_forward": 0.55,
              "dash_east": 0.5, "dash_west": 0.5}
_POSE_LABEL = {"handoff_random": "hand off (fist)",
               "dash_forward": "dash forward (3 fingers)",
               "dash_east": "dash right", "dash_west": "dash left"}
_SIDE_DX = 0.45
# canned gestures that ARE a command, so the hand must not also read as a pose
_CANNED_COMMANDS = {"Open_Palm", "Thumb_Up", "Thumb_Down", "Pointing_Up", "ILoveYou"}


def classify_pose(hand: Hand | None, canned: str = "None") -> str | None:
    """Which held finger-pose command the hand is making, or None. Fist = hand
    off, three-ish fingers up = dash forward, index held sideways = dash L/R."""
    if canned in _CANNED_COMMANDS:
        return None
    if hand is None or not hand.present:
        return None
    up = hand.up_count
    dx, dy = hand.point_dir
    sideways = abs(dx) >= _SIDE_DX and abs(dx) >= abs(dy) * 1.2

    if up == 0:                                      # closed fist
        return "handoff_random"
    if up >= 3:                                      # three (or four) fingers up
        return "dash_forward"
    if up in (1, 2) and sideways:                    # one/two fingers held sideways
        return "dash_east" if dx > 0 else "dash_west"
    return None


class HeldPose:
    """Fires a geometric pose command once it is held long enough; the pose must
    drop for ``release`` s before the same command can fire again."""

    def __init__(self, release: float = 0.4, gap: float = 0.35):
        self.release = release
        self.gap = gap
        self._pose = None
        self._since = 0.0
        self._seen = -1e9
        self._fired = None
        self._rest_since = None

    def update(self, pose: str | None, now: float):
        """Returns ``(command | None, progress 0..1, pose_label)``."""
        if pose is not None:
            if pose != self._pose:
                self._pose = pose
                self._since = now
            self._seen = now
            self._rest_since = None
        else:
            if self._pose is not None and now - self._seen > self.gap:
                self._pose = None
            if self._pose is None:
                if self._rest_since is None:
                    self._rest_since = now
                if now - self._rest_since >= self.release:
                    self._fired = None

        if self._pose is None or self._pose == self._fired:
            return None, 0.0, ""

        progress = min(1.0, (now - self._since) / _POSE_HOLD.get(self._pose, 0.6))
        label = _POSE_LABEL.get(self._pose, self._pose)
        if progress >= 1.0:
            self._fired = self._pose
            return self._pose, 1.0, label
        return None, progress, label
