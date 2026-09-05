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

On first live run the MediaPipe model (`gesture_recognizer.task`, ~8 MB) is
downloaded into `models/` automatically. `--demo` needs no camera and no model.

Keys while running: `q` quit · `r` reset · `space` take off / land.

## Flying (continuous, from hand pose)

The mapping depends on the current **flight mode**:

| Mode | palm ← → | palm ↑ ↓ | hand tilt | pinch |
| --- | --- | --- | --- | --- |
| `HEADING` (default) | yaw | throttle | roll | pitch forward |
| `POSITION` | roll | throttle | yaw | pitch forward |
| `HOVER` | — | throttle | — | — |

A centered dead zone keeps the drone steady when your hand is neutral.

## Gestures (discrete, MediaPipe GestureRecognizer)

Hold a sign steady for ~0.4 s to fire it; relax before repeating.

| Sign | Action |
| --- | --- |
| ✋ Open palm | Take off / arm |
| ✊ Closed fist | Land / disarm |
| ✌️ Victory | Cycle flight mode (heading → position → hover) |
| 👍 Thumb up | Speed up (slow → normal → sport) |
| 👎 Thumb down | Speed down |
| ☝️ Pointing up | Do a 360° spin |
| 🤟 ILoveYou | Return to the start point and hover |

Take off, land, spin, and return-home run as short autopilot routines that
override the hand until they finish (`MODE` turns red in the HUD).

## Layout

| File | Role |
| --- | --- |
| `src/main.py` | Capture loop, window, keys, `--demo` / `--headless` |
| `src/hand_tracker.py` | MediaPipe GestureRecognizer → `HandState` (landmarks + gesture) |
| `src/gestures.py` | Debounces the gesture stream → mode, speed, discrete events |
| `src/controls.py` | Hand pose + gesture state → `ControlInput`; runs autopilot routines |
| `src/simulator.py` | Arcade quadcopter physics (world frame: x right, y forward, z up) |
| `src/visualizer.py` | Look-at pinhole camera, 3D drone render, control HUD |
| `src/control_types.py` | Shared dataclasses and the `FlightMode` enum |

The simulator takes a normalized `ControlInput` (throttle, yaw_rate, roll,
pitch, armed), so swapping the simulator for a real link (Tello, MAVLink RC
override, serial) is a matter of writing one adapter that consumes the same
struct.

---

# Local LLM chat + benchmark

## Setup

```sh
pip install -U huggingface_hub mlx-vlm uv chad-code
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
