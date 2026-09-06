import Foundation

var checks = 0
func check(_ condition: @autoclosure () -> Bool, _ label: String) {
    checks += 1
    if !condition() { print("FAIL: \(label)"); exit(1) }
    print("PASS: \(label)")
}
func near(_ a: Double, _ b: Double, tolerance: Double = 0.00001) -> Bool { abs(a-b) < tolerance }
let ids = ["A","B","C","D","E"]
let rounds = RoundRobin.rounds(ids)
check(rounds.count == 5, "five participants require five round-robin rounds")
check(Set(rounds.flatMap { $0 }).count == 10, "all ten distinct pairings are covered")
check(rounds.allSatisfy { Set($0.flatMap(\.members)).count == $0.count*2 }, "no phone gets two simultaneous UWB pairs")
check(ids.allSatisfy { id in rounds.flatMap { $0 }.filter { $0.contains(id) }.count == 4 }, "every phone measures every other phone")
check(RoundRobin.rounds(ids.reversed()) == rounds, "membership order cannot change the schedule")
check(RoundRobin.rounds(["A","A","B"]).flatMap { $0 }.count == 1, "duplicate identities do not produce self-pairings")
check(RoundRobin.rounds(["A"]).isEmpty, "a lone phone never ranges to itself")
check(RoundRobin.rounds(["A","B","C"]).count == 3, "three-phone recovery has a valid schedule")

func offer(_ id: String = "attempt1", leader: String = "A", pair: RangingPair = .init("A","B")) -> PairOffer {
    .init(id:id,epoch:"epoch1",leader:leader,pair:pair,boots:["A":"bootA","B":"bootB","C":"bootC"],cycle:1,round:0,extended:true,measurementSeconds:4,leaseSeconds:9,created:0)
}
var gate = AttemptGate()
check(gate.reserve(offer(),selfID:"A",boot:"bootA",leader:"A",now:100), "ready handshake reserves one local UWB slot")
check(gate.reserve(offer(),selfID:"A",boot:"bootA",leader:"A",now:102), "duplicate offer is idempotent")
check(near(gate.deadline,109), "duplicate offers cannot extend the watchdog indefinitely")
check(!gate.reserve(offer("attempt2",pair:.init("A","C")),selfID:"A",boot:"bootA",leader:"A",now:103), "a competing partner cannot steal the reserved slot")
check(!gate.expired(at:108.9) && gate.expired(at:109), "lost start/stop messages have a bounded local recovery time")
gate.release()
check(!gate.reserve(offer(),selfID:"A",boot:"bootA",leader:"A",now:110), "a delayed retired offer cannot resurrect an old attempt")
check(gate.reserve(offer("attempt2",pair:.init("A","C")),selfID:"A",boot:"bootA",leader:"A",now:111), "a fresh attempt can proceed after teardown")
gate.release()
check(!gate.reserve(offer("attempt3",leader:"B"),selfID:"A",boot:"bootA",leader:"A",now:111), "stale coordinator commands are rejected")
check(!gate.reserve(offer("attempt3"),selfID:"A",boot:"newBoot",leader:"A",now:111), "commands for an earlier app process are rejected")
check(!gate.reserve(offer("attempt3"),selfID:"C",boot:"bootC",leader:"A",now:111), "uninvolved phones do not reserve a session")
var badOffer = offer("bad"); badOffer.leaseSeconds = .infinity
check(!badOffer.valid, "malformed leases cannot hold the radio forever")

var replay = ReplayWindow()
check(replay.insert("message1") && !replay.insert("message1"), "gossip loops and duplicate messages are suppressed")
check(replay.insert("message2"), "a later independent message still propagates")

