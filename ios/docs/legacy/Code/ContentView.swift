import SwiftUI
import ARKit

private let accent = Color(red: 0.56, green: 0.96, blue: 0.76)

struct ContentView: View {
    @ObservedObject var model: LocatorModel
    @ObservedObject var bluetooth: BluetoothScanner
    @ObservedObject var motion: MotionTracker
    @State private var showDevices = false
    @State private var showSettings = false
    @State private var deviceSearch = ""
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack {
                    HStack(spacing: 9) {
                        Image(systemName: "dot.radiowaves.left.and.right").foregroundStyle(accent)
                        Text("signal map").font(.system(size: 23, weight: .semibold, design: .rounded))
                    }
                    Spacer()
                    Button { showSettings = true } label: {
                        Image(systemName: "slider.horizontal.3").padding(12).background(.white.opacity(0.06), in: Circle())
                    }.accessibilityLabel("Calibration and help")
                }
                HStack(spacing: 7) {
                    Circle().fill(model.isDemo ? .orange : accent).frame(width: 5, height: 5)
                    Text(model.isDemo ? "DEMO · SIMULATED SIGNAL & MOVEMENT" : "BLUETOOTH EXPLORER")
                        .font(.system(size: 10, weight: .medium, design: .monospaced)).tracking(1.2)
                }.foregroundStyle(model.isDemo ? .orange : accent)
                VStack(alignment: .leading, spacing: 8) {
                    Text(model.isDemo ? "Studio beacon" : bluetooth.selectedName ?? "Find your signal.")
                        .font(.system(size: 29, weight: .medium)).lineLimit(2)
                    Text(model.isDemo ? "A simulated stationary device. No hardware connected." : bluetooth.selectedID == nil
                         ? "Connect a nearby device. Walk a little. Explore where it might be."
                         : bluetooth.status)
                        .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                MiniMap(model: model)
                HStack(alignment: .top, spacing: 0) {
                    metric("EST. DISTANCE", value: model.distance.map { $0 > 30 ? ">30" : $0.formatted(.number.precision(.fractionLength(1))) } ?? "—", unit: "m")
                    Spacer()
                    metric("SIGNAL", value: model.rssi.map { String(Int($0)) } ?? "—", unit: "dBm")
                    Spacer()
                    metric("MAP SAMPLES", value: String(model.sampleCount), unit: "/ 6+")
                }.padding(.horizontal, 6)
                VStack(alignment: .leading, spacing: 6) {
                    Text(model.samplingStatus).font(.caption).foregroundStyle(accent)
                    if let reason = model.lastResetReason {
                        Text("Last reset: \(reason)").font(.caption2).foregroundStyle(.secondary)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .leading, spacing: 12) {
                    Label(model.position == nil ? "Give the signal some perspective" : "An estimate, with room for error",
                          systemImage: model.position == nil ? "figure.walk" : "scope")
                        .font(.subheadline.weight(.medium)).foregroundStyle(accent)
                    Text(guidance).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    if motion.isRunning && !model.isDemo {
                        CameraPreview(session: motion.session)
                            .frame(height: 100).clipShape(RoundedRectangle(cornerRadius: 12))
                            .overlay(alignment: .bottomLeading) {
                                Text("CAMERA · MOTION TRACKING").font(.system(size: 9, design: .monospaced))
                                    .padding(7).background(.black.opacity(0.6), in: RoundedRectangle(cornerRadius: 6)).padding(6)
                            }.accessibilityLabel("Live camera preview for movement tracking")
                    }
                    if !model.isDemo {
                        Button(motion.isRunning ? "Restart motion mapping" : "Enable motion mapping") { motion.start() }
                            .font(.caption.weight(.semibold)).foregroundStyle(accent)
                        Text(motion.status).font(.caption2).foregroundStyle(.secondary)
                    }
                    if model.stale { Text("Signal lost. Bring the device closer or reconnect.").font(.caption).foregroundStyle(.orange) }
                }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
                    .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 18))
                Button {
                    if model.isDemo { model.toggleDemo() }
                    bluetooth.scan()
                    showDevices = true
                } label: {
                    HStack { Image(systemName: "dot.radiowaves.left.and.right"); Text(bluetooth.selectedID == nil ? "Find a Bluetooth device" : "Change device"); Spacer(); Image(systemName: "arrow.right") }
                        .font(.subheadline.weight(.semibold)).padding(18).background(accent, in: RoundedRectangle(cornerRadius: 16)).foregroundStyle(.black)
                }
                HStack {
                    Button(model.isDemo ? "Exit demo" : "Try the demo") { model.toggleDemo() }
                    Spacer()
                    Button("Reset map") { model.resetMap() }
                }.font(.caption).foregroundStyle(.secondary).padding(.horizontal, 4)
                Text("RSSI is a rough proximity estimate. Keep the source still and near phone height. Camera frames are processed on your iPhone; this app does not save or upload them.")
                    .font(.system(size: 10)).foregroundStyle(.secondary).lineSpacing(3)
            }.padding(24)
        }
        .background(Color(red: 0.035, green: 0.055, blue: 0.055))
        .tint(accent)
        .sheet(isPresented: $showDevices) { devicePicker }
        .sheet(isPresented: $showSettings) { settings }
        .onAppear {
            if ProcessInfo.processInfo.arguments.contains("--demo") && !model.isDemo { model.toggleDemo() }
        }
    }
    private var guidance: String {
        if model.position != nil { return "The dot combines signal measurements with your movement. The shaded area is a rough uncertainty guide, not a guarantee. Walls, reflections and a moving source can shift it substantially." }
        return "Keep the source still. Hold the phone upright and walk 2–3 meters, then turn and walk sideways to form an L. A single signal reading gives a distance ring; varied positions may resolve a direction."
    }
    private func metric(_ label: String, value: String, unit: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label).font(.system(size: 8, weight: .medium, design: .monospaced)).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value).font(.system(size: 25, weight: .medium, design: .rounded)).monospacedDigit()
                Text(unit).font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
    }
    private var devicePicker: some View {
        NavigationStack {
            List {
                Section {
                    Text(bluetooth.status).foregroundStyle(.secondary)
                    HStack {
                        Text(bluetooth.isListPaused ? "List frozen · Bluetooth still running" : "Stable discovery order · updates every second")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button(bluetooth.isListPaused ? "Resume" : "Freeze list") { bluetooth.toggleListUpdates() }
                            .font(.caption.weight(.semibold)).buttonStyle(.borderless)
                    }
                    if bluetooth.devices.isEmpty {
                        Text("Wake your device or enable its advertising mode. Only discoverable BLE devices appear here; ordinary Bluetooth audio pairing is different.")
                            .font(.subheadline)
                    }
                    if !bluetooth.devices.isEmpty && matchingDevices.isEmpty {
                        Text("No devices match \"\(deviceSearch)\".").foregroundStyle(.secondary)
                    }
                    ForEach(matchingDevices) { device in
                        Button {
                            bluetooth.select(device)
                            showDevices = false
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(device.name).foregroundStyle(.primary)
                                    Text("\(device.id.uuidString.prefix(8)) · \(device.connectable ? "Connectable" : "Broadcast only")")
                                        .font(.caption2).foregroundStyle(.secondary)
                                    if Date().timeIntervalSince(device.lastSeen) > 20 {
                                        Text("Not seen recently").font(.caption2).foregroundStyle(.orange)
                                    }
                                }
                                Spacer()
                                Text("\(device.rssi) dBm").font(.caption.monospaced()).foregroundStyle(accent)
                            }.padding(.vertical, 4)
                        }
                    }
                } footer: { Text("Choose a device you own or have permission to test. If it is missing, verify its BLE advertising support and whether another app is already connected.") }
            }
            .navigationTitle("Nearby devices")
            .searchable(text: $deviceSearch, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search by name or device ID")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Rescan") { bluetooth.scan() } }
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { showDevices = false } }
            }
            .onDisappear { if bluetooth.isListPaused { bluetooth.toggleListUpdates() } }
        }.tint(accent)
    }
    private var matchingDevices: [DiscoveredDevice] {
        let query = deviceSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return bluetooth.devices }
        return bluetooth.devices.filter {
            $0.name.localizedCaseInsensitiveContains(query) || $0.id.uuidString.localizedCaseInsensitiveContains(query)
        }
    }
    private var settings: some View {
        NavigationStack {
            Form {
                Section("Calibrate distance") {
                    Text("Place the device exactly 1 meter away with a clear line of sight. Hold both still for about 5 seconds, then save the signal. Calibration applies to this session.")
                    Button("Save current signal at 1 meter") { model.calibrate() }.disabled(!model.canCalibrate)
                    if let message = model.calibrationMessage { Text(message).foregroundStyle(accent) }
                    HStack { Text("Reference RSSI"); Spacer(); Text("\(Int(model.reference)) dBm").monospaced() }
                    Slider(value: $model.reference, in: -90 ... -30, step: 1).onChange(of: model.reference) { _, _ in model.settingsChanged() }
                    HStack { Text("Path-loss exponent"); Spacer(); Text(model.exponent.formatted(.number.precision(.fractionLength(1)))) }
                    Slider(value: $model.exponent, in: 1.5 ... 4, step: 0.1).onChange(of: model.exponent) { _, _ in model.settingsChanged() }
                    Text("Start around 2 outdoors, or 2–3 indoors. Changing the model clears old map samples. The advertised transmit power is not assumed to be a calibrated 1-meter RSSI.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("What to expect") {
                    Text("The mini-map is a top-down estimate relative to the rear camera’s facing direction, not a compass or floor plan. It assumes a stationary target at roughly the phone’s height. It cannot reliably track a moving Bluetooth source.")
                    Text("RSSI can vary by many dB because of your body, walls and reflections. The dot can be wrong by several meters. The app waits for at least six spatially separated samples and a path with movement in two dimensions.")
                    Text("Each map sample needs a valid signal, reliable camera tracking, at least 0.7 seconds since the previous sample, and at least 35 cm of movement. Brief tracking dips and pointing downward pause sampling. Tracking loss lasting 3 seconds resets the map. Old samples expire after 60 seconds.")
                    Text("This Bluetooth mode uses RSSI. Switch to Phone ↔ Phone for UWB distance and direction between two iPhones running Signal Map. Bluetooth Channel Sounding requires a compatible reflector accessory and a separate integration.")
                }
                Section {
                    Button("Disconnect and stop sensors", role: .destructive) { model.suspend(); showSettings = false }
                } footer: { Text("Sensors also stop when the app enters the background. Restart scanning and motion mapping when you return.") }
            }.navigationTitle("Calibration & help")
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { showSettings = false } } }
        }.tint(accent)
    }
}

private struct CameraPreview: UIViewRepresentable {
    let session: ARSession
    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.session = session
        view.automaticallyUpdatesLighting = false
        return view
    }
    func updateUIView(_ uiView: ARSCNView, context: Context) {}
}
