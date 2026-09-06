# Signal Map

A native SwiftUI app that coordinates nearby iPhones, rotates UWB ranging pairs, and shares a relative group map. No camera, GPS, server, or internet connection is used. Network.framework carries encrypted coordination and measurements; Nearby Interaction supplies UWB distances.

## Three-phone test

1. Install the current build on all three physical phones. Older builds do not understand the new group messages.
2. Keep Wi-Fi and Bluetooth enabled. Allow Local Network and Nearby Interactions. Keep Signal Map open on each phone.
3. On one phone, turn on **3-phone flat test**, then **Create a room**. Share its room code with the other two and use **Join nearby room** there. The code sets the initial mode for joining phones.
4. Once all three are present, ranging starts automatically: A↔B, A↔C, B↔C, one pair at a time. The uninvolved phone waits. Each pair receives a fresh NI session and exchanges fresh tokens.
5. Start with phones still, a few meters apart, forming a triangle at approximately equal height. The map needs all three recent, completed pair distances from one cycle. The coordinator chooses an arbitrary mirror solution and shares the same coordinates with everyone.
6. Open **Profiling & diagnostics** for per-phone latency, reliability, network timing, thermal state, failures, and cycle timing. Export the text snapshot or JSONL attempts for analysis.
7. After stationary ranging works, move one phone slowly while the others stay still. Map forward follows estimated movement relative to the group centroid. Velocity smoothing, two sustained movement samples, a 0.18 m/s start threshold, a 0.08 m/s stop threshold, and a 10° heading deadband reduce jitter. The last heading is held when stationary.

Turn the test toggle off to return the room to five-phone mode. Changing modes clears the active ranging cycle and map. Five phones cover ten pairs in five nominal rounds, up to two disjoint pairs at a time. After a started group loses a member, remaining phones can continue ranging; sufficient complete geometry is still required to draw a map.

The map centers your own dot. Its axes are a convention, not compass heading or the direction the phone faces. Pairwise distances cannot observe common translation or rotation of the entire group. Three-phone test mode assumes Z = 0; five-phone mode reconstructs a relative 3D shape, but its third axis is not gravity height. Nearly planar geometry is marked uncertain. Sequential ranges are not simultaneous: movement during a cycle can distort the shape. This prototype is for measurement and diagnostics, not validated navigation.

## Reliability and measurements

An attempt reaches its completion milestone after four valid distance readings, with no minimum time span and no deviation threshold. This does not establish consistency or accuracy. The existing 250 ms peer completion grace lets both endpoints finish before teardown, followed by the 300 ms coordinator handoff pause. Profiling labels the milestone “4 readings p50 / p95”; the compatible log outcome remains `reliable`.

The default measurement deadline is four seconds. Each phone also enforces a local lease of the deadline plus five seconds, so missing remote messages cannot reserve its radio indefinitely. Duplicate offers are idempotent; retired attempts cannot restart. Failed pairs use bounded backoff while other pairs proceed. Coordinator selection is deterministic, membership uses heartbeats, and reconnects reset stale attempts. A small group uses authenticated, encrypted peer connections with bounded relay and replay suppression. Keep the room code private: anyone holding it can participate.

Profiling records:

- Token preparation, time from NI run to first distance, time to four valid readings, and whole-attempt duration, measured on each device's monotonic clock.
- Per-device p50/p95 timings, successful attempts, failures, sample counts, network round trip, traffic, reconnects, thermal state, and battery.
- Complete reliable cycle p50/p95. Failed or skipped cycles are separately recorded; they do not count as complete reliable cycles.
- Fresh shared coordinate snapshots, fit residual, measurement span, and relative-motion heading.

Logs live in `Documents/RoomBench/latest.txt`, `attempts.jsonl`, and `events.jsonl` inside the app container. JSONL files rotate at 8 MB with one previous segment. They exclude room secrets and NI discovery tokens. Backgrounding the app releases the radio and networking; returning starts discovery again.

## Build and install

Open `SignalMap.xcodeproj`, choose your development team, pair/trust the physical iPhone, enable Developer Mode, and run the SignalMap scheme. A new phone must be included in the development provisioning profile. If iOS reports **Untrusted Developer**, trust the development profile in **Settings → General → VPN & Device Management**.

Local Xcode 26.3 is at `/Applications/Xcode 26.3.app`. The target can be built with its installed SDK even when scheme builds report a missing downloadable platform:

```sh
DEVELOPER_DIR='/Applications/Xcode 26.3.app/Contents/Developer' \
xcodebuild -project SignalMap.xcodeproj -target SignalMap -sdk iphoneos \
  -configuration Debug -allowProvisioningUpdates \
  CONFIGURATION_BUILD_DIR=/tmp/SignalMap-room-products \
  OBJROOT=/tmp/SignalMap-room-objects SYMROOT=/tmp/SignalMap-room-symbols build
```

