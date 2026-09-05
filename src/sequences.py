"""Match ordered gesture *combos* against a rolling token stream.

A "token" is one stable gesture segment (e.g. holding an open palm long enough to
count). :class:`GestureInterpreter` emits tokens as they happen; the matcher
keeps a short rolling buffer and fires when the tail of that buffer equals a
configured pattern and the whole match happened inside the pattern's time window.

Config: ``config/gesture_sequences.json`` ::

    {
      "single_delay": 0.6,
      "sequences": [
        {"pattern": ["Open_Palm", "Closed_Fist", "Open_Palm"],
         "window": 3.0, "action": "return_home"}
      ]
    }

``single_delay`` (seconds) is how long a single-gesture command is held back so a
combo has a chance to form; it is applied by the interpreter, not here. With no
config file, there are no sequences and ``single_delay`` is 0 (unchanged feel).
"""

from __future__ import annotations

import json
import os

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "gesture_sequences.json",
)
_DEFAULT_SINGLE_DELAY = 0.6
_BUFFER_LIMIT = 8
PREFIX_IDLE_SEC = 1.0  # a partial combo is "live" only this long after the last pose


def load_config():
    """Return ``(sequences, single_delay)``."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return [], 0.0
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"ignoring {os.path.relpath(_CONFIG_PATH)}: {exc}")
        return [], 0.0

    seqs = []
    for item in raw.get("sequences", []):
        pattern = [str(g) for g in item.get("pattern", []) if str(g) not in ("", "None")]
        action = str(item.get("action", "")).strip()
        window = float(item.get("window", 3.0))
        if len(pattern) >= 2 and action:
            seqs.append({"pattern": pattern, "action": action, "window": window})
        else:
            print(f"gesture_sequences.json: skipping invalid entry {item!r}")
    delay = float(raw.get("single_delay", _DEFAULT_SINGLE_DELAY if seqs else 0.0))
    if seqs:
        names = ", ".join(">".join(s["pattern"]) + "=" + s["action"] for s in seqs)
        print(f"gesture sequences: {names}  (single-gesture delay {delay:.2f}s)")
    return seqs, max(0.0, delay)


class SequenceMatcher:
    def __init__(self, sequences=None):
        if sequences is None:
            sequences, _ = load_config()
        self._sequences = sequences
        self._buf: list[tuple[str, float]] = []  # (token, time)

    @property
    def enabled(self) -> bool:
        return bool(self._sequences)

    @property
    def member_gestures(self) -> set[str]:
        """Every gesture that appears in any pattern (these get the single-delay)."""
        return {g for seq in self._sequences for g in seq["pattern"]}

    def feed(self, token: str, now: float) -> list[str]:
        """Add a token; return actions for any sequences that just completed."""
        self._buf.append((token, now))
        if len(self._buf) > _BUFFER_LIMIT:
            self._buf = self._buf[-_BUFFER_LIMIT:]

        fired = []
        for seq in self._sequences:
            pat = seq["pattern"]
            if len(self._buf) < len(pat):
                continue
            tail = self._buf[-len(pat):]
            if [t for t, _ in tail] == pat and now - tail[0][1] <= seq["window"]:
                fired.append(seq["action"])
                self._buf.clear()  # consume; don't let it re-trigger
                break
        return fired

    def prefix_active(self, now: float) -> tuple[bool, str]:
        """Is the buffer tail a live partial match? Returns (active, hint)."""
        if not self._buf or now - self._buf[-1][1] > PREFIX_IDLE_SEC:
            return False, ""
        recent = [t for t, ts in self._buf if now - ts <= self._max_window()]
        for seq in self._sequences:
            pat = seq["pattern"]
            for length in range(min(len(recent), len(pat) - 1), 0, -1):
                if recent[-length:] == pat[:length]:
                    hint = ">".join(pat[:length]) + ">…"
                    return True, hint
        return False, ""

    def _max_window(self) -> float:
        return max((s["window"] for s in self._sequences), default=3.0)
