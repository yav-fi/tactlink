import Foundation
import Combine

struct PhoneHandPose {
    struct Point { var x: Double; var y: Double; var confidence: Double }
    var wrist, thumbCMC, thumbIP, thumbTip: Point
    var indexMCP, indexPIP, indexTip: Point
    var middleMCP, middlePIP, middleTip: Point
    var ringMCP, ringPIP, ringTip: Point
    var littleMCP, littlePIP, littleTip: Point

    private func distance(_ a: Point, _ b: Point) -> Double { hypot(a.x-b.x, a.y-b.y) }
    private func extended(_ tip: Point, _ pip: Point, _ mcp: Point) -> Bool {
        distance(wrist,tip)>distance(wrist,pip)*1.16 && distance(mcp,tip)>distance(mcp,pip)*1.20
    }
    var fingers: (thumb:Bool,index:Bool,middle:Bool,ring:Bool,little:Bool) {
        let thumb=distance(wrist,thumbTip)>distance(wrist,thumbIP)*1.12 && distance(thumbCMC,thumbTip)>distance(thumbCMC,thumbIP)*1.18
        return (thumb,extended(indexTip,indexPIP,indexMCP),extended(middleTip,middlePIP,middleMCP),extended(ringTip,ringPIP,ringMCP),extended(littleTip,littlePIP,littleMCP))
    }
    var pointVector:(x:Double,y:Double) { ((indexTip.x-indexMCP.x)+(middleTip.x-middleMCP.x),(indexTip.y-indexMCP.y)+(middleTip.y-middleMCP.y)) }
    var confidence:Double { [wrist,thumbTip,indexTip,middleTip,ringTip,littleTip].map(\.confidence).min() ?? 0 }
    func cannedLabel()->String {
        let f=fingers
        if f.index && f.middle && f.ring && !f.little { return "Three_Finger_Forward" }
        if f.thumb && f.index && !f.middle && !f.ring && f.little { return "ILoveYou" }
        if f.index && f.middle && !f.ring && !f.little { return "Victory" }
        if f.index && !f.middle && !f.ring && !f.little { return "Pointing_Up" }
        if f.index && f.middle && f.ring && f.little { return "Open_Palm" }
        if !f.index && !f.middle && !f.ring && !f.little {
            let dy=thumbTip.y-wrist.y
            if f.thumb && abs(dy)>0.12 { return dy>0 ? "Thumb_Up":"Thumb_Down" }
            return "Closed_Fist"
        }
        return "None"
    }
}

/// V-H-V-H detector equivalent to `src/finger_swing.py`. Points are from an
/// upright rear-camera image, so negative x is left in the preview.
struct PhoneFingerSwing {
    private(set) var history:[(String,Double)]=[]
    private var raw:String?; private var rawSince=0.0; private var confirmed:String?
    private var lastSeen = -Double.infinity
    mutating func update(pose:PhoneHandPose?,at now:Double)->String? {
        guard let pose else { if now-lastSeen>0.6 { reset() }; return nil }
        let f=pose.fingers
        guard f.index && f.middle && !f.ring && !f.little else { if now-lastSeen>0.6 { reset() }; return nil }
        lastSeen=now
        let v=pose.pointVector, angle=abs(atan2(v.x,v.y) * 180 / Double.pi)
        let orientation:String? = (angle<=35 || angle>=145) ? "V" : (angle>=55 && angle<=125 ? "H":nil)
        guard let orientation else { return nil }
        if raw != orientation { raw=orientation; rawSince=now }
        guard confirmed != orientation, now-rawSince>=0.12 else { return nil }
        confirmed=orientation; history.append((orientation,now)); history=history.filter { now-$0.1<=4 }
        guard history.suffix(4).map(\.0)==["V","H","V","H"] else { return nil }
        let label=v.x<0 ? "Dash_Left":"Dash_Right"; reset(); return label
    }
    mutating func reset(){ history.removeAll(); raw=nil; confirmed=nil }
}

#if !ROOM_PROTOCOL_TEST
import AVFoundation
import ImageIO
import Vision

final class GestureCamera:NSObject,ObservableObject,AVCaptureVideoDataOutputSampleBufferDelegate {
    @Published private(set) var gesture="None"
    @Published private(set) var confidence=0.0
    @Published private(set) var status="Camera off"
    let session=AVCaptureSession()
    private let queue=DispatchQueue(label:"room.gesture-camera")
    @Published private(set) var processedFrames=0
    private var imageOrientation:CGImagePropertyOrientation = .right
    private let request=VNDetectHumanHandPoseRequest()
    private var configured=false,lastFrame=0.0,swing=PhoneFingerSwing()
    private var pulse:(label:String,until:Double)?
    override init(){ super.init(); request.maximumHandCount=1 }

