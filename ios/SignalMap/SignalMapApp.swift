import SwiftUI

@main
struct SignalMapApp: App {
    @StateObject private var room = RoomSession()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            RoomView(room: room).preferredColorScheme(.dark)
                .onChange(of: scenePhase) { _, phase in
                    if phase == .background { room.background() }
                    else if phase == .active { room.foreground() }
                }
                .onAppear {
                    let args = ProcessInfo.processInfo.arguments
                    if let index = args.firstIndex(of: "--name"), args.indices.contains(index+1) {
                        room.displayName = String(args[index+1].prefix(24))
                    }
                    // Device deployment can join a concrete test room without typing its key.
                    if !room.joined, let index = args.firstIndex(of: "--room"), args.indices.contains(index+1) {
                        room.join(args[index+1])
                    }
                    if args.contains("--bench") { room.benchmarkWhenPeersJoin() }
                }
        }
    }
}
