import Foundation

struct MapPoint: Equatable {
    var x: Double
    var y: Double
    static let zero = MapPoint(x: 0, y: 0)
    func distance(to other: MapPoint) -> Double { hypot(x - other.x, y - other.y) }
    func relative(to origin: MapPoint, heading: Double) -> MapPoint {
        let dx = x - origin.x, dy = y - origin.y
        return MapPoint(x: dx * cos(heading) - dy * sin(heading),
                        y: dx * sin(heading) + dy * cos(heading))
    }
}

struct RangeSample {
    let point: MapPoint
    let distance: Double
    let time: TimeInterval
}

struct PositionEstimate {
    let point: MapPoint
    /// A heuristic uncertainty radius, not a statistical confidence interval.
    let uncertainty: Double
    let residual: Double
}

struct SignalFilter {
    private(set) var values: [Double] = []
    mutating func append(_ rssi: Double) -> Double? {
        guard rssi.isFinite, (-110 ... -10).contains(rssi) else { return nil }
        values.append(rssi)
        values = Array(values.suffix(5))
        return median
    }
    var median: Double? {
        let sorted = values.sorted()
        guard !sorted.isEmpty else { return nil }
        return sorted[sorted.count / 2]
    }
    static func distance(rssi: Double, reference: Double, exponent: Double) -> Double {
        pow(10, (reference - rssi) / (10 * max(1, exponent)))
    }
}

struct PositionEstimator {
    enum SampleDecision {
        case accepted, invalidRange, tooSoon, tooClose
    }
    private(set) var samples: [RangeSample] = []
    mutating func reset() { samples.removeAll() }
    mutating func expire(at time: TimeInterval) {
        samples.removeAll { time - $0.time > 60 }
    }
    @discardableResult
    mutating func add(point: MapPoint, distance: Double, time: TimeInterval) -> SampleDecision {
        expire(at: time)
        guard point.x.isFinite, point.y.isFinite, distance.isFinite,
              (0.15 ... 30).contains(distance) else { return .invalidRange }
        if let last = samples.last {
            if time - last.time < 0.7 { return .tooSoon }
            if last.point.distance(to: point) < 0.35 { return .tooClose }
        }
        samples.append(RangeSample(point: point, distance: distance, time: time))
        samples = Array(samples.suffix(40))
        return .accepted
    }

    func solve() -> PositionEstimate? {
        guard samples.count >= 6 else { return nil }
        let n = Double(samples.count)
        let center = MapPoint(x: samples.map { $0.point.x }.reduce(0, +) / n,
                              y: samples.map { $0.point.y }.reduce(0, +) / n)
        let xx = samples.map { pow($0.point.x - center.x, 2) }.reduce(0, +) / n
        let yy = samples.map { pow($0.point.y - center.y, 2) }.reduce(0, +) / n
        let xy = samples.map { ($0.point.x - center.x) * ($0.point.y - center.y) }.reduce(0, +) / n
        let smallEigenvalue = (xx + yy - sqrt(pow(xx - yy, 2) + 4 * xy * xy)) / 2
        // Standing still or walking a line cannot resolve the two mirrored positions.
        guard smallEigenvalue > 0.12, xx + yy > 0.8 else { return nil }
        func cost(_ p: MapPoint) -> Double {
            samples.map {
                let residual = abs(p.distance(to: $0.point) - $0.distance)
                let scale = max(0.6, $0.distance * 0.35)
                let z = residual / scale
                return log1p(z * z)
            }.reduce(0, +)
        }
        let span = min(35, max(4, samples.map(\.distance).max()! + sqrt(xx + yy)))
        var best = center
        var bestCost = Double.infinity
        var seeds: [(MapPoint, Double)] = []
        for ix in -12 ... 12 {
            for iy in -12 ... 12 {
                let p = MapPoint(x: center.x + Double(ix) * span / 12,
                                 y: center.y + Double(iy) * span / 12)
                let value = cost(p)
                seeds.append((p, value))
            }
        }
        // Refine multiple seeds so a coarse grid cannot lock onto a mirrored basin.
        for seed in seeds.sorted(by: { $0.1 < $1.1 }).prefix(8) {
            var candidate = seed.0
            var candidateCost = seed.1
            var step = span / 12
            for _ in 0 ..< 40 {
                var improved = false
                for offset in [MapPoint(x: step, y: 0), MapPoint(x: -step, y: 0),
                               MapPoint(x: 0, y: step), MapPoint(x: 0, y: -step)] {
                    let p = MapPoint(x: candidate.x + offset.x, y: candidate.y + offset.y)
                    let value = cost(p)
                    if value < candidateCost { candidate = p; candidateCost = value; improved = true }
                }
                if !improved { step *= 0.5 }
            }
            if candidateCost < bestCost { best = candidate; bestCost = candidateCost }
        }
        let rms = sqrt(samples.map { pow(best.distance(to: $0.point) - $0.distance, 2) }.reduce(0, +) / n)
        let averageRange = samples.map(\.distance).reduce(0, +) / n
        guard best.distance(to: center) <= span, rms < max(1.5, averageRange * 0.55) else { return nil }
        let geometryPenalty = max(1, sqrt((xx + yy) / smallEigenvalue) / 3)
        return PositionEstimate(point: best, uncertainty: max(1, rms, averageRange * 0.35) * geometryPenalty,
                                residual: rms)
    }
}

/// Preserve measurements during brief tracking-quality dips; invalidate once
/// when a prolonged gap makes the old coordinate frame unreliable.
struct TrackingRecoveryGate {
    private var limitedSince: TimeInterval?
    private var invalidated = false
    mutating func reset() { limitedSince = nil; invalidated = false }
    mutating func shouldInvalidate(at time: TimeInterval) -> Bool {
        if limitedSince == nil { limitedSince = time }
        guard !invalidated, time - limitedSince! >= 3 else { return false }
        invalidated = true
        return true
    }
}
