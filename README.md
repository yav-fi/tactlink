# dnhacks26

Webcam gesture control for a simulated quadcopter. Your hand is the transmitter:
a webcam tracks it with MediaPipe, the gesture is mapped to a flight command,
and an arcade physics model flies a drone you watch in a 3D view next to the
camera feed.

## Setup

```sh
git clone https://github.com/yav-fi/dnhacks26.git
cd dnhacks26
python -m venv .venv && .venv\Scripts\activate   # optional
pip install -r requirements.txt
```

## Run

```sh
python src/main.py                # live webcam, camera 0
python src/main.py --camera 1     # a different camera
python src/main.py --demo         # no camera: scripted flight, good for a first check
python src/main.py --demo --headless --out flight.png   # render one sample frame
```

On first live run the MediaPipe hand model (`hand_landmarker.task`, ~7.5 MB) is
downloaded into `models/` automatically. `--demo` needs no camera and no model.

Keys while running: `q` quit · `r` reset · `space` force-arm toggle.

## Gestures (right hand, palm to the camera)

| Gesture | Effect |
| --- | --- |
| Move palm left / right | Yaw left / right |
| Move palm up / down | Climb / descend |
| Tilt hand left / right | Roll (bank and slide sideways) |
| Pinch thumb + index | Pitch forward (fly forward) |
| Open palm (4+ fingers) | Arm / take off |
| Closed fist | Disarm / land |

A centered dead zone keeps the drone steady when your hand is roughly neutral.

## Layout

| File | Role |
| --- | --- |
| `src/main.py` | Capture loop, window, keys, `--demo` / `--headless` |
| `src/hand_tracker.py` | MediaPipe Hands wrapper → `HandState` |
| `src/controls.py` | `HandState` → normalized `ControlInput` with smoothing |
| `src/simulator.py` | Arcade quadcopter physics (world frame: x right, y forward, z up) |
| `src/visualizer.py` | Look-at pinhole camera, 3D drone render, control HUD |
| `src/control_types.py` | Shared dataclasses |

The simulator takes a normalized `ControlInput` (throttle, yaw_rate, roll,
pitch, armed), so swapping the simulator for a real link (Tello, MAVLink RC
override, serial) is a matter of writing one adapter that consumes the same
struct.

Attribution: Yavin added `AGENTS.md` / `CLAUDE.md` to keep coding-agent
instructions consistent across tools.
