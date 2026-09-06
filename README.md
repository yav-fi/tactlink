# TactLink

**Mission:** Give American teams a faster, safer way to conduct reconnaissance
and protect the people beside them. TactLink turns natural finger gestures into
coordinated drone commands, so operators can keep their eyes on the mission and
their hands free from conventional flight controls.

When there is no time to command a drone stick by stick, point, signal, and let
TactLink translate intent into action.

Clone:

```sh
git clone https://github.com/yav-fi/dnhacks26.git
```

Yavin added `AGENTS.md` and `CLAUDE.md` to keep coding-agent instructions
consistent across tools.

TactLink brings together six hackathon workstreams:

- **[iOS UWB group positioning](ios/README.md)** — native iPhone room joining, rotating UWB pairs, relative maps, and profiling (`ios/`).
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
- **[Phones as live operators](#phones-on-the-ground--live-operators-in-the-runtime)** —
  iPhones appear as people on the map and the nearest one commands each drone
  (`simulation/operators.py`, `server/phone_listener.py`).

## Close-up phone demo

Open `./start` at `?mode=local`. The phone scene uses real metre offsets: a 1 m grid, 1.75 m human figures, and a drone scaled to a 0.8 m spinning-rotor envelope. The camera frames the group and drone at a close distance for 1–10 m tests. Use **Frame group** to restore that view after dragging; +/− changes zoom. Drone models are not enlarged to a minimum screen size in this mode.

The drone begins hovering 3.5 m above the scene ground. Movement stays between 2.5 m and 8 m above the highest positioned person's base; thumbs-down descends to that floor. Pointing with one index finger orbits at a target radius of 2 m. The visible **Gestures → Drone** guide lists exact finger poses, speeds, 0.4 s hold behavior, and which actions repeat. The gold person is the current controller. Hold a closed fist for 0.4 s on either phone to fly above and follow that person’s mapped position, even if another person is closer. Follow stays on after lowering the hand; another fresh held fist switches the target. Hold an open palm on either phone to cancel follow. Ordinary movement still stops when the pose is released. Follow tracks the relative UWB map, so the anchor must remain stationary.

These are chosen demo model dimensions, not measurements of a particular person or drone. UWB relative positions retain their measured metre separation; the two-phone axis and three-phone flat assumptions still apply. Five-phone shape height is relative group geometry, not measured gravity altitude. The legacy runtime controls documented below are separate from this browser-owned phone demo.

## One-command demo

Run this from the repository root:

```sh
./start
```

It installs missing dependencies, starts the phone UDP receiver and serves the detailed
3D flight simulator. The default is **one simulated drone controlled by the phones**;
the Mac camera and the separate distributed-drone simulation are not used.

1. Put the Mac and phones on the same trusted Wi-Fi. Keep Wi-Fi and Bluetooth on.
2. Install the current SignalMap build on every phone. Set **Visualizer host** to
   the address printed by `./start` (Mac Wi-Fi IP, port 9870).
3. Create one room and join it from the others. Five-phone mode remains the default.
   For two phones, enable **2-phone gesture test**: it uses real UWB distance but assumes
   a fixed vertical map line (X = Z = 0). For three, enable **3-phone flat test** (Z = 0).
   Keep exactly the selected number of phones in the room, all on the current build.
4. Keep the app open and accept Local Network, Nearby Interaction and Camera permissions.
   The console distinguishes phones connected from phones with a completed UWB position.
5. Once positions appear, the nearest phone controls the single drone. Hold gestures
   for 0.4 s: thumb up climbs, thumb down descends, open palm stops, pointing up orbits
   the operator, three fingers move forward, directional dashes move left/right,
   closed fist turns 90°, and ILoveYou returns toward the starting point. Release
   the gesture to stop motion. Loss of the controlling phone also stops the drone.

The **Group alignment** controls choose a stationary anchor, rotation and mirror.
UWB preserves relative metre offsets, not global location or compass alignment.
Keep the anchor still. Two-phone mode assumes direction and measures only separation; three-phone mode is flat; five-phone mode preserves relative
XYZ, whose third axis is not gravity height. Camera/compass processing stays on
phones; frames are never uploaded. Phone motion is 2 m/s, descent 1 m/s, and climb
is capped at 10 m above the starting point for this close-range demo.

`./start --no-open` starts without opening a browser. Open
[the phone demo](http://127.0.0.1:5173/?mode=local). Ctrl+C stops services started by
the launcher. It refuses to reuse an old server without the active LAN phone receiver.

Automatic app setup from another terminal:

```sh
./ios/scripts/dev ship
./ios/scripts/dev watch --room YOUR_ROOM_CODE --bridge MAC_WIFI_IP:9870
```

With no phones, `.venv/bin/python scripts/mock_phone_feed.py --phones 5 --still --script`
sends synthetic phone input (use `--phones 3` for the flat test). The console labels
the mock room; stop the mock feed and select the real room before a hardware test.

The former browser-camera sandbox remains available at `?mode=local&input=camera`.
The separate distributed runtime remains an advanced path started directly with
`PHONE_DEMO=0 .venv/bin/uvicorn server.main:app`; its console is `?mode=runtime`.
It is not the phone demo. Optional AI model setup is separate from `./start`.

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
python src/main.py                    # live webcam, 5-drone squad (default)
python src/main.py --drones 1         # single drone
python src/main.py --camera 1         # a different camera
python src/main.py --demo             # no camera: scripted flight, good for a first check
python src/main.py --check-gestures   # live webcam, recognition + HUD only - nothing moves
python src/main.py --demo --headless --out flight.png   # render one sample frame
```

On first live run the MediaPipe model (`gesture_recognizer.task`, ~8 MB) is
downloaded into `models/` automatically. `--demo` needs no camera and no model.

Keys while running: `q` quit · `r` reset · `space` take off / land · `1`-`9`
highlight a drone.

## Swarm

`--drones N` flies a squad. Drone 0 is the **leader** — every gesture drives it
exactly like the single-drone app — and the rest hold a fixed ring formation
around it, so the whole squad takes off, climbs, orbits, dashes and returns home
together (all relative to the controlling operator). The HUD shows
`ARMED xN` and `sel #k`; `1`-`9` highlight a drone (per-drone "single-out"
commanding is the next step — the hook is there, `Swarm.selected`).

## Operators

The picture: **N people, each with a chest-mounted phone** running the gesture
model, all in the scene; one virtual drone flies **above** them. `--operators N`
(`src/operators.py`) simulates the people — position + facing. They stand still
by default (real phone positions will drive them); `--wander` makes them walk.

- **Control = proximity.** The drone obeys whichever operator it is currently
  **nearest** to (with hysteresis so it doesn't flicker mid-pass). The HUD shows
  `CTRL opN`, and that operator is cyan in the 3D view.
- **Anchored to the controlling operator.** Right after **take off** (or
  **return-home**) the drone trails them ("leashed"); `orbit` circles them;
  `return_home` flies back to them. Operators face **north** by default, so the
  three-finger "forward" dash goes north.
- **A dash leaves the drone where it lands.** `fly_east` / `fly_west` /
  three-finger forward drop the leash — the drone hovers at the new spot instead
  of drifting back. `return_home` re-leashes it.
- **Handoff is emergent.** Dash the drone toward someone else; when it arrives it
  is nearest to them, so they take control. No dedicated handoff gesture.

Each operator has **its own gesture interpreter** (`src/operator_gestures.py`) -
hold timing and combos are per-person - and only the controlling operator's
commands reach the drone. One webcam fills in for whoever currently has control.

**Live phones (`--phone-feed`).** With `python src/main.py --phone-feed 9870`, the
operators are driven by real iPhones running SignalMap's visualizer bridge
(`ios/SignalMap/RoomBridge.swift`): each phone streams its own UWB position and
heading over UDP, and `src/phone_feed.py` turns that into operators that appear
and move at their real spots. Each phone also sends an absolute **magnetic
compass** heading (`HeadingSource.swift`), which the sim prefers for facing
(so "forward" works while standing still); it falls back to the walking-only
motion heading otherwise. UWB *positions* still have no north, so
`--phone-frame-rot DEG` rotates them to line up with the scene; the group is
recentered on its first fix. Each phone's own `gesture` field feeds its
operator's interpreter (the phones send `"None"` until on-device recognition
lands - piece 3); the webcam still covers the active operator, or use
`--no-camera` for a pure ground station. No hardware needed to try it:
`python scripts/mock_phone_feed.py --port 9870 --phones 3` fakes three phones
walking a circle, and `--script` makes phone 1 run a gesture timeline.

**What's left** (on-phone gesture recognition, Mac build steps, field
calibration) is a step-by-step runbook in
[`docs/phone-gestures-REMAINING.md`](docs/phone-gestures-REMAINING.md).

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
| 👍 Thumb up | **Arm + take off** (to ~3 m, above the operators); each one after that **steps the altitude up ~1.5 m** (to 9 m) |
| 👎 Thumb down | **Land + disarm** |
| ✋ Open palm | **Halt** — cancel whatever it's doing and hover right where it is |
| ☝️ Pointing up | Orbit: circle the **controlling operator** at ~6 m, nose kept pointed inward — point again to stop |
| 🤟 ILoveYou | Fly back to the **controlling operator** and hover |
| **Three fingers** (index+middle+ring, pinky curled), held ~0.45 s | Dash ~5 m **forward** — along the bearing the controlling operator faces (`ThreeFingerForward`). Any hand orientation. |
| ✊ Closed fist · ✌️ Victory | *no action* |

