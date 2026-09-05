"""Turn the raw gesture stream into discrete commands and mode changes.

The MediaPipe recognizer emits a gesture label every frame; a demo needs those
to be *deliberate*. This interpreter:

* requires a gesture to be held steady for ``HOLD_SEC`` before it fires, and
* fires each gesture only once per "press" - the hand must relax (go to None or
  another gesture) before the same command can fire again.

Default gesture map (override in config/gesture_actions.json)
-----------------------------------------------------------
Open_Palm    -> take off / arm
Closed_Fist  -> land / disarm
Victory      -> cycle flight mode (heading / position / hover)
Thumb_Up     -> speed up   (slow -> normal -> sport)
Thumb_Down   -> speed down
Pointing_Up  -> do a 360 deg spin
ILoveYou     -> return to the start point and hover

Recognized actions: takeoff, land, cycle_mode, speed_up, speed_down, spin360,
return_home, estop. Map any gesture name (canned or your own custom label) to one
of these in config/gesture_actions.json - it is merged over the defaults.

Ordered combos (config/gesture_sequences.json, see :mod:`sequences`) fire when
several gestures happen in order inside a time window, e.g. open -> fist -> open.
While combos are configured, single-gesture commands that could be part of a
combo are held back briefly (``single_delay``) and cancelled if the combo lands.
"""

import json
import os
import time

from control_types import FlightMode, GestureState
from finger_swing import FingerSwingDetector, ThreeFingerForward, resolve_pointed_direction
from sequences import SequenceMatcher, load_config

HOLD_SEC = 0.40           # steady-hold time before a gesture fires
RELEASE_SEC = 0.15        # time at rest before the same gesture may fire again
SEGMENT_SEC = 0.18        # hold time before a gesture counts as a combo token
COMBO_GRACE_SEC = 0.9     # keep holding pending singles this long after a combo pose

_DEFAULT_ACTION = {
    "Thumb_Up": "takeoff",     # arm + climb
    "Thumb_Down": "land",      # descend + disarm
    "Pointing_Up": "orbit",
    "ILoveYou": "return_home",
}
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "gesture_actions.json",
)
_UNMAP = ("", "none", "null", "off")


def _load_actions() -> dict:
    actions = dict(_DEFAULT_ACTION)
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            override = json.load(fh)
        n = 0
        for key, value in override.items():
            if str(key).startswith("_"):
                continue
            n += 1
            if value is None or str(value).strip().lower() in _UNMAP:
                actions.pop(str(key), None)   # explicitly disable a gesture
            else:
                actions[str(key)] = str(value)
        print(f"gesture actions: loaded {n} mapping(s) from "
              f"{os.path.relpath(_CONFIG_PATH)}")
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"ignoring {os.path.relpath(_CONFIG_PATH)}: {exc}")
    return actions


_ACTION = _load_actions()

_MODE_CYCLE = [FlightMode.HEADING, FlightMode.POSITION, FlightMode.HOVER]
_SPEEDS = [("slow", 0.5), ("normal", 1.0), ("sport", 1.6)]


class GestureInterpreter:
    def __init__(self, matcher: SequenceMatcher | None = None,
                 single_delay: float | None = None, actions: dict | None = None) -> None:
        self._mode_idx = 0
        self._speed_idx = 1
        self._held = "None"
        self._held_since = 0.0
        self._fired_gesture = ""   # gesture whose command already fired this press
        self._rest_since = 0.0
        self._actions = _ACTION if actions is None else actions

        if matcher is None:
            seqs, cfg_delay = load_config()
            matcher = SequenceMatcher(seqs)
            single_delay = cfg_delay if single_delay is None else single_delay
        self._matcher = matcher
        self._single_delay = 0.0 if single_delay is None else max(0.0, single_delay)
        self._combo_gestures = matcher.member_gestures
        self._token_gesture = ""   # last gesture emitted as a combo token
        self._pending: list[dict] = []  # single-gesture actions waiting out single_delay
        self._combo_touch = -1e9   # last time a combo-member gesture was held
        self._point_dir = (0.0, 0.0)  # latest finger-pointing vector, for fly_pointed
        self._swing = FingerSwingDetector()  # two-finger V<->H wiper -> fly_pointed
        self._three = ThreeFingerForward()   # three fingers sideways -> fly_forward

    @property
    def mode(self) -> FlightMode:
        return _MODE_CYCLE[self._mode_idx]

    def update(self, gesture: str, source: str = "canned",
               now: float | None = None, hand=None) -> GestureState:
        now = time.monotonic() if now is None else now
        events: list[str] = []
        if hand is not None and getattr(hand, "present", False):
            self._point_dir = tuple(hand.point_dir)
        events.extend(self._swing.update(hand, now))   # two-finger wiper
        events.extend(self._three.update(hand, now))   # three-finger forward

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
        if gesture in self._combo_gestures:
            self._combo_touch = now

        # --- combo tokens -------------------------------------------------
        if gesture in ("None", ""):
            if held_for >= SEGMENT_SEC:
                self._token_gesture = ""   # a gap; the next pose is a fresh token
        elif held_for >= SEGMENT_SEC and gesture != self._token_gesture:
            self._token_gesture = gesture
            for action in self._matcher.feed(gesture, now):
                if action == "fly_pointed":
                    action = self._resolve_fly_pointed()
                events.append(self._apply(action))
                self._pending.clear()
                self._fired_gesture = gesture   # don't also fire this pose as a single

        # --- single-gesture command (deferred if it could be part of a combo) --
        ready = gesture in self._actions and gesture != self._fired_gesture
        progress = min(1.0, held_for / HOLD_SEC) if ready else 0.0
        if ready and held_for >= HOLD_SEC:
            action = self._actions[gesture]
            if self._single_delay > 0.0 and gesture in self._combo_gestures:
                self._pending.append({"action": action, "at": now})
            else:
                events.append(self._apply(action))
            self._fired_gesture = gesture
            self._rest_since = 0.0
            progress = 1.0

        # Hold a pending single while a combo is plausibly still being formed:
        # either a live partial match, or a combo-member gesture touched recently.
        active, hint = self._matcher.prefix_active(now)
        if not hint and self._swing.progress:
            hint = "swing " + self._swing.progress
        mid_combo = active or (now - self._combo_touch) < COMBO_GRACE_SEC
        for p in list(self._pending):
            if now - p["at"] >= self._single_delay and not mid_combo:
                events.append(self._apply(p["action"]))
                self._pending.remove(p)

        name, scale = _SPEEDS[self._speed_idx]
        return GestureState(
            mode=self.mode,
            speed_name=name,
            speed_scale=scale,
            active_gesture=gesture,
            gesture_source=source if gesture not in ("None", "") else "none",
            hold_progress=progress,
            sequence_hint=hint,
            events=events,
        )

    def _resolve_fly_pointed(self) -> str:
        return resolve_pointed_direction(self._point_dir)

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
