"""Gesture debouncing and the finger-pointing helpers for the control demo.

No camera or MediaPipe here - this takes a gesture *label* per frame plus 21
hand landmarks and turns deliberate poses into commands: a held canned gesture
(``GestureGate``) and an index finger held pointing left/right (``SidePointDash``
-> dash the drone that way).
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
    "Victory": "handoff_random",  # hand the drone to a random other operator
}

HOLD_SEC = 0.40
RELEASE_SEC = 0.25
GAP_SEC = 0.5              # a recognizer dropout shorter than this does not break a hold
HOLDS = {"Victory": 1.5}   # per-gesture hold overrides


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
    def two_fingers(self) -> bool:
        f = self.fingers
        return bool(f[1] and f[2] and not f[3] and not f[4])


def _d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hand_from_landmarks(pts) -> Hand:
    """21 (x, y) normalized landmarks -> :class:`Hand`. A finger counts as
    extended when its tip is farther from the wrist than its middle joint."""
    if not pts or len(pts) < 21:
        return Hand(present=False)
    w = pts[0]

    def ext(tip, pip):
        return _d(pts[tip], w) > _d(pts[pip], w) * 1.02

    thumb = _d(pts[4], w) > _d(pts[2], w) * 1.05
    fingers = (thumb, ext(8, 6), ext(12, 10), ext(16, 14), ext(20, 18))

    # index direction, with the middle finger folded in only if it's also out
    dx = pts[8][0] - pts[5][0]
    dy = pts[8][1] - pts[5][1]
    if fingers[2]:
        dx += pts[12][0] - pts[9][0]
        dy += pts[12][1] - pts[9][1]
    n = math.hypot(dx, dy)
    point_dir = (dx / n, dy / n) if n > 1e-6 else (0.0, 0.0)
    return Hand(present=True, fingers=fingers, point_dir=point_dir)


# --- point-to-dash ------------------------------------------------------

_DASH_HOLD = 0.45      # hold the sideways point this long to dash
_DASH_RELEASE = 0.45   # drop it this long before it can dash again
_DASH_MIN_DX = 0.45    # |horizontal| of the point that counts as "sideways"


class SidePointDash:
    """Point your index finger clearly left or right and hold ~0.45 s -> the
    drone dashes that way. Held, not swung - easy to do on a webcam. Pointing
    up (orbit) or a Victory sign point up, so they never trigger this."""

    def __init__(self):
        self._dir = None
        self._since = 0.0
        self._seen = -1e9
        self._fired = None
        self._rest_since = None

    def update(self, hand: Hand | None, now: float):
        """Returns ``(command | None, hold_progress 0..1)``."""
        want = None
        if hand is not None and hand.present:
            f = hand.fingers
            index_out = f[1] and not f[3] and not f[4]     # index up, ring + pinky down
            dx, dy = hand.point_dir
            if index_out and abs(dx) >= _DASH_MIN_DX and abs(dx) >= abs(dy) * 1.2:
                want = "dash_east" if dx > 0 else "dash_west"

        if want is None:
            if now - self._seen > 0.35:
                self._dir = None
            if self._rest_since is None:
                self._rest_since = now
            if now - self._rest_since >= _DASH_RELEASE:
                self._fired = None
            return None, 0.0

        self._rest_since = None
        self._seen = now
        if want != self._dir:
            self._dir, self._since = want, now
        if want == self._fired:
            return None, 1.0
        progress = min(1.0, (now - self._since) / _DASH_HOLD)
        if progress >= 1.0:
            self._fired = want
            return want, 1.0
        return None, progress