Take off, land, orbit, return-home (and the `fly_*` dashes) run as autopilot
routines that take over until they finish (`MODE` turns red in the HUD). Orbit
runs until you point again, or any other command interrupts it.

A gesture the model isn't ~55% sure of is ignored, which cuts flickery misreads.
`Open_Palm` / `Closed_Fist` / `Victory` are set to `null` in
`config/gesture_actions.json` so a misread of those does nothing.

## Fly where you point (two-finger wiper)

Hold your **index + middle fingers** out and swing them **vertical → horizontal
→ vertical → horizontal** within ~4 s. The drone then dashes in the direction the
fingers point on the last horizontal — pointing right → fly east, left → fly
west. This is a built-in geometric detector (`src/finger_swing.py`), no training
and it can't shadow the other gestures; toggle `ENABLED` in that file to turn it
off. The HUD shows the swing so far as `swing V>H>…`.

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
| `src/finger_swing.py` | Geometric two-finger vertical↔horizontal wiper → `fly_*` |
| `src/controls.py` | Hand pose + gesture state → `ControlInput`; runs autopilot routines |
| `src/simulator.py` | Arcade quadcopter physics (world frame: x right, y forward, z up) |
| `src/swarm.py` | Leader + ring-formation followers flown as one squad |
| `src/operators.py` | People (position + facing) giving gestures — simulated or fed from phones; "forward" is operator-relative |
| `src/operator_gestures.py` | One `GestureInterpreter` per operator; returns the controlling operator's state |
| `src/phone_feed.py` | UDP listener for SignalMap `RoomBridge` datagrams → live operators |
| `src/visualizer.py` | Look-at pinhole camera, multi-drone 3D render, control HUD |
| `src/control_types.py` | Shared dataclasses and the `FlightMode` enum |
| `scripts/collect_gestures.py` · `scripts/train_gestures.py` | Record samples · fit the custom model |
| `scripts/mock_phone_feed.py` | Fake N phones walking a circle, for testing `--phone-feed` without hardware |
| `config/gesture_actions.json` · `config/gesture_sequences.json` | Single-gesture → action map · ordered combos |

