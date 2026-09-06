# Gesture control demo — drone hand-off

Operator 1 flies one drone with webcam hand signals among **N static operators**
(4 by default, scattered at random through the scene each run), hands the drone
between them, and dashes it forward or sideways. One window: the webcam with the
tracked hand on the left, a 3D view of the operators and the drone on the right.

Standalone: this folder imports nothing from `src/`. It runs on the repo's
dependencies (`mediapipe`, `opencv`, `numpy` from `requirements.txt`), so no
extra setup — if `python src/main.py --demo` works, this does too.

## Run

```bash
python demos/gesture-handoff/gesture_demo.py            # live webcam
python demos/gesture-handoff/gesture_demo.py --demo     # no camera, scripted flight
python demos/gesture-handoff/gesture_demo.py --camera 1 # if the default camera is wrong
python demos/gesture-handoff/gesture_demo.py --check    # just load the model and exit
```

The gesture model (~8 MB, Google) downloads next to this file on first launch
(gitignored). Use even lighting, one hand in frame, all fingers visible. Q or
Escape quits.

## Gestures

| Pose | Hold | Action |
| --- | --- | --- |
| Thumbs up | ~0.4 s | Arm and take off; again while flying, climb a step (up to 9 m) |
| Thumbs down | ~0.4 s | Land and disarm |
| Open palm | ~0.4 s | Halt — cancel the current move and hover in place |
| Point up (index finger) | ~0.4 s | Orbit the controlling operator; point up again to stop |
| I-love-you sign | ~0.4 s | Fly back to the controlling operator and hover |
| **Two fingers up** (peace sign) | **~1 s** | **Hand off** — send the drone to a random other operator |
| **Three fingers up** | **~0.6 s** | **Dash forward** ~5 m (away from the camera) |
| **Index finger held left / right** | **~0.5 s** | **Dash** the drone that way ~5 m |

The take-off / land / halt / orbit / return gestures use MediaPipe's built-in
recogniser. The hand-off and dashes are **counted from the finger positions**
(more reliable than MediaPipe's "Victory") — hold the shape until the bar at the
bottom of the scene fills. Point *up* is orbit, so two fingers up vs. sideways
never collide. The webcam panel always shows what's detected
(`model Pointing_Up 80%  fingers -IMR-  -> dash_forward`); if a pose won't fire,
read that line or run **`--diag`** to print it every frame.

The drone's nose always turns to face the nearest operator. Brief recogniser
dropouts don't reset a hold. Only operator 1 has a camera; a hand-off just moves
which operator the drone orbits, returns to, and hovers above.

## Options

```
--camera N        webcam index (default 0)
--threshold F     min model score to accept a gesture (default 0.45; try 0.3)
--diag            print what the recognizer sees each frame
--hold F          settle time for the on-screen label (default 0.35)
--operators N     number of static operators, 2..8 (default 4)
--demo            scripted flight, no webcam
--headless --out frame.png --seconds 14   render one demo frame and exit
```

## Files

| File | Role |
| --- | --- |
| `gesture_demo.py` | entry point: webcam loop, MediaPipe recognizer, compositing, `--demo` |
| `gestures.py` | `GestureGate` (canned), `HeldPose` + `classify_pose` (finger-counted poses), landmark helpers |
| `flight.py` | `Quad` physics, `Operators` (random scatter), `Autopilot` |
| `scene.py` | pinhole-camera 3D render of the grid, operators, drone + HUD |
| `test_gesture_demo.py` | `python -m unittest` from this folder — 14 tests |

The 3D view is a hand-rolled pinhole projection (no game engine); the model reads
static hand poses, not motion. The scene is a visualization, not a calibrated
simulation.
