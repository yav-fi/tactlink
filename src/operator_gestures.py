"""One :class:`GestureInterpreter` per operator.

When several phones each stream their own gesture label, every operator's
gestures have to be debounced independently - hold timing, combos and the
geometric detectors are all per-person. This keeps an interpreter per operator
(keyed by phone id, or name in the simulated pool) and returns the
:class:`GestureState` of whichever operator currently holds control. Events from
everyone else are computed but dropped, which is what enforces "only the nearest
operator flies the drone".

The laptop webcam, when present, fills in for the operator that currently has
control whenever that operator's own gesture is ``"None"`` - so the ground
station can still fly during bring-up while the phones report nothing, without
overriding a real gesture once the phones recognise them.
"""

from __future__ import annotations

from control_types import GestureState
from gestures import GestureInterpreter


def operator_key(op) -> str:
    return getattr(op, "op_id", "") or op.name


class OperatorGestures:
    def __init__(self):
        self._by_key: dict[str, GestureInterpreter] = {}

    def reset(self) -> None:
        self._by_key.clear()

    def update(self, operators, active_key: str | None, *, now: float | None = None,
               webcam_gesture: str = "None", webcam_source: str = "none",
               webcam_hand=None) -> GestureState:
        """Step every operator's interpreter; return the active operator's state.

        ``active_key`` is :func:`operator_key` of the controlling operator, or
        ``None`` when there is no operator (e.g. a phone feed with nobody joined
        yet), in which case a neutral state is returned.
        """
        live = {operator_key(op) for op in operators.operators}
        for stale in [k for k in self._by_key if k not in live]:
            del self._by_key[stale]

        active_state: GestureState | None = None
        for op in operators.operators:
            key = operator_key(op)
            interp = self._by_key.get(key)
            if interp is None:
                interp = self._by_key[key] = GestureInterpreter()

            gesture = getattr(op, "gesture", "None") or "None"
            source = getattr(op, "gesture_source", "phone")
            hand = None
            if key == active_key:
                hand = webcam_hand
                if gesture in ("None", "") and webcam_gesture not in ("None", ""):
                    gesture, source = webcam_gesture, webcam_source

            state = interp.update(gesture, source, now=now, hand=hand)
            if key == active_key:
                active_state = state

        return active_state if active_state is not None else GestureState()
