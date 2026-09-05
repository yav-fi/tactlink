"""A tiny, dependency-free custom gesture classifier.

Training data is a set of pose feature vectors (see :mod:`landmark_features`)
with string labels, collected from the webcam by ``scripts/collect_gestures.py``
and fit by ``scripts/train_gestures.py``. The model is a standardized k-nearest
-neighbour vote saved as a single ``.npz`` file - no scikit-learn, no pickle.
"""

from __future__ import annotations

import os

import numpy as np

from landmark_features import FEATURE_VERSION, extract

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models",
    "custom_gestures.npz",
)
DEFAULT_K = 5
DEFAULT_THRESHOLD = 0.6


class CustomGestureClassifier:
    """Loads ``custom_gestures.npz`` if present; a no-op otherwise."""

    def __init__(self, path: str = MODEL_PATH, k: int = DEFAULT_K,
                 threshold: float = DEFAULT_THRESHOLD):
        self.loaded = False
        self.labels: list[str] = []
        self._k = k
        self._threshold = threshold
        if os.path.exists(path):
            self._load(path)

    def _load(self, path: str) -> None:
        d = np.load(path, allow_pickle=False)
        if int(d["feature_version"]) != FEATURE_VERSION:
            print(f"custom_gestures.npz is feature v{int(d['feature_version'])}, "
                  f"code is v{FEATURE_VERSION}; retrain. Ignoring custom model.")
            return
        self._x = d["x"].astype(np.float32)          # (N, F) already standardized
        self._y = d["y"].astype(np.int64)            # (N,)
        self.labels = [str(s) for s in d["labels"]]
        self._mean = d["mean"].astype(np.float32)
        self._std = d["std"].astype(np.float32)
        self._k = int(d["k"]) if "k" in d else self._k
        self._reject_radius = float(d["reject_radius"]) if "reject_radius" in d else np.inf
        self.loaded = True

    def predict(self, landmarks_xyz, handedness: str = "Right"):
        """Return ``(label, confidence)`` or ``(None, 0.0)`` if not confident."""
        if not self.loaded:
            return None, 0.0
        feat = (extract(landmarks_xyz, handedness) - self._mean) / self._std
        dists = np.linalg.norm(self._x - feat, axis=1)
        # Not close to anything we were trained on -> stay quiet.
        if float(dists.min()) > self._reject_radius:
            return None, 0.0
        k = min(self._k, len(dists))
        nn = np.argpartition(dists, k - 1)[:k]
        weights = 1.0 / (dists[nn] + 1e-6)
        scores = np.zeros(len(self.labels), dtype=np.float64)
        for idx, w in zip(self._y[nn], weights):
            scores[idx] += w
        best = int(np.argmax(scores))
        confidence = float(scores[best] / scores.sum())
        if confidence < self._threshold:
            return None, confidence
        return self.labels[best], confidence
