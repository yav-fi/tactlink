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
brew install macmon ProducerGuy/tap/thermalforge
sudo thermalforge install
./scripts/download_model.sh
./scripts/build_dflash_draft.sh
```

## Commands

All five scripts above take `--model qwen3.5-9b|qwen3.8-27b` (default `qwen3.5-9b`);
`start_chat_server.sh`/`ask.sh`/`benchmark.sh` always agree on which is running.

```sh
./scripts/start_chat_server.sh   # spin up the inference server
./scripts/ask.sh "question"      # ask it something (no arg = interactive chat)
./scripts/benchmark.sh           # thermally-gated tok/s benchmark
```

## Performance

| Hardware | Model | Draft Head | Plug-in Algorithms | Decode tok/s |
| --- | --- | --- | --- | --- |
| MacBook Pro, Apple M4, 16GB | [Qwen3.5-9B-4bit](https://huggingface.co/mlx-community/Qwen3.5-9B-4bit) | [DFlash](https://huggingface.co/z-lab/Qwen3.5-9B-DFlash) (w4:gs64) | — (no selector, LibraSpec auto-disabled) | **47.6** ([receipt](docs/Sep5-03-24-40-PM.md)) |
| MacBook Pro, Apple M3 Max, 48GB | [Qwen3.5-9B-4bit](https://huggingface.co/mlx-community/Qwen3.5-9B-4bit) | [DFlash](https://huggingface.co/z-lab/Qwen3.5-9B-DFlash) (w4:gs64) | — (no selector, LibraSpec auto-disabled) | **125.9** |
| MacBook Pro, Apple M3 Max, 48GB | [Qwen3.8-27B-4bit](https://huggingface.co/mlx-community/Qwen3.8-27B-4bit) | [DFlash2](https://huggingface.co/z-lab/Qwen3.8-27B-DFlash2) (w4:gs64) | [LibraSpec](https://arxiv.org/abs/2608.08721) | **72.3** |

Measured with `./scripts/benchmark.sh` using a 512-token generation after an untimed
warmup. Runs are thermally gated, with fans verified at maximum RPM before and after;
the latest local run is also written to the ignored `benchmark_result.json` file.

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
python src/main.py                    # live webcam, camera 0
python src/main.py --camera 1         # a different camera
python src/main.py --demo             # no camera: scripted flight, good for a first check
python src/main.py --check-gestures   # live webcam, recognition + HUD only - the drone
                                      #   does not move and nothing is submitted
python src/main.py --demo --headless --out flight.png   # render one sample frame
```

On first live run the MediaPipe model (`gesture_recognizer.task`, ~8 MB) is
downloaded into `models/` automatically. `--demo` needs no camera and no model.

Keys while running: `q` quit · `r` reset · `space` take off / land.

**Testing gestures safely.** `--check-gestures` is the safe way to rehearse hand
poses and combos: it shows the recognized gesture, hold bar, partial-combo hint,
and a log of what *would* fire, without moving the (simulated) drone or touching
a runtime. For the recognition/timing logic itself, `python tests/test_sequences.py`
runs a deterministic, camera-free check of combos and the single-gesture delay.
When first tuning a combo, map it to a harmless action (e.g. `speed_up`) so a
misfire costs nothing.

## Control model

The drone is flown **entirely by discrete gestures** and the autopilot routines
they trigger. When nothing is running, an armed drone hovers (`MODE HOLD`).

Continuous hand-pose flying (palm position, tilt, pinch → stick inputs, per a
`HEADING` / `POSITION` / `HOVER` mode) is available but **off by default** - set
`HAND_FLIGHT_ENABLED = True` in `src/controls.py` to turn it on. While it is off,
`cycle_mode` (Victory) and `speed_up` / `speed_down` (thumbs) do nothing.

## Gestures (discrete, MediaPipe GestureRecognizer)

Hold a sign steady for ~0.4 s to fire it; relax before repeating.

| Sign | Action |
| --- | --- |
| 👍 Thumb up | **Arm + take off** |
| 👎 Thumb down | **Land + disarm** |
| ☝️ Pointing up | Orbit: fly out to a 10 m radius and circle the origin — point again to stop |
| 🤟 ILoveYou | Return to the start point and hover |
| ✋ Open palm · ✊ Closed fist · ✌️ Victory | *no standalone action — combo ingredients only* |

