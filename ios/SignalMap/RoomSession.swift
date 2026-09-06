import Foundation
import Combine
#if !ROOM_PROTOCOL_TEST
import NearbyInteraction
import UIKit
#endif

final class RoomSession: ObservableObject {
    let localID: String
    let boot = UUID().uuidString
    let transport: RoomTransport
    let ranging: RoomRanging
    let log: BenchLog
    let bridge = RoomBridge()
    let compass = HeadingSource()   // magnetic bearing, for the bridge only
    let gestureCamera = GestureCamera()
    @Published var displayName: String
    /// Optional "host:port" of an external visualizer / drone simulator on the
    /// same Wi-Fi. Empty disables the outbound telemetry stream. Persisted.
    @Published var simBridge: String = UserDefaults.standard.string(forKey: "room.simBridge") ?? "" {
        didSet {
            UserDefaults.standard.set(simBridge, forKey: "room.simBridge")
            bridge.configure(simBridge)
            if joined && sceneActive && !simBridge.isEmpty {
                compass.start(); gestureCamera.start()
            } else if simBridge.isEmpty {
                compass.stop(); gestureCamera.stop()
            }
        }
    }
    @Published private(set) var code = ""
    @Published private(set) var joined = false
    @Published private(set) var status = "Create a room or enter your group’s room code."
    @Published private(set) var networkStatus = "Offline"
    @Published private(set) var members: [RoomMember] = []
    @Published private(set) var running = false
    @Published private(set) var benchAvailable = false
    @Published private(set) var threePhoneMode = false
    @Published private(set) var twoPhoneMode = false
    var flatMode: Bool { threePhoneMode || twoPhoneMode }
    @Published private(set) var motionHeading = RelativeMotionHeading()
    @Published private(set) var paused = false
    @Published private(set) var cycle = 0
    @Published private(set) var currentRound = 0
    @Published private(set) var activePairText = "Waiting for the group"
    @Published private(set) var liveSamples = 0
    @Published private(set) var liveDistance: Double?
    @Published private(set) var reports: [AttemptReport] = []
    @Published private(set) var ranges: [String: ReceivedRange] = [:]
    @Published private(set) var clocks: [String: PeerClock] = [:]
    @Published private(set) var geometry: GeometrySolution?
    @Published private(set) var geometryStatus = "Positions need a complete cycle of ranges from at least four phones."
    @Published private(set) var geometryAge = 0.0
    @Published private(set) var diagnostics = ""
    @Published private(set) var logError: String?
    @Published private(set) var measurementSeconds = 4.0
    @Published private(set) var extended = true
    @Published private(set) var cycleTimes: [Double] = []
    @Published private(set) var lastCycleSummary = "No completed cycles"
    private var known: [String: RoomMember] = [:]
    private var gate = AttemptGate()
    private var timer: Timer?
    private var everStarted = false
    private var topology = ""
    private var topologyChanged = 0.0
    private var lastHeartbeat = 0.0
    private var lastClockProbe = 0.0
    private var lastSnapshot = 0.0
    private var epoch = UUID().uuidString
    private var controlRevision = 0
    private var receivedControlRevision = -1
    private var controlLeader = ""
    private var cycleStarted: TimeInterval?
    private var pending: [(pair: RangingPair, round: Int)] = []
    private var jobs: [String: Job] = [:]
    private var offered: [String: PairOffer] = [:]
    private var preparedCache: PreparedAttempt?
    private var reportCache: [String: AttemptReport] = [:]
    private var retries: [String: (failures: Int, after: TimeInterval)] = [:]
    private var geometryUpdated: TimeInterval?
    private var geometryCycle = -1
    private var nextDispatch = 0.0
    private var lastLocalRelease = 0.0
    private var sharedSnapshot: GroupRangeSnapshot?
    private var sentProbes: [String: (peer: String, sent: Double)] = [:]
    private var starting = false
    private var sceneActive = true
    private var cycleAttempts = 0
    private var cycleReliable = 0
    private var benchWhenReady = false

    private final class Job {
        let offer: PairOffer
        let created: Double
        var prepared: [String: Data] = [:]
        var reports: [String: AttemptReport] = [:]
        var begun = false
        var lastSent: Double
        init(offer: PairOffer, now: Double) { self.offer=offer; created=now; lastSent=now }
    }

