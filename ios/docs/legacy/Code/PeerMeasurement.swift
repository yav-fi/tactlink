import Foundation

/// One NI update. Never combine a new distance with a direction from an older update.
struct PeerMeasurement {
    let distance: Double?
    let point: MapPoint?
    let height: Double?
    let bearing: Double?
    let timestamp: TimeInterval

    init(distance: Double?, direction: SIMD3<Float>?, horizontalAngle: Double? = nil, timestamp: TimeInterval) {
        let range = distance.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil }
        self.distance = range
        self.timestamp = timestamp
        bearing = horizontalAngle.flatMap { $0.isFinite ? $0 : nil }
        if let range, let direction,
           direction.x.isFinite, direction.y.isFinite, direction.z.isFinite {
            let x = Double(direction.x), y = Double(direction.y), z = Double(direction.z)
            let length = sqrt(x * x + y * y + z * z)
            if length > 0.001 {
                // NI's device frame: +x right, +y up, -z through the rear camera.
                point = MapPoint(x: range * x / length, y: -range * z / length)
                height = range * y / length
                return
            }
        }
        point = nil
        height = nil
    }

    func isFresh(at now: TimeInterval) -> Bool {
        now >= timestamp && now - timestamp <= 2
    }
}
