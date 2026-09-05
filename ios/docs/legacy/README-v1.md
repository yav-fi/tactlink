# Signal Map

A native SwiftUI iPhone prototype for exploring a nearby Bluetooth Low Energy device on a square mini-map. No packages or server are required. Minimum deployment target: iOS 17.

## What this build does

- **Phone ↔ Phone:** measures distance and direction using Nearby Interaction UWB between two iPhones running Signal Map. A user-selected, encrypted Multipeer Connectivity session exchanges discovery tokens. The discovery connection is not used to estimate distance.
- Checks local and peer UWB capabilities, enables extended range when both support it, and offers optional camera-assisted direction. Shows a bearing arrow rather than inventing a map position when only a horizontal angle is available. Readings expire after two seconds, and backgrounding/disconnecting clears them immediately.
- Scans for advertising BLE peripherals, connects to connectable devices, and polls RSSI approximately every 0.7 seconds. Nonconnectable peripherals can be observed through their broadcasts.
- Device search matches names and IDs. Rows stay in discovery order, update once per second, and remain visible until an explicit rescan. Freeze list pauses only the picker display, so the selected device's signal readings continue.
- Converts RSSI into a rough distance using a configurable log-distance model. The distance display uses a five-reading median. Calibrate at a measured 1 meter for each device and environment.
- Uses ARKit camera tracking to measure the phone's movement. For a stationary source, spatially separated range readings feed a robust 2D multilateration solver.
- Shows a distance ring until at least six measurements and enough movement in two dimensions are available. Then it shows an estimated point and a heuristic uncertainty region, relative to the rear camera's facing direction.
- Includes a clearly labeled demo with synthetic RSSI and movement, calibration controls, sensor status, signal expiry, and a live camera preview.
- Processes data locally. It does not save camera frames, upload measurements, or operate in the background.

## Run on your iPhone

1. Open `SignalMap.xcodeproj` in Xcode. Select the **SignalMap** scheme.
2. In **Xcode → Settings → Accounts**, sign in with your Apple Account. In the target's **Signing & Capabilities**, enable automatic signing and choose your team (a Personal Team can be used for development). If needed, change `com.arulandu.SignalMap` to a unique bundle identifier.
3. Connect and unlock your iPhone, and trust the Mac. Enable **Settings → Privacy & Security → Developer Mode** on the phone; restart and confirm the prompt.
4. Select the physical iPhone as the run destination and press **Run** (⌘R). Allow Xcode to install required device support. Use an Xcode version compatible with your phone's iOS version and your Mac's macOS version.
5. In the app, tap **Find a Bluetooth device**, allow Bluetooth access, and select your device. Enable motion mapping and allow Camera access.