    init(identity: String? = nil, logDirectory: URL? = nil) {
        let saved = UserDefaults.standard.string(forKey: "room.nodeID")
        let id = identity ?? saved.flatMap { UUID(uuidString: $0)?.uuidString } ?? UUID().uuidString
        localID = id
        if identity == nil { UserDefaults.standard.set(id, forKey: "room.nodeID") }
        displayName = UserDefaults.standard.string(forKey: "room.displayName") ?? "Phone \(id.prefix(4))"
        transport = RoomTransport(localID: id, boot: boot)
        ranging = RoomRanging(localID: id, boot: boot)
        log = BenchLog(directory: logDirectory)
        UIDevice.current.isBatteryMonitoringEnabled = true
        transport.onMessage = { [weak self] message, now in self?.receive(message, at: now) }
        transport.onPeers = { [weak self] _ in self?.heartbeat() }
        transport.onStatus = { [weak self] text in self?.networkStatus = text }
        transport.onEvent = { [weak self] text in self?.log.event("network", text) }
        ranging.onEvent = { [weak self] text in self?.log.event("uwb", text) }
        ranging.onPrepared = { [weak self] prepared in
            guard let self else { return }
            self.preparedCache = prepared
            self.emit("prepared", prepared)
        }
        ranging.onReport = { [weak self] report in
            guard let self else { return }
            self.gate.release(); self.preparedCache = nil
            self.lastLocalRelease = ProcessInfo.processInfo.systemUptime
            self.activePairText = "Last attempt: \(report.outcome)"
            self.emit("report", report)
            self.heartbeat()
        }
        ranging.onUpdate = { [weak self] count, distance in self?.liveSamples=count; self?.liveDistance=distance }
        log.onError = { [weak self] error in self?.logError=error }
        log.event("launch", "build \(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") ?? "?") · local gesture camera available")
        let launchArgs = ProcessInfo.processInfo.arguments
        if let i = launchArgs.firstIndex(of: "--sim-bridge"), launchArgs.indices.contains(i + 1) {
            simBridge = launchArgs[i + 1]
        }
        bridge.configure(simBridge)
        writeSnapshot()
    }

    var onlineIDs: [String] { Array(Set(members.map(\.id) + (joined ? [localID] : []))).sorted() }
    var leader: String? { onlineIDs.first }
    var isLeader: Bool { leader == localID }
    var participantCount: Int { onlineIDs.count }
    var targetCount: Int { twoPhoneMode ? 2 : threePhoneMode ? 3 : 5 }
    var roomCodeDisplay: String { RoomTransport.displayCode(code) }
    var allProfiles: [DeviceProfile] { ([profile()] + members.map(\.profile)).sorted { $0.id < $1.id } }
    var availableRanges: [ReceivedRange] {
        let now = ProcessInfo.processInfo.systemUptime
        return ranges.values.filter { now-$0.measured < 45 && now >= $0.measured-1 }.sorted { $0.report.pair.id < $1.report.pair.id }
    }
    func name(_ id: String) -> String { id == localID ? displayName : known[id]?.profile.name ?? "Phone \(id.prefix(4))" }
    func shortName(_ id: String) -> String { String(name(id).prefix(18)) }
    func aggregate(for id: String) -> AttemptAggregate { .init(reports: reports.filter { $0.device == id }) }
    func age(of range: ReceivedRange) -> Double { max(0,ProcessInfo.processInfo.systemUptime-range.measured) }

    func create() { join(RoomTransport.newCode(threePhone:threePhoneMode,twoPhone:twoPhoneMode)) }
    func join(_ enteredCode: String) {
        let clean = RoomTransport.normalized(enteredCode)
        guard RoomTransport.validCode(clean) else { status="Enter all 16 letters/numbers from the room code."; return }
        leave()
        let name = displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        displayName = name.isEmpty ? "Phone \(localID.prefix(4))" : String(name.prefix(24))
        UserDefaults.standard.set(displayName, forKey: "room.displayName")
        code=clean; joined=true; paused=false; sceneActive=true
        threePhoneMode=clean.first=="3"; twoPhoneMode=clean.first=="2"
        running=false; everStarted=false; benchAvailable=false
        known=[:]; members=[]; ranges=[:]; geometry=nil; geometryCycle = -1
        reports=[]; reportCache=[:]; cycle=0; cycleTimes=[]; retries=[:]
        topology=""; controlLeader=""; receivedControlRevision = -1
        status="Waiting for \(targetCount) phones. Share this room code with the group."
        transport.start(code: clean)
        bridge.configure(simBridge)
        if !simBridge.isEmpty { compass.start(); gestureCamera.start() }
        UIApplication.shared.isIdleTimerDisabled = true
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in self?.tick() }
        log.event("room-joined", transport.room)
        heartbeat(); writeSnapshot()
    }
    func leave() {
        starting = true
        ranging.cancel(reason: "Left the room")
        gate.release(); preparedCache=nil
        timer?.invalidate(); timer=nil
        transport.stop()
        bridge.stop()
        compass.stop()
        gestureCamera.stop()
        joined=false; running=false; members=[]; known=[:]; clocks=[:]
        jobs=[:]; offered=[:]; pending=[]; cycleStarted=nil
        geometry=nil; geometryUpdated=nil; liveDistance=nil; liveSamples=0
        sharedSnapshot=nil
        motionHeading.reset()
        code=""; status="Create a room or enter your group’s room code."; networkStatus="Offline"
        activePairText="Waiting for the group"
        UIApplication.shared.isIdleTimerDisabled = false
        starting = false
    }
    func background() {
        guard joined else { return }
        sceneActive=false
        ranging.cancel(reason: "App entered background")
        gate.release()
        transport.stop()
        compass.stop()
        gestureCamera.stop()
        timer?.invalidate(); timer=nil
        running=false; geometry=nil; geometryUpdated=nil
        log.event("background", "Local NI lease released; networking stopped")
        status="Paused while this app is in the background."
        UIApplication.shared.isIdleTimerDisabled=false
        writeSnapshot()
    }
    func foreground() {
        guard joined, !sceneActive else { return }
        sceneActive=true
        known=[:]; members=[]; jobs=[:]; pending=[]; topology=""; cycleStarted=nil
        ranges=[:]; geometry=nil; geometryCycle = -1
        transport.start(code: code)
        if !simBridge.isEmpty { compass.start(); gestureCamera.start() }
        UIApplication.shared.isIdleTimerDisabled=true
        timer=Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in self?.tick() }
        status="Rejoining the room…"
        heartbeat()
    }
    func requestPause() { request(.init(action: "pause")) }
    func requestResume() { request(.init(action: "resume")) }
    func requestBench() { request(.init(action: "bench", availableBench: true)) }
    func setThreePhoneMode(_ enabled: Bool) {
        if joined { request(.init(action:"settings",threePhoneMode:enabled,twoPhoneMode:false)) }
        else { threePhoneMode=enabled; twoPhoneMode=false }
    }
    func setTwoPhoneMode(_ enabled: Bool) {
        if joined { request(.init(action:"settings",threePhoneMode:false,twoPhoneMode:enabled)) }
        else { twoPhoneMode=enabled; threePhoneMode=false }
    }
    func benchmarkWhenPeersJoin() { benchWhenReady = true }
    func requestSettings(seconds: Double, extended: Bool) {
        request(.init(action: "settings", measurementSeconds: seconds, extended: extended))
    }
    private func request(_ control: RoomControl) { guard let leader else { return }; emit("request", control, target: leader) }

    private func profile() -> DeviceProfile {
        let capabilities = NISession.deviceCapabilities
        let thermal: String
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: thermal="Nominal"
        case .fair: thermal="Warm"
        case .serious: thermal="Hot"
        case .critical: thermal="Critical"
        @unknown default: thermal="Unknown"
        }
        let battery = UIDevice.current.batteryLevel
        return .init(id: localID, boot: boot, name: displayName, os: UIDevice.current.systemVersion,
                     build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?",
                     distance: capabilities.supportsPreciseDistanceMeasurement, direction: capabilities.supportsDirectionMeasurement,
                     extended: capabilities.supportsExtendedDistanceMeasurement, camera: capabilities.supportsCameraAssistance,
                     activeAttempt: gate.offer?.id, hasStarted: everStarted, paused: paused,
                     connected: transport.peers, thermal: thermal, battery: battery < 0 ? nil : Double(battery),
                     txBytes: transport.sentBytes, rxBytes: transport.receivedBytes, reconnects: transport.reconnects,threePhoneMode:threePhoneMode,twoPhoneMode:twoPhoneMode)
    }
    private func emit<T: Encodable>(_ kind: String, _ payload: T, target: String? = nil) {
        guard joined, let data = try? JSONEncoder().encode(payload) else { return }
        let message = WireMessage(room: transport.room, sender: localID, boot: boot, target: target, kind: kind,
                                  sent: ProcessInfo.processInfo.systemUptime, payload: data)
        if target == nil || target == localID { receive(message, at: ProcessInfo.processInfo.systemUptime) }
        transport.send(message)
    }
    private func heartbeat() {
        guard joined, !starting, sceneActive else { return }
        emit("profile", profile())
        if isLeader && controlRevision > 0 {
            emit("control",RoomControl(action:paused ? "pause" : everStarted ? "run" : "wait",revision:controlRevision,
                                       availableBench:benchAvailable,measurementSeconds:measurementSeconds,extended:extended,threePhoneMode:threePhoneMode,twoPhoneMode:twoPhoneMode))
        }
        if isLeader, let sharedSnapshot, ProcessInfo.processInfo.systemUptime-sharedSnapshot.created<8 {
            emit("snapshot",sharedSnapshot)
        }
        lastHeartbeat=ProcessInfo.processInfo.systemUptime
    }

    private func receive(_ message: WireMessage, at now: Double) {
        guard joined, message.version == 2 else { return }
        let decoder=JSONDecoder()
        switch message.kind {
        case "profile":
            guard message.sender != localID,
                  let p=try? decoder.decode(DeviceProfile.self, from: message.payload), p.id==message.sender, p.boot==message.boot,
                  p.name.count<=80, p.connected.count<=8 else { return }
            if let old=known[p.id], old.profile.boot != p.boot {
                ranges=ranges.filter { !$0.value.report.pair.contains(p.id) }
                clocks.removeValue(forKey:p.id)
                geometry=nil; geometryUpdated=nil
                log.event("peer-reboot", p.name)
            }
            known[p.id]=RoomMember(profile:p,lastSeen:now)
            refreshMembers(now)
            if p.hasStarted && p.threePhoneMode==threePhoneMode && (p.twoPhoneMode ?? false)==twoPhoneMode { everStarted=true }
        case "request":
            guard isLeader, onlineIDs.contains(message.sender), let request=try? decoder.decode(RoomControl.self,from:message.payload) else { return }
            handleRequest(request)
        case "control":
            guard message.sender==leader, let control=try? decoder.decode(RoomControl.self,from:message.payload) else { return }
            let owner=message.sender+message.boot
            if controlLeader != owner { controlLeader=owner; receivedControlRevision = -1 }
            guard control.revision > receivedControlRevision else { return }
            receivedControlRevision=control.revision
            applyControl(control)
        case "offer":
            guard message.sender==leader, let offer=try? decoder.decode(PairOffer.self,from:message.payload), offer.valid,
                  offer.leader==message.sender, offer.pair.members.allSatisfy({ onlineIDs.contains($0) }), !paused else { return }
            offered[offer.id]=offer
            if offered.count>150 { offered=offered.filter { $0.value.cycle >= cycle-3 } }
            cycle=max(cycle,offer.cycle); currentRound=offer.round+1
            running=true; everStarted=true
            guard offer.pair.contains(localID) else { return }
            if let cached=reportCache[offer.id+":"+localID] { emit("report",cached); return }
            if gate.offer?.id==offer.id {
                if let preparedCache { emit("prepared",preparedCache) }
                return
            }
            guard now-lastLocalRelease >= 0.25,
                  gate.reserve(offer,selfID:localID,boot:boot,leader:leader ?? "",now:now) else { return }
            liveSamples=0; liveDistance=nil
            activePairText="Ranging with \(name(offer.pair.other(localID)!))"
            log.event("offer", "\(offer.id) · cycle \(offer.cycle) round \(offer.round+1)")
            ranging.prepare(offer)
        case "prepared":
            guard isLeader, let ready=try? decoder.decode(PreparedAttempt.self,from:message.payload),
                  ready.token.count<32_768, let job=jobs[ready.attempt], job.offer.pair.contains(message.sender),
                  job.offer.boots[message.sender]==message.boot else { return }
            job.prepared[message.sender]=ready.token
            if job.prepared.count==2 && !job.begun {
                job.begun=true; job.lastSent=now
                emit("begin",BeginAttempt(offer:job.offer,tokens:job.prepared))
            }
        case "begin":
            guard message.sender==leader, let begin=try? decoder.decode(BeginAttempt.self,from:message.payload),
                  begin.offer==gate.offer, begin.tokens.count==2 else { return }
            ranging.begin(begin)
        case "report":
            guard let report=try? decoder.decode(AttemptReport.self,from:message.payload), report.valid,
                  report.device==message.sender, report.boot==message.boot,
                  report.id==report.attempt+":"+report.device,
                  message.sender==localID || known[message.sender]?.profile.boot==message.boot else { return }
            if isLeader, let job=jobs[report.attempt], job.offer.pair==report.pair, job.offer.boots[report.device]==report.boot { job.reports[report.device]=report }
            acceptReport(report, received:now)
        case "cancel":
            guard message.sender==leader, let id=try? decoder.decode(String.self,from:message.payload), gate.offer?.id==id else { return }
            ranging.cancel(reason:"Coordinator ended the attempt")
            gate.release()
        case "cycle":
            guard message.sender==leader, message.sender != localID,
                  let data=try? decoder.decode([String:Double].self,from:message.payload),
                  let seconds=data["seconds"], seconds.isFinite, seconds>=0,
                  let expected=data["expected"], let reliable=data["reliable"] else { return }
            lastCycleSummary="\(Int(reliable))/\(Int(expected)) pairs reliable · \(String(format:"%.2f",seconds)) s"
            if reliable==expected { cycleTimes.append(seconds); cycleTimes=Array(cycleTimes.suffix(100)) }
        case "snapshot":
            guard message.sender==leader, let snapshot=try? decoder.decode(GroupRangeSnapshot.self,from:message.payload),
                  snapshot.valid, snapshot.flat==flatMode, (snapshot.twoPhoneMode ?? false)==twoPhoneMode, Set(snapshot.positions.keys)==Set(onlineIDs), !paused else { return }
            if let sharedSnapshot, sharedSnapshot.epoch==snapshot.epoch && snapshot.cycle<=sharedSnapshot.cycle { return }
            let created:Double
            if message.sender==localID { created=snapshot.created }
            else if let clock=clocks[message.sender], now-clock.updated<10, clock.rtt<0.5 { created=snapshot.created-clock.offset }
            else { return }
            guard now-created >= -0.5, now-created<8 else { return }
            sharedSnapshot=snapshot
            geometry = .init(positions:snapshot.positions,rms:snapshot.rms,heightResolved:snapshot.thirdAxisResolved)
            geometryCycle=snapshot.cycle; geometryUpdated=created; geometryAge=max(0,now-created)
            if let local=snapshot.positions[localID] { motionHeading.update(position:local,at:created) }
            geometryStatus="Cycle \(snapshot.cycle) · \(String(format:"%.1f",snapshot.measurementSpan)) s span · fit \(String(format:"%.2f",snapshot.rms)) m · \(snapshot.twoPhoneMode == true ? "2-phone test · axis assumed, distance measured" : snapshot.flat ? "flat test · Z assumed equal" : snapshot.thirdAxisResolved ? "3D shape" : "nearly flat; Z uncertain")"
        case "ping":
            guard message.sender != localID, let probe=try? decoder.decode(ClockProbe.self,from:message.payload), probe.sent.isFinite else { return }
            emit("pong",ClockProbe(id:probe.id,sent:probe.sent,remoteReceived:now,remoteSent:ProcessInfo.processInfo.systemUptime),target:message.sender)
        case "pong":
            guard let probe=try? decoder.decode(ClockProbe.self,from:message.payload),
                  let pending=sentProbes.removeValue(forKey:probe.id), pending.peer==message.sender, pending.sent==probe.sent,
                  let t1=probe.remoteReceived, let t2=probe.remoteSent,
                  let estimate=PeerClock.estimate(t0:probe.sent,t1:t1,t2:t2,t3:now) else { return }
            if clocks[message.sender].map({ now-$0.updated>10 || estimate.rtt < $0.rtt*1.5 }) ?? true { clocks[message.sender]=estimate }
        default: break
        }
    }

    private func handleRequest(_ request: RoomControl) {
        switch request.action {
        case "pause": paused=true
        case "resume":
            guard participantCount>=targetCount || everStarted else { return }
            paused=false; everStarted=true
        case "bench":
            guard participantCount>=2 else { return }
            benchAvailable=true; paused=false; everStarted=true
        case "settings":
            if let seconds=request.measurementSeconds, seconds.isFinite { measurementSeconds=max(1,min(12,seconds)) }
            if let extended=request.extended { self.extended=extended }
            if request.threePhoneMode != nil || request.twoPhoneMode != nil {
                let two=request.twoPhoneMode ?? false, three=request.threePhoneMode ?? false
                if two != twoPhoneMode || three != threePhoneMode { changeMode(three,two:two) }
            }
        default: return
        }
        broadcastControl()
    }
    private func broadcastControl() {
        controlRevision += 1
        emit("control", RoomControl(action:paused ? "pause" : everStarted ? "run" : "wait",revision:controlRevision,
                                    availableBench:benchAvailable,measurementSeconds:measurementSeconds,extended:extended,threePhoneMode:threePhoneMode,twoPhoneMode:twoPhoneMode))
    }
    private func applyControl(_ control: RoomControl) {
        if control.threePhoneMode != nil || control.twoPhoneMode != nil {
            let two=control.twoPhoneMode ?? false, three=control.threePhoneMode ?? false
            if two != twoPhoneMode || three != threePhoneMode { changeMode(three,two:two) }
        }
        if let seconds=control.measurementSeconds, seconds.isFinite { measurementSeconds=max(1,min(12,seconds)) }
        if let extended=control.extended { self.extended=extended }
        if let bench=control.availableBench { benchAvailable=bench }
        paused=control.action=="pause"
        if control.action=="run" { everStarted=true }
        if control.action=="wait" { everStarted=false }
        if paused {
            ranging.cancel(reason:"Group paused")
            gate.release(); jobs=[:]; pending=[]; cycleStarted=nil
            running=false; geometry=nil; geometryUpdated=nil
            status="Group paused. Resume when everyone is ready."
        }
        log.event("group-control", "\(control.action) · revision \(control.revision)")
    }
    private func refreshMembers(_ now: Double) {
        members=known.values.filter { now-$0.lastSeen<6 }.sorted { $0.id<$1.id }
        known=known.filter { now-$0.value.lastSeen<120 }
    }
    private func changeMode(_ test:Bool, two:Bool = false) {
        ranging.cancel(reason:"Group test mode changed")
        gate.release(); jobs=[:]; pending=[]; cycleStarted=nil
        ranges=[:]; geometry=nil; geometryUpdated=nil; sharedSnapshot=nil; geometryCycle = -1
        motionHeading.reset(); threePhoneMode=test && !two; twoPhoneMode=two
        everStarted=false; benchAvailable=false; running=false; paused=false
        cycleTimes=[]; cycle=0; retries=[:]
        topologyChanged=ProcessInfo.processInfo.systemUptime; epoch=UUID().uuidString
        log.event("mode",two ? "2-phone assumed-axis test" : test ? "3-phone flat test" : "5-phone XYZ")
    }
    private func tick() {
        guard joined, sceneActive else { return }
        let now=ProcessInfo.processInfo.systemUptime
        refreshMembers(now)
        if benchWhenReady && participantCount >= 2 && !everStarted {
            benchWhenReady = false
            requestBench()
        }
        if now-lastHeartbeat>=1 { heartbeat() }
        if now-lastClockProbe>=2 {
            for id in onlineIDs where id != localID {
                let probe=ClockProbe(id:UUID().uuidString,sent:now)
                sentProbes[probe.id]=(id,now)
                emit("ping",probe,target:id)
            }
            sentProbes=sentProbes.filter { now-$0.value.sent<10 }
            lastClockProbe=now
        }
        if gate.expired(at:now) { ranging.cancel(reason:"Reservation watchdog expired"); gate.release() }
        let topologyKey=([profile()]+members.map(\.profile)).sorted { $0.id<$1.id }.map { $0.id+":"+$0.boot }.joined(separator:"|")
        if topologyKey != topology {
            topology=topologyKey; topologyChanged=now; epoch=UUID().uuidString
            ranging.cancel(reason:"Group membership changed; rescheduling")
            gate.release(); jobs=[:]; pending=[]; cycleStarted=nil
            geometry=nil; geometryUpdated=nil; geometryCycle = -1; sharedSnapshot=nil; ranges=[:]; motionHeading.reset()
            if isLeader { broadcastControl() }
            log.event("topology", "\(participantCount) members · leader \(leader?.prefix(4) ?? "none")")
        }
        if let offer=gate.offer, offer.leader != leader {
            ranging.cancel(reason:"Coordinator changed"); gate.release()
        }
        if participantCount>targetCount {
            status="This mode supports \(targetCount) phones. Choose a larger group mode or have extra phones leave."
            running=false
        } else if allProfiles.contains(where:{ !$0.distance || (extended && !$0.extended) }) {
            status="A phone lacks the selected UWB capability. Try normal range in Profiling or remove that phone."
            running=false
        } else if !paused && participantCount>=2 && (participantCount==targetCount || everStarted) && now-topologyChanged>1.5 {
            if isLeader && !everStarted { everStarted=true; broadcastControl() }
            if everStarted {
                running=true
                status=twoPhoneMode ? "2-phone test · real distance, assumed map axis" : threePhoneMode ? "3-phone flat test · rotating all three pairs" : benchAvailable && participantCount<5 ? "Benchmarking \(participantCount) phones · full group target is five" : "Coordinating \(participantCount) phones · UWB distance only"
                if isLeader { coordinate(now) }
            }
        } else if !paused {
            running=false
            status=everStarted ? "Waiting for at least two reachable phones to resume." : "\(participantCount)/\(targetCount) phones joined · starts automatically at \(targetCount)"
        }
        if let updated=geometryUpdated {
            geometryAge=now-updated
            if geometryAge>8 { geometry=nil; geometryStatus="Position estimate expired. Waiting for a fresh complete cycle." }
        }
        if now-lastSnapshot>=1 { updateGeometry(now); writeSnapshot(); lastSnapshot=now }
        if joined {
            bridge.send(id:localID,name:displayName,room:transport.room,cycle:cycle,
                        position:geometry?.positions[localID],heading:motionHeading,
                        compassDegrees:compass.compassDegrees,
                        gesture:gestureCamera.gesture,
                        gestureConfidence:gestureCamera.confidence,flat:flatMode,
                        geometryAge:geometryAge,members:participantCount,twoPhone:twoPhoneMode)
        }
    }

    private func coordinate(_ now: Double) {
        for job in Array(jobs.values) {
            let complete=job.reports.count==2
            let expired=now-job.created > job.offer.leaseSeconds+0.5
            if complete || expired {
                if expired {
                    emit("cancel",job.offer.id)
                    log.event("attempt-deadline", "\(job.offer.id) · received \(job.reports.count)/2 reports")
                }
                let success=job.reports.values.contains { $0.outcome=="reliable" }
                if success { cycleReliable += 1 }
                if success { retries[job.offer.pair.id]=(0,now) }
                else {
                    let failures=retries[job.offer.pair.id]?.failures ?? 0
                    retries[job.offer.pair.id]=(failures+1,now+min(15,pow(2,Double(failures))))
                }
                jobs.removeValue(forKey:job.offer.id)
                nextDispatch=max(nextDispatch,now+0.3)
            } else if now-job.lastSent>=0.8 {
                job.lastSent=now
                if job.begun { emit("begin",BeginAttempt(offer:job.offer,tokens:job.prepared)) }
                else { emit("offer",job.offer) }
            }
        }
        guard now>=nextDispatch else { return }
        if pending.isEmpty && jobs.isEmpty {
            if let started=cycleStarted {
                let elapsed=now-started
                let expected=onlineIDs.count*(onlineIDs.count-1)/2
                lastCycleSummary="\(cycleReliable)/\(expected) pairs reliable · \(String(format:"%.2f",elapsed)) s"
                if cycleReliable==expected { cycleTimes.append(elapsed); cycleTimes=Array(cycleTimes.suffix(100)) }
                log.event("cycle-complete", "\(cycle) · \(lastCycleSummary) · attempted \(cycleAttempts)")
                emit("cycle", ["number":Double(cycle),"seconds":elapsed,"expected":Double(expected),"reliable":Double(cycleReliable)])
            }
            cycle+=1; cycleStarted=now; cycleAttempts=0; cycleReliable=0
            pending=RoundRobin.rounds(onlineIDs).enumerated().flatMap { round,pairs in pairs.map { ($0,round) } }
        }
        var reserved=Set(jobs.values.flatMap { $0.offer.pair.members })
        var index=0
        while index<pending.count {
            let item=pending[index]
            if let retry=retries[item.pair.id], now<retry.after { pending.remove(at:index); continue }
            if item.pair.members.contains(where:{reserved.contains($0)}) { index+=1; continue }
            pending.remove(at:index)
            let boots=Dictionary(uniqueKeysWithValues:allProfiles.map { ($0.id,$0.boot) })
            let offer=PairOffer(id:UUID().uuidString,epoch:epoch,leader:localID,pair:item.pair,boots:boots,cycle:cycle,
                                round:item.round,extended:extended,measurementSeconds:measurementSeconds,
                                leaseSeconds:measurementSeconds+5,created:now)
            jobs[offer.id]=Job(offer:offer,now:now)
            cycleAttempts += 1
            reserved.formUnion(offer.pair.members)
            emit("offer",offer)
        }
    }

    private func acceptReport(_ report: AttemptReport, received now: Double) {
        guard reportCache[report.id]==nil else { return }
        reportCache[report.id]=report
        if reportCache.count>1000 {
            let keep=Set(reports.suffix(400).map(\.id))
            reportCache=reportCache.filter { keep.contains($0.key) }
        }
        reports.append(report); reports=Array(reports.suffix(500))
        log.report(report)
        if report.outcome=="reliable", let measuredAt=report.measuredAt, report.distance != nil {
            let measured:Double, uncertainty:Double
            if report.device==localID { measured=measuredAt; uncertainty=0 }
            else if let clock=clocks[report.device], now-clock.updated<10 {
                measured=measuredAt-clock.offset; uncertainty=clock.rtt/2
            } else {
                // Receipt time is usable for display, never for a synchronized geometry solve.
                measured=now; uncertainty=Double.infinity
            }
            let key=report.pair.id
            if ranges[key].map({ report.cycle>$0.report.cycle || (report.cycle==$0.report.cycle && measured>$0.measured) }) ?? true {
                ranges[key] = .init(report:report,received:now,measured:measured,clockUncertainty:uncertainty)
            }
        }
    }
    private func updateGeometry(_ now: Double) {
        guard onlineIDs.count >= (twoPhoneMode ? 2 : threePhoneMode ? 3 : 4), !paused else {
            geometry=nil
            geometryStatus=twoPhoneMode ? "Waiting for two phones and one measured distance." : threePhoneMode ? "Waiting for three phones and all three pair distances." : "\(participantCount) phones joined. Four or five phones and a complete range graph are needed for XYZ."
            return
        }
        let ids=onlineIDs
        let expected=ids.count*(ids.count-1)/2
        let edges=availableRanges.filter { $0.report.pair.members.allSatisfy { ids.contains($0) } }
        guard edges.count==expected else {
            geometryStatus="\(edges.count)/\(expected) recent pair distances · missing edges stay unresolved"
            return
        }
        guard Set(edges.map { $0.report.cycle }).count==1, let currentCycle=edges.first?.report.cycle else {
            geometryStatus="Collecting one complete cycle · distances from different cycles are not combined"
            return
        }
        guard currentCycle != geometryCycle else { return }
        guard isLeader else { geometryStatus="Complete ranges received · waiting for the shared coordinate solution"; return }
        let times=edges.map(\.measured), span=(times.max() ?? now)-(times.min() ?? now)
        guard edges.allSatisfy({ $0.clockUncertainty<0.25 }), span<=15, now-(times.min() ?? 0)<20 else {
            geometryStatus="Range cycle too spread out in time, or clocks not synchronized. Positions unresolved."
            return
        }
        let distances=Dictionary(uniqueKeysWithValues:edges.compactMap { edge in edge.report.distance.map { (edge.report.pair,$0) } })
        guard let solution=RangeGeometry.solve(ids:ids,distances:distances,previous:geometry?.positions ?? [:],flat:flatMode,line:twoPhoneMode) else {
            geometryStatus="Ranges do not fit a reliable shape. Hold the phones still for a fresh cycle."
            return
        }
        emit("snapshot",GroupRangeSnapshot(cycle:currentCycle,epoch:epoch,created:now,positions:solution.positions,
                                            rms:solution.rms,thirdAxisResolved:solution.heightResolved,measurementSpan:span,flat:flatMode,twoPhoneMode:twoPhoneMode))
        log.event("geometry",geometryStatus)
    }

    private func writeSnapshot() {
        let now=ProcessInfo.processInfo.systemUptime
        let p=profile()
        let lines=allProfiles.map { p -> String in
            let s=aggregate(for:p.id)
            return "\(p.name) [\(p.id.prefix(4))] build=\(p.build) OS=\(p.os) UWB=\(p.distance) extended=\(p.extended) directDirection=\(p.direction) thermal=\(p.thermal) attempts=\(s.reports.count) reliable=\(s.succeeded) firstP50=\(Statistics.milliseconds(s.firstP50)) firstP95=\(Statistics.milliseconds(s.firstP95)) reliableP95=\(Statistics.milliseconds(s.reliableP95)) totalP95=\(Statistics.milliseconds(s.totalP95)) RTT=\(Statistics.milliseconds(clocks[p.id].map { $0.rtt*1000 })) tx=\(p.txBytes) rx=\(p.rxBytes) reconnects=\(p.reconnects)"
        }
        let measurementLines=allProfiles.compactMap { p -> String? in
            guard let r=aggregate(for:p.id).reports.last, let s=r.distanceStatistics else { return nil }
            return "\(p.name) cycle=\(r.cycle) pair=\(name(r.pair.a))↔\(name(r.pair.b)) n=\(s.count) first=\(s.first)m last=\(s.last)m mean=\(s.mean)m median=\(s.median)m sampleSD=\(s.standardDeviation.map(String.init(describing:)) ?? "undefined")m min=\(s.minimum)m max=\(s.maximum)m firstMinusMedian=\(s.firstMinusMedian)m firstZ=\(s.firstZScore.map(String.init(describing:)) ?? "undefined")"
        }
        let edgeLines=availableRanges.map { edge in "\(name(edge.report.pair.a)) ↔ \(name(edge.report.pair.b)): \(String(format:"%.3f",edge.report.distance ?? 0)) m · age \(String(format:"%.1f",now-edge.measured)) s · cycle \(edge.report.cycle)" }
        diagnostics="""
        Signal Map room diagnostics · build \(p.build)
        Updated: \(Date().ISO8601Format())
        Run: \(log.runID)
        Room fingerprint: \(transport.room)
        Local: \(displayName) [\(localID)]
        Gesture camera: \(gestureCamera.status); label=\(gestureCamera.gesture); confidence=\(String(format:"%.2f",gestureCamera.confidence)); frames remain on-device
        NI camera assistance: not used; no AR session
        Transport: Network framework, peer-to-peer enabled, AES-GCM room encryption, no cellular
        Members: \(participantCount)/\(targetCount); direct links: \(transport.peers.count)
        Coordinator: \(leader.map(name) ?? "none")
        Mode: \(twoPhoneMode ? "two-phone assumed-axis test" : threePhoneMode ? "three-phone flat test" : benchAvailable ? "available-device benchmark" : "five-person session")
        Status: \(status)
        NI: \(ranging.state); \(ranging.lastFields)
        Cycle: \(cycle); round priority: \(currentRound); active jobs: \(jobs.count)
        Extended: \(extended); measurement deadline: \(measurementSeconds) s
        Full cycle p50: \(Statistics.percentile(cycleTimes,0.5).map { String(format:"%.2f s",$0) } ?? "—")
        Full cycle p95: \(Statistics.percentile(cycleTimes,0.95).map { String(format:"%.2f s",$0) } ?? "—")
        Network: \(networkStatus)
        Rejected packets: \(transport.rejectedPackets); queue drops: \(transport.queueDrops)
        Geometry: \(geometryStatus)
        Relative motion heading: \(String(format:"%.1f",motionHeading.angle*180/Double.pi)) degrees; speed=\(String(format:"%.2f",motionHeading.speed)) m/s; moving=\(motionHeading.moving); initialized=\(motionHeading.available)
        Coordinates are arbitrary group axes, NOT camera-facing or gravity-aligned.
        Sequential range reconstruction assumes the group remains approximately stationary during a cycle.
        "Reliable" means 4+ valid readings; no minimum time span or deviation limit. It is not an accuracy or consistency guarantee.
        Latency percentiles use the attempts that reached each milestone. Failures remain in attempt counts and total duration.

        DEVICE PROFILES
        \(lines.joined(separator:"\n"))

        LATEST ATTEMPT DISTANCE STATISTICS (all readings; sample SD uses n-1; z includes first)
        \(measurementLines.joined(separator:"\n"))

        PAIR RANGES
        \(edgeLines.joined(separator:"\n"))

        RECENT EVENTS
        \(log.recent.suffix(35).joined(separator:"\n"))
        """
        log.snapshot(diagnostics)
    }
}
