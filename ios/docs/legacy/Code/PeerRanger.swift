import Foundation
import Combine
import NearbyInteraction
import MultipeerConnectivity
import UIKit
import ARKit
import AVFoundation

/// Foreground-only, one-to-one UWB ranging. Multipeer carries discovery tokens, never ranges.
final class PeerRanger: NSObject, ObservableObject {
    static let serviceType = "signalmap-uwb"
    @Published private(set) var status = "Choose a role on each iPhone."
    @Published private(set) var isActive = false
    @Published private(set) var connectedName: String?
    @Published private(set) var measurement: PeerMeasurement?
    @Published private(set) var updateCount = 0
    @Published private(set) var extendedRange = false
    @Published var invitationName: String?
    @Published var useCameraAssistance = true
    @Published var useExtendedRange = true
    @Published private(set) var cameraGuidance: String?
    @Published private(set) var cameraEnabled = false
    @Published private(set) var cameraTracking = "Not running"
    @Published private(set) var diagnostics = "Start a session to collect diagnostics."
    let cameraSession = ARSession()
    private var events: [String] = []
    private var convergenceState = "No callback yet"
    private var sessionState = "Idle"
    private var latestFields = "No NI update yet"
    private var distanceUpdates = 0
    private var directionUpdates = 0
    private var angleUpdates = 0
    private var lastNIUpdate: TimeInterval?
    private var lastDiagnosticWrite: TimeInterval = 0
    private let diagnosticWriter = DispatchQueue(label: "signalmap.uwb-diagnostics")
    private var configurationAttempt = UUID()

    let localPeer = MCPeerID(displayName: "Signal Map · " + String(UUID().uuidString.prefix(4)))
    var localName: String { localPeer.displayName }
    var supportsUWB: Bool { NISession.deviceCapabilities.supportsPreciseDistanceMeasurement }
    var supportsDirection: Bool { NISession.deviceCapabilities.supportsDirectionMeasurement }
    var supportsExtendedRange: Bool { NISession.deviceCapabilities.supportsExtendedDistanceMeasurement }
    var supportsCamera: Bool { NISession.deviceCapabilities.supportsCameraAssistance }

    private(set) var connection: MCSession?
    private var advertiser: MCNearbyServiceAdvertiser?
    private var ranging: NISession?
    private var peerToken: NIDiscoveryToken?
    private var configuration: NINearbyPeerConfiguration?
    private var acceptedPeer: MCPeerID?
    private var invitationReply: ((Bool, MCSession?) -> Void)?
    private var timer: Timer?
    private var connectionStarted: TimeInterval?

    // Small, versioned envelope; tokens are decoded with secure coding.
    private struct Packet: Codable {
        let version: Int
        let token: Data
    }

    override init() {
        super.init()
        cameraSession.delegate = self
        cameraSession.delegateQueue = .main
        refreshDiagnostics(force: true)
    }

    var directionHelp: String {
        if !isActive { return "Connect the other phone to start UWB." }
        if connectedName == nil { return "Waiting for the other phone to connect." }
        if measurement?.point != nil { return "Full direction is available." }
        if measurement?.bearing != nil { return "Apple returned a horizontal bearing, but not a full 3D direction. The arrow is usable while the dot waits for a full position." }
        if !cameraEnabled {
            if AVCaptureDevice.authorizationStatus(for: .video) == .denied || AVCaptureDevice.authorizationStatus(for: .video) == .restricted {
                return "Camera access is off. Allow it in Settings → Apps → Signal Map, then reconnect with camera assistance enabled. Distance can still work without it."
            }
            return supportsDirection
                ? "Camera assistance is off. Enable it below if pointing the phones toward each other does not produce direction."
                : "This iPhone reports no direct direction measurement. Enable camera assistance below to let Apple resolve a bearing from UWB and your movement."
        }
        return cameraGuidance ?? "Keep the other phone still and upright, 1–3 m away. Point this phone’s rear camera toward it, then move this phone slowly sideways and slightly up and down in good lighting."
    }

    func enableCameraNow() {
        useCameraAssistance = true
        if let ni = ranging, let token = peerToken { configureRanging(ni, token: token) }
    }

