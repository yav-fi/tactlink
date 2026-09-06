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
        let ax=pip.x-mcp.x, ay=pip.y-mcp.y, bx=tip.x-pip.x, by=tip.y-pip.y
        let cosine=(ax*bx+ay*by)/(hypot(ax,ay)*hypot(bx,by)+1e-6)
        return cosine>0.35 && distance(wrist,tip)>distance(wrist,pip)*1.02
    }
    var fingers: (thumb:Bool,index:Bool,middle:Bool,ring:Bool,little:Bool) {
        let thumb=distance(wrist,thumbTip)>distance(wrist,thumbIP)*1.12 && distance(thumbCMC,thumbTip)>distance(thumbCMC,thumbIP)*1.18
        return (thumb,extended(indexTip,indexPIP,indexMCP),extended(middleTip,middlePIP,middleMCP),extended(ringTip,ringPIP,ringMCP),extended(littleTip,littlePIP,littleMCP))
    }
    var pointVector:(x:Double,y:Double) { ((indexTip.x-indexMCP.x)+(middleTip.x-middleMCP.x),(indexTip.y-indexMCP.y)+(middleTip.y-middleMCP.y)) }
    var confidence:Double { [wrist,thumbTip,indexTip,middleTip,ringTip,littleTip].map(\.confidence).min() ?? 0 }
    // MediaPipe supplies the learned categories. Geometry only adds the PC's
    // custom signs and separates vertical pointing, including upside-down hands.
    func directionalLabel(canned:String,score:Double)->(String,Double) {
        let f=fingers
        if f.index && !f.middle && !f.ring && !f.little {
            let dx=indexTip.x-indexMCP.x, dy=indexTip.y-indexMCP.y
            if abs(dy)>abs(dx)*1.2 {
                return (dy>0 ? "Pointing_Up":"Pointing_Down", canned=="Pointing_Up" ? max(score,0.8):0.8)
            }
            return ("None",0)
        }
        if f.index && f.middle && f.ring && !f.little { return ("Three_Finger_Forward",0.8) }
        // Never call an arbitrary index-pointing direction "up".
        if canned=="Pointing_Up" { return ("None",0) }
        return score>=0.55 ? (canned,score):("None",0)
    }
}

/// V-H-V-H detector equivalent to `src/finger_swing.py`. Points are from an
/// upright mirrored selfie image, so negative x is left in the preview.
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

/// Vote across recent frames; brief uncertain frames cannot flip a stable command.
struct PhoneGestureFilter {
    private var history:[(label:String,score:Double,time:Double)]=[]
    private var stable="None", score=0.0, lastSeen = -Double.infinity
    mutating func update(label:String,score:Double,at now:Double)->(String,Double) {
        let label=score>=0.55 ? label:"None"
        history.append((label,score,now)); history.removeAll{now-$0.time>0.35}
        if label==stable && label != "None" { lastSeen=now }
        let matching=history.filter{$0.label==label}
        if label != "None", matching.count>=3,
           Double(matching.count)/Double(history.count)>=0.67,
           now-matching[0].time>=0.12 {
            stable=label; self.score=matching.map(\.score).reduce(0,+)/Double(matching.count); lastSeen=now
        }
        if now-lastSeen>0.18 { stable="None"; self.score=0 }
        return (stable,self.score)
    }
    mutating func reset(){self=PhoneGestureFilter()}
}

#if !ROOM_PROTOCOL_TEST
import AVFoundation
import UIKit
import MediaPipeTasksVision

final class GestureCamera:NSObject,ObservableObject,AVCaptureVideoDataOutputSampleBufferDelegate {
    @Published private(set) var gesture="None"
    @Published private(set) var confidence=0.0
    @Published private(set) var status="Camera off"
    @Published private(set) var processedFrames=0
    let session=AVCaptureSession()
    private let queue=DispatchQueue(label:"room.gesture-camera",qos:.userInitiated)
    private var recognizer:GestureRecognizer?
    private var configured=false, active=false, lastFrame=0.0, lastTimestamp=0
    private var swing=PhoneFingerSwing(), filter=PhoneGestureFilter()
    private var pulse:(label:String,until:Double)?

