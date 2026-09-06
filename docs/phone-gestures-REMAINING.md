# Phone-controlled drone: what's left to do

**Audience:** a teammate (or their coding agent) picking up the iPhone ↔ simulator
integration. Every step below is a concrete command or file edit with an
acceptance check. Work top to bottom; tasks 1–2 are verification, task 3 is the
real remaining work, tasks 4–5 are tuning.

---

## Where things stand

**The goal:** N chest-mounted iPhones each recognise their wearer's hand
gestures locally. All wearers ("operators") appear in the Python visualizer at
their real UWB positions and facings. One virtual drone flies above them and
obeys **whichever operator it is nearest to**; "forward" is that operator's
facing; handoff is emergent (dash the drone to someone → they're nearest → they
have control).

**Done and on `main`:**

| Area | State |
| --- | --- |
| Single-drone + swarm gesture sim, operator model, proximity control, leash/handoff | done (pre-existing) |
| `ios/SignalMap/RoomBridge.swift` — each phone streams its own `pos` + `heading` + `compass` over UDP | done, **not yet built on a Mac** |
| `ios/SignalMap/HeadingSource.swift` — absolute magnetic compass bearing | done, **not yet built on a Mac** |
| `src/phone_feed.py` — UDP ingest → live operators, staleness, frame rotation, compass preference | done, tested |
| `src/operator_gestures.py` — one `GestureInterpreter` per operator; only the controller's events reach the drone | done, tested |
| `src/main.py` flags `--phone-feed`, `--phone-frame-rot`, `--no-camera` | done, tested |
| `scripts/mock_phone_feed.py` — fake phones (`--script`, `--still`, `--no-compass`) | done, tested |

**Not done:** the phones do not recognise gestures yet. `RoomBridge` always
sends `"gesture": "None"`. Task 3 fills that in. Everything downstream of the
`gesture` field already works end to end (proven with `mock_phone_feed.py
--script`).

---

## Repo map

```
src/phone_feed.py          UDP listener; PhoneReport; parse_datagram; PhoneReport.sim_heading()
src/operators.py           OperatorPool.sync_from_reports() builds operators from PhoneReports
src/operator_gestures.py   OperatorGestures: per-operator GestureInterpreter, returns controller's state
src/main.py                run loop; --phone-feed / --phone-frame-rot / --no-camera
scripts/mock_phone_feed.py fake phones for testing without hardware
tests/test_phone_feed.py         13 tests
tests/test_operator_gestures.py  7 tests

ios/SignalMap/RoomBridge.swift     UDP sender (one-way). struct Sample = the wire format.
ios/SignalMap/HeadingSource.swift  CMMotionManager magnetic bearing. bearing(north:west:) is pure + testable.
ios/SignalMap/RoomSession.swift    owns `bridge` and `compass`; starts/stops them; sends from tick()
ios/SignalMap/RoomView.swift       "Visualizer host" field in the lobby
ios/scripts/test-protocol.sh       compiles RoomSession + deps with swiftc, runs the protocol test
ios/scripts/dev                    watch / ship / status — fast deploy to paired iPhones
```

### The wire format (UDP JSON, one datagram per phone ~10 Hz)

```json
{
  "v": 1, "id": "<phone id>", "name": "Ian", "room": "<fingerprint>",
  "t": 1234.5, "cycle": 7,
  "pos": [x, y], "z": 0.0,
  "heading": 1.57, "moving": true, "speed": 0.4, "headingReady": true,
  "compass": 270.0, "compassValid": true,
  "gesture": "None", "flat": true
}
```

- `pos` — metres, arbitrary UWB frame (no north).
- `heading` — radians, motion-derived, only valid while walking.
- `compass` — degrees clockwise from magnetic north; the sim prefers this for facing.
- `gesture` — **the field task 3 makes real.** One of: `Closed_Fist`, `Open_Palm`,
  `Pointing_Up`, `Thumb_Up`, `Thumb_Down`, `Victory`, `ILoveYou`, or `None`.
- Add an optional `"gestureSource"` (`"canned"` / `"custom"`) if you want; the
  parser already reads it.

Gesture → drone action (from `config/gesture_actions.json`):
`Thumb_Up`=take off / step altitude, `Thumb_Down`=land, `Open_Palm`=halt,
`Pointing_Up`=orbit the operator, `ILoveYou`=return to the operator.

---

## TASK 1 — Verify the Python side (no Mac, no phones)

```bash
git checkout main && git pull

# unit tests (standalone; pytest optional)
python tests/test_phone_feed.py
python tests/test_operator_gestures.py
python tests/test_operators.py
```
**Accept:** `13/13`, `7/7`, `6/6` passed.

