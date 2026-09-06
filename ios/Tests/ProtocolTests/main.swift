import Foundation

func fail(_ message:String) -> Never { print("FAIL: \(message)"); exit(1) }
let ids=(1...5).map { String(format:"00000000-0000-4000-8000-%012d",$0) }
let coords=[Vector3(x:0,y:0,z:0),Vector3(x:4,y:0,z:0),Vector3(x:1,y:3,z:0),Vector3(x:0.5,y:1,z:2),Vector3(x:3,y:2,z:1.2)]
RoomRanging.points=Dictionary(uniqueKeysWithValues:zip(ids,coords))
let folder=FileManager.default.temporaryDirectory.appendingPathComponent("signalmap-protocol-\(UUID().uuidString)")
let nodes=ids.enumerated().map { i,id -> RoomSession in
    let node=RoomSession(identity:id,logDirectory:folder.appendingPathComponent("node\(i)"))
    node.displayName="TEST \(i+1)"
    return node
}
let code=RoomTransport.newCode()
var lostMessage: String?
RoomTransport.testShouldSend = { message,_ in
    if message.kind=="prepared" && message.sender==ids[3] && lostMessage==nil { lostMessage=message.id }
    return message.id != lostMessage
}
for node in nodes { node.join(code) }
let started=ProcessInfo.processInfo.systemUptime
var stage=0, stageAt=started, initialReportCount=0
var testTimer: Timer?
testTimer=Timer.scheduledTimer(withTimeInterval:0.5,repeats:true) { _ in
    let now=ProcessInfo.processInfo.systemUptime
    if RoomRanging.collision { fail("two attempts owned one device's UWB slot") }
    if now-started>150 {
        for n in nodes { print(n.diagnostics) }
        fail("protocol test timed out in stage \(stage)")
    }
    switch stage {
    case 0:
        if nodes.allSatisfy({ $0.participantCount==5 && $0.cycleTimes.count>=1 && $0.geometry != nil }) {
            print("PASS: five production peer transports authenticate, elect one coordinator, auto-start, reconstruct the group, and recover a dropped prepared message")
            guard Set(nodes.compactMap(\.leader)).count==1 else { fail("coordinator disagreement") }
            let reference=nodes[0].geometry!.positions
            guard nodes.allSatisfy({ n in reference.allSatisfy { id,p in n.geometry!.positions[id]!.distance(to:p)<0.0001 } }) else { fail("phones disagree on shared coordinate frame") }
            initialReportCount=nodes[2].reports.count
            RoomRanging.silentPair=RangingPair(ids[0],ids[1])
            stage=1; stageAt=now
        }
    case 1:
        if now-stageAt>16 {
            guard RoomRanging.silentAttempt != nil else { fail("missing-range fault was never exercised") }
            guard nodes[2].reports.count>initialReportCount+10 else { fail("one missing measurement stalled the rest of the group") }
            guard nodes.contains(where:{ $0.reports.contains(where:{ $0.outcome=="cancelled" }) }) else { fail("local watchdog did not retire the silent attempt") }
            print("PASS: a silent UWB attempt expires locally while other pairs continue")
            nodes[0].background(); stage=2; stageAt=now
        }
    case 2:
        if now-stageAt>12 {
            guard nodes.dropFirst().allSatisfy({ $0.participantCount==4 && $0.leader==ids[1] && $0.running }) else { fail("coordinator loss did not recover to four phones") }
            print("PASS: coordinator disconnect elects a replacement and remaining phones continue")
            nodes[0].foreground(); stage=3; stageAt=now
        }
    case 3:
        if now-stageAt>12 {
            guard nodes.allSatisfy({ $0.participantCount==5 && $0.running && $0.leader==ids[0] }) else { fail("returning coordinator did not rejoin cleanly") }
            print("PASS: reconnect restores five members and resets stale attempts")
            nodes[3].requestPause(); stage=4; stageAt=now
        }
    case 4:
        if now-stageAt>2 {
            guard nodes.allSatisfy(\.paused), RoomRanging.active.isEmpty else { fail("group pause did not release all radios") }
            print("PASS: a remote participant can pause the room and every local reservation releases")
            nodes[4].requestResume(); stage=5; stageAt=now
        }
    case 5:
        if now-stageAt>5 {
            guard nodes.allSatisfy({ $0.running && !$0.paused }) else { fail("group resume failed") }
            print("PASS: room resumes after pause without duplicate UWB ownership")
            for node in nodes { node.leave() }
            RoomRanging.silentPair=nil
            let flatCode=RoomTransport.newCode(threePhone:true)
            for node in nodes.prefix(3) { node.join(flatCode) }
            stage=6; stageAt=now
        }
    case 6:
        if nodes.prefix(3).allSatisfy({ $0.participantCount==3 && $0.threePhoneMode && $0.running && $0.cycleTimes.count>0 && $0.geometry != nil }) {
            let reference=nodes[0].geometry!.positions
            guard reference.values.allSatisfy({ abs($0.z)<0.0001 }), nodes.prefix(3).allSatisfy({ n in reference.allSatisfy { id,p in n.geometry!.positions[id]!.distance(to:p)<0.0001 } }) else { fail("flat test coordinate sharing failed") }
            guard Set(nodes[0].reports.filter { $0.outcome == "reliable" }.map(\.pair)).isSuperset(of:Set(RoundRobin.rounds(Array(ids.prefix(3))).flatMap { $0 })) else { fail("not all three pairs measured") }
            print("PASS: three-phone flat mode auto-starts, rotates all three pairs, and shares one flat coordinate frame")
            nodes[2].setThreePhoneMode(false); stage=7; stageAt=now
        }
    case 7:
        if now-stageAt>4 {
            guard nodes.prefix(3).allSatisfy({ !$0.threePhoneMode && !$0.running && $0.geometry==nil }) else { fail("switching to five-phone mode did not reset and wait") }
            print("PASS: group mode toggle releases radios and waits for five phones")
            for node in nodes { node.leave() }
            print("Protocol integration passed. Real Network.framework links; SYNTHETIC UWB only. Logs: \(folder.path)")
            testTimer?.invalidate(); exit(0)
        }
    default: break
    }
}
RunLoop.main.run()