    private func configureRanging(_ ni: NISession, token: NIDiscoveryToken) {
        let attempt = UUID()
        configurationAttempt = attempt
        measurement = nil
        convergenceState = "Waiting for convergence callback"
        cameraGuidance = nil
        if useCameraAssistance && supportsCamera {
            sessionState = "Awaiting camera permission"
            record("Requested camera-assisted ranging")
            AVCaptureDevice.requestAccess(for: .video) { [weak self, weak ni] allowed in
                DispatchQueue.main.async {
                    guard let self, let ni, self.ranging === ni, self.isActive,
                          self.configurationAttempt == attempt else { return }
                    self.runRanging(ni, token: token, camera: allowed)
                }
            }
        } else {
            runRanging(ni, token: token, camera: false)
        }
    }

    private func runRanging(_ ni: NISession, token: NIDiscoveryToken, camera: Bool) {
        let config = NINearbyPeerConfiguration(peerToken: token)
        config.isExtendedDistanceMeasurementEnabled = useExtendedRange && supportsExtendedRange && token.deviceCapabilities.supportsExtendedDistanceMeasurement
        config.isCameraAssistanceEnabled = camera && supportsCamera
        cameraEnabled = config.isCameraAssistanceEnabled
        if cameraEnabled {
            let ar = ARWorldTrackingConfiguration()
            ar.worldAlignment = .gravity
            cameraTracking = "Initializing"
            cameraSession.run(ar, options: [.resetTracking, .removeExistingAnchors])
            ni.setARSession(cameraSession)
        }
        extendedRange = config.isExtendedDistanceMeasurementEnabled
        configuration = config
        sessionState = "Starting"
        status = "Waiting for UWB measurements"
        record("Run NI: camera=\(cameraEnabled), extendedRange=\(extendedRange)")
        ni.run(config)
    }

    private func record(_ event: String) {
        events.append("\(Date().formatted(date: .omitted, time: .standard)): \(event)")
        events = Array(events.suffix(60))
        refreshDiagnostics(force: true)
    }

