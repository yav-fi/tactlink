import SwiftUI
import MultipeerConnectivity
import ARKit

struct PeerRangingView: View {
    @ObservedObject var ranger: PeerRanger
    @State private var showBrowser = false
    private let mint = Color(red: 0.56, green: 0.96, blue: 0.76)

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Text("Find your other iPhone.").font(.system(size: 29, weight: .medium))
                Label("ULTRA WIDEBAND · PHONE TO PHONE", systemImage: "wave.3.right")
                    .font(.system(size: 10, weight: .semibold, design: .monospaced)).foregroundStyle(mint)
                Text(ranger.connectedName ?? "This phone: \(ranger.localName)")
                    .font(.headline).textSelection(.enabled)
                Text(ranger.status).font(.subheadline).foregroundStyle(.secondary)
                    .frame(minHeight: 44, alignment: .topLeading)

                PeerMap(reading: ranger.measurement)

                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 7) {
                        Text("MEASURED DISTANCE").font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                        Text(ranger.measurement?.distance.map { $0.formatted(.number.precision(.fractionLength(2))) + " m" } ?? "—")
                            .font(.system(size: 33, weight: .medium, design: .rounded)).monospacedDigit()
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 7) {
                        Text("DIRECTION").font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                        Text(ranger.measurement?.point != nil ? "Live" : ranger.measurement?.bearing != nil ? "Bearing only" : "Unavailable")
                            .font(.headline).foregroundStyle(ranger.measurement?.point == nil ? .secondary : mint)
                    }
                }
                if let height = ranger.measurement?.height, abs(height) > 0.3 {
                    Text("Other phone: about \(abs(height).formatted(.number.precision(.fractionLength(1)))) m \(height > 0 ? "above" : "below") this phone.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(ranger.directionHelp).font(.caption).foregroundStyle(mint)
                if ranger.cameraEnabled {
                    PeerCameraPreview(session: ranger.cameraSession)
                        .frame(height: 140).clipShape(RoundedRectangle(cornerRadius: 14))
                        .accessibilityLabel("Live camera view for direction assistance")
                    Text("Camera tracking: \(ranger.cameraTracking)")
                        .font(.caption).foregroundStyle(.secondary)
                } else if ranger.connectedName != nil && ranger.supportsCamera {
                    Button("Enable camera assistance") { ranger.enableCameraNow() }
                        .buttonStyle(.bordered)
                }

                if !ranger.isActive {
                    Toggle("Camera-assisted direction", isOn: $ranger.useCameraAssistance)
                        .disabled(!ranger.supportsCamera)
                    Text("Recommended for direction. Allow Camera, keep the other phone still, then move this phone slowly in good lighting. Frames stay on this phone.")
                        .font(.caption).foregroundStyle(.secondary)
                    Toggle("Extended UWB range", isOn: $ranger.useExtendedRange)
                        .disabled(!ranger.supportsExtendedRange)
                    Text("If direction still fails, disconnect and try with extended range off to compare the two ranging modes.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button {
                        ranger.start(advertise: true)
                    } label: {
                        Label("Make this phone discoverable", systemImage: "antenna.radiowaves.left.and.right")
                            .frame(maxWidth: .infinity).padding(16)
                    }.buttonStyle(.borderedProminent).disabled(!ranger.supportsUWB)
                    Button {
                        ranger.start(advertise: false)
                        showBrowser = ranger.connection != nil
                    } label: {
                        Label("Find the other phone", systemImage: "magnifyingglass")
                            .frame(maxWidth: .infinity).padding(12)
                    }.buttonStyle(.bordered).disabled(!ranger.supportsUWB)
                } else {
                    Button("Disconnect", role: .destructive) { ranger.stop() }
                        .frame(maxWidth: .infinity).buttonStyle(.bordered)
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("First connection").font(.headline)
                    Text("1. Open Phone ↔ Phone on both iPhones.\n2. Tap Make this phone discoverable on one.\n3. On the other, tap Find the other phone and choose the matching name.\n4. Accept the invitation on the first phone and allow Nearby Interaction on both.")
                    Text("Turn on Wi-Fi and Bluetooth, and allow Local Network and Camera. Keep both apps open, about 1–3 meters apart. Leave the target phone still and upright. Aim the finding phone’s rear camera toward it and move the finding phone slowly sideways, then up and down.")
                    Text("UWB distance does not need RSSI calibration or map samples. Direction may need camera assistance and movement before it becomes available. The map turns with your phone; it is not a room floor plan.")
                }.font(.caption).foregroundStyle(.secondary).lineSpacing(4)
                    .padding(18).background(.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 18))

                VStack(alignment: .leading, spacing: 7) {
                    Text("Capabilities reported by this iPhone").font(.caption.weight(.semibold))
                    Text("UWB distance: \(ranger.supportsUWB ? "Supported" : "Unavailable") · Direct direction: \(ranger.supportsDirection ? "Supported" : "Unavailable") · Camera assistance: \(ranger.supportsCamera ? "Supported" : "Unavailable")")
                    Text("Extended UWB range: \(ranger.extendedRange ? "Enabled for both phones" : ranger.supportsExtendedRange ? "Supported locally; checked again when connected" : "Unavailable")")
                    Text("Ranging updates: \(ranger.updateCount) · build \(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?")").monospacedDigit()
                }.font(.caption2).foregroundStyle(.secondary)
                DisclosureGroup("UWB diagnostics") {
                    VStack(alignment: .leading, spacing: 12) {
                        Button("Copy diagnostics") { UIPasteboard.general.string = ranger.diagnostics }
                        Text(ranger.diagnostics)
                            .font(.system(size: 10, design: .monospaced)).textSelection(.enabled)
                        Text("A local text snapshot also lets us inspect measurements over USB. It contains no camera images or discovery tokens.")
                            .font(.caption2).foregroundStyle(.secondary)
                    }.padding(.top, 12)
                }
            }.padding(24)
        }
        .background(Color(red: 0.035, green: 0.055, blue: 0.055))
        .tint(mint)
        .sheet(isPresented: $showBrowser) {
            if let session = ranger.connection {
                PeerBrowser(session: session) {
                    showBrowser = false
                    if ranger.connectedName == nil { ranger.stop(message: "No phone connected. Try again when the other phone is discoverable.") }
                }
            }
        }
        .onChange(of: ranger.connectedName) { _, name in
            if name != nil { showBrowser = false }
        }
        .onChange(of: ranger.isActive) { _, active in
            if !active { showBrowser = false }
        }
        .alert("Connect to this iPhone?", isPresented: Binding(
            get: { ranger.invitationName != nil },
            set: { if !$0 && ranger.invitationName != nil { ranger.answerInvitation(accept: false) } }
        )) {
            Button("Accept") { ranger.answerInvitation(accept: true) }
            Button("Decline", role: .cancel) { ranger.answerInvitation(accept: false) }
        } message: {
            Text("\(ranger.invitationName ?? "Another phone") wants to measure distance and direction with you. Check that this name matches your other phone.")
        }
    }
}

