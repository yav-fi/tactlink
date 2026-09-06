import CoreBluetooth
import Combine

struct DiscoveredDevice: Identifiable {
    let id: UUID
    var name: String
    var rssi: Int
    var connectable: Bool
    var lastSeen: Date
}

final class BluetoothScanner: NSObject, ObservableObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    @Published var devices: [DiscoveredDevice] = []
    @Published var status = "Bluetooth is idle"
    @Published var selectedID: UUID?
    @Published var selectedName: String?
    @Published var isScanning = false
    @Published var isListPaused = false
    var onRSSI: ((Double) -> Void)?
    var onSelection: (() -> Void)?
    var onUnavailable: (() -> Void)?
    private var central: CBCentralManager!
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var selected: CBPeripheral?
    private var timer: Timer?
    private var connectionTimeout: Timer?
    private var wantsScan = false
    private var discoveryOrder: [UUID] = []
    private var latestDevices: [UUID: DiscoveredDevice] = [:]
    private var listTimer: Timer?

    func scan() {
        wantsScan = true
        if central == nil { central = CBCentralManager(delegate: self, queue: .main) }
        else if central.state == .poweredOn { beginScan() }
    }
    private func beginScan() {
        devices.removeAll()
        discoveryOrder.removeAll()
        latestDevices.removeAll()
        isListPaused = false
        listTimer?.invalidate()
        listTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.publishDeviceList()
        }
        central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
        isScanning = true
        status = "Scanning for BLE devices"
    }
    private func publishDeviceList() {
        guard !isListPaused else { return }
        // Keep each row's position for this scan, even when RSSI fluctuates or a
        // device stops advertising. An explicit rescan clears the list.
        devices = discoveryOrder.compactMap { latestDevices[$0] }
    }
    func toggleListUpdates() {
        isListPaused.toggle()
        if !isListPaused { publishDeviceList() }
    }
    func select(_ device: DiscoveredDevice) {
        disconnect()
        guard let peripheral = peripherals[device.id] else { return }
        selected = peripheral
        selectedID = device.id
        selectedName = device.name
        peripheral.delegate = self
        onSelection?()
        if device.connectable {
            status = "Connecting to \(device.name)…"
            central.connect(peripheral)
            connectionTimeout = Timer.scheduledTimer(withTimeInterval: 10, repeats: false) { [weak self] _ in
                guard let self, self.selected === peripheral, peripheral.state != .connected else { return }
                self.central.cancelPeripheralConnection(peripheral)
                self.status = "Connection timed out · listening to broadcasts"
            }
        } else { status = "Listening to broadcasts · device is not connectable" }
    }
    func disconnect() {
        connectionTimeout?.invalidate()
        timer?.invalidate()
        let old = selected
        selected = nil
        selectedID = nil
        selectedName = nil
        if let old { central?.cancelPeripheralConnection(old) }
        onUnavailable?()
    }
    func stop() {
        wantsScan = false
        central?.stopScan()
        isScanning = false
        listTimer?.invalidate()
        isListPaused = false
        disconnect()
        status = "Bluetooth is idle"
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn: if wantsScan { beginScan() }
        case .poweredOff: status = "Turn on Bluetooth in Settings"
        case .unauthorized: status = "Allow Bluetooth access in Settings → Signal Map"
        case .unsupported: status = "Bluetooth is unavailable on this device"
        default: status = "Bluetooth is starting…"
        }
        if central.state != .poweredOn {
            isScanning = false
            devices.removeAll()
            discoveryOrder.removeAll()
            latestDevices.removeAll()
            listTimer?.invalidate()
            disconnect()
        }
    }
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let rssi = RSSI.intValue
        guard (-110 ... -10).contains(rssi) else { return }
        peripherals[peripheral.identifier] = peripheral
        let device = DiscoveredDevice(id: peripheral.identifier,
            name: advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name ?? latestDevices[peripheral.identifier]?.name ?? "Unnamed BLE device",
            rssi: rssi, connectable: (advertisementData[CBAdvertisementDataIsConnectable] as? NSNumber)?.boolValue ?? false,
            lastSeen: Date())
        if latestDevices[device.id] == nil { discoveryOrder.append(device.id) }
        latestDevices[device.id] = device
        if selected === peripheral, peripheral.state != .connected { onRSSI?(Double(rssi)) }
    }
    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        guard selected === peripheral else { central.cancelPeripheralConnection(peripheral); return }
        connectionTimeout?.invalidate()
        status = "Connected · reading signal strength"
        peripheral.readRSSI()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.7, repeats: true) { [weak self] _ in
            guard let self, self.selected?.state == .connected else { return }
            self.selected?.readRSSI()
        }
    }
    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        guard selected === peripheral else { return }
        connectionTimeout?.invalidate()
        status = "Could not connect · listening to broadcasts"
    }
    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        guard selected === peripheral else { return }
        timer?.invalidate()
        status = "Disconnected · listening for broadcasts"
        onUnavailable?()
    }
    func peripheral(_ peripheral: CBPeripheral, didReadRSSI RSSI: NSNumber, error: Error?) {
        guard selected === peripheral, peripheral.state == .connected, error == nil else { return }
        if (-110 ... -10).contains(RSSI.intValue), var device = latestDevices[peripheral.identifier] {
            device.rssi = RSSI.intValue
            device.lastSeen = Date()
            latestDevices[device.id] = device
        }
        onRSSI?(RSSI.doubleValue)
    }
}
