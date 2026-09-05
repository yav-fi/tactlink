"""Webcam hand tracking built on the MediaPipe Tasks HandLandmarker.

The tracker turns a BGR frame into a :class:`HandState`. Landmark indices follow
the MediaPipe hand model (0 = wrist, 4 = thumb tip, 8 = index tip, ...).

The model bundle (``hand_landmarker.task``) is downloaded to ``models/`` on first
use if it is not already present.
"""

import math
import os
import time
import urllib.request

import numpy as np

from control_types import HandState

try:  # MediaPipe is optional so the simulator can run without a camera.
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import (
        HandLandmarker,
        HandLandmarkerOptions,
        HandLandmarksConnections,
        RunningMode,
    )
    _MP_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when the wheel is missing
    _MP_AVAILABLE = False

_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models",
    "hand_landmarker.task",
)

_FINGER_TIPS = (4, 8, 12, 16, 20)
_FINGER_PIPS = (2, 6, 10, 14, 18)


def _ensure_model() -> str:
    if not os.path.exists(_MODEL_PATH):
        os.makedirs(os.path.dirname(_MODEL_PATH), exist_ok=True)
        print("downloading hand_landmarker.task (~7.5 MB) ...")
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
    return _MODEL_PATH


class HandTracker:
    def __init__(self, max_hands: int = 1, detection_confidence: float = 0.6):
        if not _MP_AVAILABLE:
            raise RuntimeError(
                "mediapipe is not installed; run `pip install -r requirements.txt` "
                "or start the app with --demo"
            )
        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=_ensure_model()),
            running_mode=RunningMode.VIDEO,
            num_hands=max_hands,
            min_hand_detection_confidence=detection_confidence,
            min_tracking_confidence=0.5,
        )
        self._landmarker = HandLandmarker.create_from_options(options)
        self._connections = [
            (c.start, c.end) for c in HandLandmarksConnections.HAND_CONNECTIONS
        ]
        self._t0 = time.monotonic()
        self._last_ts = -1  # detect_for_video requires strictly increasing stamps
        self._last_pts = None  # pixel-space landmarks for drawing

    def process(self, frame_bgr: np.ndarray) -> HandState:
        """Detect a hand in ``frame_bgr`` and return its normalized state."""
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ts_ms = max(self._last_ts + 1, int((time.monotonic() - self._t0) * 1000))
        self._last_ts = ts_ms
        result = self._landmarker.detect_for_video(mp_image, ts_ms)

        if not result.hand_landmarks:
            self._last_pts = None
            return HandState(present=False)

        lm = result.hand_landmarks[0]
        pts = np.array([(p.x, p.y) for p in lm], dtype=np.float32)
        h, w = frame_bgr.shape[:2]
        self._last_pts = (pts * (w, h)).astype(np.int32)

        palm = pts[[0, 5, 9, 13, 17]].mean(axis=0)

        # Hand roll: angle of the line across the knuckles (index MCP -> pinky MCP).
        across = pts[17] - pts[5]
        roll_angle = math.atan2(across[1], across[0])

        # Pinch: thumb tip to index tip, scaled by hand size so distance from the
        # camera does not matter.
        hand_size = float(np.linalg.norm(pts[0] - pts[9])) + 1e-6
        pinch_dist = float(np.linalg.norm(pts[4] - pts[8])) / hand_size
        pinch = float(np.clip(1.0 - (pinch_dist - 0.15) / 0.85, 0.0, 1.0))

        fingers_up = self._count_fingers(pts)

        return HandState(
            present=True,
            palm_x=float(np.clip(palm[0], 0.0, 1.0)),
            palm_y=float(np.clip(palm[1], 0.0, 1.0)),
            roll_angle=roll_angle,
            pinch=pinch,
            fingers_up=fingers_up,
        )

    def draw(self, frame_bgr: np.ndarray) -> None:
        """Overlay the most recent landmarks onto ``frame_bgr`` in place."""
        if self._last_pts is None:
            return
        import cv2

        for a, b in self._connections:
            cv2.line(frame_bgr, tuple(self._last_pts[a]), tuple(self._last_pts[b]),
                     (240, 200, 90), 2, cv2.LINE_AA)
        for x, y in self._last_pts:
            cv2.circle(frame_bgr, (int(x), int(y)), 4, (90, 240, 140), -1, cv2.LINE_AA)

    def close(self) -> None:
        self._landmarker.close()

    @staticmethod
    def _count_fingers(pts: np.ndarray) -> int:
        """Count extended fingers from landmark geometry (orientation agnostic).

        A finger is "up" when its tip is clearly farther from the wrist than both
        its PIP and MCP joints - true regardless of which way the hand points.
        """
        wrist = pts[0]
        d = lambda i: float(np.linalg.norm(pts[i] - wrist))
        count = 0
        # Thumb: tip beyond the MCP joint and splayed away from the index MCP.
        if d(4) > d(2) * 1.05 and np.linalg.norm(pts[4] - pts[5]) > \
                np.linalg.norm(pts[3] - pts[5]) * 1.1:
            count += 1
        for tip, pip, mcp in zip(_FINGER_TIPS[1:], _FINGER_PIPS[1:], (5, 9, 13, 17)):
            if d(tip) > d(pip) * 1.08 and d(tip) > d(mcp) * 1.15:
                count += 1
        return count
