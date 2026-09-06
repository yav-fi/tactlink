import Foundation
import Network
import CryptoKit

/// Router-free Bonjour discovery and TCP connections with room-key authenticated encryption.
/// Every device listens; the lower node ID dials, avoiding duplicate symmetric connections.
final class RoomTransport {
#if ROOM_PROTOCOL_TEST
    static var testShouldSend: ((WireMessage, String?) -> Bool)?
#endif
    static let serviceType = "_signal-room._tcp"
    static let alphabet = Array("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    static func newCode(threePhone: Bool = false) -> String { (threePhone ? "3" : "5") + String((0..<15).map { _ in alphabet.randomElement()! }) }
    static func normalized(_ code: String) -> String { code.uppercased().filter { !$0.isWhitespace && $0 != "-" } }
    static func validCode(_ code: String) -> Bool { code.count == 16 && code.allSatisfy { alphabet.contains($0) } }
    static func displayCode(_ code: String) -> String {
        stride(from: 0, to: code.count, by: 4).map { String(Array(code)[$0..<min($0+4,code.count)]) }.joined(separator: " ")
    }

    let localID: String
    let boot: String
    private(set) var room = ""
    private var key: SymmetricKey?
    private var listener: NWListener?
    private var browser: NWBrowser?
    private var timer: Timer?
    private var endpoints: [String: NWEndpoint] = [:]
    private var links: [UUID: Link] = [:]
    private var retryAt: [String: TimeInterval] = [:]
    private var failures: [String: Int] = [:]
    private var replay = ReplayWindow()
    private var listenerRetry: TimeInterval = 0
    private(set) var sentBytes = 0
    private(set) var receivedBytes = 0
    private(set) var reconnects = 0
    private(set) var rejectedPackets = 0
    private(set) var queueDrops = 0
    var onMessage: ((WireMessage, TimeInterval) -> Void)?
    var onPeers: (([String]) -> Void)?
    var onEvent: ((String) -> Void)?
    var onStatus: ((String) -> Void)?
    var peers: [String] { links.values.compactMap { $0.authenticated ? $0.peer : nil }.sorted() }
    var active: Bool { key != nil }
    private let maxFrame = 131_072
    private let aad = Data("SignalMap.Room.v2".utf8)

    private final class Link {
        let id = UUID()
        let connection: NWConnection
        let outgoing: Bool
        var peer: String?
        var authenticated = false
        var ready = false
        var queued = 0
        let created = ProcessInfo.processInfo.systemUptime
        init(_ connection: NWConnection, outgoing: Bool, peer: String?) {
            self.connection = connection; self.outgoing = outgoing; self.peer = peer
        }
    }

    init(localID: String, boot: String) { self.localID = localID; self.boot = boot }
    func start(code: String) {
        stop()
        let digest = SHA256.hash(data: Data(code.utf8))
        key = SymmetricKey(data: Data(digest))
        room = digest.prefix(6).map { String(format: "%02x", $0) }.joined()
        sentBytes = 0; receivedBytes = 0; reconnects = 0; rejectedPackets = 0; queueDrops = 0
        listenerRetry = 0
        startListener(); startBrowser()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.maintain() }
    }
    func stop() {
        timer?.invalidate(); timer = nil
        browser?.cancel(); browser = nil
        listener?.cancel(); listener = nil
        for link in links.values { link.connection.stateUpdateHandler = nil; link.connection.cancel() }
        links.removeAll(); endpoints.removeAll(); retryAt.removeAll(); failures.removeAll()
        key = nil; replay = ReplayWindow()
        onPeers?([])
    }
    private func parameters() -> NWParameters {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true
        tcp.enableKeepalive = true
        tcp.keepaliveIdle = 5
        tcp.keepaliveInterval = 2
        tcp.keepaliveCount = 3
        let parameters = NWParameters(tls: nil, tcp: tcp)
        parameters.includePeerToPeer = true
        parameters.prohibitedInterfaceTypes = [.cellular]
        return parameters
    }
    private func startListener() {
        guard active, listener == nil else { return }
        do {
            let listener = try NWListener(using: parameters())
            self.listener = listener
            listener.service = NWListener.Service(name: room + "-" + localID, type: Self.serviceType)
            listener.newConnectionHandler = { [weak self] connection in
                guard let self, self.active, self.links.count < 8 else { connection.cancel(); return }
                self.attach(Link(connection, outgoing: false, peer: nil))
            }
            listener.stateUpdateHandler = { [weak self, weak listener] state in
                guard let self, let listener, self.listener === listener else { return }
                switch state {
                case .ready: self.onStatus?("Discovering nearby room members · no router needed")
                case .waiting(let error): self.onStatus?("Network waiting: \(error.localizedDescription). Check Local Network permission and Wi-Fi.")
                case .failed(let error):
                    self.onEvent?("Listener failed: \(error.localizedDescription)")
                    self.listener?.cancel(); self.listener = nil
                    self.listenerRetry = ProcessInfo.processInfo.systemUptime + 3
                default: break
                }
            }
            listener.start(queue: .main)
        } catch { onStatus?("Cannot start nearby discovery: \(error.localizedDescription)") }
    }
    private func startBrowser() {
        guard active, browser == nil, peers.count < 4 else { return }
        let browser = NWBrowser(for: .bonjour(type: Self.serviceType, domain: nil), using: parameters())
        self.browser = browser
        browser.browseResultsChangedHandler = { [weak self, weak browser] results, _ in
            guard let self, let browser, self.browser === browser else { return }
            var discovered: [String: NWEndpoint] = [:]
            for result in results {
                if case let .service(name, _, _, _) = result.endpoint,
                   name.hasPrefix(self.room + "-") {
                    let id = String(name.dropFirst(self.room.count + 1))
                    if UUID(uuidString: id) != nil, id != self.localID { discovered[id] = result.endpoint }
                }
            }
            self.endpoints = discovered
            self.maintain()
        }
        browser.stateUpdateHandler = { [weak self, weak browser] state in
            guard let self, let browser, self.browser === browser else { return }
            switch state {
            case .waiting(let error): self.onStatus?("Discovery waiting: \(error.localizedDescription). Allow Local Network in Settings → Apps → Signal Map.")
            case .failed(let error):
                self.onEvent?("Browser failed: \(error.localizedDescription)")
                self.browser?.cancel(); self.browser = nil
            default: break
            }
        }
        browser.start(queue: .main)
    }
    private func maintain() {
        guard active else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if listener == nil && now >= listenerRetry { startListener() }
        if peers.count < 4 { startBrowser() }
        else { browser?.cancel(); browser = nil }
        for link in Array(links.values) where !link.authenticated && now-link.created > 12 { close(link, reason: "Handshake timeout") }
        for (id,endpoint) in endpoints.sorted(by: { $0.key < $1.key }) {
            guard localID < id, links.count < 6, !links.values.contains(where: { $0.peer == id }), now >= retryAt[id, default: 0] else { continue }
            if failures[id, default: 0] > 0 { reconnects += 1 }
            attach(Link(NWConnection(to: endpoint, using: parameters()), outgoing: true, peer: id))
        }
    }
    private func attach(_ link: Link) {
        links[link.id] = link
        link.connection.stateUpdateHandler = { [weak self, weak link] state in
            guard let self, let link, self.links[link.id] != nil else { return }
            switch state {
            case .ready:
                link.ready = true
                let hello = WireMessage(room: self.room, sender: self.localID, boot: self.boot, kind: "hello", sent: ProcessInfo.processInfo.systemUptime, payload: Data())
                self.write(hello, to: link)
                self.readHeader(link)
            case .failed(let error): self.close(link, reason: error.localizedDescription)
            case .cancelled: self.close(link, reason: "Connection closed")
            default: break
            }
        }
        link.connection.start(queue: .main)
    }
    private func close(_ link: Link, reason: String) {
        guard links.removeValue(forKey: link.id) != nil else { return }
        link.connection.stateUpdateHandler = nil
        link.connection.cancel()
        if let id = link.peer {
            failures[id, default: 0] += 1
            retryAt[id] = ProcessInfo.processInfo.systemUptime + min(8, pow(1.5, Double(failures[id, default: 1])))
            if link.authenticated { onEvent?("Link \(id.prefix(4)) lost: \(reason)") }
        }
        onPeers?(peers)
    }
    func send(_ message: WireMessage) {
        guard active else { return }
        _ = replay.insert(message.id)
        for link in links.values where link.authenticated { write(message, to: link) }
    }
    private func write(_ message: WireMessage, to link: Link) {
#if ROOM_PROTOCOL_TEST
        if Self.testShouldSend?(message,link.peer) == false { return }
#endif
        guard let key, link.ready else { return }
        do {
            let plain = try JSONEncoder().encode(message)
            let box = try AES.GCM.seal(plain, using: key, authenticating: aad)
            guard let encrypted = box.combined, encrypted.count <= maxFrame else { queueDrops += 1; return }
            guard link.queued + encrypted.count <= 512_000 else { queueDrops += 1; close(link, reason: "Send backlog exceeded"); return }
            var length = UInt32(encrypted.count).bigEndian
            var frame = withUnsafeBytes(of: &length) { Data($0) }
            frame.append(encrypted)
            let count = frame.count
            link.queued += count
            link.connection.send(content: frame, completion: .contentProcessed { [weak self, weak link] error in
                guard let self, let link else { return }
                link.queued = max(0,link.queued-count)
                if let error { self.close(link, reason: error.localizedDescription) }
                else { self.sentBytes += count }
            })
        } catch { onEvent?("Packet encoding failed: \(error.localizedDescription)") }
    }
    private func readHeader(_ link: Link) {
        link.connection.receive(minimumIncompleteLength: 4, maximumLength: 4) { [weak self, weak link] data, _, ended, error in
            guard let self, let link, self.links[link.id] != nil else { return }
            guard error == nil, let data, data.count == 4 else { self.close(link, reason: "Header stream ended"); return }
            let length = data.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
            guard length >= 28 && length <= self.maxFrame else { self.rejectedPackets += 1; self.close(link, reason: "Invalid frame size"); return }
            self.readBody(link, length: Int(length))
        }
    }
    private func readBody(_ link: Link, length: Int) {
        link.connection.receive(minimumIncompleteLength: length, maximumLength: length) { [weak self, weak link] data, _, _, error in
            guard let self, let link, self.links[link.id] != nil else { return }
            guard error == nil, let data, data.count == length, let key = self.key else { self.close(link, reason: "Body stream ended"); return }
            self.receivedBytes += data.count + 4
            do {
                let plain = try AES.GCM.open(AES.GCM.SealedBox(combined: data), using: key, authenticating: self.aad)
                var message = try JSONDecoder().decode(WireMessage.self, from: plain)
                guard message.version == 2, message.room == self.room, UUID(uuidString: message.sender) != nil,
                      UUID(uuidString: message.boot) != nil, message.sent.isFinite,
                      message.hops >= 0 && message.hops <= 6 else { throw TransportError.invalid }
                if !link.authenticated {
                    guard message.kind == "hello", message.sender != self.localID,
                          link.peer == nil || link.peer == message.sender,
                          link.outgoing ? self.localID < message.sender : message.sender < self.localID,
                          !self.links.values.contains(where: { $0.id != link.id && $0.authenticated && $0.peer == message.sender }) else { throw TransportError.invalid }
                    link.peer = message.sender; link.authenticated = true
                    self.failures[message.sender] = 0
                    self.onEvent?("Authenticated link \(message.sender.prefix(4))")
                    self.onPeers?(self.peers)
                } else if message.kind != "hello", message.sender != self.localID, self.replay.insert(message.id) {
                    let now = ProcessInfo.processInfo.systemUptime
                    if message.target == nil || message.target == self.localID { self.onMessage?(message, now) }
                    if message.hops < 6 {
                        message.hops += 1
                        for other in self.links.values where other.id != link.id && other.authenticated { self.write(message, to: other) }
                    }
                }
            } catch {
                self.rejectedPackets += 1
                self.close(link, reason: "Room authentication or packet validation failed")
                return
            }
            self.readHeader(link)
        }
    }
    private enum TransportError: Error { case invalid }
}
