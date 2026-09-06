import SwiftUI

private let roomMint = Color(red: 0.55, green: 0.95, blue: 0.76)
private let roomBackground = Color(red: 0.025, green: 0.045, blue: 0.045)
private let roomMuted = Color(red: 0.55, green: 0.65, blue: 0.63)
private let roomColors: [Color] = [roomMint, .cyan, .orange, .purple, .pink]

struct RoomView: View {
    @ObservedObject var room: RoomSession
    @State private var enteredCode = ""
    @State private var showProfile = false
    @State private var showCode = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack {
                    HStack(spacing: 8) {
                        Image(systemName: "circle.hexagongrid.fill").foregroundStyle(roomMint)
                        Text("SIGNAL MAP").tracking(2)
                    }.font(.system(size: 12, weight: .semibold, design: .monospaced))
                    Spacer()
                    Text("UWB ONLY").font(.system(size: 9, weight: .medium, design: .monospaced))
                        .padding(.horizontal, 9).padding(.vertical, 6)
                        .background(roomMint.opacity(0.12), in: Capsule()).foregroundStyle(roomMint)
                }.padding(.top, 12)
                if room.joined { sessionContent } else { lobby }
                Text("No GPS · Camera gestures stay on this phone")
                    .font(.system(size: 10, design: .monospaced)).foregroundStyle(roomMuted)
                    .frame(maxWidth: .infinity).padding(.bottom, 14)
            }.padding(.horizontal, 22)
        }
        .background(roomBackground).tint(roomMint)
        .sheet(isPresented: $showProfile) { RoomProfiler(room: room) }
    }
    private var lobby: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 12) {
                Text("A shared sense\nof where you are.")
                    .font(.system(size: 34, weight: .medium, design: .rounded)).tracking(-1)
                Text(room.twoPhoneMode ? "Test gestures with two phones. Distance is measured; map direction is assumed." : room.threePhoneMode ? "Join three phones. Rotate every pair. Test a flat map." : "Join five phones. Measure between pairs. See the group around you.")
                    .font(.subheadline).foregroundStyle(roomMuted).lineSpacing(4)
            }.padding(.top, 12)
            HStack(spacing: 12) {
                ForEach(0..<room.targetCount) { i in
                    VStack(spacing: 8) {
                        Image(systemName: "iphone").font(.system(size: 24))
                            .frame(maxWidth: .infinity).frame(height: 58)
                            .background(roomColors[i].opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
                        Circle().fill(roomColors[i]).frame(width: 5, height: 5)
                    }.foregroundStyle(roomColors[i])
                }
            }.padding(.vertical, 8)
            VStack(alignment: .leading, spacing: 9) {
                eyebrow("YOUR NAME")
                TextField("Name shown to the group", text: $room.displayName)
                    .textContentType(.nickname).submitLabel(.done)
                    .padding(14).background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
            }
            VStack(alignment: .leading, spacing: 9) {
                eyebrow("VISUALIZER HOST (OPTIONAL)")
                TextField("192.168.1.50:9870", text: $room.simBridge)
                    .font(.system(size: 15, design: .monospaced))
                    .keyboardType(.numbersAndPunctuation).autocorrectionDisabled()
                    .textInputAutocapitalization(.never).submitLabel(.done)
                    .padding(14).background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                Text("Streams only your own position and heading to a drone simulator on the same Wi-Fi. Leave blank to keep it off.")
                    .font(.caption2).foregroundStyle(roomMuted).lineSpacing(2)
            }
            testModeToggle
            Button { room.create() } label: {
                HStack { Text("Create a room"); Spacer(); Image(systemName: "arrow.up.right") }
                    .font(.headline).padding(17).frame(maxWidth: .infinity)
            }.buttonStyle(.borderedProminent).foregroundStyle(roomBackground)
            VStack(alignment: .leading, spacing: 12) {
                eyebrow("HAVE A ROOM CODE?")
                TextField("ABCD EFGH JKLM NPQR", text: $enteredCode)
                    .font(.system(size: 17, design: .monospaced)).textInputAutocapitalization(.characters)
                    .autocorrectionDisabled().submitLabel(.join)
                    .padding(14).background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                    .onSubmit { room.join(enteredCode) }
                Button("Join nearby room") { room.join(enteredCode) }
                    .font(.headline).frame(maxWidth: .infinity).padding(10).buttonStyle(.bordered)
            }
            Text(room.status).font(.caption).foregroundStyle(roomMuted)
            VStack(alignment: .leading, spacing: 8) {
                Label("Phones connect directly", systemImage: "wifi")
                    .font(.subheadline.weight(.medium)).foregroundStyle(.white)
                Text("Keep Wi-Fi enabled and allow Local Network and Nearby Interactions. A router or internet connection is not needed. Keep this app open on every phone.")
                    .font(.caption).foregroundStyle(roomMuted).lineSpacing(3)
            }.padding(16).background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 16))
        }
    }
    private var sessionContent: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 7) {
                    Text(room.running ? "Your group, nearby." : room.paused ? "Room paused." : "Gather the group.")
                        .font(.system(size: 28, weight: .medium, design: .rounded)).tracking(-0.7)
                    Text(room.status).font(.caption).foregroundStyle(roomMuted).lineSpacing(3)
                }
                Spacer(minLength: 8)
                Text("\(room.participantCount)/\(room.targetCount)")
                    .font(.system(size: 25, weight: .medium, design: .rounded)).monospacedDigit().foregroundStyle(roomMint)
            }
            DisclosureGroup(isExpanded: $showCode) {
                VStack(spacing: 12) {
                    Text(room.roomCodeDisplay).font(.system(size: 21, weight: .semibold, design: .monospaced)).textSelection(.enabled)
                    HStack {
                        Button("Copy code", systemImage: "doc.on.doc") { UIPasteboard.general.string = room.code }
                        Spacer()
                        Button("Leave room", role: .destructive) { room.leave() }
                    }.font(.caption)
                    Text("Share this code only with your group. Anyone with it can join and read the room’s updates.")
                        .font(.caption2).foregroundStyle(roomMuted)
                }.padding(.vertical, 12)
            } label: { Label("Room code & members", systemImage: "person.3.sequence.fill").font(.subheadline.weight(.medium)) }
                .padding(14).background(.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 14))
            testModeToggle
            HStack(alignment: .top, spacing: 8) {
                ForEach(Array(room.allProfiles.enumerated()), id: \.element.id) { index, p in
                    VStack(spacing: 6) {
                        ZStack(alignment: .bottomTrailing) {
                            Text(String(p.name.prefix(1)).uppercased()).font(.headline)
                                .frame(width: 38, height: 38).background(roomColors[index%5].opacity(0.14), in: Circle())
                            Circle().fill(p.activeAttempt != nil ? roomMint : roomMuted).frame(width: 9, height: 9)
                        }
                        Text(p.id == room.localID ? "YOU" : String(p.name.prefix(9)))
                            .font(.system(size: 9, design: .monospaced)).lineLimit(1)
                    }.foregroundStyle(p.id == room.localID ? .white : roomColors[index%5]).frame(maxWidth: .infinity)
                }
                if room.participantCount < room.targetCount {
                    ForEach(room.participantCount..<room.targetCount, id: \.self) { _ in
                        VStack(spacing: 6) {
                            Image(systemName: "plus").frame(width: 38, height: 38)
                                .background(.white.opacity(0.03), in: Circle())
                            Text("JOIN").font(.system(size: 9, design: .monospaced))
                        }.foregroundStyle(roomMuted.opacity(0.5)).frame(maxWidth: .infinity)
                    }
                }
            }
            RoomMap(room: room)
            GestureStatus(camera: room.gestureCamera)
            HStack(spacing: 12) {
                stat("CYCLE", String(room.cycle))
                stat("PAIR RANGES", "\(room.availableRanges.count)/\(max(1,room.participantCount*(room.participantCount-1)/2))")
                stat("DATA LINKS", String(room.transport.peers.count))
            }
            HStack(spacing: 9) {
                Circle().fill(room.running ? roomMint : roomMuted).frame(width: 6, height: 6)
                VStack(alignment: .leading, spacing: 4) {
                    Text(room.activePairText).font(.caption.weight(.medium))
                    if room.liveSamples > 0 {
                        Text("\(room.liveSamples) readings · \(room.liveDistance.map { String(format: "%.2f m", $0) } ?? "—")")
                            .font(.system(size: 10, design: .monospaced)).foregroundStyle(roomMuted)
                    }
                }
                Spacer()
                if room.running { Button("Pause") { room.requestPause() }.font(.caption) }
                else if room.paused { Button("Resume") { room.requestResume() }.font(.caption) }
            }.padding(14).background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 14))
            Button { showProfile = true } label: {
                HStack {
                    Label("Profiling & diagnostics", systemImage: "waveform.path.ecg")
                    Spacer(); Image(systemName: "arrow.up.right")
                }.font(.subheadline.weight(.medium)).padding(16)
            }.buttonStyle(.bordered)
            if !room.availableRanges.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    eyebrow("LATEST PAIR MEASUREMENTS")
                    ForEach(room.availableRanges, id: \.report.pair.id) { edge in
                        HStack {
                            Text("\(room.shortName(edge.report.pair.a)) ↔ \(room.shortName(edge.report.pair.b))").lineLimit(1)
                            Spacer(minLength: 6)
                            VStack(alignment: .trailing, spacing: 3) {
                                Text(String(format: "%.2f m", edge.report.distance ?? 0)).monospacedDigit().foregroundStyle(roomMint)
                                Text(String(format: "%.1f s ago", room.age(of: edge))).font(.caption2).foregroundStyle(roomMuted)
                            }
                        }.font(.caption)
                    }
                }
            }
        }
    }
    private func eyebrow(_ text: String) -> some View {
        Text(text).font(.system(size: 10, weight: .semibold, design: .monospaced)).tracking(1).foregroundStyle(roomMuted)
    }
    private var testModeToggle: some View {
        VStack(alignment:.leading,spacing:7) {
            Toggle("2-phone gesture test",isOn:Binding(get:{room.twoPhoneMode},set:{room.setTwoPhoneMode($0)}))
                .font(.subheadline.weight(.medium))
            Toggle("3-phone flat test",isOn:Binding(get:{room.threePhoneMode},set:{room.setThreePhoneMode($0)}))
                .font(.subheadline.weight(.medium))
            Text(room.twoPhoneMode ? "Starts at two. One real UWB distance places phones on a fixed vertical map line, X = Z = 0. Direction is assumed, not measured." : room.threePhoneMode ? "Starts at three. All phones share Z = 0; one mirror layout is chosen and kept consistent." : "Full mode starts at five and estimates a 3D group shape.")
                .font(.caption2).foregroundStyle(roomMuted).lineSpacing(2)
        }.padding(14).background(.white.opacity(0.035),in:RoundedRectangle(cornerRadius:14))
    }
    private func stat(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.system(size: 8, weight: .medium, design: .monospaced)).foregroundStyle(roomMuted)
            Text(value).font(.system(size: 23, weight: .medium, design: .rounded)).monospacedDigit()
        }.frame(maxWidth: .infinity, alignment: .leading).padding(12).background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct GestureStatus: View {
    @ObservedObject var camera: GestureCamera
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: camera.gesture == "None" ? "hand.raised.slash" : "hand.raised.fill")
                .foregroundStyle(camera.gesture == "None" ? roomMuted : roomMint)
            VStack(alignment: .leading, spacing: 3) {
                Text("GESTURE · \(camera.gesture)")
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                Text(camera.status).font(.caption2).foregroundStyle(roomMuted)
            }
            Spacer()
            Text(camera.gesture == "None" ? "—" : String(format: "%.0f%%", camera.confidence * 100))
                .font(.system(size: 13, weight: .medium, design: .monospaced)).foregroundStyle(roomMint)
        }.padding(14).background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct RoomMap: View {
    @ObservedObject var room: RoomSession
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("RELATIVE RANGE MAP"); Spacer()
                Text(room.geometry == nil ? "UNRESOLVED" : String(format: "%.1f s OLD", room.geometryAge))
            }.font(.system(size: 9, weight: .medium, design: .monospaced)).foregroundStyle(roomMuted)
            Canvas { context, size in
                let center = CGPoint(x: size.width/2, y: size.height/2)
                let positions = room.geometry?.positions ?? [:]
                let origin = positions[room.localID] ?? .zero
                let radius = max(3, (positions.values.map { ($0-origin).length }.max() ?? 0)*1.2)
                let scale = (Double(min(size.width,size.height))/2-28)/radius
                for i in 0...8 {
                    let x = size.width*Double(i)/8, y = size.height*Double(i)/8
                    var grid = Path()
                    grid.move(to: .init(x:x,y:0)); grid.addLine(to: .init(x:x,y:size.height))
                    grid.move(to: .init(x:0,y:y)); grid.addLine(to: .init(x:size.width,y:y))
                    context.stroke(grid, with: .color(.white.opacity(i == 4 ? 0.12 : 0.035)))
                }
                for fraction in [0.5,1.0] {
                    let r = radius*scale*fraction
                    context.stroke(Path(ellipseIn: .init(x:center.x-r,y:center.y-r,width:r*2,height:r*2)), with: .color(roomMint.opacity(0.13)), style: .init(lineWidth:1,dash:[3,5]))
                }
                for (index,id) in room.onlineIDs.enumerated() where id != room.localID {
                    guard let absolute = positions[id] else { continue }
                    let relative = room.motionHeading.project(absolute-origin)
                    let p = CGPoint(x:center.x+relative.x*scale,y:center.y-relative.y*scale)
                    let color = roomColors[index%5]
                    var line = Path(); line.move(to:center); line.addLine(to:p)
                    context.stroke(line, with: .color(color.opacity(0.3)), style: .init(lineWidth:1,dash:[4,5]))
                    context.fill(Path(ellipseIn:.init(x:p.x-14,y:p.y-14,width:28,height:28)),with:.color(color.opacity(0.09)))
                    context.fill(Path(ellipseIn:.init(x:p.x-5,y:p.y-5,width:10,height:10)),with:.color(color))
                    context.draw(Text(room.shortName(id)).font(.system(size:10,weight:.medium)).foregroundColor(color),at:.init(x:p.x,y:p.y-21))
                }
                if positions.isEmpty {
                    context.draw(Text("WAITING FOR A COMPLETE RANGE CYCLE").font(.system(size:9,design:.monospaced)).foregroundColor(roomMuted),at:.init(x:center.x,y:size.height*0.18))
                }
                context.fill(Path(ellipseIn:.init(x:center.x-15,y:center.y-15,width:30,height:30)),with:.color(.white.opacity(0.1)))
                if room.motionHeading.available {
                    var arrow=Path()
                    arrow.move(to:.init(x:center.x,y:center.y-17)); arrow.addLine(to:.init(x:center.x,y:center.y-36))
                    arrow.move(to:.init(x:center.x-5,y:center.y-30)); arrow.addLine(to:.init(x:center.x,y:center.y-36)); arrow.addLine(to:.init(x:center.x+5,y:center.y-30))
                    context.stroke(arrow,with:.color(.white.opacity(room.motionHeading.moving ? 0.9 : 0.35)),lineWidth:2)
                }
                context.fill(Path(ellipseIn:.init(x:center.x-5,y:center.y-5,width:10,height:10)),with:.color(.white))
                context.draw(Text("YOU").font(.system(size:10,weight:.semibold,design:.monospaced)).foregroundColor(.white),at:.init(x:center.x,y:center.y+24))
                context.draw(Text(String(format:"%.0f m to outer ring",radius)).font(.system(size:9,design:.monospaced)).foregroundColor(roomMuted),at:.init(x:center.x,y:size.height-8))
            }.aspectRatio(1, contentMode:.fit)
                .accessibilityLabel(room.geometry == nil ? "Your dot is centered. Other positions are unresolved." : "Group positions reconstructed from UWB distances, centered on your phone.")
            Text(room.geometryStatus).font(.caption2).foregroundStyle(roomMuted).lineSpacing(3)
            Text(room.motionHeading.available ? String(format:"Forward follows relative movement · %.2f m/s · %@",room.motionHeading.speed,room.motionHeading.moving ? "turning smoothly" : "heading held") : "Forward will follow your movement relative to the group once enough motion is measured.")
                .font(.caption2).foregroundStyle(roomMint)
            Text("Radio-only snapshot. Movement during a cycle can distort positions. Group-wide motion is not observable; this is not compass heading.")
                .font(.caption2).foregroundStyle(.orange.opacity(0.85)).lineSpacing(3)
        }.padding(16).background(.white.opacity(0.025),in:RoundedRectangle(cornerRadius:20))
    }
}

