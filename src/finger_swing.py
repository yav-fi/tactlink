"""Training-free detector for the two-finger "windshield wiper" gesture.

Hold the index and middle fingers out and swing them vertical -> horizontal ->
vertical -> horizontal within a few seconds; the drone then dashes in the
direction the fingers point on the last horizontal. Purely geometric - it reads
the finger-line angle straight from the landmarks, so it never shadows the
built-in MediaPipe gestures.
"""

from __future__ import annotations

import math

ENABLED = True
WINDOW_SEC = 4.0        # whole V-H-V-H swing must land inside this
STABLE_SEC = 0.12       # hold an orientation this long before it counts
LOST_SEC = 0.6          # drop the swing if the 2-finger pose vanishes this long
_V_MAX_DEG = 35.0       # within this of straight up  -> "vertical"
_H_MIN_DEG = 55.0       # between these of straight up -> "horizontal"
_H_MAX_DEG = 125.0
_PATTERN = ["V", "H", "V", "H"]

_POINT_HORIZONTAL = 0.55   # |image dx| above which a point counts as left/right


def resolve_pointed_direction(point_dir) -> str:
    """Finger-pointing vector -> compass fly command. Mirrored feed: +x = east."""
    dx, dy = point_dir
    if abs(dx) >= _POINT_HORIZONTAL and abs(dx) >= abs(dy):
        return "fly_east" if dx > 0 else "fly_west"
    return "fly_north"


def _two_fingers(hand) -> bool:
    f = hand.fingers
    return bool(f[1] and f[2] and not f[3] and not f[4])


def _three_fingers(hand) -> bool:
    # Index + middle + ring extended, thumb tucked. Pinky ignored - it tends to
    # follow the ring finger, and no other gesture is "3 up + thumb in".
    f = hand.fingers
    return bool(f[1] and f[2] and f[3] and not f[0])


_THREE_HOLD_SEC = 0.35     # steady hold before "forward" fires
_THREE_RELEASE_SEC = 0.35  # pose must drop this long before it can fire again
_THREE_MIN_H_DEG = 42.0    # how far from vertical the fingers must lean to count as "sideways"


def _is_sideways(point_dir) -> bool:
    dx, dy = point_dir
    if dx == 0.0 and dy == 0.0:
        return False
    return abs(math.degrees(math.atan2(dx, -dy))) >= _THREE_MIN_H_DEG


class ThreeFingerForward:
    """Three fingers (index+middle+ring) held sideways -> a 'forward' dash,
    taken relative to the operator who gave it."""

    def __init__(self):
        self._held_since = None
        self._fired = False
        self._away_since = 0.0

    def update(self, hand, now: float) -> list[str]:
        ok = (ENABLED and hand is not None and getattr(hand, "present", False)
              and _three_fingers(hand) and _is_sideways(hand.point_dir))
        if not ok:
            if self._away_since == 0.0:
                self._away_since = now
            if now - self._away_since >= _THREE_RELEASE_SEC:
                self._fired = False
            self._held_since = None
            return []
        self._away_since = 0.0
        if self._held_since is None:
            self._held_since = now
        if not self._fired and now - self._held_since >= _THREE_HOLD_SEC:
            self._fired = True
            return ["fly_forward"]
        return []


def _orientation(point_dir) -> str | None:
    dx, dy = point_dir
    if dx == 0.0 and dy == 0.0:
        return None
    angle = abs(math.degrees(math.atan2(dx, -dy)))   # 0 = up, 90 = horizontal
    if angle <= _V_MAX_DEG:
        return "V"
    if _H_MIN_DEG <= angle <= _H_MAX_DEG:
        return "H"
    return None


class FingerSwingDetector:
    def __init__(self, window: float = WINDOW_SEC):
        self._window = window
        self._orient = None            # last confirmed orientation
        self._raw = None               # orientation currently settling
        self._raw_since = 0.0
        self._history: list[tuple[str, float]] = []
        self._last_seen = -1e9

    def update(self, hand, now: float) -> list[str]:
        if not ENABLED or hand is None or not getattr(hand, "present", False) \
                or not _two_fingers(hand):
            if now - self._last_seen > LOST_SEC:
                self._orient = self._raw = None
                self._history.clear()
            return []
        self._last_seen = now

        raw = _orientation(hand.point_dir)
        if raw is None:
            return []
        if raw != self._raw:
            self._raw, self._raw_since = raw, now
        if raw == self._orient or now - self._raw_since < STABLE_SEC:
            return []

        # A new confirmed orientation.
        self._orient = raw
        self._history.append((raw, now))
        self._history = [(o, t) for o, t in self._history if now - t <= self._window]

        if [o for o, _ in self._history[-4:]] == _PATTERN and len(self._history) >= 4:
            self._history.clear()
            self._orient = self._raw = None
            return [resolve_pointed_direction(hand.point_dir)]
        return []

    @property
    def progress(self) -> str:
        """Short HUD string of the swing so far, e.g. 'V>H>...'."""
        seq = [o for o, _ in self._history[-4:]]
        return ">".join(seq) + (">…" if 0 < len(seq) < 4 else "")
