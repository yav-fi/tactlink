import Foundation

var checks = 0
func check(_ condition: @autoclosure () -> Bool, _ name: String) {
    guard condition() else { fatalError("FAIL: \(name)") }
    checks += 1
    print("PASS: \(name)")
}

check(abs(SignalFilter.distance(rssi: -59, reference: -59, exponent: 2) - 1) < 0.001, "1-meter calibration")
check(abs(SignalFilter.distance(rssi: -79, reference: -59, exponent: 2) - 10) < 0.001, "log-distance conversion")
var filter = SignalFilter()
check(filter.append(127) == nil && filter.append(.nan) == nil, "invalid RSSI rejected")
for value in [-60.0, -61, -59, -60, -100] { _ = filter.append(value) }
check(filter.median == -60, "median rejects isolated signal spike")

let target = MapPoint(x: 3, y: 4)
var still = PositionEstimator()
for i in 0 ..< 12 { still.add(point: .zero, distance: 5, time: Double(i)) }
check(still.samples.count == 1 && still.solve() == nil, "stationary phone cannot invent a bearing")
var line = PositionEstimator()
for i in 0 ..< 12 {
    let p = MapPoint(x: Double(i) * 0.4, y: 0)
    line.add(point: p, distance: p.distance(to: target), time: Double(i))
}
check(line.solve() == nil, "straight path rejects mirrored-position ambiguity")
let path = (0 ..< 8).map { MapPoint(x: Double($0) * 0.5, y: 0) }
    + (1 ... 8).map { MapPoint(x: 3.5, y: Double($0) * 0.5) }
var estimator = PositionEstimator()
for (i, p) in path.enumerated() { estimator.add(point: p, distance: p.distance(to: target), time: Double(i)) }
check(estimator.solve() != nil, "L-shaped path produces a position")
check(estimator.solve()!.point.distance(to: target) < 0.15, "recovers synthetic noise-free target within 15 cm")
var noisy = PositionEstimator()
for (i, p) in path.enumerated() {
    noisy.add(point: p, distance: p.distance(to: target) + (i == 3 ? 4 : 0.12 * sin(Double(i))), time: Double(i))
}
check(noisy.solve() != nil && noisy.solve()!.point.distance(to: target) < 0.8, "robust fit tolerates one synthetic range outlier")
estimator.add(point: .zero, distance: 5, time: 100)
check(estimator.samples.count == 1 && estimator.solve() == nil, "expired samples cannot preserve an old position")
var invalid = PositionEstimator()
invalid.add(point: MapPoint(x: .nan, y: 0), distance: 3, time: 0)
invalid.add(point: .zero, distance: .infinity, time: 1)
invalid.add(point: .zero, distance: 40, time: 2)
check(invalid.samples.isEmpty, "invalid and out-of-model ranges rejected")
let relative = MapPoint(x: 3, y: 4).relative(to: MapPoint(x: 1, y: 1), heading: .pi / 2)
check(abs(relative.x + 3) < 0.001 && abs(relative.y - 2) < 0.001, "map rotates into phone-facing coordinates")

var gatedSamples = PositionEstimator()
check(gatedSamples.add(point: .zero, distance: 2, time: 0) == .accepted, "first valid sample is accepted")
check(gatedSamples.add(point: MapPoint(x: 1, y: 0), distance: 2, time: 0.3) == .tooSoon,
      "fast readings do not become extra map samples")
check(gatedSamples.add(point: MapPoint(x: 0.2, y: 0), distance: 2, time: 1) == .tooClose,
      "standing near the last position does not add samples")
check(gatedSamples.add(point: MapPoint(x: 0.36, y: 0), distance: 2, time: 1) == .accepted,
      "enough time and translation produce another sample")
check(gatedSamples.samples.count == 2, "rejected readings preserve collected samples")
gatedSamples.expire(at: 60.5)
check(gatedSamples.samples.count == 1, "expiry removes only measurements older than 60 seconds")
gatedSamples.expire(at: 62)
check(gatedSamples.samples.isEmpty, "measurements also expire without fresh Bluetooth readings")

var recovery = TrackingRecoveryGate()
check(!recovery.shouldInvalidate(at: 10) && !recovery.shouldInvalidate(at: 12.9),
      "brief camera tracking loss preserves the map")
check(recovery.shouldInvalidate(at: 13), "three-second tracking loss invalidates the map")
check(!recovery.shouldInvalidate(at: 14), "ongoing tracking loss does not repeatedly clear the map")
recovery.reset()
check(!recovery.shouldInvalidate(at: 20) && !recovery.shouldInvalidate(at: 22.9),
      "recovered tracking starts a fresh grace period")
let ahead = PeerMeasurement(distance: 3, direction: SIMD3(0, 0, -1), timestamp: 10)
check(ahead.point?.distance(to: MapPoint(x: 0, y: 3)) == 0, "UWB forward maps above the phone")
let right = PeerMeasurement(distance: 2, direction: SIMD3(1, 0, 0), timestamp: 10)
check(right.point?.distance(to: MapPoint(x: 2, y: 0)) == 0, "UWB right maps right")
let behind = PeerMeasurement(distance: 4, direction: SIMD3(0, 0, 1), timestamp: 10)
check(behind.point?.y == -4, "UWB behind maps below the phone")
let above = PeerMeasurement(distance: 5, direction: SIMD3(0, 1, 0), timestamp: 10)
check(above.point?.distance(to: .zero) == 0 && above.height == 5, "vertical separation is not drawn as horizontal distance")
let distanceOnly = PeerMeasurement(distance: 2, direction: nil, timestamp: 11)
check(distanceOnly.distance == 2 && distanceOnly.point == nil, "missing direction never reuses a previous bearing")
let directionOnly = PeerMeasurement(distance: nil, direction: SIMD3(1, 0, 0), timestamp: 11)
check(directionOnly.point == nil, "direction without distance does not create a map position")
check(PeerMeasurement(distance: .nan, direction: SIMD3(1, 0, 0), timestamp: 0).distance == nil, "nonfinite UWB distance is rejected")
check(PeerMeasurement(distance: -1, direction: nil, timestamp: 0).distance == nil, "negative UWB distance is rejected")
check(PeerMeasurement(distance: 1, direction: SIMD3(.infinity, 0, 0), timestamp: 0).point == nil, "nonfinite UWB direction is rejected")
check(PeerMeasurement(distance: 1, direction: .zero, timestamp: 0).point == nil, "zero direction vector is not treated as a bearing")
check(ahead.isFresh(at: 12) && !ahead.isFresh(at: 12.01) && !ahead.isFresh(at: 9), "UWB freshness rejects stale or future measurements")
let bearingOnly = PeerMeasurement(distance: 2, direction: nil, horizontalAngle: .pi / 2, timestamp: 10)
check(bearingOnly.bearing == .pi / 2 && bearingOnly.point == nil, "camera bearing does not invent a horizontal target position")
check(PeerMeasurement(distance: 2, direction: nil, horizontalAngle: .nan, timestamp: 0).bearing == nil, "nonfinite camera bearing is rejected")
check(!bearingOnly.isFresh(at: 13), "camera bearings expire with their measurement")
print("\(checks) checks passed. Synthetic tests do not establish real-world ranging accuracy.")
