"""Fit the custom gesture classifier from recorded samples.

    python scripts/train_gestures.py

Reads every data/gestures/*.npy, standardizes the pose features, estimates
accuracy with k-fold nearest-neighbour voting, and writes
models/custom_gestures.npz for the live app to pick up automatically.
"""

from __future__ import annotations

import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from gesture_model import DEFAULT_K, MODEL_PATH  # noqa: E402
from landmark_features import FEATURE_DIM, FEATURE_VERSION  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "data", "gestures")


def _knn_predict(train_x, train_y, query, k, n_labels):
    dists = np.linalg.norm(train_x - query, axis=1)
    kk = min(k, len(dists))
    nn = np.argpartition(dists, kk - 1)[:kk]
    w = 1.0 / (dists[nn] + 1e-6)
    scores = np.zeros(n_labels)
    for idx, wi in zip(train_y[nn], w):
        scores[idx] += wi
    return int(np.argmax(scores))


def main() -> int:
    if not os.path.isdir(DATA_DIR):
        print("no data/gestures/ - record some with scripts/collect_gestures.py")
        return 1

    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".npy"))
    labels = [f[:-4] for f in files]
    if len(labels) < 2:
        print(f"need at least 2 gesture labels, found {labels}")
        return 1

    xs, ys = [], []
    for i, f in enumerate(files):
        arr = np.load(os.path.join(DATA_DIR, f)).astype(np.float32)
        if arr.ndim != 2 or arr.shape[1] != FEATURE_DIM:
            print(f"{f}: unexpected shape {arr.shape}, skipping")
            continue
        xs.append(arr)
        ys.append(np.full(len(arr), i))
        print(f"  {labels[i]:20s} {len(arr):5d} samples")
    x = np.vstack(xs)
    y = np.concatenate(ys)

    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-6] = 1.0
    xn = (x - mean) / std

    # 5-fold accuracy estimate.
    rng = np.random.default_rng(0)
    order = rng.permutation(len(xn))
    folds = np.array_split(order, 5)
    correct = 0
    conf = np.zeros((len(labels), len(labels)), dtype=int)
    for fi in range(5):
        test = folds[fi]
        train = np.concatenate([folds[j] for j in range(5) if j != fi])
        for t in test:
            pred = _knn_predict(xn[train], y[train], xn[t], DEFAULT_K, len(labels))
            conf[y[t], pred] += 1
            correct += pred == y[t]
    acc = correct / len(xn)

    print(f"\n5-fold accuracy: {acc:.1%}  ({correct}/{len(xn)})")
    print("confusion (rows = true, cols = predicted):")
    head = "".join(f"{l[:8]:>9s}" for l in labels)
    print(" " * 20 + head)
    for i, l in enumerate(labels):
        print(f"  {l:18s}" + "".join(f"{conf[i, j]:9d}" for j in range(len(labels))))

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    # Absolute-distance gate: reject a live pose whose nearest training sample is
    # much farther than samples typically sit from each other. Uses the 90th
    # percentile of within-training nearest-neighbour distance, times a margin.
    nn_dist = []
    for i in range(len(xn)):
        dd = np.linalg.norm(xn - xn[i], axis=1)
        dd[i] = np.inf
        nn_dist.append(dd.min())
    reject_radius = float(np.percentile(nn_dist, 90) * 3.0)
    print(f"reject radius: {reject_radius:.3f}")

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    np.savez(
        MODEL_PATH,
        x=xn.astype(np.float32),
        y=y.astype(np.int64),
        labels=np.array(labels),
        mean=mean.astype(np.float32),
        std=std.astype(np.float32),
        k=np.int64(DEFAULT_K),
        reject_radius=np.float32(reject_radius),
        feature_version=np.int64(FEATURE_VERSION),
    )
    print(f"\nwrote {os.path.relpath(MODEL_PATH)}  ({len(xn)} samples, {len(labels)} labels)")
    print("map the new labels to actions in config/gesture_actions.json, then run the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
