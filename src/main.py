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


def _load_mission_integration():
    """Import the mission-runtime adapters lazily.

    They pull in the ``simulation`` package (pydantic etc.), so the standalone
    simulator/demo path must not import them - only ``--mission-url`` needs them.
    """
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from integrations.gesture_mission import GestureMissionAdapter
    from integrations.mission_client import MissionClient, MissionClientError

    return GestureMissionAdapter, MissionClient, MissionClientError

# (start, end) seconds -> gesture held during the demo timeline.
_DEMO_GESTURES = [
    (1.5, 3.0, "Open_Palm"),      # takeoff
    (7.0, 8.5, "Victory"),        # cycle flight mode
    (12.0, 13.3, "Pointing_Up"),  # 360 spin
    (16.5, 17.2, "Open_Palm"),    # combo: open ->
    (17.6, 18.3, "Closed_Fist"),  #        fist ->
    (18.7, 19.4, "Open_Palm"),    #        open  => return home (within 3 s)
    (25.0, 27.0, "Closed_Fist"),  # land
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


def _compose(cam_bgr: np.ndarray, side_bgr: np.ndarray) -> np.ndarray:
    h = side_bgr.shape[0]
    scale = h / cam_bgr.shape[0]
    cam_resized = cv2.resize(cam_bgr, (int(cam_bgr.shape[1] * scale), h))
    return np.hstack([cam_resized, side_bgr])


def _runtime_panel(gesture: str, status: str, width: int = 640, height: int = 720) -> np.ndarray:
    """Render runtime connection state without implying local drone telemetry."""
    panel = np.full((height, width, 3), (28, 26, 24), dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(panel, "RUNTIME MODE", (40, 90), font, 1.1, (90, 220, 130), 2, cv2.LINE_AA)
    cv2.putText(
        panel,
        "drone state rendered elsewhere",
        (40, 130),
        font,
        0.65,
        (235, 235, 235),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(panel, f"gesture: {gesture}", (40, 220), font, 0.7, (90, 200, 255), 2, cv2.LINE_AA)
    cv2.putText(panel, status, (40, 270), font, 0.55, (180, 180, 180), 1, cv2.LINE_AA)
    return panel


def _gesture_check_panel(hand, gstate, log, width: int = 640, height: int = 720) -> np.ndarray:
    """Dry-run panel: shows what the recognizer + interpreter see, nothing acts."""
    panel = np.full((height, width, 3), (28, 26, 24), dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(panel, "GESTURE CHECK  (no drone, nothing acts)", (24, 34), font,
                0.6, (90, 220, 130), 1, cv2.LINE_AA)

    g = hand.gesture if hand.present else "None"
    src = f"  [{hand.gesture_source}]" if g != "None" else ""
    cv2.putText(panel, f"{g}{src}", (24, 80), font, 0.9, (235, 235, 235), 2, cv2.LINE_AA)
    if hand.present and g != "None":
        cv2.putText(panel, f"score {hand.gesture_score:.2f}", (24, 106), font, 0.5,
                    (150, 150, 150), 1, cv2.LINE_AA)

    cv2.rectangle(panel, (24, 120), (224, 132), (60, 58, 55), -1)
    cv2.rectangle(panel, (24, 120), (24 + int(200 * gstate.hold_progress), 132),
                  (90, 220, 130), -1)

    if gstate.sequence_hint:
        cv2.putText(panel, f"combo: {gstate.sequence_hint}", (24, 168), font, 0.6,
                    (90, 200, 255), 1, cv2.LINE_AA)

    cv2.putText(panel, "fired:", (24, 220), font, 0.55, (150, 150, 150), 1, cv2.LINE_AA)
    for i, line in enumerate(list(log)[-14:]):
        cv2.putText(panel, line, (24, 248 + i * 26), font, 0.55, (235, 235, 235), 1,
                    cv2.LINE_AA)
    return panel


def run(args: argparse.Namespace) -> int:
    controller = GestureController()
    interp = GestureInterpreter()
    check_mode = args.check_gestures
    runtime_mode = bool(args.mission_url) and not check_mode
    sim = None if (runtime_mode or check_mode) else QuadSimulator()
    viz = None if (runtime_mode or check_mode) else Visualizer()
    event_log: list[str] = []
    mission_adapter = mission_client = mission_error_cls = None
    if runtime_mode:
        adapter_cls, client_cls, mission_error_cls = _load_mission_integration()
        mission_adapter = adapter_cls()
        mission_client = client_cls(args.mission_url)
    runtime_status = f"connected: {args.mission_url}" if runtime_mode else ""

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
                        mission_id = created.get("id", mission.type)
                        runtime_status = f"submitted: {mission_id}"
                        print(f"runtime mission: {mission_id}")
                    except mission_error_cls as exc:
                        runtime_status = f"submission rejected: {exc}"
                        print(f"runtime mission rejected: {exc}")

            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt
            if check_mode:
                for ev in gstate.events:
                    event_log.append(f"{elapsed:6.1f}s  {ev}")
                    print(f"[{elapsed:6.1f}s] {ev}")
                side_frame = _gesture_check_panel(hand, gstate, event_log)
            elif runtime_mode:
                gesture = hand.gesture if hand.present else "None"
                side_frame = _runtime_panel(gesture, runtime_status)
            else:
                cmd = controller.update(hand, gstate, sim.state)
                state = sim.step(cmd, dt if dt > 0 else 1 / 60)
                side_frame = viz.render(state, cmd, fps, sim.trail, gstate)
            composite = _compose(cam_frame, side_frame)

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
                if sim is not None:
                    sim.reset()
                controller = GestureController()
                interp = GestureInterpreter()
            if key == ord(" "):
                armed = sim is not None and sim.state.armed
                kbd_event = "land" if (runtime_mode or armed) else "takeoff"
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
    p.add_argument("--check-gestures", action="store_true",
                   help="dry run: webcam + recognition + HUD only, the drone does "
                        "not move and nothing is submitted")
    p.add_argument(
        "--mission-url",
        default="",
        help="submit discrete HOLD/RETURN gestures to this runtime URL (for example http://127.0.0.1:8000)",
    )
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
