# dnhacks26

Clone:

```sh
git clone https://github.com/yav-fi/dnhacks26.git
```

Yavin added `AGENTS.md` and `CLAUDE.md` to keep coding-agent instructions
consistent across tools.

This repo hosts two hackathon workstreams:

- **[Webcam gesture quadcopter control](#webcam-gesture-quadcopter-control)** —
  fly a simulated drone with hand gestures (`src/`).
- **[Local LLM chat + benchmark](#local-llm-chat--benchmark)** — fast on-device
  chat inference (`chat_client.py`, `scripts/`).

---

# Webcam gesture quadcopter control

Your hand is the transmitter: a webcam tracks it with MediaPipe, the gesture is
mapped to a flight command, and an arcade physics model flies a drone you watch
in a 3D view next to the camera feed.

## Setup

```sh
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
| `src/hand_tracker.py` | MediaPipe Tasks HandLandmarker → `HandState` |
| `src/controls.py` | `HandState` → normalized `ControlInput` with smoothing |
| `src/simulator.py` | Arcade quadcopter physics (world frame: x right, y forward, z up) |
| `src/visualizer.py` | Look-at pinhole camera, 3D drone render, control HUD |
| `src/control_types.py` | Shared dataclasses |

The simulator takes a normalized `ControlInput` (throttle, yaw_rate, roll,
pitch, armed), so swapping the simulator for a real link (Tello, MAVLink RC
override, serial) is a matter of writing one adapter that consumes the same
struct.

---

# Local LLM chat + benchmark

## Setup

```sh
pip install -r requirements.txt
git clone https://github.com/kaarelkaarelson/mlx-bench.git ~/mlx-bench
./scripts/download_model.sh
./scripts/build_dflash_draft.sh
```

## Commands

```sh
./scripts/start_chat_server.sh   # spin up the inference server
./scripts/ask.sh "question"      # ask it something (no arg = interactive chat)
./scripts/benchmark.sh           # thermally-gated tok/s benchmark
```
