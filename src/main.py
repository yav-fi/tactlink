"""Webcam gesture control for a simulated quadcopter.

Live mode (default) reads a webcam, tracks one hand with MediaPipe, maps the
gesture to a flight command, steps the simulator, and shows the camera feed
beside a 3D view of the drone.

    python src/main.py                 # live, camera 0
    python src/main.py --camera 1      # pick another camera
    python src/main.py --demo          # no camera: scripted flight for testing
    python src/main.py --demo --headless --out flight.png   # render a sample

Keys: q quit  ·  r reset  ·  space arm/disarm toggle
"""

from __future__ import annotations

import argparse
import math
import time

import cv2
import numpy as np

from control_types import ControlInput, HandState
from controls import GestureController
from simulator import QuadSimulator
from visualizer import Visualizer


def _demo_hand(t: float) -> HandState:
    """A scripted hand path so the pipeline can run without a camera."""
    if t < 1.0:
        return HandState(present=False)
    phase = t - 1.0
    fingers = 5 if phase < 8.0 else 0
    return HandState(
        present=True,
        palm_x=0.5 + 0.28 * math.sin(phase * 0.7),
        palm_y=0.5 - 0.30 * math.sin(phase * 0.5),
        roll_angle=0.5 * math.sin(phase * 0.9),
        pinch=max(0.0, math.sin(phase * 0.4)),
        fingers_up=fingers,
    )


def _compose(cam_bgr: np.ndarray, sim_bgr: np.ndarray) -> np.ndarray:
    h = sim_bgr.shape[0]
    scale = h / cam_bgr.shape[0]
    cam_resized = cv2.resize(cam_bgr, (int(cam_bgr.shape[1] * scale), h))
    return np.hstack([cam_resized, sim_bgr])


def run(args: argparse.Namespace) -> int:
    controller = GestureController()
    sim = QuadSimulator()
    viz = Visualizer()

    tracker = None
    cap = None
    if not args.demo:
        from hand_tracker import HandTracker

        tracker = HandTracker()
        cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0)
        if not cap.isOpened():
            print(f"could not open camera {args.camera}; try --camera N or --demo")
            return 1

    window = "quadcopter gesture control"
    if not args.headless:
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    start = time.time()
    prev = start
    manual_arm_override = False
    fps = 0.0
    frame_count = 0

    try:
        while True:
            now = time.time()
            dt = now - prev
            prev = now
            elapsed = now - start

            if args.demo:
                hand = _demo_hand(elapsed)
                cam_frame = np.full((480, 640, 3), (30, 30, 30), dtype=np.uint8)
                cv2.putText(cam_frame, "DEMO MODE (no camera)", (60, 240),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
            else:
                ok, frame = cap.read()
                if not ok:
                    print("camera read failed")
                    break
                frame = cv2.flip(frame, 1)
                hand = tracker.process(frame)
                tracker.draw(frame)
                cam_frame = frame

            cmd = controller.update(hand)
            if manual_arm_override:
                cmd = ControlInput(cmd.throttle, cmd.yaw_rate, cmd.roll, cmd.pitch,
                                   armed=True, event="manual arm (space)")
            state = sim.step(cmd, dt if dt > 0 else 1 / 60)

            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt
            sim_frame = viz.render(state, cmd, fps, sim.trail)
            composite = _compose(cam_frame, sim_frame)

            frame_count += 1
            if args.headless:
                if elapsed >= args.duration:
                    if args.out:
                        cv2.imwrite(args.out, composite)
                        print(f"wrote {args.out}  ({frame_count} frames simulated)")
                    break
                continue

            cv2.imshow(window, composite)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                sim.reset()
                controller = GestureController()
            if key == ord(" "):
                manual_arm_override = not manual_arm_override
    finally:
        if cap is not None:
            cap.release()
        if tracker is not None:
            tracker.close()
        if not args.headless:
            cv2.destroyAllWindows()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera", type=int, default=0, help="webcam index (default 0)")
    p.add_argument("--demo", action="store_true",
                   help="run without a camera using a scripted hand path")
    p.add_argument("--headless", action="store_true",
                   help="do not open a window; with --out, save a frame and exit")
    p.add_argument("--duration", type=float, default=6.0,
                   help="seconds to simulate before exiting in --headless")
    p.add_argument("--out", type=str, default="", help="path to save a composite frame")
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