    private func refreshDiagnostics(force: Bool = false) {
        let now = ProcessInfo.processInfo.systemUptime
        guard force || now - lastDiagnosticWrite >= 1 else { return }
        lastDiagnosticWrite = now
        func capabilities(_ c: NIDeviceCapability) -> String {
            "distance=\(c.supportsPreciseDistanceMeasurement), direction=\(c.supportsDirectionMeasurement), camera=\(c.supportsCameraAssistance), extended=\(c.supportsExtendedDistanceMeasurement)"
        }
        let permission: String
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: permission = "Allowed"
        case .denied: permission = "Denied"
        case .restricted: permission = "Restricted"
        case .notDetermined: permission = "Not requested"
        @unknown default: permission = "Unknown"
        }
        let age = lastNIUpdate.map { String(format: "%.1f s", now - $0) } ?? "Never"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        let report = """
        Signal Map UWB diagnostics · build \(build)
        Updated: \(Date().formatted())
        OS: \(UIDevice.current.systemVersion)
        Local: \(capabilities(NISession.deviceCapabilities))
        Peer: \(peerToken.map { capabilities($0.deviceCapabilities) } ?? "No token")
        Link connected: \(connectedName != nil)
        NI session: \(sessionState)
        Camera requested: \(useCameraAssistance); enabled: \(cameraEnabled); permission: \(permission)
        AR tracking: \(cameraTracking)
        Extended range requested: \(useExtendedRange); enabled: \(extendedRange)
        Convergence: \(convergenceState)
        Update age: \(age)
        NI updates: \(updateCount); with distance: \(distanceUpdates); with direction: \(directionUpdates); with angle: \(angleUpdates)
        Latest NI fields: \(latestFields)
        UI status: \(status)

        Recent events:
        \(events.joined(separator: "\n"))
        """
        diagnostics = report
        // Only this app's latest diagnostic snapshot; no camera images, tokens or device IDs.
        diagnosticWriter.async {
            do {
                let directory = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
                try report.write(to: directory.appendingPathComponent("uwb-diagnostics.txt"), atomically: true, encoding: .utf8)
            } catch {
                // The on-screen report remains available if the container is temporarily locked.
            }
        }
    }

    func start(advertise: Bool) {
        stop()
        guard supportsUWB else {
            status = "Precise UWB ranging is unavailable on this iPhone. Bluetooth RSSI mode is still available."
            return
        }
        let ni = NISession()
        ni.delegate = self
        ni.delegateQueue = .main
        ranging = ni
        let link = MCSession(peer: localPeer, securityIdentity: nil, encryptionPreference: .required)
        link.delegate = self
        connection = link
        isActive = true
        updateCount = 0
        distanceUpdates = 0
        directionUpdates = 0
        angleUpdates = 0
        lastNIUpdate = nil
        latestFields = "No NI update yet"
        events = []
        sessionState = "Discovering"
        UIApplication.shared.isIdleTimerDisabled = true
        if advertise {
            let ad = MCNearbyServiceAdvertiser(peer: localPeer, discoveryInfo: ["v": "1"], serviceType: Self.serviceType)
            ad.delegate = self
            advertiser = ad
            ad.startAdvertisingPeer()
            status = "Waiting for the other iPhone to connect. Accept its invitation here."
        } else {
            status = "Select the name shown on the other iPhone, then accept the invitation there."
        }
        record(advertise ? "Advertising" : "Browsing")
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self else { return }
            let now = ProcessInfo.processInfo.systemUptime
            if let reading = self.measurement, !reading.isFresh(at: now) {
                self.measurement = nil
                self.status = "No fresh UWB reading. Keep both apps open and bring the phones closer."
            }
            if let started = self.connectionStarted, now - started > 25, self.peerToken == nil {
                self.stop(message: "The other phone did not finish setup. Check its Nearby Interaction permission, then reconnect.")
            }
            self.refreshDiagnostics()
        }
    }

    func answerInvitation(accept: Bool) {
        let reply = invitationReply
        invitationReply = nil
        invitationName = nil
        if !accept { acceptedPeer = nil }
        reply?(accept, accept ? connection : nil)
    }

    func stop(message: String = "Stopped. Choose a role to connect again.") {
        configurationAttempt = UUID()
        answerInvitation(accept: false)
        timer?.invalidate()
        timer = nil
        advertiser?.stopAdvertisingPeer()
        advertiser?.delegate = nil
        advertiser = nil
        let oldNI = ranging
        ranging = nil
        oldNI?.delegate = nil
        oldNI?.invalidate()
        cameraSession.pause()
        cameraEnabled = false
        cameraTracking = "Not running"
        let oldConnection = connection
        connection = nil
        oldConnection?.delegate = nil
        oldConnection?.disconnect()
        peerToken = nil
        configuration = nil
        acceptedPeer = nil
        connectedName = nil
        measurement = nil
        connectionStarted = nil
        extendedRange = false
        cameraGuidance = nil
        isActive = false
        UIApplication.shared.isIdleTimerDisabled = false
        status = message
        sessionState = "Stopped"
        record(message)
    }

    private func sendToken(to peer: MCPeerID) {
        guard let token = ranging?.discoveryToken, let connection else {
            stop(message: "Nearby Interaction did not provide a discovery token. Check permission and reconnect.")
            return
        }
        do {
            let archive = try NSKeyedArchiver.archivedData(withRootObject: token, requiringSecureCoding: true)
            let data = try JSONEncoder().encode(Packet(version: 1, token: archive))
            try connection.send(data, toPeers: [peer], with: .reliable)
        } catch {
            stop(message: "Could not exchange ranging tokens: \(error.localizedDescription)")
        }
    }
}

extension PeerRanger: MCNearbyServiceAdvertiserDelegate {
    func advertiser(_ advertiser: MCNearbyServiceAdvertiser, didReceiveInvitationFromPeer peerID: MCPeerID,
                    withContext context: Data?, invitationHandler: @escaping (Bool, MCSession?) -> Void) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.advertiser === advertiser, self.isActive,
                  self.acceptedPeer == nil, self.invitationReply == nil,
                  self.connection?.connectedPeers.isEmpty == true else {
                invitationHandler(false, nil)
                return
            }
            self.acceptedPeer = peerID
            self.invitationReply = invitationHandler
            self.invitationName = peerID.displayName
        }
    }

    func advertiser(_ advertiser: MCNearbyServiceAdvertiser, didNotStartAdvertisingPeer error: Error) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.advertiser === advertiser else { return }
            self.stop(message: "Discovery failed: \(error.localizedDescription). Allow Local Network in Settings → Apps → Signal Map.")
        }
    }
}

