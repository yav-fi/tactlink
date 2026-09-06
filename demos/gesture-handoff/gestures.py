"""Gesture debouncing, the finger-pointing vector, and the two-finger wiper.

No camera or MediaPipe here - this takes a gesture *label* per frame plus 21
hand landmarks, and turns deliberate poses into commands: a held canned gesture
(``GestureGate``), a steady sideways point (used by ``flight.Operators`` to aim
a hand-off), and a vertical<->horizontal "wiper" swing (``FingerSwingDetector``)
that dashes the drone left or right.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Canned MediaPipe gesture name -> demo command.
ACTIONS = {
    "Thumb_Up": "takeoff",        # arm + take off; again while flying = climb a step
    "Thumb_Down": "land",         # descend + disarm
    "Open_Palm": "halt",          # cancel the routine, hover in place
    "Pointing_Up": "orbit",       # circle the controlling operator (toggle)
    "ILoveYou": "return",         # fly back to the controlling operator
    "Victory": "handoff_random",  # hold ~2 s -> hand the drone to a random operator
}

HOLD_SEC = 0.40
RELEASE_SEC = 0.20
GAP_SEC = 0.45             # a recognizer dropout shorter than this does not break a hold
HOLDS = {"Victory": 2.0}   # per-gesture hold overrides


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
    point_dir: tuple = (0.0, 0.0)      # index+middle direction, image space (x right, y down)

    @property
    def two_fingers(self) -> bool:
        f = self.fingers
        return bool(f[1] and f[2] and not f[3] and not f[4])


def _d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hand_from_landmarks(pts) -> Hand:
    """21 (x, y) normalized landmarks -> :class:`Hand`."""
    if not pts or len(pts) < 21:
        return Hand(present=False)
    wrist = pts[0]

    def extended(tip, pip):
        return _d(pts[tip], wrist) > _d(pts[pip], wrist) * 1.06

    thumb = _d(pts[4], pts[0]) > _d(pts[3], pts[0]) * 1.10
    fingers = (thumb, extended(8, 6), extended(12, 10),
               extended(16, 14), extended(20, 18))

    dx = (pts[8][0] - pts[5][0]) + (pts[12][0] - pts[9][0])
    dy = (pts[8][1] - pts[5][1]) + (pts[12][1] - pts[9][1])
    n = math.hypot(dx, dy)
    point_dir = (dx / n, dy / n) if n > 1e-6 else (0.0, 0.0)
    return Hand(present=True, fingers=fingers, point_dir=point_dir)


# --- two-finger wiper ----------------------------------------------------

_WINDOW_SEC = 4.0
_STABLE_SEC = 0.12
_LOST_SEC = 0.6
_V_MAX_DEG = 35.0
_H_MIN_DEG = 55.0
_H_MAX_DEG = 125.0
_PATTERN = ["V", "H", "V", "H"]
_POINT_HORIZONTAL = 0.5


def orientation(point_dir) -> str | None:
    dx, dy = point_dir
    if dx == 0.0 and dy == 0.0:
        return None
    angle = abs(math.degrees(math.atan2(dx, -dy)))    # 0 = up, 90 = horizontal
    if angle <= _V_MAX_DEG:
        return "V"
    if _H_MIN_DEG <= angle <= _H_MAX_DEG:
        return "H"
    return None


def dash_from_point(point_dir) -> str | None:
    """Which way the fingers point on the last horizontal -> a dash command.
    Mirrored webcam: +x (your right) = the drone's +x (scene right)."""
    dx, dy = point_dir
    if abs(dx) >= _POINT_HORIZONTAL and abs(dx) >= abs(dy):
        return "dash_east" if dx > 0 else "dash_west"
    return None


class FingerSwingDetector:
    """Hold index+middle out and swing vertical->horizontal->vertical->horizontal
    within a few seconds -> a dash the way the fingers point on the last swing."""

    def __init__(self, window: float = _WINDOW_SEC):
        self._window = window
        self._orient = None
        self._raw = None
        self._raw_since = 0.0
        self._history: list = []
        self._last_seen = -1e9

    def update(self, hand: Hand | None, now: float):
        if hand is None or not hand.present or not hand.two_fingers:
            if now - self._last_seen > _LOST_SEC:
                self._orient = self._raw = None
                self._history.clear()
            return None
        self._last_seen = now

        raw = orientation(hand.point_dir)
        if raw is None:
            return None
        if raw != self._raw:
            self._raw, self._raw_since = raw, now
        if raw == self._orient or now - self._raw_since < _STABLE_SEC:
            return None

        self._orient = raw
        self._history.append((raw, now))
        self._history = [(o, t) for o, t in self._history if now - t <= self._window]

        if len(self._history) >= 4 and [o for o, _ in self._history[-4:]] == _PATTERN:
            self._history.clear()
            self._orient = self._raw = None
            return dash_from_point(hand.point_dir)
        return None

    @property
    def active(self) -> bool:
        return bool(self._history)

    @property
    def progress(self) -> str:
        seq = [o for o, _ in self._history[-4:]]
        return ">".join(seq) + (">.." if 0 < len(seq) < 4 else "")
