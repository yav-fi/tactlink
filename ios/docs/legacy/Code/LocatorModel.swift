import Foundation
import Combine

final class LocatorModel: ObservableObject {
    let bluetooth = BluetoothScanner()
    let motion = MotionTracker()
    @Published var distance: Double?
    @Published var rssi: Double?
    @Published var position: PositionEstimate?
    @Published var phone = MapPoint.zero
    @Published var heading = 0.0
    @Published var trail: [MapPoint] = []
    @Published var sampleCount = 0
    @Published var isDemo = false
    @Published var reference = -59.0
    @Published var exponent = 2.2
    @Published var hasPose = false
    @Published var stale = false
    @Published var calibrationMessage: String?
    @Published var samplingStatus = "Choose a device and enable motion mapping"
    @Published var lastResetReason: String?
    private var filter = SignalFilter()
    private var estimator = PositionEstimator()
    private var lastSignal: Date?
    private var heartbeat: Timer?
    private var demoTimer: Timer?
    private var demoStep = 0

    init() {
        bluetooth.onRSSI = { [weak self] value in self?.receive(value) }
        bluetooth.onSelection = { [weak self] in self?.resetSignal(reason: "Selected a Bluetooth device") }
        bluetooth.onUnavailable = { [weak self] in self?.resetSignal(reason: "Bluetooth connection changed") }
        motion.onPose = { [weak self] point, heading in
            guard let self, !self.isDemo else { return }
            let wasPaused = !self.hasPose
            self.phone = point
            self.heading = heading
            self.hasPose = true
            if wasPaused { self.samplingStatus = "Tracking ready · waiting for a Bluetooth reading" }
            if self.trail.last.map({ $0.distance(to: point) > 0.15 }) ?? true {
                self.trail.append(point)
                self.trail = Array(self.trail.suffix(150))
            }
        }
        motion.onTrackingPaused = { [weak self] reason in
            self?.hasPose = false
            self?.position = nil
            self?.samplingStatus = "Paused: \(reason)"
        }
        motion.onWorldReset = { [weak self] reason in
            self?.hasPose = false
            self?.resetMap(reason: reason)
            self?.samplingStatus = "Waiting for camera tracking"
        }
        heartbeat = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.estimator.expire(at: Date().timeIntervalSince1970)
            if self.sampleCount != self.estimator.samples.count {
                self.sampleCount = self.estimator.samples.count
                self.position = self.hasPose && !self.stale ? self.estimator.solve() : nil
                if self.sampleCount == 0 { self.lastResetReason = "Samples expired after 60 seconds" }
            }
            guard let last = self.lastSignal, Date().timeIntervalSince(last) > 5, !self.stale else { return }
            self.filter = SignalFilter()
            self.distance = nil
            self.rssi = nil
            self.position = nil
            self.stale = true
            self.samplingStatus = "Paused: no Bluetooth reading for 5 seconds · samples kept until expiry"
        }
    }
    func resetMap(reason: String = "You reset the map") {
        estimator.reset()
        position = nil
        sampleCount = 0
        trail = []
        lastResetReason = reason
        samplingStatus = "Map cleared · collect new samples"
    }
    func resetSignal(reason: String = "Started a new measurement session") {
        filter = SignalFilter()
        distance = nil
        rssi = nil
        lastSignal = nil
        stale = false
        calibrationMessage = nil
        resetMap(reason: reason)
        samplingStatus = "Waiting for Bluetooth and camera tracking"
    }
    private func receive(_ value: Double) {
        guard let smoothed = filter.append(value) else { return }
        lastSignal = Date()
        stale = false
        rssi = smoothed
        let range = SignalFilter.distance(rssi: smoothed, reference: reference, exponent: exponent)
        distance = range
        guard hasPose else { position = nil; return }
        // Use this measurement's RSSI for the current pose. A moving median would
        // pair earlier positions' signals with the phone's new position.
        let currentRange = SignalFilter.distance(rssi: value, reference: reference, exponent: exponent)
        let decision = estimator.add(point: phone, distance: currentRange, time: Date().timeIntervalSince1970)
        sampleCount = estimator.samples.count
        position = estimator.solve()
        switch decision {
        case .accepted:
            samplingStatus = position == nil ? "Sample added · keep walking an L-shaped path" : "Sample added · position updated"
        case .tooSoon: break
        case .tooClose: samplingStatus = "Move at least 35 cm from the last sample position"
        case .invalidRange: samplingStatus = "Reading outside the usable 0.15–30 m range"
        }
    }
    func calibrate() {
        guard filter.values.count >= 5, let value = filter.median else { return }
        reference = value
        resetMap(reason: "Distance calibration changed")
        distance = 1
        calibrationMessage = "Saved \(Int(value)) dBm at 1 meter for this session"
    }
    var canCalibrate: Bool { !isDemo && filter.values.count >= 5 && !stale }
    func settingsChanged() {
        resetMap(reason: "Distance model settings changed")
        if let rssi { distance = SignalFilter.distance(rssi: rssi, reference: reference, exponent: exponent) }
    }
    func toggleDemo() {
        if isDemo {
            demoTimer?.invalidate()
            isDemo = false
            hasPose = false
            phone = .zero
            heading = 0
            resetSignal()
        } else {
            bluetooth.stop()
            motion.stop()
            resetSignal()
            isDemo = true
            demoStep = 0
            tickDemo()
            demoTimer = Timer.scheduledTimer(withTimeInterval: 0.8, repeats: true) { [weak self] _ in self?.tickDemo() }
        }
    }
    private func tickDemo() {
        let t = Double(demoStep) * 0.2
        phone = MapPoint(x: 2 * sin(t), y: 1.6 * (1 - cos(t)))
        heading = 0.15 * sin(t)
        hasPose = true
        trail.append(phone)
        trail = Array(trail.suffix(100))
        let target = MapPoint(x: 2.8, y: 4.5)
        let range = phone.distance(to: target)
        receive(reference - 10 * exponent * log10(range) + 0.65 * sin(t * 3))
        demoStep += 1
    }
    func suspend() {
        if isDemo { toggleDemo() }
        bluetooth.stop()
        motion.stop()
        resetSignal()
    }
}