extension PeerRanger: MCSessionDelegate {
    func session(_ session: MCSession, peer peerID: MCPeerID, didChange state: MCSessionState) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.connection === session else { return }
            switch state {
            case .connecting:
                self.status = "Connecting to \(peerID.displayName)…"
            case .connected:
                guard self.acceptedPeer == nil || self.acceptedPeer == peerID,
                      session.connectedPeers.count == 1 else {
                    self.stop(message: "Only one peer is supported. Reconnect to one iPhone.")
                    return
                }
                self.acceptedPeer = peerID
                self.connectedName = peerID.displayName
                self.connectionStarted = ProcessInfo.processInfo.systemUptime
                self.advertiser?.stopAdvertisingPeer()
                self.status = "Connected · exchanging UWB tokens. Allow Nearby Interaction on both phones."
                self.record("Peer connected; exchanging tokens")
                self.sendToken(to: peerID)
            case .notConnected:
                // A failed invitation also needs a clean session and a fresh token.
                if self.acceptedPeer == peerID || self.connectedName == nil {
                    self.stop(message: "Connection ended. Keep both apps open and reconnect.")
                }
            @unknown default: self.stop(message: "Connection state changed. Reconnect to the other iPhone.")
            }
        }
    }

    func session(_ session: MCSession, didReceive data: Data, fromPeer peerID: MCPeerID) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.connection === session, self.acceptedPeer == peerID,
                  session.connectedPeers.contains(peerID), let ni = self.ranging else { return }
            do {
                guard data.count <= 32_768 else { throw PeerError.invalidToken }
                let packet = try JSONDecoder().decode(Packet.self, from: data)
                guard packet.version == 1,
                      let token = try NSKeyedUnarchiver.unarchivedObject(ofClass: NIDiscoveryToken.self, from: packet.token),
                      token.deviceCapabilities.supportsPreciseDistanceMeasurement else { throw PeerError.invalidToken }
                if self.peerToken == token { return }
                self.peerToken = token
                self.record("Received peer discovery token")
                self.configureRanging(ni, token: token)
            } catch {
                self.stop(message: "Could not start peer ranging: \(error.localizedDescription)")
            }
        }
    }

    private enum PeerError: LocalizedError {
        case invalidToken
        var errorDescription: String? { "The peer sent an incompatible or invalid ranging token. Update Signal Map on both phones." }
    }

    func session(_ session: MCSession, didReceive stream: InputStream, withName streamName: String, fromPeer peerID: MCPeerID) { stream.close() }
    func session(_ session: MCSession, didStartReceivingResourceWithName resourceName: String, fromPeer peerID: MCPeerID, with progress: Progress) { progress.cancel() }
    func session(_ session: MCSession, didFinishReceivingResourceWithName resourceName: String, fromPeer peerID: MCPeerID, at localURL: URL?, withError error: Error?) {}
}

extension PeerRanger: NISessionDelegate {
    func session(_ session: NISession, didUpdate nearbyObjects: [NINearbyObject]) {
        guard ranging === session, let object = nearbyObjects.first(where: { $0.discoveryToken == peerToken }) else { return }
        let reading = PeerMeasurement(distance: object.distance.map(Double.init), direction: object.direction,
                                      horizontalAngle: object.horizontalAngle.map(Double.init),
                                      timestamp: ProcessInfo.processInfo.systemUptime)
        measurement = reading
        updateCount += 1
        lastNIUpdate = ProcessInfo.processInfo.systemUptime
        if object.distance != nil { distanceUpdates += 1 }
        if object.direction != nil { directionUpdates += 1 }
        if object.horizontalAngle != nil { angleUpdates += 1 }
        let rawDistance = object.distance.map { String(format: "%.3f m", $0) } ?? "nil"
        let rawDirection = object.direction.map { "(\($0.x), \($0.y), \($0.z))" } ?? "nil"
        let rawAngle = object.horizontalAngle.map { String(format: "%.3f rad", $0) } ?? "nil"
        let rawWorld = cameraEnabled ? session.worldTransform(for: object).map { String(describing: $0.columns.3) } ?? "nil" : "Camera off"
        latestFields = "distance=\(rawDistance), direction=\(rawDirection), horizontalAngle=\(rawAngle), vertical=\(object.verticalDirectionEstimate.rawValue), AR world position=\(rawWorld)"
        if reading.point != nil {
            status = "Live UWB distance and direction"
        } else if reading.bearing != nil {
            status = "Camera-assisted bearing available. The arrow shows direction; a full map position is not yet available."
        } else if reading.distance != nil {
            status = cameraEnabled ? "Distance available · resolving direction with the camera" : "Distance available · camera assistance is off"
        } else {
            status = "UWB temporarily unavailable. Bring the phones closer with a clear line of sight."
        }
        refreshDiagnostics()
    }

