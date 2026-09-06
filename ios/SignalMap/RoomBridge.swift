import Foundation
import Network

/// One-way UDP telemetry to an external visualizer / drone simulator on the same
/// Wi-Fi. It sends only this phone's own group-frame position and relative-motion
/// heading, a few times a second, and never receives anything. It is the single
/// outbound path that leaves the room mesh; it carries no room code, no NI
/// discovery tokens, and no ranging data from other phones.
///
/// Off unless a host is configured (a launch argument `--sim-bridge host:port`,
/// or the "Visualizer host" field in the lobby). The datagram is small JSON:
///
///     {"v":1,"id":...,"name":...,"room":...,"t":...,"cycle":...,
///      "pos":[x,y],"z":...,"heading":...,"moving":...,"speed":...,
///      "headingReady":...,"gesture":"None","flat":...}
///
/// consumed by `src/phone_feed.py` on the simulator side.
final class RoomBridge {
    struct Sample: Codable {
        var v = 1
        var id: String
        var name: String
        var room: String
        var t: Double            // sender ProcessInfo.systemUptime, seconds
        var cycle: Int
        var pos: [Double]        // [x, y] metres, arbitrary shared group frame
        var z: Double
        var heading: Double      // radians in the same frame; held when stationary
        var moving: Bool
        var speed: Double
        var headingReady: Bool
        var gesture: String      // reserved; "None" until phones classify on-device
        var flat: Bool
    }

    /// Minimum gap between datagrams (~15 Hz). `tick()` runs at 10 Hz, so in
    /// practice every tick sends.
    var minInterval = 0.066

    private let queue = DispatchQueue(label: "room.bridge")
    private var connection: NWConnection?
    private var endpointText = ""
    private var lastSend = 0.0
    private let encoder = JSONEncoder()
    private(set) var lastError: String?

    /// `"host:port"` or `"host"` (port defaults to 9870). An empty or unchanged
    /// string is a no-op; a new value tears down any existing link first.
    func configure(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        queue.async { [weak self] in
            guard let self, trimmed != self.endpointText else { return }
            self.endpointText = trimmed
            self.connection?.cancel()
            self.connection = nil
            self.lastError = nil
            guard !trimmed.isEmpty else { return }

            let host: String
            let portText: String
            if let colon = trimmed.lastIndex(of: ":") {
                host = String(trimmed[..<colon])
                portText = String(trimmed[trimmed.index(after: colon)...])
            } else {
                host = trimmed
                portText = "9870"
            }
            guard !host.isEmpty,
                  let raw = UInt16(portText), let port = NWEndpoint.Port(rawValue: raw) else {
                self.lastError = "expected host or host:port"
                return
            }
            let connection = NWConnection(host: NWEndpoint.Host(host), port: port, using: .udp)
            connection.stateUpdateHandler = { [weak self] state in
                if case .failed(let error) = state { self?.lastError = "\(error)" }
            }
            connection.start(queue: self.queue)
            self.connection = connection
        }
    }

    func send(id: String, name: String, room: String, cycle: Int,
              position: Vector3, heading: RelativeMotionHeading, flat: Bool) {
        queue.async { [weak self] in
            guard let self, let connection = self.connection, position.finite else { return }
            let now = ProcessInfo.processInfo.systemUptime
            guard now - self.lastSend >= self.minInterval else { return }
            self.lastSend = now
            let sample = Sample(id: id, name: String(name.prefix(24)), room: room, t: now,
                                cycle: cycle, pos: [position.x, position.y], z: position.z,
                                heading: heading.angle, moving: heading.moving,
                                speed: heading.speed, headingReady: heading.available,
                                gesture: "None", flat: flat)
            guard let data = try? self.encoder.encode(sample) else { return }
            connection.send(content: data, completion: .idempotent)
        }
    }

    func stop() {
        queue.async { [weak self] in
            self?.connection?.cancel()
            self?.connection = nil
            self?.endpointText = ""
        }
    }
}
