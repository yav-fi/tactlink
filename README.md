# dnhacks26

Clone:

```sh
git clone https://github.com/yav-fi/dnhacks26.git
```

Yavin added `AGENTS.md` and `CLAUDE.md` to keep coding-agent instructions
consistent across tools.

This repo hosts five hackathon workstreams:

- **[Local LLM chat + benchmark](#local-llm-chat--benchmark)** — fast on-device
  chat inference (`chat_client.py`, `scripts/`).
- **[Webcam gesture quadcopter control](#webcam-gesture-quadcopter-control)** —
  fly a simulated drone with hand gestures (`src/`).
- **[Distributed mission runtime](#distributed-mission-runtime)** — multi-node
  autonomy under degraded communications (`simulation/`, `server/`).
- **[Autonomous planning engine](planning/README.md)** — local mission and
  motion planning with obstacle routing, deconfliction, and energy checks (`planning/`).
- **[Cesium 3D mapping](#cesium-3d-mapping)** — browser-based fleet deployment,
  piloting, and deterministic local missions (`3d-mapping/`).

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

## Performance

| Hardware | Model | Decode tok/s |
| --- | --- | --- |
| MacBook Pro, Apple M3 Max, 48GB | mlx-community/Qwen3.8-27B-4bit + w4:gs64 DFlash2 draft + LibraSpec | 72.3 |

Measured with `./scripts/benchmark.sh`, thermally gated (GPU cooled to 37.4°C, fans
verified at max RPM before/after) — see `benchmark_result.json` for the full receipt.

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

## Custom gestures (train your own)

Record your own hand signs and the app will prefer them over the 7 built-ins
(falling back to a canned gesture when the custom model isn't confident). No
image dataset, no TensorFlow - it classifies the 21 hand landmarks directly.

```sh
# 1. record ~200 samples per sign (hold pose, SPACE to record, Q to save)
python scripts/collect_gestures.py --label flat_palm_down
python scripts/collect_gestures.py --label point_left
python scripts/collect_gestures.py --list          # see sample counts

# 2. train (writes models/custom_gestures.npz, picked up automatically)
python scripts/train_gestures.py

# 3. map the new labels to actions
#    edit config/gesture_actions.json, e.g.  "point_left": "return_home"
```

Actions available: `takeoff`, `land`, `cycle_mode`, `speed_up`, `speed_down`,
`spin360`, `return_home`, `estop`. Feature layout is versioned
(`landmark_features.FEATURE_VERSION`) - bump it and retrain if you change it.
`models/custom_gestures.npz` is the one model file that *is* committed, so a
trained set of gestures travels with the repo; raw recordings under `data/` stay
local.

## Gesture combos (sequences)

Several gestures in order, inside a time window, can mean one command - like a
gesture password. Configured in `config/gesture_sequences.json`:

```json
{
  "single_delay": 0.6,
  "sequences": [
    { "pattern": ["Open_Palm", "Closed_Fist", "Open_Palm"], "window": 3.0, "action": "return_home" }
  ]
}
```

So flashing **open → fist → open within 3 s** triggers return-home. `pattern`
entries are gesture names (canned or your own trained labels); `action` is any of
the actions above.

Because a combo's gestures could also fire on their own (open palm = take off),
any gesture used in a pattern has its single-gesture command **held back
`single_delay` seconds** and cancelled if the combo completes. Gestures not in
any pattern are unaffected. Delete the file or set `"sequences": []` to disable
(then there is no delay at all). The HUD shows a partial combo as
`combo: Open_Palm>Closed_Fist>…`.

## Layout

| File | Role |
| --- | --- |
| `src/main.py` | Capture loop, window, keys, `--demo` / `--headless` |
| `src/hand_tracker.py` | MediaPipe GestureRecognizer → `HandState`; custom model overrides canned |
| `src/landmark_features.py` | 21 landmarks → normalized 63-d pose vector (shared by collect/train/infer) |
| `src/gesture_model.py` | Dependency-free k-NN custom gesture classifier (`custom_gestures.npz`) |
| `src/gestures.py` | Debounces the gesture stream → mode, speed, discrete events |
| `src/sequences.py` | Matches ordered gesture combos against the token stream |
| `src/controls.py` | Hand pose + gesture state → `ControlInput`; runs autopilot routines |
| `src/simulator.py` | Arcade quadcopter physics (world frame: x right, y forward, z up) |
| `src/visualizer.py` | Look-at pinhole camera, 3D drone render, control HUD |
| `src/control_types.py` | Shared dataclasses and the `FlightMode` enum |
| `scripts/collect_gestures.py` · `scripts/train_gestures.py` | Record samples · fit the custom model |
| `config/gesture_actions.json` · `config/gesture_sequences.json` | Single-gesture → action map · ordered combos |

The simulator takes a normalized `ControlInput` (throttle, yaw_rate, roll,
pitch, armed), so swapping the simulator for a real link (Tello, MAVLink RC
override, serial) is a matter of writing one adapter that consumes the same
struct.

---

# Operator input → mission runtime

Start the local chat server and mission runtime in separate terminals, then translate and submit a natural-language instruction:

```sh
./scripts/start_chat_server.sh
.venv/bin/uvicorn server.main:app --reload
.venv/bin/python -m integrations.llm_mission \
  "send two drones to search sector alpha" --submit
```

The LLM output is treated as untrusted: it must be one JSON object, pass the canonical `simulation.models.MissionCommand` schema, and satisfy target semantics before submission. Invalid output is printed as a rejection and is never sent.

The gesture app can submit deliberate discrete gesture events to the same
endpoint while keeping the webcam and recognizer visible. In runtime-connected
mode it replaces the local quad view with connection/submission status and does
not advance the standalone simulator:

```sh
.venv/bin/python src/main.py --mission-url http://127.0.0.1:8000
```

`Closed_Fist`/`land` submits HOLD and `ILoveYou`/`return_home` submits RETURN.
Analog hand axes are not submitted to the runtime; they control motion only in
standalone mode. Custom entries in `config/gesture_actions.json` may map to
values such as `mission:HOLD`, `mission:RETURN`, or another mission type;
target-requiring types are submitted only when their adapter context supplies
the required point, region, waypoints, or entity.

---

# Cesium 3D mapping

The Cesium frontend is a separate, browser-local geospatial drone simulator.
Run it beside the distributed mission runtime on an explicit, non-conflicting
port:

```sh
# Terminal 1: distributed runtime + lightweight console
.venv/bin/uvicorn server.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2: Cesium mapping simulator
npm --prefix 3d-mapping install
npm --prefix 3d-mapping run dev
```

- Runtime console and API: <http://127.0.0.1:8000>
- Cesium mapping simulator: <http://127.0.0.1:5173>

The Cesium mission JSON (`goto`, `hover`, `orbit`, `return_home`) executes only
inside that browser app. It is intentionally distinct from the canonical
distributed-runtime `simulation.models.MissionCommand`; the two UIs do not
claim to show the same authoritative drone state.

See [`3d-mapping/README.md`](3d-mapping/README.md) for Cesium token setup,
controls, and its local mission format.

---

# Distributed mission runtime

This repository includes a deterministic, multi-node autonomous aerial mission simulator. It separates simulator-owned ground truth from each drone's local estimate and routes all peer knowledge through a lossy, delayed network model. It is a software simulation and has not been validated for real-world or safety-critical deployment.

## Setup and run

Python 3.11 or newer is required.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
pytest
uvicorn server.main:app --reload
```

Open <http://127.0.0.1:8000> for the lightweight control console. API documentation is at <http://127.0.0.1:8000/docs>.

Run the accelerated end-to-end scenario with:

```sh
./scripts/demo
```

Add `--realtime` to play it at wall-clock speed. The scenario allocates WATCH and SEARCH tasks across four drones, fails Drone 2 at 10 seconds, waits for heartbeat timeout and reassignment, removes GPS from Drone 3, and raises network packet loss.

If `python3 -m simulation.demo` reports that `pydantic` is missing, it is using the system Python instead of the project environment. Either activate the environment with `source .venv/bin/activate` first, or invoke it directly:

```sh
.venv/bin/python -m simulation.demo
```

## Integration contracts

Mission-producing systems (`llms/`, gesture, and UI) submit the typed `MissionCommand` JSON schema to `POST /api/missions`. Environment code can construct `WorldDefinition` with bounds, regions, obstacles, and moving entities. Mobile code can emit the provided `OperatorPositionUpdate` schema. The frontend consumes `SimulationSnapshot`, `SimulationEvent`, `DronePublicState`, `MissionTask`, and `LinkState` from `GET /api/state` or `/ws`.

Important endpoints:

- `GET /api/state`, `/api/drones`, `/api/missions`, `/api/events`
- `POST /api/missions`
- `POST /api/simulation/pause`, `/resume`, `/reset`
- `POST /api/simulation/scenario/{NORMAL|DEGRADED|CONTESTED|CHAOS}`
- `POST /api/interference`, `/api/events/inject`
- `POST /api/drones/{id}/fail`, `/api/drones/{id}/recover`
- WebSocket `/ws` sends JSON state snapshots at the configured publish rate.

The in-process `DroneNode` intentionally has no reference to `World` or `SimulationEngine`. Its inputs are measurements, delivered messages, and typed task assignments; its outputs are messages and `MotionIntent`. Those ports are defined as protocols in `simulation/interfaces.py` so a later process or laptop transport can implement the same boundary.