var samples = AttemptSamples()
for i in 0..<8 { samples.add(3 + Double(i%3)*0.01,at:Double(i)*0.06) }
check(samples.reliable, "four or more valid readings reach the completion milestone")
check(samples.distinct == 3, "quantized duplicate values are counted separately from distinct values")
var tooFast = AttemptSamples()
for i in 0..<12 { tooFast.add(3,at:Double(i)*0.001) }
check(tooFast.reliable, "fast readings complete without a minimum time span")
var noisy = AttemptSamples()
for i in 0..<20 { noisy.add(i%2 == 0 ? 1 : 5,at:Double(i)*0.1) }
check(noisy.reliable, "spread does not gate four-reading completion")
var fourReadings=AttemptSamples()
for i in 0..<3 { fourReadings.add(1,at:Double(i)*0.001) }
check(!fourReadings.reliable, "three readings cannot complete an attempt")
fourReadings.add(.nan,at:0.003)
check(!fourReadings.reliable, "an invalid fourth reading cannot complete an attempt")
fourReadings.add(10,at:0.004)
check(fourReadings.reliable, "the fourth valid reading completes even with high deviation and a 4 ms span")
let before = samples.values.count
samples.add(.nan,at:1); samples.add(-2,at:1); samples.add(4,at:-1)
check(samples.values.count == before, "nonfinite, negative and out-of-order ranges are rejected")
check(near(Statistics.percentile([10,20,30,40],0.5)!,25), "median uses the middle of an even sample")
check(near(Statistics.percentile([10,20,30,40],0.95)!,38.5), "p95 interpolation is correct")
check(Statistics.percentile([],0.95) == nil, "no attempts displays no percentile, not zero latency")

let clock = PeerClock.estimate(t0:100,t1:500.1,t2:500.2,t3:100.3)
check(clock != nil && near(clock!.offset,400) && near(clock!.rtt,0.2), "monotonic peer clocks align without GPS or wall-clock agreement")
check(PeerClock.estimate(t0:100,t1:500.1,t2:500,t3:100.3) == nil, "backwards remote timestamps are rejected")
check(PeerClock.estimate(t0:100,t1:.nan,t2:500,t3:101) == nil, "nonfinite timing is rejected")

