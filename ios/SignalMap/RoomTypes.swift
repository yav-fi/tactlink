import Foundation

struct Vector3: Codable, Equatable {
    var x: Double
    var y: Double
    var z: Double
    static let zero = Vector3(x: 0, y: 0, z: 0)
    var finite: Bool { x.isFinite && y.isFinite && z.isFinite }
    var length: Double { sqrt(x*x + y*y + z*z) }
    static func + (a: Self, b: Self) -> Self { .init(x: a.x+b.x, y: a.y+b.y, z: a.z+b.z) }
    static func - (a: Self, b: Self) -> Self { .init(x: a.x-b.x, y: a.y-b.y, z: a.z-b.z) }
    static func * (a: Self, b: Double) -> Self { .init(x: a.x*b, y: a.y*b, z: a.z*b) }
    func distance(to other: Self) -> Double { (self - other).length }
}

struct DeviceProfile: Codable, Equatable {
    var id: String
    var boot: String
    var name: String
    var os: String
    var build: String
    var distance: Bool
    var direction: Bool
    var extended: Bool
    var camera: Bool
    var activeAttempt: String?
    var hasStarted: Bool
    var paused: Bool
    var connected: [String]
    var thermal: String
    var battery: Double?
    var txBytes: Int
    var rxBytes: Int
    var reconnects: Int
    var threePhoneMode: Bool
    var twoPhoneMode: Bool? = nil
}

struct RoomMember: Identifiable {
    var profile: DeviceProfile
    var lastSeen: TimeInterval
    var id: String { profile.id }
}

struct RangingPair: Codable, Hashable, Identifiable {
    let a: String
    let b: String
    init(_ a: String, _ b: String) { self.a = min(a, b); self.b = max(a, b) }
    var id: String { a + ":" + b }
    var members: [String] { [a, b] }
    func contains(_ id: String) -> Bool { a == id || b == id }
    func other(_ id: String) -> String? { a == id ? b : b == id ? a : nil }
}

enum RoundRobin {
    static func rounds(_ ids: [String]) -> [[RangingPair]] {
        var ring = Array(Set(ids)).sorted().map(Optional.some)
        guard ring.count >= 2 else { return [] }
        if ring.count % 2 != 0 { ring.append(nil) }
        var rounds: [[RangingPair]] = []
        for _ in 0..<(ring.count - 1) {
            var pairs: [RangingPair] = []
            for i in 0..<(ring.count / 2) {
                if let a = ring[i], let b = ring[ring.count - 1 - i] { pairs.append(.init(a, b)) }
            }
            rounds.append(pairs)
            ring.insert(ring.removeLast(), at: 1)
        }
        return rounds
    }
}

struct PairOffer: Codable, Equatable {
    var id: String
    var epoch: String
    var leader: String
    var pair: RangingPair
    var boots: [String: String]
    var cycle: Int
    var round: Int
    var extended: Bool
    var measurementSeconds: Double
    var leaseSeconds: Double
    var created: TimeInterval
    var valid: Bool {
        !id.isEmpty && id.count <= 64 && epoch.count <= 64 && pair.a != pair.b &&
        pair.members.allSatisfy { boots[$0] != nil } &&
        measurementSeconds.isFinite && (1...15).contains(measurementSeconds) &&
        leaseSeconds.isFinite && (5...25).contains(leaseSeconds)
    }
}

/// A local reservation survives coordinator loss, but never outlives its watchdog.
/// The owner must close its NI session before releasing this gate.
struct AttemptGate {
    private(set) var offer: PairOffer?
    private(set) var deadline: TimeInterval = 0
    private var retired: [String] = []
    mutating func reserve(_ next: PairOffer, selfID: String, boot: String, leader: String, now: TimeInterval) -> Bool {
        guard next.valid, next.leader == leader, next.pair.contains(selfID), next.boots[selfID] == boot,
              !retired.contains(next.id) else { return false }
        if let current = offer { return current == next }
        offer = next
        deadline = now + next.leaseSeconds
        return true
    }
    func expired(at now: TimeInterval) -> Bool { offer != nil && now >= deadline }
    mutating func release() {
        if let offer { retired.append(offer.id); retired = Array(retired.suffix(128)) }
        offer = nil
        deadline = 0
    }
}