The simulator takes a normalized `ControlInput` (throttle, yaw_rate, roll,
pitch, armed), so swapping the simulator for a real link (Tello, MAVLink RC
override, serial) is a matter of writing one adapter that consumes the same
struct.

---

# Operator input → mission runtime

Start the local chat server and mission runtime in separate terminals, then translate and submit a natural-language instruction:

```sh
./scripts/start_chat_server.sh --model qwen3.5-9b
SIMULATION_DRONE_COUNT=4 CHAT_SERVER_URL=http://127.0.0.1:8081 \
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

`Thumb_Down`/`land` submits HOLD and `ILoveYou`/`return_home` submits RETURN;
the disabled `Closed_Fist` does not submit a runtime command.
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
# Terminal 1: Qwen 3.5 9B mission intelligence
./scripts/start_chat_server.sh --model qwen3.5-9b

# Terminal 2: four-node distributed runtime + lightweight console
SIMULATION_DRONE_COUNT=4 CHAT_SERVER_URL=http://127.0.0.1:8081 \
  .venv/bin/uvicorn server.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 3: Cesium operator UI
npm --prefix 3d-mapping install
VITE_RUNTIME_URL=http://127.0.0.1:8000 npm --prefix 3d-mapping run dev
```

- Runtime console and API: <http://127.0.0.1:8000>
- Cesium connected mode: <http://127.0.0.1:5173/?mode=runtime>
- Cesium local sandbox: <http://127.0.0.1:5173/?mode=local>

