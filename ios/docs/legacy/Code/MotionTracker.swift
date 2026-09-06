import ARKit
import AVFoundation
import Combine

final class MotionTracker: NSObject, ObservableObject, ARSessionDelegate {
    let session = ARSession()
    @Published var status = "Enable motion mapping to estimate direction"
    @Published var isRunning = false
    var onPose: ((MapPoint, Double) -> Void)?
    var onTrackingPaused: ((String) -> Void)?
    var onWorldReset: ((String) -> Void)?
    private var requested = false
    private var lastUpdate: TimeInterval = 0
    private var recoveryGate = TrackingRecoveryGate()
    override init() {
        super.init()
        session.delegate = self
        session.delegateQueue = .main
    }
    func start() {
        guard ARWorldTrackingConfiguration.isSupported else {
            status = "Motion mapping requires a physical iPhone"
            return
        }
        requested = true
        AVCaptureDevice.requestAccess(for: .video) { [weak self] allowed in
            DispatchQueue.main.async {
                guard let self, self.requested else { return }
                guard allowed else { self.status = "Allow Camera access in Settings → Signal Map"; return }
                self.recoveryGate.reset()
                self.onWorldReset?("Motion mapping started a new coordinate frame")
                let config = ARWorldTrackingConfiguration()
                config.worldAlignment = .gravity
                self.session.run(config, options: [.resetTracking, .removeExistingAnchors])
                self.isRunning = true
                self.status = "Move slowly so the camera can track the room"
            }
        }
    }
    func stop() {
        requested = false
        session.pause()
        isRunning = false
        status = "Motion mapping is paused"
        recoveryGate.reset()
        onWorldReset?("Motion mapping stopped")
    }
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        guard isRunning, frame.timestamp - lastUpdate > 0.1 else { return }
        lastUpdate = frame.timestamp
        guard case .normal = frame.camera.trackingState else {
            switch frame.camera.trackingState {
            case .limited(.excessiveMotion): status = "Move more slowly · camera tracking is limited"
            case .limited(.insufficientFeatures): status = "Aim at a well-lit, textured area · camera needs features"
            case .limited(.relocalizing): status = "Camera is recovering its position in the room"
            default: status = "Camera is initializing · move slowly"
            }
            onTrackingPaused?(status)
            if recoveryGate.shouldInvalidate(at: frame.timestamp) {
                onWorldReset?("Camera tracking was unreliable for 3 seconds")
            }
            return
        }
        recoveryGate.reset()
        let transform = frame.camera.transform
        let forwardX = -Double(transform.columns.2.x)
        let forwardY = Double(transform.columns.2.z)
        guard hypot(forwardX, forwardY) > 0.3 else {
            status = "Hold your phone upright and point into the room"
            onTrackingPaused?("Hold the phone upright to resume sampling · samples kept")
            return
        }
        status = "Tracking movement · walk an L-shaped path"
        onPose?(MapPoint(x: Double(transform.columns.3.x), y: -Double(transform.columns.3.z)),
                atan2(forwardX, forwardY))
    }
    func sessionWasInterrupted(_ session: ARSession) {
        status = "Camera interrupted · restart motion mapping"
        onWorldReset?("The camera session was interrupted")
    }
    func sessionInterruptionEnded(_ session: ARSession) { if requested { start() } }
    func session(_ session: ARSession, didFailWithError error: Error) {
        stop()
        status = "Camera tracking failed · try restarting motion mapping"
    }
}