struct PreparedAttempt: Codable {
    var attempt: String
    var token: Data
}
struct BeginAttempt: Codable {
    var offer: PairOffer
    var tokens: [String: Data]
}
struct AttemptReport: Codable, Identifiable {
    var id: String // attempt ID + reporting device; stable across retransmission
    var attempt: String
    var device: String
    var boot: String
    var pair: RangingPair
    var cycle: Int
    var round: Int
    var outcome: String
    var reason: String
    var extended: Bool
    var preparedMS: Double
    var runMS: Double?
    var firstDistanceMS: Double?
    var firstDirectionMS: Double?
    var reliableMS: Double?
    var totalMS: Double
    var sampleCount: Int
    var distinctCount: Int
    var distance: Double?
    var spread: Double?
    var measuredAt: TimeInterval?
    var distanceStatistics: DistanceStatistics? = nil
    var valid: Bool {
        pair.contains(device) && sampleCount >= 0 && sampleCount <= 100_000 && totalMS.isFinite && totalMS >= 0 &&
        [preparedMS, runMS, firstDistanceMS, firstDirectionMS, reliableMS, spread].compactMap { $0 }.allSatisfy { $0.isFinite && $0 >= 0 } &&
        (distance == nil || (distance!.isFinite && (0.05...1000).contains(distance!))) &&
        (measuredAt == nil || measuredAt!.isFinite) &&
        (distanceStatistics == nil || (distanceStatistics!.valid && distanceStatistics!.count == sampleCount))
    }
}

/// Statistics over all accepted readings in one bounded NI attempt, including the first.
struct DistanceStatistics: Codable {
    var count: Int
    var first: Double
    var last: Double
    var mean: Double
    var median: Double
    var standardDeviation: Double? // Sample standard deviation (n - 1).
    var minimum: Double
    var maximum: Double
    var firstZScore: Double?
    var firstMinusMedian: Double { first-median }
    var valid: Bool {
        count>0 && count<=100_000 && [first,last,mean,median,minimum,maximum].allSatisfy { $0.isFinite && (0.05...1000).contains($0) } &&
        minimum<=maximum && [first,last,mean,median].allSatisfy { $0>=minimum-1e-9 && $0<=maximum+1e-9 } &&
        (standardDeviation == nil || (count>1 && standardDeviation!.isFinite && standardDeviation!>=0)) &&
        (firstZScore == nil || (firstZScore!.isFinite && (standardDeviation ?? 0)>1e-9))
    }
    init?(_ values:[Double]) {
        guard !values.isEmpty, values.count<=100_000, values.allSatisfy({ $0.isFinite && (0.05...1000).contains($0) }) else { return nil }
        count=values.count; first=values[0]; last=values[count-1]
        // Welford avoids cancellation when readings are nearly identical.
        var average=0.0, m2=0.0
        for (index,value) in values.enumerated() {
            let delta=value-average
            average+=delta/Double(index+1)
            m2+=delta*(value-average)
        }
        mean=average; median=Statistics.percentile(values,0.5)!
        minimum=values.min()!; maximum=values.max()!
        standardDeviation=count>1 ? sqrt(max(0,m2/Double(count-1))) : nil
        firstZScore=standardDeviation.flatMap { $0>1e-9 ? (first-average)/$0 : nil }
    }
}

struct AttemptSamples {
    private(set) var values: [Double] = []
    private(set) var times: [TimeInterval] = []
    private(set) var allValues: [Double] = []
    var statistics: DistanceStatistics? { DistanceStatistics(allValues) }
    mutating func add(_ value: Double, at time: TimeInterval) {
        guard allValues.count<100_000, value.isFinite, (0.05...1000).contains(value), time.isFinite,
              times.last.map({ time >= $0 }) ?? true else { return }
        allValues.append(value)
        values.append(value); times.append(time)
        if values.count > 200 { values.removeFirst(); times.removeFirst() }
    }
    var median: Double? { Statistics.percentile(values, 0.5) }
    var mad: Double? { median.flatMap { median in Statistics.percentile(values.map { abs($0 - median) }, 0.5) } }
    var distinct: Int { Set(values.map { Int(($0 * 1000).rounded()) }).count }
    var reliable: Bool {
        values.count >= 4
    }
}

enum Statistics {
    static func percentile(_ values: [Double], _ fraction: Double) -> Double? {
        let sorted = values.filter(\.isFinite).sorted()
        guard !sorted.isEmpty else { return nil }
        let index = Double(sorted.count - 1) * max(0, min(1, fraction))
        let low = Int(floor(index)), high = Int(ceil(index))
        return sorted[low] + (sorted[high] - sorted[low]) * (index - Double(low))
    }
    static func milliseconds(_ value: Double?) -> String { value.map { String(format: "%.0f ms", $0) } ?? "—" }
}

struct AttemptAggregate {
    var reports: [AttemptReport] = []
    var succeeded: Int { reports.filter { $0.outcome == "reliable" }.count }
    var successRate: Double? { reports.isEmpty ? nil : Double(succeeded) / Double(reports.count) }
    var firstP50: Double? { Statistics.percentile(reports.compactMap(\.firstDistanceMS), 0.5) }
    var firstP95: Double? { Statistics.percentile(reports.compactMap(\.firstDistanceMS), 0.95) }
    var reliableP50: Double? { Statistics.percentile(reports.compactMap(\.reliableMS), 0.5) }
    var reliableP95: Double? { Statistics.percentile(reports.compactMap(\.reliableMS), 0.95) }
    var totalP95: Double? { Statistics.percentile(reports.map(\.totalMS), 0.95) }
}