private struct PeerCameraPreview: UIViewRepresentable {
    let session: ARSession
    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.session = session
        view.automaticallyUpdatesLighting = false
        return view
    }
    func updateUIView(_ view: ARSCNView, context: Context) {}
}

private struct PeerBrowser: UIViewControllerRepresentable {
    let session: MCSession
    let finished: () -> Void
    func makeCoordinator() -> Coordinator { Coordinator(finished: finished) }
    func makeUIViewController(context: Context) -> MCBrowserViewController {
        let browser = MCBrowserViewController(serviceType: PeerRanger.serviceType, session: session)
        browser.minimumNumberOfPeers = 2
        browser.maximumNumberOfPeers = 2
        browser.delegate = context.coordinator
        return browser
    }
    func updateUIViewController(_ uiViewController: MCBrowserViewController, context: Context) {}
    final class Coordinator: NSObject, MCBrowserViewControllerDelegate {
        let finished: () -> Void
        init(finished: @escaping () -> Void) { self.finished = finished }
        func browserViewControllerDidFinish(_ browserViewController: MCBrowserViewController) { finished() }
        func browserViewControllerWasCancelled(_ browserViewController: MCBrowserViewController) { finished() }
    }
}

private struct PeerMap: View {
    let reading: PeerMeasurement?
    private let mint = Color(red: 0.56, green: 0.96, blue: 0.76)
    private var radius: Double {
        let needed = (reading?.distance ?? 0) * 1.15
        return [3.0, 5, 10, 20, 40, 80].first(where: { $0 >= needed }) ?? ceil(needed / 20) * 20
    }
    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Text("RELATIVE MAP")
                Spacer()
                Text("↑ REAR CAMERA FACING")
            }.font(.system(size: 9, weight: .medium, design: .monospaced)).foregroundStyle(.secondary)
            Canvas { context, size in
                let center = CGPoint(x: size.width / 2, y: size.height / 2)
                let scale = (Double(min(size.width, size.height)) / 2 - 24) / radius
                for i in 0...8 {
                    let x = size.width * Double(i) / 8, y = size.height * Double(i) / 8
                    var grid = Path()
                    grid.move(to: CGPoint(x: x, y: 0)); grid.addLine(to: CGPoint(x: x, y: size.height))
                    grid.move(to: CGPoint(x: 0, y: y)); grid.addLine(to: CGPoint(x: size.width, y: y))
                    context.stroke(grid, with: .color(.white.opacity(i == 4 ? 0.14 : 0.05)))
                }
                for r in [radius / 2, radius] {
                    let pixels = r * scale
                    context.stroke(Path(ellipseIn: CGRect(x: center.x - pixels, y: center.y - pixels, width: pixels * 2, height: pixels * 2)),
                                   with: .color(.white.opacity(0.12)), style: StrokeStyle(lineWidth: 1, dash: [3, 5]))
                }
                if let point = reading?.point {
                    let p = CGPoint(x: center.x + point.x * scale, y: center.y - point.y * scale)
                    var line = Path(); line.move(to: center); line.addLine(to: p)
                    context.stroke(line, with: .color(mint.opacity(0.45)), style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
                    context.fill(Path(ellipseIn: CGRect(x: p.x - 7, y: p.y - 7, width: 14, height: 14)), with: .color(mint))
                    context.draw(Text("PEER").font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundColor(mint), at: CGPoint(x: p.x, y: p.y - 18))
                } else if let bearing = reading?.bearing {
                    // A horizontal angle alone does not establish horizontal range.
                    // Draw a fixed-length bearing arrow, never a falsely precise target dot.
                    let length = min(size.width, size.height) * 0.3
                    let tip = CGPoint(x: center.x + sin(bearing) * length, y: center.y - cos(bearing) * length)
                    var arrow = Path(); arrow.move(to: center); arrow.addLine(to: tip)
                    for side in [-1.0, 1.0] {
                        arrow.move(to: tip)
                        arrow.addLine(to: CGPoint(x: tip.x - sin(bearing + side * 0.5) * 16,
                                                 y: tip.y + cos(bearing + side * 0.5) * 16))
                    }
                    context.stroke(arrow, with: .color(mint), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    context.draw(Text("BEARING ONLY").font(.system(size: 9, design: .monospaced)).foregroundColor(mint), at: CGPoint(x: center.x, y: size.height * 0.1))
                } else {
                    context.draw(Text(reading?.distance == nil ? "WAITING FOR UWB" : "DIRECTION UNAVAILABLE")
                        .font(.system(size: 10, design: .monospaced)).foregroundColor(.gray), at: CGPoint(x: center.x, y: size.height * 0.22))
                }
                var cone = Path(); cone.move(to: center)
                cone.addLine(to: CGPoint(x: center.x - 17, y: center.y - 38))
                cone.addLine(to: CGPoint(x: center.x + 17, y: center.y - 38)); cone.closeSubpath()
                context.fill(cone, with: .color(.white.opacity(0.15)))
                context.fill(Path(ellipseIn: CGRect(x: center.x - 5, y: center.y - 5, width: 10, height: 10)), with: .color(.white))
                context.draw(Text("YOU").font(.system(size: 9, design: .monospaced)).foregroundColor(.white), at: CGPoint(x: center.x, y: center.y + 18))
            }.aspectRatio(1, contentMode: .fit).clipped()
            Text("\(radius.formatted()) m to outer ring · hold upright · dot shows horizontal position")
                .font(.system(size: 9, design: .monospaced)).foregroundStyle(.secondary)
        }.padding(16).background(.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 20))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(reading?.point.map { "Peer \(abs($0.x).formatted()) meters \($0.x >= 0 ? "right" : "left"), \(abs($0.y).formatted()) meters \($0.y >= 0 ? "ahead" : "behind")" } ?? "Peer direction unavailable")
    }
}
