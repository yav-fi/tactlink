"""Record hand-pose samples for a custom gesture from the webcam.

    python scripts/collect_gestures.py --label two_flat
    python scripts/collect_gestures.py --label two_flat --reset   # start this label over
    python scripts/collect_gestures.py --list

Hold the pose, press SPACE to start/stop recording, Q when you have enough
(aim for ~250 per gesture, from a few angles and distances). Each run APPENDS to
data/gestures/<label>.npy - re-run to add more; `--target` is new samples per
run, not a cap. Retrain afterwards with scripts/train_gestures.py.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from landmark_features import extract  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "data", "gestures")


def _counts() -> dict[str, int]:
    out = {}
    if os.path.isdir(DATA_DIR):
        for name in sorted(os.listdir(DATA_DIR)):
            if name.endswith(".npy"):
                out[name[:-4]] = len(np.load(os.path.join(DATA_DIR, name)))
    return out


def _save(label: str, rows: list[np.ndarray]) -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{label}.npy")
    new = np.array(rows, dtype=np.float32)
    if os.path.exists(path):
        new = np.vstack([np.load(path), new])
    np.save(path, new)
    return len(new)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", help="gesture name to record")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--target", type=int, default=250,
                   help="auto-stop after this many NEW samples this session")
    p.add_argument("--list", action="store_true", help="show sample counts and exit")
    p.add_argument("--reset", action="store_true",
                   help="delete this label's existing samples before recording")
    args = p.parse_args()

    if args.list or not args.label:
        counts = _counts()
        print("samples in data/gestures/:" if counts else "no samples recorded yet")
        for k, v in counts.items():
            print(f"  {k:20s} {v:5d}")
        if not args.label:
            print("\nto record a gesture, pass --label, e.g.:")
            print("  python scripts/collect_gestures.py --label two_up")
        return 0 if args.list else 2

    import cv2
    from hand_tracker import HandTracker

    tracker = HandTracker()
    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0)
    if not cap.isOpened():
        print(f"could not open camera {args.camera}")
        return 1

    if args.reset:
        path = os.path.join(DATA_DIR, f"{args.label}.npy")
        if os.path.exists(path):
            os.remove(path)
            print(f"cleared existing {args.label}.npy")

    existing = _counts().get(args.label, 0)
    rows: list[np.ndarray] = []
    recording = False
    read_fails = 0
    font = cv2.FONT_HERSHEY_SIMPLEX
    print(f"recording '{args.label}' (already have {existing}). "
          f"SPACE=start/stop, Q=save+quit. Auto-stops after {args.target} new samples.")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                read_fails += 1
                if read_fails > 60:      # camera really gone, not just warming up
                    print("camera stopped returning frames")
                    break
                continue
            read_fails = 0
            frame = cv2.flip(frame, 1)
            hand = tracker.process(frame)
            tracker.draw(frame)

            if recording and hand.present and tracker.raw_landmarks is not None:
                rows.append(extract(tracker.raw_landmarks, tracker.handedness))

            total = existing + len(rows)
            cv2.putText(frame, f"{args.label}", (12, 28), font, 0.8,
                        (140, 240, 140), 2)
            state = "REC" if recording else "paused"
            color = (60, 60, 240) if recording else (200, 200, 200)
            cv2.putText(frame, f"{state}  new {len(rows)}/{args.target}   total {total}",
                        (12, 58), font, 0.7, color, 2)
            if not hand.present:
                cv2.putText(frame, "no hand", (12, 88), font, 0.6, (60, 60, 240), 2)
            cv2.imshow("collect gestures", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                recording = not recording
            if len(rows) >= args.target:
                print("reached this session's target")
                break
    finally:
        cap.release()
        tracker.close()
        cv2.destroyAllWindows()

    if rows:
        n = _save(args.label, rows)
        print(f"saved {len(rows)} new samples -> {args.label}.npy ({n} total)")
    else:
        print("no samples recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
