"""Latest phone telemetry for the browser flight simulator; no second drone simulation."""
from __future__ import annotations

import json
import math
import time

from simulation.operators import report_from_payload


class PhoneFeed:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples: dict[str, tuple[float, dict]] = {}
        self.packets = 0

    def ingest(self, data: bytes) -> None:
        if len(data) > 8192:
            return
        try:
            sample = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(sample, dict):
            return
        identity, room = sample.get("id"), sample.get("room")
        if not isinstance(identity, str) or not 0 < len(identity) <= 128:
            return
        if not isinstance(room, str) or not 0 < len(room) <= 128:
            return
        report = report_from_payload(sample)

        def finite(key, default):
            value = sample.get(key, default)
            return value if isinstance(value, (int, float)) and math.isfinite(value) else default

        item = {
            "id": identity, "room": room,
            "name": str(sample.get("name", identity))[:24],
            "pos": list(report.group_pos) if report else None, "z": finite("z", 0),
            "heading": report.enu_heading(0) if report else 0,
            "compassValid": bool(report and report.compass_valid),
            "gesture": report.gesture if report else "None",
            "confidence": report.gesture_confidence if report else 0,
            "flat": sample.get("flat") is True,
            "geometryAge": max(0, finite("geometryAge", 0)),
            "cycle": max(0, int(finite("cycle", 0))),
            "members": max(0, min(5, int(finite("members", 0)))),
        }
        now = self.clock()
        self.samples = {key: value for key, value in self.samples.items() if now - value[0] < 10}
        if len(self.samples) >= 32 and identity not in self.samples:
            return
        self.samples[identity] = (now, item)
        self.packets += 1

    def snapshot(self) -> list[dict]:
        now = self.clock()
        return [dict(item, age=now - received, geometryAge=item["geometryAge"] + now - received)
                for received, item in sorted(self.samples.values(), key=lambda value: value[1]["id"])
                if now - received < 10]