In the local Cesium control, the browser opens its hidden gesture camera,
places the current gesture guess in the compact bottom command dock, and maps a
held closed fist to repeating smooth 90-degree clockwise heading turns. The
original two-finger V-H-V-H pointing motion and three-finger forward control
remain active while held; losing the active pose stops momentary gesture motion.

Connected mode consumes `ws://127.0.0.1:8000/ws`, converts backend local
coordinates as x=east, y=north, z=up metres from the snapshot origin, and shows
truth/estimated positions, uncertainty, plans, missions, links, relay roles,
node details, and topology metrics. Its interference, preset, failure, control,
pause, and reset controls call the FastAPI runtime. Its command workspace can
preview and confirm natural-language plans, apply live amendments, and ask
read-only questions grounded in the current mission snapshot. The default four
nodes include a relay-capable specialist; override the bounded demo fleet with
`SIMULATION_DRONE_COUNT=1..32`. Local mode's mission JSON
(`goto`, `hover`, `orbit`, `return_home`) remains browser-only.

See [`3d-mapping/README.md`](3d-mapping/README.md) for Cesium token setup,
controls, and its local mission format.

---

# Advanced: phones in the separate distributed runtime

For the integrated single-drone phone test, use **One-command demo** above. This
section describes the older distributed mission runtime and its separate task auction.
Do not use its runtime-mode browser URL for the single-drone test.

The same iPhones that range each other over UWB also show up as **people** in
the Cesium console, standing on the lawn beside the drones, and the drone obeys
whichever person is **nearest to it**.

Nothing new is needed on the phone. `RoomBridge` already streams each phone's own
position, compass facing, and locally recognized gesture as a small JSON datagram
(~10 Hz). The runtime now listens for exactly that datagram, so a phone already
in the field only needs its **Visualizer host** pointed at the runtime machine.

```sh
# Terminal 1: runtime. It also opens the phone feed on udp://127.0.0.1:9870.
SIMULATION_DRONE_COUNT=3 .venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8000

# Terminal 2: Cesium console -> http://127.0.0.1:5173/?mode=runtime
VITE_RUNTIME_URL=http://127.0.0.1:8000 npm --prefix 3d-mapping run dev

# No phones handy? Three fake ones walking a scripted gesture timeline:
.venv/bin/python scripts/mock_phone_feed.py --phones 3 --still --script
```