Local setup: Xcode 26.3 is installed at `/Applications/Xcode 26.3.app` alongside Xcode 16.4. Use `DEVELOPER_DIR='/Applications/Xcode 26.3.app/Contents/Developer'` for command-line builds when the global selection still points to 16.4. The project has a Personal Team configured. The first build was installed on iOS 26.6.1; the phones are now being tested with iOS 27 beta. Build 2 / version 1.1 adds peer UWB using APIs available in the installed SDK. If iOS reports Untrusted Developer, trust your own development certificate under Settings → General → VPN & Device Management. See [Apple's Xcode compatibility matrix](https://developer.apple.com/xcode/system-requirements/).

## Two-iPhone UWB test

1. Install the same current build on **both** physical iPhones. Each must be paired to Xcode, have Developer Mode enabled, and be included in the development provisioning profile. Enable Wi-Fi and Bluetooth on both. Start on the same Wi-Fi network; Multipeer can also use peer-to-peer networking.
2. Open Signal Map → **Phone ↔ Phone** on both phones. Allow Local Network when prompted.
3. On one phone, tap **Make this phone discoverable**. On the other, tap **Find the other phone**, select the displayed `Signal Map · XXXX` name, and accept the invitation on the first phone. Confirm that the invitation's name matches the second phone.
4. Allow **Nearby Interaction** on both. Keep the apps foregrounded. Start 1–3 meters apart, holding the phones upright with their backs facing each other and a clear line of sight. The app prevents automatic screen sleep while the session is active.
5. The distance is the measured 3D range. A dot uses the horizontal projection of a full direction vector; height is displayed separately. The mini-map is relative to the phone's rear-camera facing direction. Hold upright for an intuitive top-down view.
6. **Camera-assisted direction** defaults on in build 3. Allow Camera. Hold the target still and slowly move the viewing phone sideways and slightly vertically in good lighting. Follow the convergence hints and camera tracking status. An arrow labeled **BEARING ONLY** conveys horizontal angle without pretending to know a horizontal position. No images are saved or uploaded.
7. Validate at measured separations of 1, 2, and 4 meters. Move the peer to the left/right and turn the viewing phone to verify orientation. Then background the peer, disconnect, and reconnect: old dots must disappear, and a new session must start cleanly. Tests of the math cannot establish beta OS behavior or real-world accuracy.

If discovery is empty, check **Settings → Apps → Signal Map → Local Network**, ensure one phone is discoverable, and check Wi-Fi/Bluetooth. If ranging fails, check **Nearby Interactions** on both and Camera if assistance is enabled. Backgrounding stops the session; reconnect after returning. If installation fails with an unsupported OS/developer disk image error, the installed Xcode needs compatible device support; compiling with the older SDK does not guarantee it can deploy to an iOS beta.

Apple instructions: [Run on a device](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices), [enable Developer Mode](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device).

### UWB diagnostics (build 3)

Expand **UWB diagnostics** to inspect local/peer capabilities, active NI configuration, camera permission, AR tracking, convergence reasons, update counts, and the optional measurement fields returned by Apple. The latest snapshot is also written to the app's `Documents/uwb-diagnostics.txt` once per second while active, plus session events. It contains no camera images or discovery tokens. Over USB, retrieve it with `xcrun devicectl device copy from --device DEVICE_ID --source Documents/uwb-diagnostics.txt --destination /tmp/uwb-diagnostics.txt --domain-type appDataContainer --domain-identifier com.arulandu.SignalMap` using the configured Xcode developer directory.

If direction remains unavailable with normal camera tracking, compare a fresh session with **Extended UWB range** off on both phones. This is a diagnostic comparison, not a guaranteed fix. Apple documents the stationary-target/moving-observer procedure in [Finding devices with precision](https://developer.apple.com/documentation/nearbyinteraction/finding-devices-with-precision). Hardware convergence and iOS beta behavior require on-device testing.

## First real-device experiment

1. Use a BLE device you own and verify that it advertises while disconnected. Wake it or use its documented advertising mode. Not every Bluetooth speaker/headphone exposes a discoverable BLE peripheral to third-party apps.
2. Keep the source stationary, roughly at phone height, in a room with good lighting and visible texture. Start with clear line of sight.
3. Place the phone exactly 1 meter away. Hold it still for about 5 seconds, then open the sliders button and choose **Save current signal at 1 meter**.
4. Enable motion mapping. Hold the phone upright with the rear camera facing the room. Walk 2–3 meters, turn, and walk another 2–3 meters to make an L-shaped path. Turning in place is insufficient.
5. Compare the dot with the source's actual location. Repeat from several starting points and compare estimated distance at measured 1, 2, and 4 meter separations. Recalibrate if the distance model is substantially wrong.
6. Verify that turning the phone rotates the map, disconnecting or losing the signal clears stale results, and covering the camera removes the position while tracking is limited.

The dot is experimental. RSSI can be distorted by walls, orientation, body occlusion, transmit-power changes, and multipath. Errors can be several meters or worse. The uncertainty region is a heuristic, not a statistical confidence interval. An inconsistent measurement set is rejected, but a plausible-looking wrong estimate is still possible. The solver assumes the source is stationary and near the phone's height; it does not provide reliable moving-source or 3D tracking. Samples expire after 60 seconds; the live signal expires after 5 seconds.

Sampling requires reliable camera tracking, an RSSI-derived range of 0.15–30 meters, at least 0.7 seconds since the last accepted sample, and at least 35 cm of horizontal movement. Six samples alone do not guarantee a position: the path must cover two dimensions and the range readings must fit reasonably well. A temporary tracking dip or pointing the phone down pauses sampling and hides the estimate while preserving measurements. Three seconds of unreliable camera tracking, a session interruption/restart, a connection change, recalibration, or backgrounding clears the map. A silent Bluetooth signal hides the live estimate after five seconds but keeps prior samples until their normal expiry. The screen shows sampling status and the last reset reason.

## More accurate ranging

This build uses **RSSI for BLE peripherals** and **UWB for two iPhones running Signal Map**. It does not implement Bluetooth Channel Sounding, AirTag Precision Finding, or an unspecified accessory protocol.

- **Phone-to-phone UWB:** Apple's documented peer API is `NINearbyPeerConfiguration`. Both apps exchange `NIDiscoveryToken` values and run a session. This is the implemented path for the user's two phones. [Apple peer API](https://developer.apple.com/documentation/nearbyinteraction/ninearbypeerconfiguration).
- **UWB accessories:** Need an accessory implementing Apple's Nearby Interaction protocol plus its configuration exchange. The BLE picker does not automatically enable UWB for arbitrary peripherals. [Apple overview](https://developer.apple.com/nearby-interaction/).
- **Actual Bluetooth Channel Sounding:** Apple's iOS 27 path has an N1-capable iPhone as initiator and a compatible accessory as reflector, paired using AccessorySetupKit and connected via Core Bluetooth. Apple specifies Bluetooth 6.3, inline PCT, phase-based ranging modes 0 and 2, and T_FCS of at least 100 µs. Core Bluetooth supplies distance; Nearby Interaction plus camera assistance can supply directional information. A generic Bluetooth 6 device is not sufficient. I found no documented iPhone app API to make a second iPhone the reflector; peer UWB is the documented Apple-device path. [Apple session and accessory requirements](https://developer.apple.com/videos/play/wwdc2026/369/), [sample implementation](https://developer.apple.com/documentation/corebluetooth/measuring-distance-between-devices-using-channel-sounding).
- To add and validate actual Channel Sounding later, provide the reflector's exact board/model, firmware, and advertised service/manufacturer identifiers, and an Xcode installation with the iOS 27 SDK. Then implement AccessorySetupKit's declared discovery filters, runtime support checks, connection, and a Channel Sounding session. This build intentionally does not mislabel RSSI or UWB as Channel Sounding.

Share the target device's brand/model (or board and firmware) to determine which path is available.

## Development and validation

Verified locally: the signed physical-iPhone target compiles with Xcode 26.3; all 23 estimator and tracking-recovery checks pass. The initial build's demo was also checked in an iOS 17 simulator; [that screenshot](docs/demo.png) predates the picker and sampling-status improvements. Real-world RSSI accuracy is not established by these tests.

Run the standalone estimator checks from the project directory:

```sh
sh scripts/test-estimator.sh
```

These cover calibration math, invalid readings, isolated signal spikes, stationary and collinear paths, a known synthetic target, a synthetic range outlier, stale samples, phone-relative rotation, UWB 3D-to-map projection, missing/invalid UWB components, and UWB reading expiry. Synthetic position accuracy is not evidence of real-world ranging accuracy. Two-device discovery, permission, suspension, and beta hardware behavior still require the physical test above.

For a fully configured Xcode installation:

```sh
xcodebuild -project SignalMap.xcodeproj -scheme SignalMap \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/SignalMap-build CODE_SIGNING_ALLOWED=NO build
```

Add the launch argument `--demo` in **Edit Scheme → Run → Arguments** to start the demo automatically. The simulator can exercise the interface and synthetic estimator; Bluetooth and ARKit behavior require the physical phone.

If scheme builds are blocked by the local missing-platform message, the installed SDK can still compile the target directly (this does not fix device signing or Xcode's platform setup):

```sh
xcodebuild -project SignalMap.xcodeproj -target SignalMap -sdk iphonesimulator \
  -configuration Debug CONFIGURATION_BUILD_DIR=/tmp/SignalMap-products \
  OBJROOT=/tmp/SignalMap-objects SYMROOT=/tmp/SignalMap-symbols \
  CODE_SIGNING_ALLOWED=NO build
```

Code is split into `BluetoothScanner.swift` (BLE), `MotionTracker.swift` (ARKit), `PositionEstimator.swift` (pure math and recovery timing), `LocatorModel.swift` (coordination), and the SwiftUI views.
