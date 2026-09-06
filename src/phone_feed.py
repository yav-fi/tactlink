"""Ingest position + heading datagrams from SignalMap phones.

Each iPhone in the UWB room runs ``RoomBridge`` (``ios/SignalMap/RoomBridge.swift``)
and streams one small JSON datagram a few times a second over UDP:

    {"v": 1, "id": "<phone id>", "name": "Ian", "room": "<fingerprint>",
     "t": 1234.5, "cycle": 7, "pos": [x, y], "z": 0.0,
     "heading": 1.57, "moving": true, "speed": 0.4,
     "headingReady": true, "gesture": "None", "flat": true}

``pos`` is metres in the room's *arbitrary* shared frame (no north, origin
wherever the mesh initialised); ``heading`` is radians in that same frame from
the phone's relative-motion estimate (only meaningful while walking - the sim
holds the last value otherwise). ``gesture`` is reserved: the phones do not
classify hand poses yet, so it is always ``"None"`` for now and the webcam still
supplies gestures in ``main.py``.

``PhoneFeed`` runs a daemon UDP listener and keeps the newest datagram per phone
id, dropping any that go quiet. ``OperatorPool.sync_from_reports`` turns that
table into live operators.
"""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from dataclasses import dataclass


@dataclass
class PhoneReport:
    """The newest datagram from one phone, already parsed and clamped."""

    op_id: str
    name: str
    pos: tuple            # (x, y) metres, phone's arbitrary UWB frame
    heading: float        # radians, motion heading in the UWB frame (walking only)
    moving: bool = False
    heading_ready: bool = False
    compass: float = 0.0        # degrees clockwise from magnetic north (absolute)
    compass_valid: bool = False
    gesture: str = "None"
    gesture_source: str = "phone"
    cycle: int = 0
    recv_time: float = 0.0

    def sim_heading(self, rot: float = 0.0) -> float:
        """Best facing for this operator in the sim frame (radians, 0 = +x/east).

        Prefers the phone's absolute compass when it is valid; otherwise the
        UWB-frame motion heading rotated by ``rot`` (``--phone-frame-rot``).
        """
        if self.compass_valid:
            # compass: degrees CW from north -> radians CCW from +x, north = +y.
            return math.radians(90.0 - self.compass)
        return self.heading + rot


def parse_datagram(data: bytes, now: float | None = None) -> PhoneReport | None:
    """Parse one UDP payload. Returns None for anything malformed."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None

    op_id = obj.get("id")
    if not isinstance(op_id, str) or not op_id:
        return None

    pos = obj.get("pos")
    if (not isinstance(pos, (list, tuple)) or len(pos) < 2
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pos[:2])):
        return None

    try:
        heading = float(obj.get("heading", 0.0) or 0.0)
    except (TypeError, ValueError):
        heading = 0.0
    if not math.isfinite(heading):
        heading = 0.0

    try:
        compass = float(obj.get("compass", 0.0) or 0.0)
    except (TypeError, ValueError):
        compass = 0.0
    compass_valid = bool(obj.get("compassValid", False)) and math.isfinite(compass)

    cycle = obj.get("cycle", 0)
    return PhoneReport(
        op_id=op_id[:64],
        name=(str(obj.get("name"))[:32] if obj.get("name") else op_id[:32]),
        pos=(float(pos[0]), float(pos[1])),
        heading=heading,
        moving=bool(obj.get("moving", False)),
        heading_ready=bool(obj.get("headingReady", False)),
        compass=compass,
        compass_valid=compass_valid,
        gesture=(str(obj.get("gesture"))[:32] if obj.get("gesture") else "None"),
        gesture_source=(str(obj.get("gestureSource"))[:16] if obj.get("gestureSource") else "phone"),
        cycle=int(cycle) if isinstance(cycle, (int, float)) and math.isfinite(cycle) else 0,
        recv_time=time.monotonic() if now is None else now,
    )


class PhoneFeed:
    """UDP listener that keeps the latest report per phone, pruning stale ones."""

    def __init__(self, port: int, host: str = "0.0.0.0", stale_sec: float = 2.0):
        self.host = host
        self.port = int(port)
        self.stale_sec = float(stale_sec)
        self.packets = 0
        self._table: dict[str, PhoneReport] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    @classmethod
    def from_arg(cls, arg: str, **kwargs) -> "PhoneFeed":
        """Build from a ``"PORT"`` or ``"HOST:PORT"`` command-line string."""
        arg = str(arg).strip()
        if ":" in arg:
            host, _, port = arg.rpartition(":")
            return cls(port=int(port), host=host or "0.0.0.0", **kwargs)
        return cls(port=int(arg), **kwargs)

    def start(self) -> None:
        if self._thread is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.settimeout(0.4)
        self._sock = sock
        self.port = sock.getsockname()[1]      # resolve an ephemeral (0) port
        self._thread = threading.Thread(target=self._loop, name="phone-feed", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            report = parse_datagram(data)
            if report is None:
                continue
            with self._lock:
                self._table[report.op_id] = report
                self.packets += 1

    def latest(self, now: float | None = None) -> list[PhoneReport]:
        """Fresh reports, newest per phone, sorted by id. Prunes stale entries."""
        now = time.monotonic() if now is None else now
        with self._lock:
            fresh = [r for r in self._table.values() if now - r.recv_time <= self.stale_sec]
            self._table = {r.op_id: r for r in fresh}
        fresh.sort(key=lambda r: r.op_id)
        return fresh

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
