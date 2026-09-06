"""Webcam gesture control demo: operator 1 flies one drone among N static
operators (scattered at random through the scene) and hands it between them.

    python demos/gesture-handoff/gesture_demo.py            # live webcam
    python demos/gesture-handoff/gesture_demo.py --demo     # no camera: scripted flight
    python demos/gesture-handoff/gesture_demo.py --check    # load the model and exit

Canned gestures (hold ~0.4 s): thumbs up = take off / climb, thumbs down = land,
open palm = halt, point up = orbit, I-love-you = return. Held finger poses:
two fingers up ~1 s = hand off to a random operator, three fingers ~0.6 s = dash
forward, index held left/right ~0.5 s = dash that way.

``--diag`` prints what the recognizer sees each frame. Runs on the repo's deps
(mediapipe / opencv / numpy); imports nothing from src/. Recognition is local;
webcam frames are neither recorded nor uploaded.
"""

from __future__ import annotations

import argparse
import time
import urllib.request
from pathlib import Path

MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task'
CANNED = {'Open_Palm', 'Thumb_Up', 'Thumb_Down', 'Pointing_Up', 'ILoveYou', 'Victory'}
FRIENDLY = {'Open_Palm': 'Open palm', 'Thumb_Up': 'Thumbs up',
            'Thumb_Down': 'Thumbs down', 'Pointing_Up': 'Pointing up',
            'ILoveYou': 'I love you', 'Victory': 'Victory'}
EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
         (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
         (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]

PANEL_W, PANEL_H = 640, 480


class StableLabel:
    """Time-based settling for the label shown on the webcam panel."""

    def __init__(self, hold=0.35):
        self.hold = hold
        self.candidate = 'Unknown'
        self.since = 0.0
        self.score = 0.0

    def update(self, label, score, now):
        if label != self.candidate:
            self.candidate, self.since, self.score = label, now, score
        else:
            self.score = 0.25 * score + 0.75 * self.score
        if label == 'Unknown':
            return 'Unknown', 0.0, 0.0
        progress = min(1.0, max(0.0, (now - self.since) / self.hold))
        return (label if progress == 1 else 'Settling...'), self.score, progress


# --- the control demo ------------------------------------------------------

class Sim:
    """Operators + drone + autopilot + gesture gate, advanced one frame at a time."""

    def __init__(self, n_operators=4, seed=None):
        from flight import Autopilot, Operators, Quad
        from gestures import GestureGate, HeldPose

        self.ops = Operators(n_operators, seed=seed)
        self.quad = Quad(self.ops.anchor_pos())
        self.pilot = Autopilot(self.ops)
        self.gate = GestureGate()
        self.pose = HeldPose()
        self.hud = {'mode': 'idle', 'gesture': 'None', 'progress': 0.0,
                    'note': '', 'pose': 0.0, 'pose_label': ''}
        self._note_until = 0.0

    def _note(self, text, now):
        if text:
            self.hud['note'] = text
            self._note_until = now + 2.5

    def advance(self, raw_label, hand, dt, now, force_pose=None):
        from gestures import classify_pose

        command, progress, held = self.gate.update(raw_label, now)
        if command:
            self._note(self.pilot.command(command, self.quad, now), now)

        # Geometric held poses: 2 fingers up = hand off, 3 fingers = dash
        # forward, index held sideways = dash left/right. A canned "Victory" also
        # counts as the hand-off pose.
        pose = force_pose if force_pose is not None else classify_pose(hand, raw_label)
        pcmd, pprog, plabel = self.pose.update(pose, now)
        if pcmd:
            self._note(self.pilot.command(pcmd, self.quad, now), now)

        self.pilot.update(self.quad, dt, now)

        if now > self._note_until:
            self.hud['note'] = ''
        self.hud['mode'] = self.pilot.mode
        self.hud['gesture'] = FRIENDLY.get(held, held if held != 'None' else 'None')
        self.hud['progress'] = progress
        self.hud['pose'] = pprog
        self.hud['pose_label'] = plabel

    def render_scene(self, scene):
        return scene.render(self.quad, self.ops, self.hud)


# --- scripted demo input -------------------------------------------------

# (start, end) seconds -> (canned label, geometric pose) held during the demo.
_DEMO = [
    (1.5, 2.6, 'Thumb_Up', None),                 # take off
    (4.0, 5.0, 'Thumb_Up', None),                 # climb a step
    (6.5, 8.0, 'Pointing_Up', None),              # orbit the starting operator
    (9.5, 10.5, 'Open_Palm', None),               # halt / stop orbiting
    (12.5, 13.5, 'None', 'dash_east'),            # index right -> dash right
    (16.0, 17.0, 'None', 'dash_west'),            # index left -> dash left
    (19.5, 20.5, 'None', 'dash_forward'),         # three fingers -> dash forward
    (23.0, 25.0, 'None', 'handoff_random'),       # two fingers up -> hand off
    (28.0, 29.5, 'Pointing_Up', None),            # orbit the new operator
    (32.0, 33.0, 'ILoveYou', None),               # return to them
    (35.5, 37.5, 'None', 'handoff_random'),       # hand off again
    (40.0, 41.2, 'Thumb_Down', None),             # land
]


def _demo_input(t):
    for a, b, label, pose in _DEMO:
        if a <= t < b:
            return label, pose
    return 'None', None


# --- panels + compositing ---------------------------------------------------

def _webcam_panel(cv2, np, frame, hands, stable_label, score, progress, raw_hint=''):
    panel = cv2.resize(frame, (PANEL_W, PANEL_H))
    for hand in hands:
        pts = [(int(p[0] * PANEL_W), int(p[1] * PANEL_H)) for p in hand]
        for a, b in EDGES:
            cv2.line(panel, pts[a], pts[b], (170, 230, 60), 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(panel, p, 3, (240, 255, 220), -1, cv2.LINE_AA)
    cv2.rectangle(panel, (0, PANEL_H - 54), (PANEL_W, PANEL_H), (24, 20, 17), -1)
    cv2.putText(panel, stable_label, (14, PANEL_H - 28), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (170, 230, 60), 2, cv2.LINE_AA)
    if stable_label not in ('Unknown', 'Settling...'):
        cv2.putText(panel, f'{score:.0%}', (PANEL_W - 90, PANEL_H - 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, cv2.LINE_AA)
    if raw_hint:                                # what the model sees, every frame
        cv2.putText(panel, raw_hint, (14, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (150, 200, 255), 1, cv2.LINE_AA)
    cv2.rectangle(panel, (14, PANEL_H - 16), (PANEL_W - 14, PANEL_H - 10), (60, 60, 60), -1)
    cv2.rectangle(panel, (14, PANEL_H - 16),
                  (14 + int((PANEL_W - 28) * progress), PANEL_H - 10),
                  (170, 230, 60), -1)
    return panel


def _demo_panel(cv2, np, raw_label, pose=None):
    panel = np.full((PANEL_H, PANEL_W, 3), (30, 28, 26), dtype=np.uint8)
    cv2.putText(panel, 'DEMO MODE (no camera)', (60, 220), cv2.FONT_HERSHEY_SIMPLEX,
                0.9, (200, 200, 200), 2, cv2.LINE_AA)
    text = FRIENDLY.get(raw_label, raw_label) if raw_label != 'None' else pose
    if text:
        cv2.putText(panel, str(text), (60, 262), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (140, 240, 140), 2, cv2.LINE_AA)
    return panel


# --- run loops ------------------------------------------------------------

def run_demo(args):
    import cv2
    import numpy as np
    from scene import Scene

    sim = Sim(args.operators)
    scene = Scene(PANEL_W, PANEL_H)

    if args.headless:                       # virtual clock: renders immediately
        dt, vt, raw_label = 1 / 60, 0.0, 'None'
        while vt < args.seconds:
            vt += dt
            raw_label, pose = _demo_input(vt)
            sim.advance(raw_label, None, dt, vt, force_pose=pose)
        composite = np.hstack([_demo_panel(cv2, np, raw_label, pose), sim.render_scene(scene)])
        if args.out:
            cv2.imwrite(args.out, composite)
            print(f'wrote {args.out}')
        return

    window = 'Gesture control demo'
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    start = time.monotonic()
    prev = start
    while True:
        now = time.monotonic()
        t = now - start
        dt = min(0.05, now - prev)
        prev = now
        raw_label, pose = _demo_input(t)
        sim.advance(raw_label, None, dt if dt > 0 else 1 / 60, now, force_pose=pose)
        composite = np.hstack([_demo_panel(cv2, np, raw_label, pose), sim.render_scene(scene)])
        cv2.imshow(window, composite)
        if cv2.waitKey(16) & 0xFF in (27, ord('q')):
            break
        if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()


def run_live(args):
    import cv2
    import mediapipe as mp
    import numpy as np
    from gestures import classify_pose, hand_from_landmarks
    from scene import Scene

    model = Path(__file__).with_name('gesture_recognizer.task')
    if not model.exists():
        print('Downloading gesture model once from Google...')
        tmp = model.with_suffix('.download')
        try:
            with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
                tmp.write_bytes(response.read())
            tmp.replace(model)
        finally:
            tmp.unlink(missing_ok=True)

    options = mp.tasks.vision.GestureRecognizerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=1)
    with mp.tasks.vision.GestureRecognizer.create_from_options(options) as recognizer:
        if args.check:
            blank = mp.Image(image_format=mp.ImageFormat.SRGB,
                             data=np.zeros((480, 640, 3), dtype=np.uint8))
            assert not recognizer.recognize_for_video(blank, 1).hand_landmarks
            print('Model loaded; blank-frame inference passed.')
            return

        camera = cv2.VideoCapture(args.camera)
        if not camera.isOpened():
            raise RuntimeError('Cannot open webcam. Check camera permissions or try --camera 1.')
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)

        sim = Sim(args.operators)
        scene = Scene(PANEL_W, PANEL_H)
        stable = StableLabel(args.hold)
        window = 'Gesture control demo'
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        prev_ms = -1
        prev = time.monotonic()
        try:
            while True:
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError('Webcam stopped. Close other camera apps and restart.')
                frame = cv2.flip(frame, 1)
                now = time.monotonic()
                dt = min(0.05, now - prev)
                prev = now
                timestamp = max(prev_ms + 1, int(now * 1000))
                prev_ms = timestamp

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = recognizer.recognize_for_video(
                    mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)

                raw, score, top_name, top_score = 'None', 0.0, '-', 0.0
                if result.gestures and result.gestures[0]:
                    top = max(result.gestures[0], key=lambda c: c.score)
                    top_name, top_score = top.category_name, top.score
                    if top.category_name in CANNED and top.score >= args.threshold:
                        raw, score = top.category_name, top.score

                hands = [[(p.x, p.y) for p in h] for h in result.hand_landmarks]
                hand = hand_from_landmarks(hands[0]) if hands else None
                sim.advance(raw, hand, dt if dt > 0 else 1 / 60, now)

                fs = ''.join('TIMRP'[k] if hand and hand.fingers[k] else '-'
                             for k in range(5)) if hand else '-----'
                pose = classify_pose(hand, raw)
                readout = f'model {top_name} {top_score:.0%}  fingers {fs}' + \
                          (f'  -> {pose}' if pose else '')
                if args.diag and int(now * 4) != int((now - dt) * 4):
                    pd = hand.point_dir if hand else (0, 0)
                    print(f'{readout}  point ({pd[0]:+.2f},{pd[1]:+.2f})', flush=True)

                disp = FRIENDLY.get(raw, 'Unknown') if raw != 'None' else 'Unknown'
                label, conf, prog = stable.update(disp, score, now)
                panel = _webcam_panel(cv2, np, frame, hands, label, conf, prog, readout)
                composite = np.hstack([panel, sim.render_scene(scene)])
                cv2.imshow(window, composite)
                if cv2.waitKey(1) & 0xFF in (27, ord('q')):
                    break
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            camera.release()
            cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--threshold', type=float, default=0.45)
    parser.add_argument('--diag', action='store_true',
                        help='print what the recognizer sees each frame')
    parser.add_argument('--hold', type=float, default=0.35)
    parser.add_argument('--operators', type=int, default=4)
    parser.add_argument('--demo', action='store_true', help='no webcam: scripted flight')
    parser.add_argument('--check', action='store_true', help='load the model and exit')
    parser.add_argument('--headless', action='store_true', help='with --demo: no window')
    parser.add_argument('--out', default='', help='with --headless: save a frame here')
    parser.add_argument('--seconds', type=float, default=14.0, help='--headless run length')
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1 or args.hold <= 0:
        parser.error('threshold must be 0..1 and hold must be positive')
    if not 2 <= args.operators <= 8:
        parser.error('operators must be 2..8')

    if args.demo:
        run_demo(args)
    else:
        run_live(args)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ImportError, ValueError, AssertionError) as exc:
        raise SystemExit(f'Gesture control demo: {exc}') from exc