struct ReceivedRange {
    var report: AttemptReport
    var received: TimeInterval
    var measured: TimeInterval
    var clockUncertainty: Double
}

struct GroupRangeSnapshot: Codable {
    var cycle: Int
    var epoch: String
    var created: Double
    var positions: [String: Vector3]
    var rms: Double
    var thirdAxisResolved: Bool
    var measurementSpan: Double
    var flat: Bool
    var twoPhoneMode: Bool? = nil
    var valid: Bool {
        (twoPhoneMode == true ? (flat && positions.count == 2 && positions.values.allSatisfy { abs($0.x)<0.001 }) : (flat ? (3...5).contains(positions.count) : (4...5).contains(positions.count))) && positions.values.allSatisfy { $0.finite && $0.length < 3000 && (!flat || abs($0.z)<0.001) } &&
        created.isFinite && rms.isFinite && rms >= 0 && rms < 0.4 &&
        measurementSpan.isFinite && (0...15).contains(measurementSpan)
    }
}

struct ClockProbe: Codable {
    var id: String
    var sent: TimeInterval
    var remoteReceived: TimeInterval?
    var remoteSent: TimeInterval?
}
struct PeerClock {
    var offset: Double // remote monotonic clock minus local monotonic clock
    var rtt: Double
    var updated: TimeInterval
    static func estimate(t0: Double, t1: Double, t2: Double, t3: Double) -> Self? {
        guard [t0,t1,t2,t3].allSatisfy(\.isFinite), t3 >= t0, t2 >= t1 else { return nil }
        let rtt = (t3-t0) - (t2-t1)
        guard rtt >= -0.001, rtt < 5 else { return nil }
        return .init(offset: ((t1-t0) + (t2-t3))/2, rtt: max(0,rtt), updated: t3)
    }
}

struct RoomControl: Codable {
    var action: String
    var revision: Int = 0
    var availableBench: Bool?
    var measurementSeconds: Double?
    var extended: Bool?
    var threePhoneMode: Bool?
    var twoPhoneMode: Bool? = nil
}

/// Heading from changes in centroid-relative positions. This is not inertial navigation:
/// common translation/rotation of the whole group is unobservable from pair distances.
struct RelativeMotionHeading {
    private(set) var angle = 0.0
    private(set) var speed = 0.0
    private(set) var available = false
    private(set) var moving = false
    private var position: Vector3?
    private var time: Double?
    private var velocity = Vector3.zero
    private var movingSamples = 0
    mutating func reset() { self = Self() }
    mutating func update(position next: Vector3, at now: Double) {
        guard next.finite, now.isFinite else { return }
        guard let previous=position, let last=time else { position=next; time=now; return }
        let dt=now-last
        guard dt>=0.2 else { return }
        position=next; time=now
        guard dt<=15 else { velocity = .zero; speed=0; moving=false; movingSamples=0; return }
        let raw=Vector3(x:(next.x-previous.x)/dt,y:(next.y-previous.y)/dt,z:0)
        guard raw.length<4 else { moving=false; movingSamples=0; return }
        let alpha=dt/(0.8+dt)
        velocity=velocity*(1-alpha)+raw*alpha
        speed=velocity.length
        if speed<0.08 { moving=false; movingSamples=0; return }
        if !moving {
            if speed>=0.18 { movingSamples+=1 } else { movingSamples=0 }
            guard movingSamples>=2 else { return }
            moving=true
        }
        let target=atan2(velocity.x,velocity.y)
        if !available { angle=target; available=true; return }
        let delta=atan2(sin(target-angle),cos(target-angle))
        guard abs(delta)>Double.pi/18 else { return } // 10-degree deadband
        let turn=max(-Double.pi/3*dt,min(Double.pi/3*dt,delta*0.5))
        angle=atan2(sin(angle+turn),cos(angle+turn))
    }
    func project(_ point: Vector3) -> Vector3 {
        .init(x:point.x*cos(angle)-point.y*sin(angle),y:point.x*sin(angle)+point.y*cos(angle),z:point.z)
    }
}

struct WireMessage: Codable {
    var version: Int = 2
    var id: String = UUID().uuidString
    var room: String
    var sender: String
    var boot: String
    var target: String?
    var kind: String
    var sent: TimeInterval
    var payload: Data
    var hops: Int = 0
}

struct ReplayWindow {
    private var ids = Set<String>()
    private var order: [String] = []
    mutating func insert(_ id: String) -> Bool {
        guard !ids.contains(id) else { return false }
        ids.insert(id); order.append(id)
        if order.count > 4096 { ids.remove(order.removeFirst()) }
        return true
    }
}