let points: [String: Vector3] = [
    "A": .init(x:0,y:0,z:0), "B": .init(x:4,y:0,z:0), "C": .init(x:1,y:3,z:0),
    "D": .init(x:0.5,y:1,z:2), "E": .init(x:3,y:2,z:1.2)
]
func distances(_ points: [String: Vector3]) -> [RangingPair: Double] {
    var result: [RangingPair: Double] = [:]
    let ids = points.keys.sorted()
    for i in 0..<ids.count { for j in (i+1)..<ids.count { result[.init(ids[i],ids[j])] = points[ids[i]]!.distance(to:points[ids[j]]!) } }
    return result
}
let ranges = distances(points)
let solution = RangeGeometry.solve(ids:ids,distances:ranges)
check(solution != nil && solution!.rms < 0.0001, "five noncoplanar phones reconstruct from ten exact distances")
check(solution!.heightResolved, "noncoplanar geometry exposes a third dimension")
check(solution!.positions.values.reduce(.zero,+).length < 0.0001, "the reconstructed group centroid is at zero")
check(ranges.allSatisfy { pair,range in near(solution!.positions[pair.a]!.distance(to:solution!.positions[pair.b]!),range) }, "all reconstructed pair distances match the supplied constraints")
let aligned = RangeGeometry.solve(ids:ids,distances:ranges,previous:points)
check(aligned != nil && ids.allSatisfy { aligned!.positions[$0]!.distance(to:points[$0]!) < 0.0001 }, "subsequent snapshots preserve orientation and reflection continuity")
let planar = points.mapValues { Vector3(x:$0.x,y:$0.y,z:0) }
let flat = RangeGeometry.solve(ids:ids,distances:distances(planar))
check(flat != nil && !flat!.heightResolved, "near-planar groups explicitly report unresolved third-axis depth")
var missing = ranges; missing.removeValue(forKey:.init("A","E"))
check(RangeGeometry.solve(ids:ids,distances:missing) == nil, "a missing edge is not filled with zero or an invented distance")
var bad = ranges; bad[.init("A","E")] = 100
check(RangeGeometry.solve(ids:ids,distances:bad) == nil, "incompatible range geometry cannot create a plausible-looking map")
bad = ranges; bad[.init("A","E")] = .nan
check(RangeGeometry.solve(ids:ids,distances:bad) == nil, "nonfinite range matrices are rejected")
let line = Dictionary(uniqueKeysWithValues:ids.enumerated().map { ($0.element,Vector3(x:Double($0.offset),y:0,z:0)) })
check(RangeGeometry.solve(ids:ids,distances:distances(line)) == nil, "a collinear group does not invent transverse position")
check(RangeGeometry.solve(ids:["A","B","C"],distances:ranges) == nil, "two or three phones do not pretend to establish full XYZ")
var mildNoise = ranges
for (i,pair) in mildNoise.keys.sorted(by: { $0.id<$1.id }).enumerated() { mildNoise[pair]! += Double(i%3-1)*0.02 }
let imperfect = RangeGeometry.solve(ids:ids,distances:mildNoise)
check(imperfect != nil && imperfect!.rms < 0.1, "small synthetic range noise yields a bounded fit residual")
let triangle = points.filter { ["A","B","C"].contains($0.key) }
let triangleRanges = distances(triangle)
let triangleFit = RangeGeometry.solve(ids:["A","B","C"],distances:triangleRanges,flat:true)
check(triangleFit != nil && triangleFit!.positions.values.allSatisfy { near($0.z,0) }, "three-phone test reconstructs a flat triangle")
check(triangleRanges.allSatisfy { pair,range in near(triangleFit!.positions[pair.a]!.distance(to:triangleFit!.positions[pair.b]!),range) }, "flat triangle preserves all three measured distances")
let mirrored = triangle.mapValues { Vector3(x:-$0.x,y:$0.y,z:0) }
let mirroredFit = RangeGeometry.solve(ids:["A","B","C"],distances:triangleRanges,previous:mirrored,flat:true)
check(mirroredFit != nil && mirrored.allSatisfy { id,p in p.distance(to:mirroredFit!.positions[id]!)<0.0001 }, "an arbitrary mirror solution remains continuous across snapshots")
check(RangeGeometry.solve(ids:["A","B","C"],distances:distances(line),flat:true) != nil, "flat test can display a collinear arrangement without inventing height")
var heading = RelativeMotionHeading()
heading.update(position:.zero,at:0)
for i in 1...4 { heading.update(position:.init(x:Double(i)*0.02,y:0,z:0),at:Double(i)) }
check(!heading.available, "small position jitter does not initialize a heading")
heading.reset(); heading.update(position:.zero,at:0)
heading.update(position:.init(x:1,y:0,z:0),at:1)
check(!heading.available, "one moving sample cannot start a heading")
heading.update(position:.init(x:2,y:0,z:0),at:2)
check(heading.available && heading.moving && near(heading.angle,Double.pi/2), "two sustained movement samples establish the relative heading")
let ahead=heading.project(.init(x:1,y:0,z:0))
check(near(ahead.x,0) && near(ahead.y,1), "travel direction projects to map forward")
let held=heading.angle
for i in 3...12 { heading.update(position:.init(x:2,y:0,z:0),at:Double(i)) }
check(!heading.moving && near(heading.angle,held), "stopping holds the previous heading")
heading.update(position:.init(x:100,y:100,z:0),at:13)
check(near(heading.angle,held), "an implausible position jump cannot flip the heading")
heading.update(position:.init(x:100,y:100,z:0),at:40)
check(!heading.moving && heading.speed==0 && near(heading.angle,held), "a stale gap clears velocity while preserving map orientation")
var snapshot=GroupRangeSnapshot(cycle:1,epoch:"test",created:1,positions:triangleFit!.positions,rms:0,thirdAxisResolved:false,measurementSpan:3,flat:true)
check(snapshot.valid, "three-node flat coordinate snapshots are valid")
snapshot.positions["A"]!.z=1
check(!snapshot.valid, "flat snapshots reject nonzero height")
let distribution=DistanceStatistics([1,2,3,4,5])!
check(distribution.count==5 && distribution.first==1 && distribution.last==5, "first and last retain acquisition order")
check(near(distribution.mean,3) && near(distribution.median,3), "distance mean and median match a known distribution")
check(near(distribution.standardDeviation!,sqrt(2.5)), "distance standard deviation uses sample n-1 denominator")
check(near(distribution.firstZScore!,-2/sqrt(2.5)), "first reading z-score has the correct signed standardized deviation")
check(distribution.firstMinusMedian == -2 && distribution.minimum==1 && distribution.maximum==5, "range bounds and first-minus-median remain signed")
let evenDistribution=DistanceStatistics([4,1,2,3])!
check(near(evenDistribution.median,2.5) && evenDistribution.first==4 && evenDistribution.last==3, "sorting for median does not replace temporal first and last")
let constantDistribution=DistanceStatistics([2,2,2])!
check(constantDistribution.standardDeviation==0 && constantDistribution.firstZScore==nil, "zero spread produces an undefined z-score instead of NaN")
let oneDistribution=DistanceStatistics([2])!
check(oneDistribution.mean==2 && oneDistribution.median==2 && oneDistribution.standardDeviation==nil && oneDistribution.firstZScore==nil, "one sample has a mean but no sample SD or z-score")
check(DistanceStatistics([])==nil && DistanceStatistics([.nan])==nil && DistanceStatistics([1,.infinity])==nil, "invalid statistics never enter JSON logs")
var longAttempt=AttemptSamples()
for i in 0..<250 { longAttempt.add(1+Double(i)*0.001,at:Double(i)*0.05) }
check(longAttempt.values.count==200 && longAttempt.statistics!.count==250 && longAttempt.statistics!.first==1, "diagnostics retain the real first reading beyond the 200-reading quality window")
let countBefore=longAttempt.statistics!.count
longAttempt.add(.nan,at:20); longAttempt.add(2,at:-1)
check(longAttempt.statistics!.count==countBefore, "rejected readings do not contaminate distance statistics")
let decodedDistribution=try! JSONDecoder().decode(DistanceStatistics.self,from:JSONEncoder().encode(distribution))
check(decodedDistribution.valid && near(decodedDistribution.firstZScore!,distribution.firstZScore!), "distance statistics and z-score survive peer exchange and log export")
var malformedDistribution=distribution; malformedDistribution.standardDeviation = -1
check(!malformedDistribution.valid, "negative sample deviations from peers are rejected")
let twoFit=RangeGeometry.solve(ids:["B","A"],distances:[.init("A","B"):4],line:true)
check(twoFit != nil && near(twoFit!.positions["A"]!.y,-2) && near(twoFit!.positions["B"]!.y,2), "two-phone test uses a stable assumed vertical axis and real distance")
check(twoFit!.positions.values.allSatisfy { $0.x==0 && $0.z==0 } && !twoFit!.heightResolved, "two-phone assumed geometry never claims measured direction or height")
check(RangeGeometry.solve(ids:["A","B"],distances:[:],line:true)==nil, "two-phone test never invents a missing range")
check(RangeGeometry.solve(ids:["A","B"],distances:[.init("A","B"):-1],line:true)==nil, "two-phone test rejects invalid distance")
var twoSnapshot=GroupRangeSnapshot(cycle:1,epoch:"test",created:1,positions:twoFit!.positions,rms:0,thirdAxisResolved:false,measurementSpan:1,flat:true,twoPhoneMode:true)
check(twoSnapshot.valid, "two-phone snapshot requires the explicit assumed-axis mode")
twoSnapshot.twoPhoneMode=false
check(!twoSnapshot.valid, "ordinary flat mode does not silently accept two-phone geometry")
print("\(checks) room checks passed. These test protocol invariants and geometry, not real RF latency or accuracy.")