Take off, land, orbit, return-home (and the `fly_*` dashes) run as autopilot
routines that take over until they finish (`MODE` turns red in the HUD). Orbit
runs until you point again, or any other command interrupts it.

A gesture the model isn't ~55% sure of is ignored, which cuts flickery misreads.
`Open_Palm` / `Closed_Fist` / `Victory` are set to `null` in
`config/gesture_actions.json` so a misread of those does nothing on its own; they
still work as combo steps.

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

If a custom label shadows a built-in gesture it shouldn't (e.g. a two-finger
pose overriding one-finger `Pointing_Up`), record a class named **`other`**
holding every pose that is *not* a custom gesture (thumbs, one-finger point,
fist, relaxed hand) and retrain. When the model lands on `other` the tracker
keeps the MediaPipe gesture instead. (`none` / `neutral` / `background` /
`ignore` work as label names too.)

Actions available: `takeoff`, `land`, `cycle_mode`, `speed_up`, `speed_down`,
`spin360`, `return_home`, `estop`, `orbit` (fly to a 10 m radius then circle the
origin; fire again to stop), `fly_north` / `fly_south` / `fly_east` / `fly_west`
(dash ~5 m that compass way then hover), and `fly_pointed` (combos only - dash
toward wherever the fingers point when the combo completes; a clear left/right
becomes west/east, anything else falls back to north). Feature layout is
versioned (`landmark_features.FEATURE_VERSION`) - bump it and retrain if you
change it.
`models/custom_gestures.npz` is the one model file that *is* committed, so a
trained set of gestures travels with the repo; raw recordings under `data/` stay
local.

## Gesture combos (sequences)

**Combos are off right now** (`"sequences": []` in
`config/gesture_sequences.json`). The machinery stays: several gestures in order,
inside a time window, fire one command - like a gesture password.

```json
{
  "single_delay": 0.6,
  "sequences": [
    { "pattern": ["Victory", "v_flat", "Victory", "v_flat"], "window": 5.0, "action": "fly_pointed" }
  ]
}
```

`pattern` entries are gesture names (canned or your own trained labels); `action`
is any of the actions above plus `fly_pointed` (dash toward where the fingers
point when the combo completes). The example above is the planned **"fly where I
point"** wiper - a spread ✌️ V rotated up → flat → up → flat - and needs a
trained `v_flat` label (`python scripts/collect_gestures.py --label v_flat`)
before it can fire.

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

Cesium has two explicit modes. **Backend runtime** renders the authoritative
Python `SimulationSnapshot` and never advances drone physics in the browser.
**Local sandbox** preserves the original browser-only fleet and mission tools.
Run the UI beside the runtime on a non-conflicting port:

```sh
# Terminal 1: distributed runtime + lightweight console
.venv/bin/uvicorn server.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2: Cesium operator UI
npm --prefix 3d-mapping install
npm --prefix 3d-mapping run dev
```

- Runtime console and API: <http://127.0.0.1:8000>
- Cesium connected mode: <http://127.0.0.1:5173/?mode=runtime>
- Cesium local sandbox: <http://127.0.0.1:5173/?mode=local>

Connected mode consumes `ws://127.0.0.1:8000/ws`, converts backend local
coordinates as x=east, y=north, z=up metres from the snapshot origin, and shows
truth/estimated positions, uncertainty, plans, missions, links, relay roles,
node details, and topology metrics. Its interference, preset, failure, control,
pause, and reset controls call the FastAPI runtime. Local mode's mission JSON
(`goto`, `hover`, `orbit`, `return_home`) remains browser-only.

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

Run the accelerated end-to-end "killer" scenario with:

```sh
./scripts/demo
```

Add `--realtime` to play it at wall-clock speed. With seed `49281`, nodes
auction WATCH and SEARCH tasks, network interference degrades the physical
mesh, simulated mission control goes offline, an executing drone fails, peers
detect it through missing heartbeats and re-auction its work, a relay-capable
drone physically moves toward a topology-repair point, and GPS is removed from
one survivor. The final summary distinguishes the simulator host from the
simulated control authority.

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
- `POST /api/drones/fail-random`
- `POST /api/control/fail`, `/api/control/recover`
- WebSocket `/ws` sends JSON state snapshots at the configured publish rate.

