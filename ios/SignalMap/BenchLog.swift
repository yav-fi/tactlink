import Foundation

final class BenchLog {
    let runID = UUID().uuidString
    let directory: URL
    private let queue = DispatchQueue(label: "signalmap.room.log")
    private(set) var recent: [String] = []
    var onError: ((String) -> Void)?
    init(directory: URL? = nil) {
        self.directory = directory ?? FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("RoomBench")
        try? FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
    }
    func event(_ name: String, _ detail: String = "") {
        let now = Date()
        recent.append("\(now.formatted(date: .omitted, time: .standard))  \(name)  \(detail)")
        recent = Array(recent.suffix(100))
        let item: [String: Any] = ["run": runID, "date": now.ISO8601Format(), "uptime": ProcessInfo.processInfo.systemUptime, "event": name, "detail": detail]
        if let data = try? JSONSerialization.data(withJSONObject: item) { append(data + Data([10]), to: "events.jsonl") }
    }
    func report(_ report: AttemptReport) {
        if let data = try? JSONEncoder().encode(report) { append(data + Data([10]), to: "attempts.jsonl") }
    }
    func snapshot(_ text: String) {
        let url = directory.appendingPathComponent("latest.txt")
        queue.async { [weak self] in
            do { try text.write(to: url, atomically: true, encoding: .utf8) }
            catch { self?.failed(error) }
        }
    }
    private func append(_ data: Data, to name: String) {
        let url = directory.appendingPathComponent(name)
        queue.async { [weak self] in
            do {
                // Bounded logs; preserve one previous 8 MB segment on device.
                let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                if size > 8_000_000 {
                    let previous = url.appendingPathExtension("previous")
                    try? FileManager.default.removeItem(at: previous)
                    try FileManager.default.moveItem(at: url, to: previous)
                }
                if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
                let file = try FileHandle(forWritingTo: url)
                defer { try? file.close() }
                try file.seekToEnd(); try file.write(contentsOf: data)
            } catch { self?.failed(error) }
        }
    }
    private func failed(_ error: Error) {
        DispatchQueue.main.async { [weak self] in self?.onError?(error.localizedDescription) }
    }
}
