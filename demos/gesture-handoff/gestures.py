"""Gesture debouncing for the control demo.

No camera or MediaPipe here - this takes a gesture *label* per frame (whatever
the recognizer, or a scripted demo, produced) and turns a deliberate, held pose
into a single command. It tolerates brief recognizer dropouts so a multi-second
hold (the Victory hand-off) actually completes.
"""

from __future__ import annotations

# Canned MediaPipe gesture name -> demo command.
ACTIONS = {
    "Thumb_Up": "takeoff",     # arm + take off; again while flying = climb a step
    "Thumb_Down": "land",      # descend + disarm
    "Open_Palm": "halt",       # cancel the routine, hover in place
    "Pointing_Up": "orbit",    # circle the controlling operator (toggle)
    "ILoveYou": "return",      # fly back to the controlling operator
    "Victory": "handoff",      # hold ~2 s -> hand the drone to a random other operator
}

HOLD_SEC = 0.40      # steady hold before a normal gesture fires
RELEASE_SEC = 0.20   # time fully at rest before the same gesture may fire again
GAP_SEC = 0.45       # a recognizer dropout shorter than this does not break the hold
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
        self._held = "None"     # gesture currently being accumulated
        self._since = 0.0       # when the current hold started
        self._seen = 0.0        # last frame the pose was actually observed
        self._fired = ""        # gesture whose command already fired this hold
        self._rest_since = None  # when the hand went (and stayed) empty

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
                self._held = "None"          # dropped for too long: hold is over
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
