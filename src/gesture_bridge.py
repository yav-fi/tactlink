"""Run webcam recognition headlessly and relay gestures to the web simulator.

This process deliberately has no OpenCV window or secondary drone renderer. It
owns the camera, uses the existing MediaPipe recognizer/interpreter, and posts
only compact gesture state to the local FastAPI service.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

import cv2

from gestures import GestureInterpreter
from hand_tracker import HandTracker


def _post(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=0.5) as response:
        if response.status >= 300:
            raise RuntimeError(f"gesture service returned HTTP {response.status}")


def run(camera: int, runtime_url: str, publish_hz: float) -> int:
    tracker = HandTracker()
    interpreter = GestureInterpreter()
    # DirectShow is Windows-only; forcing its numeric backend on macOS makes a
    # perfectly good AVFoundation camera look unavailable.
    backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
    cap = cv2.VideoCapture(camera, backend)
    if not cap.isOpened():
        tracker.close()
        print(f"gesture camera {camera} unavailable; the 3D demo will continue without hand control")
        return 1

    endpoint = runtime_url.rstrip("/") + "/api/gesture"
    interval = 1.0 / max(1.0, publish_hz)
    last_publish = 0.0
    last_connection_warning = 0.0
    print(f"gesture camera {camera} active in background")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("gesture camera read failed")
                return 1
            hand = tracker.process(cv2.flip(frame, 1))
            state = interpreter.update(
                hand.gesture if hand.present else "None",
                hand.gesture_source if hand.present else "none",
                hand=hand,
            )
            now = time.monotonic()
            if not state.events and now - last_publish < interval:
                continue
            payload = {
                "present": hand.present,
                "gesture": hand.gesture if hand.present else "None",
                "score": hand.gesture_score if hand.present else 0.0,
                "source": hand.gesture_source if hand.present else "none",
                "hold_progress": state.hold_progress,
                "sequence_hint": state.sequence_hint,
                "events": state.events,
            }
            try:
                _post(endpoint, payload)
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                if now - last_connection_warning >= 5.0:
                    print(f"gesture service waiting for runtime: {exc}")
                    last_connection_warning = now
            last_publish = now
    except KeyboardInterrupt:
        return 0
    finally:
        cap.release()
        tracker.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0, help="webcam index (default: 0)")
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8000")
    parser.add_argument("--publish-hz", type=float, default=10.0)
    args = parser.parse_args()
    return run(args.camera, args.runtime_url, args.publish_hz)


if __name__ == "__main__":
    raise SystemExit(main())