```bash
# end to end with fake phones — terminal 1:
python scripts/mock_phone_feed.py --port 9870 --phones 3 --script

# terminal 2:
python src/main.py --phone-feed 9870 --no-camera --drones 3 --check-gestures --headless --duration 25
```
**Accept:** terminal 2 prints `[ ... ] takeoff`, `orbit`, `halt`, `land` lines —
phone 1's scripted gestures driving drone events. (With a display, drop
`--headless` to watch the drone respond.)

---

## TASK 2 — Verify the iOS side compiles and deploys (Mac)

Prereqs: macOS, Xcode 26.3 at `/Applications/Xcode 26.3.app`, the three test
iPhones paired and trusted, Developer Mode on.

```bash
cd ios

# protocol test — compiles RoomSession.swift + RoomBridge.swift + HeadingSource.swift
DEVELOPER_DIR='/Applications/Xcode 26.3.app/Contents/Developer' sh scripts/test-protocol.sh
DEVELOPER_DIR='/Applications/Xcode 26.3.app/Contents/Developer' sh scripts/test-room.sh
```
**Accept:** both print `PASS` lines and exit 0. If `RoomBridge.swift` or
`HeadingSource.swift` fails to compile, fix it here — this is the first real
build of that code.

```bash
# deploy the current build to every paired iPhone
./scripts/dev watch &          # leave running
./scripts/dev ship             # build + sign + publish; the watcher installs
./scripts/dev status           # shows install receipts
```
**Accept:** all three phones show the new build. First run per phone: accept the
Local Network prompt; if a motion permission prompt appears, add
`NSMotionUsageDescription` to `ios/SignalMap/Info.plist` and re-ship.

### Confirm the compass conventions (one phone, 2 minutes)

`HeadingSource.bearing()` assumes (a) `CMAttitude.rotationMatrix` column 3 is the
device **+Z** axis in the reference frame, and (b) the wearer faces along device
+Z (screen-out, worn upright). Verify:

1. Run the app, set **Visualizer host** = your laptop IP (see Task 4).
2. On the laptop: `python src/main.py --phone-feed 9870 --no-camera --operators 1`.
3. Face **magnetic north** with the phone on your chest. The operator arrow in
   the visualizer should point **+y (up the screen)**.
4. Turn 90° right (east). The arrow should swing to **+x (right)**.

If the arrow is **mirrored** (turns the wrong way): flip the sign in
`HeadingSource.bearing` — `atan2(-west, north)` → `atan2(west, north)`, and
update the sign in the doc-comment.
If it's **180° off**: negate both components, or add `180` before the `% 360`.
If it's off by a **constant** (mount tilt): leave the Swift alone, use
`--phone-frame-rot DEG` on the laptop (one value for all phones).

---

## TASK 3 — On-phone gesture recognition (Mac) — the remaining work

Add a front-camera hand-gesture recognizer to each phone and put the label in
`RoomBridge`'s `gesture` field.

**Chosen engine: MediaPipe Tasks for iOS** (`GestureRecognizer`) — same model and
same 7 gestures as the Python side, so all five phones and the laptop agree.

> **Decision point — build system.** MediaPipe iOS ships via **CocoaPods**, which
> means a `Podfile`, `pod install`, and building the `.xcworkspace` instead of
> the `.xcodeproj`. That also means updating `ios/scripts/dev_deploy.py` and
> `ios/scripts/test-protocol.sh` to use `-workspace SignalMap.xcworkspace
> -scheme SignalMap` instead of `-project`. If that friction is too much for the
> timeline, the **fallback is Apple's Vision framework** (`VNDetectHumanHand
> PoseRequest`) — zero dependencies, no build-system change — porting the finger
> logic from `src/hand_tracker.py::_finger_states` and `src/finger_swing.py`.
> Ask the human which path before starting. Steps below are for MediaPipe.

### 3a. Add the dependency

```bash
cd ios
sudo gem install cocoapods                 # if not installed

cat > Podfile <<'EOF'
platform :ios, '17.0'
target 'SignalMap' do
  use_frameworks!
  pod 'MediaPipeTasksVision'
end
EOF

pod install
# from now on open/build SignalMap.xcworkspace, NOT SignalMap.xcodeproj
```

Update the build scripts to use the workspace:

```bash
# in ios/scripts/dev_deploy.py: change the xcodebuild invocation from
#   -project SignalMap.xcodeproj -target SignalMap
# to
#   -workspace SignalMap.xcworkspace -scheme SignalMap
```

`test-protocol.sh` / `test-room.sh` compile individual files with `swiftc` and do
**not** touch MediaPipe — leave them as they are (they still validate the room
protocol). Add `git add ios/Podfile ios/Podfile.lock` and commit
`ios/Pods/` per your team's convention (usually gitignored + `pod install` in
CI/onboarding).

### 3b. Bundle the model

```bash
cd ios
curl -L -o SignalMap/gesture_recognizer.task \
  'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task'