Install using `xcrun devicectl device install app --device DEVICE_ID /tmp/SignalMap-room-products/SignalMap.app` with the same developer directory. Development installs work over USB or a reachable paired wireless connection. All participants need the same current app build. Launch arguments `--name NAME --room CODE` prefill a test session; `--bench` explicitly enables an available-device diagnostic run at two devices.

## Validation

```sh
DEVELOPER_DIR='/Applications/Xcode 26.3.app/Contents/Developer' sh scripts/test-room.sh
DEVELOPER_DIR='/Applications/Xcode 26.3.app/Contents/Developer' sh scripts/test-protocol.sh
```

The first checks scheduling, watchdogs, replay handling, timing, geometry, mirror continuity, and heading thresholds. The second runs production Network.framework transports and coordinator logic across five local nodes, injects packet loss and missing ranging callbacks, exercises leader loss/rejoin and pause/resume, then checks three-phone mode and shared coordinates. Its radio measurements are synthetic. Neither test measures hardware switching latency or proves router-free radio operation. Real three- and five-phone benchmarks must be collected from physical phones.

Source: `RoomTransport.swift` handles data links, `RoomSession.swift` coordinates the group, `RoomRanging.swift` owns NI sessions, `RangeGeometry.swift` reconstructs distances, `RoomTypes.swift` holds protocol and motion math, and `RoomView.swift` presents the map and profiler. Earlier BLE/camera experiments are archived under `docs/legacy/` and excluded from the app target.

## Fast automatic deployment

Run `./scripts/dev watch` in a terminal and leave it running. It checks every two seconds and installs the latest published build on any reachable, paired physical iPhone. USB and wireless devices use the same path. For this three-phone test, use `./scripts/dev watch --room YOUR_ROOM_CODE` to reopen that room after an update.

After changing app code, run **`./scripts/dev ship`** in another terminal. This incrementally builds and signs once, then atomically publishes an immutable app for the watcher to install on phones in parallel. Source changes are shipped explicitly, so saving an unfinished edit does not interrupt a live test. Unchanged source reuses the previous build. `ship --force` rebuilds anyway. `./scripts/dev status` shows local installation receipts. Ctrl-C in the watcher terminal stops automatic installs; it does not register a login service or modify system startup settings.

New devices trigger automatic development registration and profile refresh. Trust, Developer Mode, signing account access, certificate trust, and phone permission prompts still require the owner. Registration may use the installed older Xcode as a fallback when the primary Xcode scheme driver demands its missing optional platform; the final artifact is rebuilt with the configured primary Xcode. Override paths with `DEVELOPER_DIR` and `SIGNALMAP_PROVISIONING_XCODE` if needed.

The watcher skips the exact app fingerprint after a successful installation/launch. An unsuccessful launch retries without reinstalling. Installation failures retry after 15 seconds; individual phones cannot block the other installations. A single-instance lock prevents competing watchers. State and logs are under the ignored `build/dev-deploy/` directory. To reinstall after manually deleting the app, remove that phone's local receipt under `build/dev-deploy/receipts/` or publish a new build. `./scripts/dev adopt /absolute/path/SignalMap.app` can publish an already signed physical-device build.

Deployment regression checks: `python3 -m unittest discover -s Tests/DeployTests -v`. The first live parallel deployment of build 5 installed and launched in 1.9 seconds (Ian), 2.8 seconds (Alvan), and 7.6 seconds (Yavin); these exclude compilation and discovery. First-time provisioning and wireless availability can take longer.

### Distance statistics in Profiling

Each device card now shows the latest attempt's first and last accepted distance, mean, median, minimum, maximum, sample standard deviation (`n - 1` denominator), signed first-minus-median difference, and first-reading z-score `(first - mean) / sample SD`. These summarize every accepted reading in that attempt, including the first and completion-grace readings. The existing quality check and map measurement still use their bounded recent-reading window. No raw sensor precision or ground-truth accuracy is implied.

The z-score is undefined with fewer than two readings or effectively zero standard deviation. Per-device p50/p95 of `abs(first - median)` summarizes attempts with readings, including unsuccessful attempts; it does not pool different partners' distances. Expand entries under **Recent attempts** to inspect older attempts. The JSONL `distanceStatistics` object and text snapshot include the new fields. Older peer builds can still exchange reports but cannot supply these statistics. Tests include known distributions, single/identical readings, signed scores, JSON round trips, and retaining the initial reading beyond the quality window.
