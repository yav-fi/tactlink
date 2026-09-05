"""Webcam gesture control for a simulated quadcopter.

Live mode (default) reads a webcam, tracks one hand with MediaPipe's
GestureRecognizer, turns the hand pose + gesture into a flight command, steps
the simulator, and shows the camera feed beside a 3D view of the drone.

    python src/main.py                 # live, camera 0
    python src/main.py --camera 1      # pick another camera
    python src/main.py --demo          # no camera: scripted flight for testing
    python src/main.py --demo --headless --out flight.png   # render a sample

Gestures: Open_Palm take off · Closed_Fist land · Victory cycle mode ·
Thumb_Up/Thumb_Down speed · Pointing_Up 360 spin · ILoveYou return home.

Keys: q quit · r reset · space take off / land
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from control_types import HandState
from controls import GestureController
from gestures import GestureInterpreter
from simulator import QuadSimulator
from visualizer import Visualizer

# ``python src/main.py`` puts src/ rather than the repository root on sys.path.
# Add the root only for importing the sibling integration package.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from integrations.gesture_mission import GestureMissionAdapter
from integrations.mission_client import MissionClient, MissionClientError

# (start, end) seconds -> gesture held during the demo timeline.
_DEMO_GESTURES = [
    (1.5, 3.0, "Open_Palm"),
    (7.0, 8.5, "Victory"),
    (13.0, 14.5, "Pointing_Up"),
    (18.0, 19.5, "ILoveYou"),
    (24.0, 26.0, "Closed_Fist"),
]


def _demo_hand(t: float) -> HandState:
    """A scripted hand path + gesture stream so the pipeline runs without a camera."""
    if t < 0.8:
        return HandState(present=False)
    gesture = "None"
    for a, b, g in _DEMO_GESTURES:
        if a <= t < b:
            gesture = g
    return HandState(
        present=True,
        palm_x=0.5 + 0.26 * math.sin(t * 0.7),
        palm_y=0.5 - 0.28 * math.sin(t * 0.5),
        roll_angle=0.5 * math.sin(t * 0.9),
        pinch=max(0.0, 0.6 * math.sin(t * 0.4)),
        fingers_up=5 if gesture in ("None", "Open_Palm") else 1,
        gesture=gesture,
        gesture_score=0.9,
        gesture_source="canned" if gesture != "None" else "none",
    )


def _compose(cam_bgr: np.ndarray, sim_bgr: np.ndarray) -> np.ndarray:
    h = sim_bgr.shape[0]
    scale = h / cam_bgr.shape[0]
    cam_resized = cv2.resize(cam_bgr, (int(cam_bgr.shape[1] * scale), h))
    return np.hstack([cam_resized, sim_bgr])


def run(args: argparse.Namespace) -> int:
    controller = GestureController()
    interp = GestureInterpreter()
    sim = QuadSimulator()
    viz = Visualizer()
    mission_adapter = GestureMissionAdapter() if args.mission_url else None
    mission_client = MissionClient(args.mission_url) if args.mission_url else None

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
    fps = 0.0
    frame_count = 0
    kbd_event = None

    try:
        while True:
            now = time.time()
            dt = now - prev
            prev = now
            elapsed = now - start

            if args.demo:
                hand = _demo_hand(elapsed)
                cam_frame = np.full((480, 640, 3), (30, 30, 30), dtype=np.uint8)
                cv2.putText(cam_frame, "DEMO MODE (no camera)", (60, 230),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
                if hand.present and hand.gesture != "None":
                    cv2.putText(cam_frame, hand.gesture, (60, 275),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (140, 240, 140), 2)
            else:
                ok, frame = cap.read()
                if not ok:
                    print("camera read failed")
                    break
                frame = cv2.flip(frame, 1)
                hand = tracker.process(frame)
                tracker.draw(frame)
                if hand.present and hand.gesture != "None":
                    cv2.putText(frame, f"{hand.gesture} {hand.gesture_score:.2f}",
                                (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                                (140, 240, 140), 2)
                cam_frame = frame

            gstate = interp.update(hand.gesture if hand.present else "None",
                                   hand.gesture_source)
            if kbd_event:
                gstate.events.append(kbd_event)
                kbd_event = None
            if mission_adapter and mission_client:
                for event in gstate.events:
                    mission = mission_adapter.event_to_command(event)
                    if mission is None:
                        continue
                    try:
                        created = mission_client.submit(mission)
                        print(f"runtime mission: {created.get('id', mission.type)}")
                    except MissionClientError as exc:
                        print(f"runtime mission rejected: {exc}")
            cmd = controller.update(hand, gstate, sim.state)
            state = sim.step(cmd, dt if dt > 0 else 1 / 60)

            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt
            sim_frame = viz.render(state, cmd, fps, sim.trail, gstate)
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
                interp = GestureInterpreter()
            if key == ord(" "):
                kbd_event = "land" if sim.state.armed else "takeoff"
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
    p.add_argument("--duration", type=float, default=10.0,
                   help="seconds to simulate before exiting in --headless")
    p.add_argument("--out", type=str, default="", help="path to save a composite frame")
    p.add_argument(
        "--mission-url",
        default="",
        help="submit discrete HOLD/RETURN gestures to this runtime URL (for example http://127.0.0.1:8000)",
    )
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