# Add it to the SignalMap target: Xcode → drag into the project → check "Copy
# items if needed" and target membership "SignalMap".
```

### 3c. Info.plist

Add to `ios/SignalMap/Info.plist`:

```xml
<key>NSCameraUsageDescription</key>
<string>Signal Map reads your hand gestures on this phone to command the shared drone. Video never leaves the device.</string>
```

### 3d. New file: `ios/SignalMap/GestureCamera.swift`

Responsibilities (≈150–200 lines):

- `AVCaptureSession` with `.front` camera, `AVCaptureVideoDataOutput`, a serial
  queue, `sessionPreset = .medium` (640×480 is plenty).
- In `captureOutput(_:didOutput:from:)`: wrap the `CMSampleBuffer` in an
  `MPImage`, call `recognizer.recognizeAsync(image:timestampInMilliseconds:)`
  (LIVE_STREAM mode) with a monotonically increasing millisecond timestamp.
- `GestureRecognizerLiveStreamDelegate` → on result, take
  `result.gestures.first?.first` (top category); keep it only if
  `categoryName != "None"` and `score >= 0.55` (mirror `_GESTURE_MIN_SCORE` in
  `src/hand_tracker.py`); expose it as `@Published var gesture: String` (default
  `"None"`).
- `GestureRecognizerOptions`: `baseOptions.modelAssetPath =
  Bundle.main.path(forResource: "gesture_recognizer", ofType: "task")`,
  `runningMode = .liveStream`, `.numHands = 1`, `.minHandDetectionConfidence =
  0.5`.
- `start()` / `stop()` mirroring `HeadingSource`.
- Guard the whole file with `#if !ROOM_PROTOCOL_TEST` like `HeadingSource.swift`
  so `test-protocol.sh` still compiles (it will not have the MediaPipe module).
  Provide stub `start()/stop()` and `gesture = "None"` under `#else`.

### 3e. Wire it in `ios/SignalMap/RoomSession.swift`

Mirror exactly how `compass` (`HeadingSource`) is already wired:

```swift
let gestureCamera = GestureCamera()          // near `let compass = HeadingSource()`

// in join()  (after `if !simBridge.isEmpty { compass.start() }`)
if !simBridge.isEmpty { gestureCamera.start() }

// in leave() (after `compass.stop()`)
gestureCamera.stop()

// in background() (after `compass.stop()`)
gestureCamera.stop()

// in foreground() (after `if !simBridge.isEmpty { compass.start() }`)
if !simBridge.isEmpty { gestureCamera.start() }

// in tick(), the bridge.send(...) call — add the gesture argument
bridge.send(id: localID, name: displayName, room: transport.room, cycle: cycle,
            position: mine, heading: motionHeading,
            compassDegrees: compass.compassDegrees,
            gesture: gestureCamera.gesture,            // <-- new
            flat: threePhoneMode)
```

### 3f. `ios/SignalMap/RoomBridge.swift`

Change `send(...)` to take `gesture: String` and put it in `Sample` instead of
the hard-coded `"None"`:

```swift
func send(id: String, name: String, room: String, cycle: Int,
          position: Vector3, heading: RelativeMotionHeading,
          compassDegrees: Double?, gesture: String, flat: Bool) {
    ...
    let sample = Sample(..., compass: ..., compassValid: ...,
                        gesture: gesture.isEmpty ? "None" : gesture, flat: flat)
    ...
}
```

Update the doc-comment's `"gesture":"None"` note.

### 3g. Register `GestureCamera.swift` in the project

Add it to `ios/SignalMap.xcodeproj/project.pbxproj` in all four places
(`PBXBuildFile`, `PBXFileReference`, the `SignalMap` `PBXGroup` children, the
`Sources` `PBXSourcesBuildPhase` files) — copy the pattern used for
`HeadingSource.swift` (grep the file for `B1D6E0A72F00A102` to see all four).
Also add `SignalMap/GestureCamera.swift` to the `swiftc` file list in
`ios/scripts/test-protocol.sh`.

### 3h. Optional: a preview in `RoomView.swift`

In `sessionContent`, show the live label so each wearer can see what their phone
sees: `Text("Gesture: \(room.gestureCamera.gesture)")`. A camera preview layer
is nice-to-have, not required.

### 3i. Verify

