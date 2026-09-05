import Foundation
import NearbyInteraction

/// One bounded NI session per attempt. No camera or AR session is created.
final class RoomRanging: NSObject, NISessionDelegate {
    let localID: String
    let boot: String
    var onPrepared: ((PreparedAttempt) -> Void)?
    var onReport: ((AttemptReport) -> Void)?
    var onEvent: ((String) -> Void)?
    var onUpdate: ((Int, Double?) -> Void)?
    private var ni: NISession?
    private var peerToken: NIDiscoveryToken?
    private(set) var offer: PairOffer?
    private var timer: Timer?
    private var preparedAt = 0.0
    private var tokenReadyAt = 0.0
    private var runAt: TimeInterval?
    private var startedMS: Double?
    private var firstMS: Double?
    private var directionMS: Double?
    private var reliableMS: Double?
    private var samples = AttemptSamples()
    private(set) var state = "Idle"
    private(set) var lastFields = "No measurement yet"

    init(localID: String, boot: String) { self.localID = localID; self.boot = boot; super.init() }
    func prepare(_ offer: PairOffer) {
        guard self.offer == nil else { return }
        self.offer = offer
        preparedAt = ProcessInfo.processInfo.systemUptime
        runAt = nil; startedMS = nil; firstMS = nil; directionMS = nil; reliableMS = nil
        samples = AttemptSamples(); lastFields = "Waiting for ranging"
        state = "Preparing"
        guard NISession.deviceCapabilities.supportsPreciseDistanceMeasurement else {
            finish(outcome: "unsupported", reason: "UWB distance unavailable"); return
        }
        let session = NISession()
        ni = session
        session.delegate = self; session.delegateQueue = .main
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in self?.tick() }
        do {
            guard let token = session.discoveryToken else {
                finish(outcome: "token-error", reason: "No discovery token. Check Nearby Interactions permission."); return
            }
            let data = try NSKeyedArchiver.archivedData(withRootObject: token, requiringSecureCoding: true)
            tokenReadyAt = ProcessInfo.processInfo.systemUptime
            state = "Ready · awaiting peer"
            onPrepared?(.init(attempt: offer.id, token: data))
        } catch { finish(outcome: "token-error", reason: error.localizedDescription) }
    }
    func begin(_ begin: BeginAttempt) {
        guard let offer, offer == begin.offer, runAt == nil, let ni,
              let other = offer.pair.other(localID), let data = begin.tokens[other], data.count < 32_768 else { return }
        do {
            guard let token = try NSKeyedUnarchiver.unarchivedObject(ofClass: NIDiscoveryToken.self, from: data),
                  token.deviceCapabilities.supportsPreciseDistanceMeasurement else {
                finish(outcome: "token-error", reason: "Peer token is invalid or incompatible"); return
            }
            guard !offer.extended || (NISession.deviceCapabilities.supportsExtendedDistanceMeasurement && token.deviceCapabilities.supportsExtendedDistanceMeasurement) else {
                finish(outcome: "unsupported", reason: "Extended UWB range is not supported by both phones"); return
            }
            peerToken = token
            let configuration = NINearbyPeerConfiguration(peerToken: token)
            configuration.isExtendedDistanceMeasurementEnabled = offer.extended
            configuration.isCameraAssistanceEnabled = false
            runAt = ProcessInfo.processInfo.systemUptime
            state = "Ranging"
            onEvent?("NI run \(offer.id.prefix(6)) · extended=\(offer.extended) · camera=false")
            ni.run(configuration)
        } catch { finish(outcome: "token-error", reason: error.localizedDescription) }
    }
    func cancel(reason: String = "Cancelled") { if offer != nil { finish(outcome: "cancelled", reason: reason) } }
    private func tick() {
        guard let offer else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if let reliableMS, let runAt, samples.reliable, now-runAt >= reliableMS/1000+0.25 {
            finish(outcome:"reliable",reason:"4 valid readings; no minimum span or deviation limit; 250 ms peer completion grace")
        } else if now-preparedAt >= offer.leaseSeconds {
            finish(outcome: "lease-timeout", reason: "Local watchdog released UWB without waiting for a remote stop")
        } else if let runAt, now-runAt >= offer.measurementSeconds {
            finish(outcome: samples.values.isEmpty ? "no-range" : "insufficient-quality", reason: samples.values.isEmpty ? "No valid distance before deadline" : "Fewer than 4 valid readings before deadline")
        }
    }
    private func finish(outcome: String, reason: String) {
        guard let offer else { return }
        let now = ProcessInfo.processInfo.systemUptime
        let old = ni; ni = nil; old?.delegate = nil; old?.invalidate()
        timer?.invalidate(); timer = nil
        self.offer = nil; peerToken = nil
        state = "Idle · \(outcome)"
        let report = AttemptReport(id: offer.id + ":" + localID, attempt: offer.id, device: localID, boot: boot,
                                   pair: offer.pair, cycle: offer.cycle, round: offer.round, outcome: outcome, reason: reason,
                                   extended: offer.extended, preparedMS: max(0,(tokenReadyAt > preparedAt ? tokenReadyAt-preparedAt : now-preparedAt)*1000),
                                   runMS: startedMS, firstDistanceMS: firstMS, firstDirectionMS: directionMS, reliableMS: reliableMS,
                                   totalMS: (now-preparedAt)*1000, sampleCount: samples.allValues.count, distinctCount: Set(samples.allValues.map { Int(($0*1000).rounded()) }).count,
                                   distance: samples.median, spread: samples.mad, measuredAt: samples.times.last, distanceStatistics:samples.statistics)
        onEvent?("NI end \(offer.id.prefix(6)) · \(outcome) · \(samples.values.count) readings")
        onReport?(report)
    }
    func sessionDidStartRunning(_ session: NISession) {
        guard ni === session, let runAt else { return }
        if startedMS == nil { startedMS = (ProcessInfo.processInfo.systemUptime-runAt)*1000 }
    }
    func session(_ session: NISession, didUpdate objects: [NINearbyObject]) {
        guard ni === session, let runAt, let object = objects.first(where: { $0.discoveryToken == peerToken }) else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if let distance = object.distance, distance.isFinite, (0.05...1000).contains(Double(distance)) {
            if firstMS == nil { firstMS = (now-runAt)*1000 }
            samples.add(Double(distance), at: now)
        }
        if object.direction != nil || object.horizontalAngle != nil, directionMS == nil { directionMS = (now-runAt)*1000 }
        lastFields = "distance=\(object.distance.map { String(format: "%.3f", $0) } ?? "nil"), direction=\(object.direction == nil ? "nil" : "present"), angle=\(object.horizontalAngle == nil ? "nil" : "present")"
        onUpdate?(samples.values.count, samples.median)
        if samples.reliable && reliableMS == nil {
            reliableMS = (now-runAt)*1000
        }
    }
    func session(_ session: NISession, didInvalidateWith error: Error) {
        guard ni === session else { return }
        let error = error as NSError
        finish(outcome: "ni-error", reason: "\(error.domain) \(error.code): \(error.localizedDescription)")
    }
    func session(_ session: NISession, didRemove objects: [NINearbyObject], reason: NINearbyObject.RemovalReason) {
        guard ni === session else { return }
        // One endpoint may finish first. Preserve a valid result from the other endpoint.
        if samples.reliable, let runAt {
            if reliableMS == nil { reliableMS = (ProcessInfo.processInfo.systemUptime-runAt)*1000 }
            finish(outcome: "reliable", reason: "Peer ended after 4+ valid readings")
        } else {
            finish(outcome: "peer-ended", reason: "NI removal \(reason.rawValue); no complete local measurement")
        }
    }
    func sessionWasSuspended(_ session: NISession) {
        guard ni === session else { return }
        finish(outcome: "suspended", reason: "iOS suspended ranging; a fresh attempt will retry")
    }
}