The `NetworkSimulator` alone receives a read-only ground-truth position
provider so distance and obstacle line of sight affect real packet delivery,
latency, and loss. `DroneNode` still has no `World` or `SimulationEngine`
reference. Nodes replicate mission announcements, exchange explainable bids,
deterministically select winners (cost then node ID), gossip awards and
completion, and re-auction after local heartbeat timeouts. `MissionManager`
accepts commands and maintains an operator projection; it no longer assigns
tasks. The Python host remains a centralized simulation clock/world and is not
claimed to be a physically distributed simulator.

## World knowledge and mission effectiveness

Every `DroneNode` now owns a separate `WorldBelief`. The simulator generates
typed observations only when a capable node is physically within sensor range;
the observation enters that node's belief first and reaches peers only through
normal delayed, lossy `WORLD_UPDATE` messages. Periodic anti-entropy converges
newer/high-confidence cell and entity records after partitions heal. The
operator snapshot exposes both the aggregate view and per-node known-cell
counts without making simulator truth available to autonomy.

SEARCH regions are deterministic circular grids. Their progress and
effectiveness come from observed cells rather than planned-waypoint completion:
45% coverage, 35% fresh coverage, and 20% mean confidence. WATCH uses 75%
freshness and 25% confidence from the most recent region observation; FOLLOW
uses entity freshness times confidence; RELAY uses measured network health;
other motion tasks use actual task progress. Freshness decays by half every
`sensing.freshness_half_life_seconds`. Priority-weighted effectiveness is
reported separately from assignment/resource capability.

Fabric awards carry deterministic task leases (`owner`, `lease_id`, expiry,
revision). Reachable owners renew them, expired owners stop, and equal-revision
partition conflicts resolve by later expiry and then node ID. The local policy
layer holds motion when position uncertainty is excessive or battery reserve is
required. `NodeIdentity.resources` provides the first generic mobility,
compute, sensing, communications, power, relay, and storage abstraction while
preserving the legacy capability set.

## Record, replay, and compare

The integrated 55-second demo writes a JSONL timeline containing the seed,
configuration, commands, disturbances, events, state, network history,
coverage, and effectiveness:

```sh
.venv/bin/python -m simulation.demo --seed 49281
.venv/bin/python -m simulation.replay runs/demo-49281.jsonl
```

Run the same initial state and disturbance schedule in the reasonable
centralized baseline and resilient fabric modes. The table and JSON output are
computed from the two simulations; no result is hardcoded:

```sh
.venv/bin/python -m simulation.benchmark --seed 49281
```

Artifacts are written under `runs/` and intentionally ignored by Git.

## External DroneNode worker

An autonomy core can run in another local process or on a LAN host over a
reliable WebSocket transport. The worker receives only measurements, locally
generated sensor observations, messages that survived the simulated network,
mission data carried in those messages, and simulation timing. It returns
`MotionIntent`, outbound messages, timeouts, and public node state; world truth
and physics remain in the host.

```sh
# Process / laptop 1
.venv/bin/python -m simulation.worker --node-id drone-3 --host 0.0.0.0 --port 8765

# Before the first simulation tick, attach through Python:
engine.attach_external_worker("drone-3", "ws://127.0.0.1:8765")

# Or against a paused/not-yet-ticked FastAPI engine:
curl -X POST 'http://127.0.0.1:8000/api/workers/drone-3/connect?uri=ws://127.0.0.1:8765'
```

In-process autonomy remains the default. A worker disconnect causes a HOLD and
a `WORKER_DISCONNECTED` event. Set `SimulationConfig.security.enabled=True` to
derive local seeded Ed25519 development identities, sign node messages, and
reject unknown, missing, or invalid node signatures. Security is disabled by
default and is simulation authentication, not a production PKI.

Connected Cesium mode renders unknown/fresh/stale coverage cells, coverage and
confidence metrics, node-local belief counts, objective effectiveness, a
filtered mission timeline, auction/lease events, and a compact network-health
history in addition to the existing truth/debug, localization, route, link,
relay, and task layers.