To take real phones, bind the feed to the Wi-Fi the phones are on and set that
host in each app's lobby. The datagrams are **unauthenticated**, so do this only
on a trusted network:

```sh
PHONE_FEED_HOST=0.0.0.0 SIMULATION_DRONE_COUNT=3 \
  .venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000
```

`PHONE_FEED_PORT` moves the port; `0` disables the UDP feed entirely, leaving the
HTTP route below. Anything that can speak HTTP can publish the same object:

```sh
curl -X POST http://127.0.0.1:8000/api/operators \
  -H 'content-type: application/json' \
  -d '{"id":"ian","name":"Ian","pos":[0,0],"compass":0,"compassValid":true,
       "gesture":"Thumb_Up","gestureConfidence":0.9}'
```

## Putting the group on the lawn

UWB ranging fixes the group's **shape** but can never observe its absolute
position or north. So one phone is nominated as the **anchor**: it stands at a
chosen point, and everyone else keeps the offset UWB actually measured, rotated
to line up with north.

```sh
curl -X POST http://127.0.0.1:8000/api/operators/frame \
  -H 'content-type: application/json' \
  -d '{"anchor_id":"ian","anchor_east":0,"anchor_north":-45,"rotation_deg":0}'
```

`anchor_east` / `anchor_north` are metres from the scene origin, which is already
the Washington Monument (38.8895, -77.0353). The default puts the anchor 45 m
south of it, on the open ground in front of the drone staging line.
`rotation_deg` absorbs magnetic declination and any phone mount-angle offset in
one constant — the runtime equivalent of `--phone-frame-rot`. `GET
/api/operators` shows where everyone landed; `GET /api/state` carries them to the
frontend as `operators`.

## Who is flying what

Control is decided **per drone**: each drone listens to the operator nearest to
it, with hysteresis so control does not flicker as a drone passes between two
people. In the console, a person commanding a drone turns amber, gains a control
ring, and draws a dashed line to each drone listening to them; the anchor is
labelled. A phone that goes quiet dims and then disappears, so lost tracking
never leaves a stale commander on the map.

A gesture must be **held ~0.4 s** to fire, fires once, and cannot repeat until the
hand changes — timed per person, so one operator's hold never affects another's.
Only the controlling operator's gestures reach a drone; everyone else's are
recognized, shown, and dropped.

| Gesture | Runtime command |
| --- | --- |
| 👍 Thumb up | `GOTO` above that operator, 25 m |
| 👎 Thumb down | `GOTO` the drone's own x/y at 2 m |
| ✋ Open palm | `HOLD` where it is |
| ☝️ Pointing up | `WATCH` aimed at that operator |
| 🤟 ILoveYou | `GOTO` where that person is standing, 15 m |
| Three fingers / two-finger dashes | `GOTO` 25 m along that operator's facing (±90°) |

Dashes resolve against the operator's **own compass facing**, so "forward" means
forward for the person who signalled it. Gesture missions carry
`metadata.source = "operator-gesture"` with the operator, gesture, and the drone
addressed. They are submitted to the ordinary auction rather than pinned to that
drone: the addressed drone almost always wins, because bids are distance-based
and the target sits next to it, but a better-placed peer may take it.

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
.venv/bin/python -m simulation.benchmark --seed 49281 --ablation --bandwidth 10
.venv/bin/python -m simulation.replay runs/<run-directory>/fabric.jsonl
```

Artifacts are written under `runs/` and intentionally ignored by Git.

Run the mission-intelligence scorecard against the deterministic baseline or
the live Qwen server with:

```sh
.venv/bin/python -m integrations.mission_compiler --baseline evaluate \
  --output docs/evaluations/mission-baseline.json
CHAT_SERVER_URL=http://127.0.0.1:8081 \
  .venv/bin/python -m integrations.mission_compiler evaluate \
  --output docs/evaluations/mission-qwen3.5-9b.json
```

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
