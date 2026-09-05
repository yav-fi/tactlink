"""Turn 21 raw hand landmarks into a pose feature vector for classification.

The same function is used when recording training samples and when predicting at
run time, so the feature layout must stay stable - bump ``FEATURE_VERSION`` if it
changes and retrain.

Normalization makes the vector invariant to where the hand is in frame, how big
it appears (distance from the camera), and which hand it is, while keeping the
hand's *orientation* so tilt / pointing-direction gestures remain learnable.
"""

import numpy as np

FEATURE_VERSION = 1
NUM_LANDMARKS = 21
FEATURE_DIM = NUM_LANDMARKS * 3  # 63

_WRIST = 0
_MIDDLE_MCP = 9


def extract(landmarks_xyz, handedness: str = "Right") -> np.ndarray:
    """``landmarks_xyz``: (21, 3) array of MediaPipe normalized coordinates.

    Returns a float32 vector of length :data:`FEATURE_DIM`.
    """
    pts = np.asarray(landmarks_xyz, dtype=np.float64).reshape(NUM_LANDMARKS, 3).copy()

    # Canonicalize to a right hand so both hands share one feature space.
    if str(handedness).lower().startswith("l"):
        pts[:, 0] = -pts[:, 0]

    # Translate so the wrist is the origin.
    pts -= pts[_WRIST]

    # Scale so the wrist -> middle-finger knuckle distance is 1.
    ref = np.linalg.norm(pts[_MIDDLE_MCP])
    if ref < 1e-6:
        ref = np.linalg.norm(pts, axis=1).max() + 1e-6
    pts /= ref

    return pts.reshape(-1).astype(np.float32)
