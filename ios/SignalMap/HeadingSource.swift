import Foundation
#if !ROOM_PROTOCOL_TEST
import CoreMotion
#endif

/// Magnetic-compass bearing of the direction the phone's screen faces, for the
/// visualizer bridge only. Uses Core Motion's device-motion fusion - no location
/// permission, no GPS, no internet. It reports **magnetic** north, not true
/// north: every phone in one place shares the same reference, and any residual
/// offset (declination, mount angle) is a single constant dialed in on the
/// simulator with `--phone-frame-rot`.
///
/// Assumes the phone is worn roughly upright with the screen facing forward
/// (the wearer looks the same way the screen points). When the phone is near
/// flat the bearing is undefined and `compassDegrees` is nil.
final class HeadingSource {
    private(set) var compassDegrees: Double?

    /// Compass bearing in degrees clockwise from magnetic north, 0..<360, from
    /// the north and west components of the screen-forward (+Z) axis expressed
    /// in the `.xMagneticNorthZVertical` reference frame (X = north, Y = west).
    /// Returns nil when the axis is too close to vertical to project.
    static func bearing(north: Double, west: Double) -> Double? {
        let horizontal = (north * north + west * west).squareRoot()
        guard horizontal > 0.15 else { return nil }
        var degrees = atan2(-west, north) * 180 / .pi   // east = -west
        if degrees < 0 { degrees += 360 }
        return degrees
    }

#if !ROOM_PROTOCOL_TEST
    private let motion = CMMotionManager()
    private let queue: OperationQueue = {
        let q = OperationQueue()
        q.maxConcurrentOperationCount = 1
        return q
    }()

    func start() {
        guard motion.isDeviceMotionAvailable, !motion.isDeviceMotionActive else { return }
        motion.deviceMotionUpdateInterval = 0.1
        motion.showsDeviceMovementDisplay = true   // allows the system calibration prompt
        motion.startDeviceMotionUpdates(using: .xMagneticNorthZVertical, to: queue) { [weak self] motion, _ in
            guard let self, let motion else { return }
            // rotationMatrix columns are the device axes in the reference frame;
            // the third column is device +Z (out of the screen).
            let r = motion.attitude.rotationMatrix
            self.compassDegrees = Self.bearing(north: r.m13, west: r.m23)
        }
    }

    func stop() {
        motion.stopDeviceMotionUpdates()
        compassDegrees = nil
    }
#else
    func start() {}
    func stop() {}
#endif
}
