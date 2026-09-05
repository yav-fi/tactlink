import Foundation

// Test-only platform and UWB endpoints. Networking and RoomSession are production code.
final class UIApplication { static let shared = UIApplication(); var isIdleTimerDisabled = false }
final class UIDevice {
    static let current = UIDevice()
    var isBatteryMonitoringEnabled = false
    var batteryLevel: Float = 0.8
    var systemVersion = "protocol-test"
}
struct TestCapabilities {
    let supportsPreciseDistanceMeasurement = true
    let supportsDirectionMeasurement = false
    let supportsExtendedDistanceMeasurement = true
    let supportsCameraAssistance = false
}
enum NISession { static let deviceCapabilities = TestCapabilities() }

final class RoomRanging {
    static var points: [String: Vector3] = [:]
    static var active = Set<String>()
    static var collision = false
    static var silentPair: RangingPair?
    static var silentAttempt: String?
    let localID: String
    let boot: String
    var onPrepared: ((PreparedAttempt) -> Void)?
    var onReport: ((AttemptReport) -> Void)?
    var onEvent: ((String) -> Void)?
    var onUpdate: ((Int, Double?) -> Void)?
    private(set) var offer: PairOffer?
    private var prepared = 0.0
    private var begun = false
    var state = "Idle"
    var lastFields = "Synthetic test endpoint"
    init(localID: String, boot: String) { self.localID=localID; self.boot=boot }
    func prepare(_ next: PairOffer) {
        if offer != nil || Self.active.contains(localID) { Self.collision=true; return }
        offer=next; prepared=ProcessInfo.processInfo.systemUptime; begun=false
        Self.active.insert(localID)
        onPrepared?(.init(attempt:next.id,token:Data(localID.utf8)))
    }
    func begin(_ begin: BeginAttempt) {
        guard let offer, offer==begin.offer, !begun else { return }
        begun=true
        if Self.silentPair==offer.pair && Self.silentAttempt == nil { Self.silentAttempt=offer.id; Self.silentPair=nil }
        if Self.silentAttempt==offer.id { return } // Simulated missing range/result; local lease must recover.
        DispatchQueue.main.asyncAfter(deadline:.now()+0.55) { [weak self] in
            guard let self, self.offer?.id==offer.id else { return }
            self.finish("reliable")
        }
    }
    func cancel(reason:String) { finish("cancelled") }
    private func finish(_ outcome:String) {
        guard let offer else { return }
        self.offer=nil; Self.active.remove(localID)
        let now=ProcessInfo.processInfo.systemUptime
        let distance=Self.points[offer.pair.a]!.distance(to:Self.points[offer.pair.b]!)
        let report=AttemptReport(id:offer.id+":"+localID,attempt:offer.id,device:localID,boot:boot,pair:offer.pair,
                                 cycle:offer.cycle,round:offer.round,outcome:outcome,reason:"SYNTHETIC PROTOCOL TEST",extended:offer.extended,
                                 preparedMS:1,runMS:2,firstDistanceMS:outcome=="reliable" ? 100 : nil,
                                 firstDirectionMS:nil,reliableMS:outcome=="reliable" ? 500 : nil,totalMS:(now-prepared)*1000,
                                 sampleCount:outcome=="reliable" ? 12 : 0,distinctCount:3,
                                 distance:outcome=="reliable" ? distance : nil,spread:0.01,measuredAt:now)
        onReport?(report)
    }
}