private struct RoomProfiler: View {
    @ObservedObject var room: RoomSession
    @Environment(\.dismiss) private var dismiss
    @State private var seconds = 4.0
    @State private var extended = true
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Measure the handoff.").font(.system(size:28,weight:.medium,design:.rounded))
                        Text("Every device logs every attempt. Latencies are measured with that device’s monotonic clock.")
                            .font(.caption).foregroundStyle(roomMuted)
                    }
                    if room.participantCount >= 2 && !room.running && !room.paused && !room.benchAvailable {
                        Button("Benchmark these \(room.participantCount) phones") { room.requestBench() }
                            .buttonStyle(.borderedProminent).foregroundStyle(roomBackground)
                        Text("Uses the real protocol and UWB hardware. For a map with three phones, turn on the flat test toggle on the main screen.")
                            .font(.caption2).foregroundStyle(roomMuted)
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Cycle performance").font(.headline)
                        metric("Latest cycle",room.lastCycleSummary)
                        metric("Complete reliable cycles",String(room.cycleTimes.count))
                        metric("Complete cycle p50",Statistics.percentile(room.cycleTimes,0.5).map { String(format:"%.2f s",$0) } ?? "—")
                        metric("Complete cycle p95",Statistics.percentile(room.cycleTimes,0.95).map { String(format:"%.2f s",$0) } ?? "—")
                        metric("Coordinator",room.leader.map(room.name) ?? "—")
                        Text("Cycle timings require a reliable result for every pair. Failed and skipped cycles are recorded separately in the event log.")
                            .font(.caption2).foregroundStyle(roomMuted)
                    }.card()
                    ForEach(room.allProfiles,id:\.id) { p in
                        deviceCard(p)
                    }
                    VStack(alignment:.leading,spacing:12) {
                        Text("Ranging controls").font(.headline)
                        Toggle("Extended range",isOn:$extended)
                        HStack { Text("Measurement deadline"); Spacer(); Text(String(format:"%.1f s",seconds)).monospacedDigit() }.font(.caption)
                        Slider(value:$seconds,in:1...12,step:0.5)
                        Button("Apply to future attempts") { room.requestSettings(seconds:seconds,extended:extended) }.buttonStyle(.bordered)
                        Text("Local lease = deadline + 5 seconds for setup. Retries use bounded backoff. Completion needs 4 valid readings, with no minimum span or deviation limit. A 250 ms peer completion grace follows. Camera assistance is always off.")
                            .font(.caption2).foregroundStyle(roomMuted).lineSpacing(3)
                    }.card()
                    VStack(alignment:.leading,spacing:10) {
                        Text("Recent attempts").font(.headline)
                        ForEach(Array(room.reports.suffix(16).reversed())) { r in
                            VStack(alignment:.leading,spacing:4) {
                                HStack { Text(room.shortName(r.device)); Spacer(); Text(r.outcome).foregroundStyle(r.outcome == "reliable" ? roomMint : .orange) }
                                Text("c\(r.cycle) · \(r.sampleCount) samples · setup \(Statistics.milliseconds(r.preparedMS)) · first \(Statistics.milliseconds(r.firstDistanceMS)) · total \(Statistics.milliseconds(r.totalMS))")
                                    .font(.system(size:9,design:.monospaced)).foregroundStyle(roomMuted)
                                if let measurements=r.distanceStatistics {
                                    DisclosureGroup("Distance statistics · \(room.shortName(r.pair.a)) ↔ \(room.shortName(r.pair.b))") {
                                        distanceMetrics(measurements).padding(.top,8)
                                    }
                                }
                            }.font(.caption).padding(.vertical,3)
                        }
                    }
                    HStack {
                        Button("Copy snapshot",systemImage:"doc.on.doc") { UIPasteboard.general.string = room.diagnostics }
                        Spacer()
                        ShareLink(item:room.log.directory.appendingPathComponent("latest.txt")) { Label("Export",systemImage:"square.and.arrow.up") }
                    }.font(.caption)
                    if !room.reports.isEmpty {
                        ShareLink(item:room.log.directory.appendingPathComponent("attempts.jsonl")) { Label("Export attempt log (JSONL)",systemImage:"doc.text") }.font(.caption)
                    }
                    if let error = room.logError { Text("Log write failed: \(error)").font(.caption).foregroundStyle(.orange) }
                    DisclosureGroup("Full diagnostic snapshot") {
                        Text(room.diagnostics).font(.system(size:9,design:.monospaced)).textSelection(.enabled).padding(.top,8)
                    }
                }.padding(22)
            }.background(roomBackground).navigationTitle("Profiling").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement:.topBarTrailing) { Button("Done") { dismiss() } } }
        }.tint(roomMint).preferredColorScheme(.dark)
            .onAppear { seconds = room.measurementSeconds; extended = room.extended }
    }
    private func deviceCard(_ p: DeviceProfile) -> some View {
        let stats = room.aggregate(for:p.id)
        return VStack(alignment:.leading,spacing:10) {
            HStack { Text(p.name + (p.id == room.localID ? " · YOU" : "")).font(.headline); Spacer(); Text("b\(p.build)").font(.caption2).foregroundStyle(roomMuted) }
            metric("Attempts / reliable","\(stats.reports.count) / \(stats.succeeded)")
            metric("Reliability",stats.successRate.map { String(format:"%.0f%%",$0*100) } ?? "—")
            metric("First distance p50 / p95","\(Statistics.milliseconds(stats.firstP50)) / \(Statistics.milliseconds(stats.firstP95))")
            metric("4 readings p50 / p95","\(Statistics.milliseconds(stats.reliableP50)) / \(Statistics.milliseconds(stats.reliableP95))")
            metric("Whole attempt p95",Statistics.milliseconds(stats.totalP95))
            metric("Network round trip",p.id == room.localID ? "Local" : Statistics.milliseconds(room.clocks[p.id].map { $0.rtt*1000 }))
            metric("Sent / received",String(format:"%.0f / %.0f KB",Double(p.txBytes)/1024,Double(p.rxBytes)/1024))
            metric("Link reconnects",String(p.reconnects))
            metric("Thermal / battery","\(p.thermal) / \(p.battery.map { String(format:"%.0f%%",$0*100) } ?? "—")")
            metric("UWB / extended","\(p.distance ? "Yes" : "No") / \(p.extended ? "Yes" : "No")")
            metric("Instant direction",p.direction ? "Supported" : "Unavailable")
            if let last = stats.reports.last {
                if let measurements=last.distanceStatistics {
                    Divider().overlay(roomMuted.opacity(0.2))
                    Text("Latest attempt · \(room.shortName(last.pair.a)) ↔ \(room.shortName(last.pair.b)) · cycle \(last.cycle)")
                        .font(.caption.weight(.medium))
                    distanceMetrics(measurements)
                } else {
                    Text("No distance statistics for the latest attempt.").font(.caption2).foregroundStyle(roomMuted)
                }
                let differences=stats.reports.compactMap { $0.distanceStatistics.map { abs($0.firstMinusMedian) } }
                if !differences.isEmpty {
                    metric("|First − median| p50 / p95", "\(meters(Statistics.percentile(differences,0.5))) / \(meters(Statistics.percentile(differences,0.95)))")
                    Text("Across \(differences.count) attempts with readings, including incomplete attempts. Agreement with later readings is not measured accuracy.")
                        .font(.caption2).foregroundStyle(roomMuted)
                }
                Text("Latest: \(last.outcome) · \(last.reason)").font(.caption2).foregroundStyle(last.outcome == "reliable" ? roomMint : .orange)
            }
        }.card()
    }
    private func meters(_ value:Double?) -> String { value.map { String(format:"%.3f m",$0) } ?? "—" }
    private func distanceMetrics(_ s:DistanceStatistics) -> some View {
        VStack(alignment:.leading,spacing:8) {
            metric("Distance samples",String(s.count))
            metric("First / last", "\(meters(s.first)) / \(meters(s.last))")
            metric("Mean / median", "\(meters(s.mean)) / \(meters(s.median))")
            metric("Sample standard deviation",meters(s.standardDeviation))
            metric("Minimum / maximum", "\(meters(s.minimum)) / \(meters(s.maximum))")
            metric("First − median",String(format:"%+.3f m",s.firstMinusMedian))
            metric("First z-score",s.firstZScore.map { String(format:"%+.2f σ",$0) } ?? "Undefined")
            Text("z = (first − mean) / sample SD. All readings in this attempt, including the first; undefined with fewer than two readings or zero spread. This measures consistency, not true-distance error.")
                .font(.caption2).foregroundStyle(roomMuted).lineSpacing(2)
        }
    }
    private func metric(_ name:String,_ value:String) -> some View {
        HStack(alignment:.firstTextBaseline) { Text(name).foregroundStyle(roomMuted); Spacer(minLength:12); Text(value).monospacedDigit().multilineTextAlignment(.trailing) }.font(.caption)
    }
}

private extension View {
    func card() -> some View { padding(16).background(.white.opacity(0.045),in:RoundedRectangle(cornerRadius:16)) }
}
