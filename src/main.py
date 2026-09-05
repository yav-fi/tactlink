"""Webcam gesture control for a simulated quadcopter.

Live mode (default) reads a webcam, tracks one hand with MediaPipe's
GestureRecognizer, turns the hand pose + gesture into a flight command, steps
the simulator, and shows the camera feed beside a 3D view of the drone.

    python src/main.py                 # live, camera 0
    python src/main.py --camera 1      # pick another camera
    python src/main.py --demo          # no camera: scripted flight for testing
    python src/main.py --demo --headless --out flight.png   # render a sample

Gestures: Thumb_Up take off / step altitude · Thumb_Down land · Pointing_Up
orbit · ILoveYou return home · two-finger wiper directional dash · three-finger
pose forward dash. Open_Palm, Closed_Fist, and Victory are disabled by default.

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
from gestures import GestureInterpreter
from operators import OperatorPool
from swarm import Swarm
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
    (1.5, 2.5, "Thumb_Up"),       # arm + takeoff
    (4.0, 5.0, "Thumb_Up"),       # airborne: step altitude up
    (7.0, 8.3, "Pointing_Up"),    # orbit the origin (until the next command)
    (17.5, 18.5, "ILoveYou"),     # return home + hover
    (22.0, 23.5, "Thumb_Down"),   # disarm + land
]
_DEMO_SWING = (10.5, 14.0)        # two-finger wiper V-H-V-H -> fly east
_DEMO_THREE = (15.5, 16.6)        # three fingers sideways -> forward (operator-relative)


def _demo_hand(t: float) -> HandState:
    """A scripted hand path + gesture stream so the pipeline runs without a camera."""
    if t < 0.8:
        return HandState(present=False)

    if _DEMO_SWING[0] <= t < _DEMO_SWING[1]:
        vertical = int((t - _DEMO_SWING[0]) / 0.6) % 2 == 0
        pd = (0.03, -0.99) if vertical else (0.98, 0.05)   # up / horizontal-right
        return HandState(present=True, fingers=(False, True, True, False, False),
                         fingers_up=2, point_dir=pd, gesture="None")

    if _DEMO_THREE[0] <= t < _DEMO_THREE[1]:
        return HandState(present=True, fingers=(False, True, True, True, False),
                         fingers_up=3, point_dir=(0.98, 0.05), gesture="None")

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
        fingers_up=1 if gesture != "None" else 5,
        point_dir=(0.1, -0.9),
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

    # Finger / orientation debug for the geometric detectors.
    from finger_swing import _orientation
    names = "TIMRP"
    fs = "".join(names[i] if up else "-" for i, up in enumerate(hand.fingers)) \
        if hand.present else "-----"
    orient = _orientation(hand.point_dir) or "?" if hand.present else "-"
    cv2.putText(panel, f"fingers {fs}   orient {orient}", (24, 196), font, 0.55,
                (200, 200, 120), 1, cv2.LINE_AA)

    cv2.putText(panel, "fired:", (24, 232), font, 0.55, (150, 150, 150), 1, cv2.LINE_AA)
    for i, line in enumerate(list(log)[-13:]):
        cv2.putText(panel, line, (24, 258 + i * 26), font, 0.55, (235, 235, 235), 1,
                    cv2.LINE_AA)
    return panel


def run(args: argparse.Namespace) -> int:
    interp = GestureInterpreter()
    check_mode = args.check_gestures
    runtime_mode = bool(args.mission_url) and not check_mode
    swarm = None if (runtime_mode or check_mode) else Swarm(args.drones)
    viz = None if (runtime_mode or check_mode) else Visualizer()
    operators = OperatorPool(max(args.operators, 4) if args.demo else args.operators)
    operators.wander_enabled = args.wander and not args.demo
    if args.demo:                        # deterministic: a line of people facing east
        for i, op in enumerate(operators.operators):
            op.pos[:] = [i * 12.0 - (operators.n - 1) * 6.0, 0.0]
            op.heading = 0.0
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
                                   hand.gesture_source, hand=hand)
            if kbd_event:
                gstate.events.append(kbd_event)
                kbd_event = None
            operators.step(dt if dt > 0 else 1 / 60)
            if swarm is not None:
                # Control goes to whoever the drone is nearest; idle drone trails them.
                operators.set_active_by_proximity(swarm.drones[0].state.pos[:2])
                gstate.follow_pos = tuple(float(v) for v in operators.active_op.pos)
            # "forward" is relative to the operator who gestured.
            gstate.events = [
                f"fly_bearing:{operators.resolve_forward_bearing():.4f}"
                if e == "fly_forward" else e
                for e in gstate.events
            ]
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
                if hand.present and int(elapsed * 2) != int((elapsed - dt) * 2):
                    from finger_swing import _orientation, _three_fingers, _two_fingers
                    fs = "".join("TIMRP"[i] if u else "-"
                                 for i, u in enumerate(hand.fingers))
                    print(f"  hand: fingers={fs} pointdir=({hand.point_dir[0]:+.2f},"
                          f"{hand.point_dir[1]:+.2f}) orient={_orientation(hand.point_dir)}"
                          f" | gesture={hand.gesture}({hand.gesture_source})"
                          f" 3finger={_three_fingers(hand)} 2finger={_two_fingers(hand)}")
                side_frame = _gesture_check_panel(hand, gstate, event_log)
            elif runtime_mode:
                gesture = hand.gesture if hand.present else "None"
                side_frame = _runtime_panel(gesture, runtime_status)
            else:
                _, cmd = swarm.step(hand, gstate, dt if dt > 0 else 1 / 60)
                side_frame = viz.render(swarm.states(), cmd, fps, swarm.trails(),
                                        gstate, swarm.selected, operators)
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
                if swarm is not None:
                    swarm.reset()
                interp = GestureInterpreter()
            if key == ord(" "):
                armed = swarm is not None and swarm.drones[0].state.armed
                kbd_event = "land" if (runtime_mode or armed) else "takeoff"
            if swarm is not None and ord("1") <= key <= ord("9"):
                swarm.selected = min(key - ord("1"), swarm.n - 1)
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
    p.add_argument("--drones", type=int, default=5,
                   help="squad size flown in formation (1 = single drone)")
    p.add_argument("--operators", type=int, default=1,
                   help="simulated people giving gestures (built for 5)")
    p.add_argument("--wander", action="store_true",
                   help="let the simulated operators walk around (default: they stand still)")
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
