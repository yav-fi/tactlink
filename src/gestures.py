"""Turn the raw gesture stream into discrete commands and mode changes.

The MediaPipe recognizer emits a gesture label every frame; a demo needs those
to be *deliberate*. This interpreter:

* requires a gesture to be held steady for ``HOLD_SEC`` before it fires, and
* fires each gesture only once per "press" - the hand must relax (go to None or
  another gesture) before the same command can fire again.

Gesture map
-----------
Open_Palm    -> take off / arm
Closed_Fist  -> land / disarm
Victory      -> cycle flight mode (heading / position / hover)
Thumb_Up     -> speed up   (slow -> normal -> sport)
Thumb_Down   -> speed down
Pointing_Up  -> do a 360 deg spin
ILoveYou     -> return to the start point and hover
"""

import time

from control_types import FlightMode, GestureState

HOLD_SEC = 0.40           # steady-hold time before a gesture fires
RELEASE_SEC = 0.15        # time at rest before the same gesture may fire again

_ACTION = {
    "Open_Palm": "takeoff",
    "Closed_Fist": "land",
    "Victory": "cycle_mode",
    "Thumb_Up": "speed_up",
    "Thumb_Down": "speed_down",
    "Pointing_Up": "spin360",
    "ILoveYou": "return_home",
}

_MODE_CYCLE = [FlightMode.HEADING, FlightMode.POSITION, FlightMode.HOVER]
_SPEEDS = [("slow", 0.5), ("normal", 1.0), ("sport", 1.6)]


class GestureInterpreter:
    def __init__(self) -> None:
        self._mode_idx = 0
        self._speed_idx = 1
        self._held = "None"
        self._held_since = 0.0
        self._fired_gesture = ""   # gesture whose command already fired this press
        self._rest_since = 0.0

    @property
    def mode(self) -> FlightMode:
        return _MODE_CYCLE[self._mode_idx]

    def update(self, gesture: str, now: float | None = None) -> GestureState:
        now = time.monotonic() if now is None else now
        events: list[str] = []

        if gesture != self._held:
            self._held = gesture
            self._held_since = now

        # Track time spent not holding a fireable gesture, so a repeat needs a gap.
        if gesture in ("None", "") or gesture != self._fired_gesture:
            if gesture in ("None", ""):
                if self._rest_since == 0.0:
                    self._rest_since = now
                if now - self._rest_since >= RELEASE_SEC:
                    self._fired_gesture = ""
        else:
            self._rest_since = 0.0

        held_for = now - self._held_since
        ready = gesture in _ACTION and gesture != self._fired_gesture
        progress = min(1.0, held_for / HOLD_SEC) if ready else 0.0

        if ready and held_for >= HOLD_SEC:
            events.append(self._apply(_ACTION[gesture]))
            self._fired_gesture = gesture
            self._rest_since = 0.0
            progress = 1.0

        name, scale = _SPEEDS[self._speed_idx]
        return GestureState(
            mode=self.mode,
            speed_name=name,
            speed_scale=scale,
            active_gesture=gesture,
            hold_progress=progress,
            events=events,
        )

    def _apply(self, action: str) -> str:
        if action == "cycle_mode":
            self._mode_idx = (self._mode_idx + 1) % len(_MODE_CYCLE)
            return f"mode: {self.mode.value}"
        if action == "speed_up":
            self._speed_idx = min(self._speed_idx + 1, len(_SPEEDS) - 1)
            return f"speed: {_SPEEDS[self._speed_idx][0]}"
        if action == "speed_down":
            self._speed_idx = max(self._speed_idx - 1, 0)
            return f"speed: {_SPEEDS[self._speed_idx][0]}"
        # Pass-through commands the controller/autopilot acts on.
        return action