```bash
cd ios
DEVELOPER_DIR='/Applications/Xcode 26.3.app/Contents/Developer' sh scripts/test-protocol.sh   # still passes (MediaPipe #if'd out)
./scripts/dev ship
```
On the laptop:
```bash
python src/main.py --phone-feed 9870 --no-camera --operators 1
```
Hold 👍 in front of the chest phone. **Accept:** the drone arms and takes off;
`MODE` and the event line in the HUD change. Try 👎 (land), ☝️ (orbit), ✋ (halt).

---

## TASK 4 — Field calibration (Mac + phones + laptop, same Wi-Fi)

1. **Laptop IP:** `ipconfig getifaddr en0` (Mac) / `ipconfig` (Windows). All
   devices on the same Wi-Fi, no VPN.
2. **Start the sim:**
   ```bash
   python src/main.py --phone-feed 9870 --no-camera --drones 1 --phone-frame-rot 0
   ```
3. **Each phone:** open SignalMap → **Visualizer host** = `LAPTOP_IP:9870` →
   create/join the room (use the 3-phone flat test if you have 3). Or pass
   `--sim-bridge LAPTOP_IP:9870` via `./scripts/dev watch --room CODE`.
4. Wait for the SignalMap map to resolve (needs one full range cycle). Operators
   then appear in the visualizer.
5. **Rotate the position frame:** the operators' *relative* layout is right but
   the whole group may be rotated. Bump `--phone-frame-rot` (degrees) until the
   layout matches reality. Compass facings are already absolute and unaffected.
6. **Tune** in `src/`:
   - `src/operators.py` `_AREA`, and the proximity `hysteresis` in
     `set_active_by_proximity` (default `0.72`) — lower = stickier control.
   - `src/controls.py` `_DASH_DISTANCE`, `_ORBIT_RADIUS`, `_HOME_RADIUS`,
     `_TAKEOFF_ALT` — scale these to your real space (metres).
   - `src/phone_feed.py` `PhoneFeed(stale_sec=2.0)` — raise if the UWB map
     updates slowly and operators flicker out.

**Accept:** all wearers visible at their real spots facing the right way; walking
moves their markers; the drone follows the nearest wearer; a 👍 from the nearest
wearer arms it; dashing it toward another wearer hands them control.

---

## TASK 5 — Optional polish

| Item | Where |
| --- | --- |
| Per-drone "single out" — currently only the cyan highlight works, not individual commands | `src/swarm.py`, `src/main.py` keys `1`–`9` |
| Auto-solve the UWB→north rotation from multi-phone compass + geometry (kills `--phone-frame-rot`) | new, in `src/phone_feed.py` or `src/operators.py` |
| Per-phone mount-angle offset (if phones sit at different angles) | add `offsetDeg` to the datagram, subtract in `PhoneReport.sim_heading` |
| Custom gestures on phones (your `models/custom_gestures.npz` k-NN) — retrain as a MediaPipe model, or port the k-NN to Swift | `scripts/train_gestures.py`, new Swift |
| Real drone link (Tello / MAVLink) instead of the sim | new adapter consuming `ControlInput` |

---

## Full demo acceptance checklist

- [ ] `python tests/test_phone_feed.py && python tests/test_operator_gestures.py` — green
- [ ] `sh ios/scripts/test-protocol.sh` — green on the Mac
- [ ] Same build on all phones (`./scripts/dev status`)
- [ ] All phones point at `LAPTOP_IP:9870`; operators appear in the visualizer
- [ ] Operator facings track where each wearer actually faces (compass)
- [ ] `--phone-frame-rot` set so positions match the real room
- [ ] Nearest wearer's 👍 arms + launches the drone
- [ ] 👎 lands, ☝️ orbits that wearer, ✋ halts, 🤟 returns to that wearer
- [ ] Dash the drone toward another wearer → control passes to them
- [ ] Non-nearest wearers' gestures are ignored

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| No operators in the visualizer | phone not sending: check same Wi-Fi, `Visualizer host` set, SignalMap map resolved. `python -c "import socket;s=socket.socket(2,2);s.bind(('',9870));print(s.recvfrom(2048))"` should print a datagram. |
| Operators appear then vanish | UWB map updates slower than `stale_sec` (2 s). Raise `PhoneFeed(stale_sec=...)` in `src/main.py`. |
| Whole group rotated | `--phone-frame-rot DEG`. |
| One operator's facing mirrored/backwards | compass convention — see Task 2. |
| Gestures do nothing | `--no-camera` given? nearest operator is the one gesturing? hold the pose ~0.5 s. Check `bridge.send` actually passes `gestureCamera.gesture`. |
| `pod install` / workspace breaks `./scripts/dev` | update `dev_deploy.py` to `-workspace SignalMap.xcworkspace -scheme SignalMap`. |
| Two Claude sessions on one machine, push rejected | `git pull --rebase origin main` then push; commit only your workstream's files. |