    func start(){
        switch AVCaptureDevice.authorizationStatus(for:.video) {
        case .authorized:startAuthorized()
        case .notDetermined:
            publish("None",0,"Camera permission requested")
            AVCaptureDevice.requestAccess(for:.video){[weak self] ok in ok ? self?.startAuthorized():self?.publish("None",0,"Camera permission denied")}
        default:publish("None",0,"Camera permission denied")
        }
    }
    private func startAuthorized(){ queue.async{[weak self] in
        guard let self else{return}
        if !configured { do { try configure() } catch { publish("None",0,"Camera unavailable: \(error.localizedDescription)"); return } }
        guard !session.isRunning else{return}; session.startRunning(); publish("None",0,"Rear camera starting")
    }}
    private func configure() throws {
        guard let camera=AVCaptureDevice.default(.builtInWideAngleCamera,for:.video,position:.back) else { throw NSError(domain:"GestureCamera",code:1,userInfo:[NSLocalizedDescriptionKey:"Rear camera not found"]) }
        session.beginConfiguration(); defer{session.commitConfiguration()}; session.sessionPreset = .medium
        let input=try AVCaptureDeviceInput(device:camera); guard session.canAddInput(input) else{throw NSError(domain:"GestureCamera",code:2)}; session.addInput(input)
        let output=AVCaptureVideoDataOutput(); output.alwaysDiscardsLateVideoFrames=true
        output.videoSettings=[kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_420YpCbCr8BiPlanarFullRange]
        output.setSampleBufferDelegate(self,queue:queue); guard session.canAddOutput(output) else{throw NSError(domain:"GestureCamera",code:3)}; session.addOutput(output)
        if let connection=output.connection(with:.video) {
            if connection.isVideoMirroringSupported { connection.isVideoMirrored=false }
            // This app is portrait-only. Rotate buffers once, then give Vision upright pixels.
            if connection.isVideoRotationAngleSupported(90) { connection.videoRotationAngle=90; imageOrientation = .up }
        }
        configured=true
    }
    func stop(){queue.async{[weak self] in guard let self else{return}; if session.isRunning{session.stopRunning()}; swing.reset();pulse=nil;publish("None",0,"Camera off")}}
    func captureOutput(_ output:AVCaptureOutput,didOutput sampleBuffer:CMSampleBuffer,from connection:AVCaptureConnection){
        let now=ProcessInfo.processInfo.systemUptime; guard now-lastFrame>=1.0/15 else{return};lastFrame=now
        DispatchQueue.main.async { [weak self] in self?.processedFrames += 1 }
        do {
            try VNImageRequestHandler(cmSampleBuffer:sampleBuffer,orientation:imageOrientation).perform([request])
            guard let observation=request.results?.first,let pose=try makePose(observation),pose.confidence>=0.45 else{_=swing.update(pose:nil,at:now);publish("None",0,"Rear camera live · show your whole hand");return}
            if let dash=swing.update(pose:pose,at:now){pulse=(dash,now+0.65)}
            let label=pulse.flatMap{now<=$0.until ? $0.label:nil} ?? pose.cannedLabel();if pulse != nil && now>pulse!.until{pulse=nil}
            let score=label=="None" ? 0:pose.confidence;publish(score>=0.55 ? label:"None",score,score>=0.55 ? "Rear camera · recognizing locally":"Rear camera · low-confidence hand")
        }catch{publish("None",0,"Recognition error")}
    }
    private func makePose(_ observation:VNHumanHandPoseObservation)throws->PhoneHandPose?{
        let points=try observation.recognizedPoints(.all)
        func p(_ name:VNHumanHandPoseObservation.JointName)->PhoneHandPose.Point?{guard let v=points[name],v.confidence>=0.35 else{return nil};return .init(x:v.location.x,y:v.location.y,confidence:Double(v.confidence))}
        guard let a=p(.wrist),let b=p(.thumbCMC),let c=p(.thumbIP),let d=p(.thumbTip),let e=p(.indexMCP),let f=p(.indexPIP),let g=p(.indexTip),let h=p(.middleMCP),let i=p(.middlePIP),let j=p(.middleTip),let k=p(.ringMCP),let l=p(.ringPIP),let m=p(.ringTip),let n=p(.littleMCP),let o=p(.littlePIP),let q=p(.littleTip) else{return nil}
        return .init(wrist:a,thumbCMC:b,thumbIP:c,thumbTip:d,indexMCP:e,indexPIP:f,indexTip:g,middleMCP:h,middlePIP:i,middleTip:j,ringMCP:k,ringPIP:l,ringTip:m,littleMCP:n,littlePIP:o,littleTip:q)
    }
    private func publish(_ label:String,_ score:Double,_ message:String){DispatchQueue.main.async{[weak self] in self?.gesture=label;self?.confidence=label=="None" ? 0:score;self?.status=message}}
}
#else
final class GestureCamera:ObservableObject {
    @Published private(set) var gesture="None";@Published private(set) var confidence=0.0;@Published private(set) var status="Camera unavailable in protocol test"
    func start(){};func stop(){gesture="None";confidence=0}
}
#endif