    override init() {
        super.init()
        for name in [AVCaptureSession.wasInterruptedNotification, AVCaptureSession.runtimeErrorNotification] {
            NotificationCenter.default.addObserver(self,selector:#selector(captureInterrupted(_:)),name:name,object:session)
        }
    }
    deinit { NotificationCenter.default.removeObserver(self) }
    @objc private func captureInterrupted(_ notification:Notification) { queue.async { [weak self] in
        guard let self else{return};filter.reset();swing.reset();pulse=nil
        publish("None",0,"Camera interrupted · waiting to resume")
    }}

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
        do {
            if recognizer==nil {
                guard let path=Bundle.main.path(forResource:"gesture_recognizer",ofType:"task") else {
                    throw NSError(domain:"GestureCamera",code:4,userInfo:[NSLocalizedDescriptionKey:"MediaPipe model missing; run ios/scripts/setup-gestures.sh and rebuild"])
                }
                let options=GestureRecognizerOptions()
                options.baseOptions.modelAssetPath=path
                // Same video-mode tracker as the PC, on a serial background queue.
                options.runningMode = .video; options.numHands=1
                options.minHandDetectionConfidence=0.6
                options.minHandPresenceConfidence=0.5; options.minTrackingConfidence=0.5
                recognizer=try GestureRecognizer(options:options)
            }
            if !configured { try configure() }
            active=true
            guard !session.isRunning else{return}
            session.startRunning(); publish("None",0,"MediaPipe · front camera starting")
        } catch { publish("None",0,"Camera unavailable: \(error.localizedDescription)") }
    }}
    private func configure() throws {
        guard let camera=AVCaptureDevice.default(.builtInWideAngleCamera,for:.video,position:.front) else { throw NSError(domain:"GestureCamera",code:1,userInfo:[NSLocalizedDescriptionKey:"Front camera not found"]) }
        let input=try AVCaptureDeviceInput(device:camera)
        session.beginConfiguration(); defer{session.commitConfiguration()}; session.sessionPreset = .medium
        // A failed setup can be retried without retaining half a capture graph.
        for old in session.inputs { session.removeInput(old) }; for old in session.outputs { session.removeOutput(old) }
        guard session.canAddInput(input) else{throw NSError(domain:"GestureCamera",code:2)}; session.addInput(input)
        let output=AVCaptureVideoDataOutput(); output.alwaysDiscardsLateVideoFrames=true
        output.videoSettings=[kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA]
        output.setSampleBufferDelegate(self,queue:queue)
        guard session.canAddOutput(output) else{throw NSError(domain:"GestureCamera",code:3)}; session.addOutput(output)
        if let connection=output.connection(with:.video) {
            if connection.isVideoMirroringSupported { connection.automaticallyAdjustsVideoMirroring=false; connection.isVideoMirrored=true }
            // Portrait-only app: rotate actual pixels once, then pass orientation .up.
            if connection.isVideoRotationAngleSupported(90) { connection.videoRotationAngle=90 }
        }
        configured=true
    }
    func stop(){queue.async{[weak self] in
        guard let self else{return}; active=false
        if session.isRunning{session.stopRunning()}
        swing.reset();filter.reset();pulse=nil;publish("None",0,"Camera off")
    }}
    func captureOutput(_ output:AVCaptureOutput,didOutput sampleBuffer:CMSampleBuffer,from connection:AVCaptureConnection){
        let now=ProcessInfo.processInfo.systemUptime
        guard active, now-lastFrame>=1.0/15, let recognizer else{return};lastFrame=now
        DispatchQueue.main.async { [weak self] in self?.processedFrames += 1 }
        do {
            let image=try MPImage(sampleBuffer:sampleBuffer,orientation:.up)
            lastTimestamp=max(lastTimestamp+1,Int(now*1000))
            let result=try recognizer.recognize(videoFrame:image,timestampInMilliseconds:lastTimestamp)
            guard let landmarks=result.landmarks.first, landmarks.count==21,
                  let buffer=CMSampleBufferGetImageBuffer(sampleBuffer) else {
                _=swing.update(pose:nil,at:now);pulse=nil
                let stable=filter.update(label:"None",score:0,at:now)
                publish(stable.0,stable.1,"MediaPipe · show your whole hand"); return
            }
            // Pixel aspect ratio matters for finger angles. Internal y points up.
            let aspect=Double(CVPixelBufferGetWidth(buffer))/Double(CVPixelBufferGetHeight(buffer))
            func p(_ i:Int)->PhoneHandPose.Point{.init(x:Double(landmarks[i].x)*aspect,y:1-Double(landmarks[i].y),confidence:1)}
            let pose=PhoneHandPose(wrist:p(0),thumbCMC:p(1),thumbIP:p(3),thumbTip:p(4),
                indexMCP:p(5),indexPIP:p(6),indexTip:p(8),middleMCP:p(9),middlePIP:p(10),middleTip:p(12),
                ringMCP:p(13),ringPIP:p(14),ringTip:p(16),littleMCP:p(17),littlePIP:p(18),littleTip:p(20))
            let top=result.gestures.first?.first
            var candidate=pose.directionalLabel(canned:top?.categoryName ?? "None",score:Double(top?.score ?? 0))
            if let dash=swing.update(pose:pose,at:now){pulse=(dash,now+0.8)}
            if let value=pulse { if now<=value.until { candidate=(value.label,0.8) } else {pulse=nil} }
            let stable=filter.update(label:candidate.0,score:candidate.1,at:now)
            publish(stable.0,stable.1,"MediaPipe · recognizing on this iPhone")
        }catch{
            filter.reset();swing.reset();pulse=nil
            publish("None",0,"MediaPipe error: \(error.localizedDescription)")
        }
    }
    private func publish(_ label:String,_ score:Double,_ message:String){DispatchQueue.main.async{[weak self] in self?.gesture=label;self?.confidence=label=="None" ? 0:score;self?.status=message}}
}
#else
final class GestureCamera:ObservableObject {
    @Published private(set) var gesture="None";@Published private(set) var confidence=0.0;@Published private(set) var status="Camera unavailable in protocol test"
    func start(){};func stop(){gesture="None";confidence=0}
}
#endif