    func session(_ session: NISession, didRemove nearbyObjects: [NINearbyObject], reason: NINearbyObject.RemovalReason) {
        guard ranging === session else { return }
        measurement = nil
        record("NI removed peer: \(reason.rawValue)")
        if reason == .timeout, let configuration {
            status = "Peer out of range. Move closer; retrying UWB…"
            session.run(configuration)
        } else {
            stop(message: "The other phone ended ranging. Reconnect on both phones.")
        }
    }

    func sessionWasSuspended(_ session: NISession) {
        guard ranging === session else { return }
        measurement = nil
        status = "UWB paused by iOS. Keep this app open on both phones."
        sessionState = "Suspended"
        record("NI suspended")
    }

    func sessionSuspensionEnded(_ session: NISession) {
        guard ranging === session, let configuration else { return }
        status = "Resuming UWB…"
        sessionState = "Resuming"
        record("NI suspension ended")
        session.run(configuration)
    }

    func session(_ session: NISession, didInvalidateWith error: Error) {
        guard ranging === session else { return }
        let code = (error as NSError).code
        stop(message: "Nearby Interaction stopped (\(code)): \(error.localizedDescription). Check Nearby Interactions and Camera permissions in Settings → Apps → Signal Map, then reconnect. You can turn camera assistance off to test direct UWB.")
    }

    func session(_ session: NISession, didUpdateAlgorithmConvergence convergence: NIAlgorithmConvergence, for object: NINearbyObject?) {
        guard ranging === session, object == nil || object?.discoveryToken == peerToken else { return }
        switch convergence.status {
        case .converged: cameraGuidance = "Camera assistance ready"
        case .notConverged(let reasons):
            if reasons.contains(.insufficientLighting) {
                cameraGuidance = "Camera assistance: move to better lighting."
            } else if reasons.contains(.insufficientSignalStrength) {
                cameraGuidance = "Camera assistance: bring the phones closer."
            } else {
                cameraGuidance = "Camera assistance: keep the other phone still and move this phone slowly side to side, then slightly up and down."
            }
        case .unknown: cameraGuidance = "Camera assistance is finding the other phone’s direction."
        @unknown default: cameraGuidance = "Waiting for camera assistance."
        }
        let state = String(describing: convergence.status)
        if convergenceState != state {
            convergenceState = state
            record("Convergence: \(state)")
        }
    }

    func sessionDidStartRunning(_ session: NISession) {
        guard ranging === session else { return }
        sessionState = "Running"
        record("NI started running")
    }
}

extension PeerRanger: ARSessionDelegate {
    func session(_ session: ARSession, cameraDidChangeTrackingState camera: ARCamera) {
        guard cameraEnabled else { return }
        cameraTracking = String(describing: camera.trackingState)
        record("AR tracking: \(cameraTracking)")
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        guard cameraEnabled else { return }
        cameraTracking = "Failed: \(error.localizedDescription)"
        cameraGuidance = "Camera tracking failed. Disconnect and reconnect to restart it."
        record(cameraTracking)
    }

    func sessionWasInterrupted(_ session: ARSession) {
        guard cameraEnabled else { return }
        cameraTracking = "Interrupted"
        measurement = nil
        record("AR interrupted")
    }

    func sessionInterruptionEnded(_ session: ARSession) {
        guard cameraEnabled else { return }
        cameraTracking = "Interruption ended; reconnect if tracking does not recover"
        record(cameraTracking)
    }
}
