import Foundation
import Combine

struct PhoneHandPose {
    struct Point { var x:Double; var y:Double; var z:Double=0; var confidence:Double }
    enum FingerState:String { case extended="up", curled="curled", uncertain="?" }
    var wrist, thumbCMC, thumbIP, thumbTip:Point
    var indexMCP, indexPIP, indexTip:Point
    var middleMCP, middlePIP, middleTip:Point
    var ringMCP, ringPIP, ringTip:Point
    var littleMCP, littlePIP, littleTip:Point

    private func state(_ mcp:Point,_ pip:Point,_ tip:Point)->FingerState {
        let a=(pip.x-mcp.x,pip.y-mcp.y,pip.z-mcp.z)
        let b=(tip.x-pip.x,tip.y-pip.y,tip.z-pip.z)
        let proximal=sqrt(a.0*a.0+a.1*a.1+a.2*a.2)
        let distal=sqrt(b.0*b.0+b.1*b.1+b.2*b.2)
        guard proximal>1e-6,distal>1e-6 else{return .uncertain}
        let cosine=(a.0*b.0+a.1*b.1+a.2*b.2)/(proximal*distal)
        let dx=tip.x-mcp.x,dy=tip.y-mcp.y,dz=tip.z-mcp.z
        let reach=sqrt(dx*dx+dy*dy+dz*dz)/proximal
        // A gap between thresholds prevents a partly bent finger from becoming
        // either a fist or an extended finger just because one comparison failed.
        if cosine>0.5 && reach>1.35 {return .extended}
        if cosine<0.1 || reach<1.15 {return .curled}
        return .uncertain
    }
    var fingerStates:[FingerState] {[
        state(indexMCP,indexPIP,indexTip),state(middleMCP,middlePIP,middleTip),
        state(ringMCP,ringPIP,ringTip),state(littleMCP,littlePIP,littleTip)
    ]}
    var fingerReadout:String {
        zip(["thumb","index","middle","ring","pinky"],[state(thumbCMC,thumbIP,thumbTip)] + fingerStates).map{"\($0.0): \($0.1.rawValue)"}.joined(separator:" · ")
    }
    func command(canned:String,score:Double)->(String,Double) {
        let f=fingerStates
        // Rule scores are acceptance flags for the existing command transport,
        // not class probabilities. The UI exposes the actual raw model score separately.
        let thumb=state(thumbCMC,thumbIP,thumbTip)
        if f.allSatisfy({$0 == .curled}) {
            // Classify thumb direction before fist so thumbs never call the drone.
            if thumb == .extended {
                if score >= 0.55 && (canned == "Thumb_Up" || canned == "Thumb_Down") { return (canned,score) }
                return ("None",0)
            }
            if thumb == .curled { return ("Closed_Fist",1) }
        }
        if f == [.extended,.curled,.curled,.curled] {return ("Pointing_Up",1)}
        if f == [.extended,.extended,.curled,.curled] {return ("Victory",1)}
        if f.allSatisfy({$0 == .extended}), thumb == .extended {return ("Open_Palm",1)}
        return ("None",0)
    }
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
    @Published private(set) var rawStatus="Waiting for camera frames"
    @Published private(set) var handLandmarks:[CGPoint]=[]
    @Published private(set) var frameSize=CGSize(width:480,height:640)
    let session=AVCaptureSession()
    private let queue=DispatchQueue(label:"room.gesture-camera",qos:.userInitiated)
    private var recognizer:GestureRecognizer?
    private var rotationCoordinator:AVCaptureDevice.RotationCoordinator?
    private var rotationObservation:NSKeyValueObservation?
    private var configured=false, active=false, lastFrame=0.0, lastTimestamp=0
    private var filter=PhoneGestureFilter()

    override init() {
        super.init()
        for name in [AVCaptureSession.wasInterruptedNotification, AVCaptureSession.runtimeErrorNotification] {
            NotificationCenter.default.addObserver(self,selector:#selector(captureInterrupted(_:)),name:name,object:session)
        }
    }
    deinit { NotificationCenter.default.removeObserver(self) }
    @objc private func captureInterrupted(_ notification:Notification) { queue.async { [weak self] in
        guard let self else{return};filter.reset()
        reportRaw("Camera interrupted",points:[],size:nil)
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
        session.beginConfiguration(); defer{session.commitConfiguration()}
        guard session.canSetSessionPreset(.vga640x480) else { throw NSError(domain:"GestureCamera",code:5,userInfo:[NSLocalizedDescriptionKey:"640×480 camera capture unavailable"]) }
        session.sessionPreset = .vga640x480
        // A failed setup can be retried without retaining half a capture graph.
        for old in session.inputs { session.removeInput(old) }; for old in session.outputs { session.removeOutput(old) }
        guard session.canAddInput(input) else{throw NSError(domain:"GestureCamera",code:2)}; session.addInput(input)
        let output=AVCaptureVideoDataOutput(); output.alwaysDiscardsLateVideoFrames=true
        output.videoSettings=[kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA]
        output.setSampleBufferDelegate(self,queue:queue)
        guard session.canAddOutput(output) else{throw NSError(domain:"GestureCamera",code:3)}; session.addOutput(output)
        if let connection=output.connection(with:.video) {
            if connection.isVideoMirroringSupported { connection.automaticallyAdjustsVideoMirroring=false; connection.isVideoMirrored=true }
            // Sensor mounting differs between cameras. Ask AVFoundation for this
            // camera's upright angle instead of assuming that every front camera is 90°.
            let rotation=AVCaptureDevice.RotationCoordinator(device:camera,previewLayer:nil)
            rotationCoordinator=rotation
            func quarterTurn(_ angle:CGFloat)->CGFloat { (round(angle/90)*90).truncatingRemainder(dividingBy:360) }
            let angle=quarterTurn(rotation.videoRotationAngleForHorizonLevelCapture)
            if connection.isVideoRotationAngleSupported(angle) { connection.videoRotationAngle=angle }
            rotationObservation=rotation.observe(\.videoRotationAngleForHorizonLevelCapture,options:[.new]) { [weak self] coordinator,_ in
                let angle=quarterTurn(coordinator.videoRotationAngleForHorizonLevelCapture)
                self?.queue.async { [weak self] in
                    guard let self,connection.videoRotationAngle != angle,connection.isVideoRotationAngleSupported(angle) else{return}
                    connection.videoRotationAngle=angle;filter.reset()
                }
            }
        }
        configured=true
    }
    func stop(){queue.async{[weak self] in
        guard let self else{return}; active=false
        if session.isRunning{session.stopRunning()}
        filter.reset();publish("None",0,"Camera off")
        reportRaw("Camera off",points:[],size:nil)
    }}
    func captureOutput(_ output:AVCaptureOutput,didOutput sampleBuffer:CMSampleBuffer,from connection:AVCaptureConnection){
        let now=ProcessInfo.processInfo.systemUptime
        guard active, now-lastFrame>=1.0/15, let recognizer else{return};lastFrame=now
        DispatchQueue.main.async { [weak self] in self?.processedFrames += 1 }
        do {
            let image=try MPImage(sampleBuffer:sampleBuffer,orientation:.up)
            lastTimestamp=max(lastTimestamp+1,Int(now*1000))
            let result=try recognizer.recognize(videoFrame:image,timestampInMilliseconds:lastTimestamp)
            let buffer=CMSampleBufferGetImageBuffer(sampleBuffer)
            let size=buffer.map{CGSize(width:CVPixelBufferGetWidth($0),height:CVPixelBufferGetHeight($0))} ?? .zero
            let top=result.gestures.first?.first
            let rawLabel=top?.categoryName ?? "None", rawScore=Double(top?.score ?? 0)
            let points=(result.landmarks.first ?? []).map{CGPoint(x:CGFloat($0.x),y:CGFloat($0.y))}
            let raw="Hands: \(result.landmarks.count) · raw: \(rawLabel) \(Int(rawScore*100))% · \(Int(size.width))×\(Int(size.height)) · rotation \(Int(connection.videoRotationAngle))°"
            reportRaw(raw,points:points,size:size)
            guard let landmarks=result.landmarks.first, landmarks.count==21,
                  let buffer else {
                let stable=filter.update(label:"None",score:0,at:now)
                publish(stable.0,stable.1,"MediaPipe · no hand detected"); return
            }
            // Use MediaPipe's 3D hand geometry for bend angles. This avoids
            // treating a finger aimed toward the camera as a curled 2D projection.
            let world=result.worldLandmarks.first
            let aspect=Double(CVPixelBufferGetWidth(buffer))/Double(CVPixelBufferGetHeight(buffer))
            func p(_ i:Int)->PhoneHandPose.Point {
                if let world,world.count==21 {return .init(x:Double(world[i].x),y:Double(world[i].y),z:Double(world[i].z),confidence:1)}
                return .init(x:Double(landmarks[i].x)*aspect,y:Double(landmarks[i].y),z:Double(landmarks[i].z)*aspect,confidence:1)
            }
            let pose=PhoneHandPose(wrist:p(0),thumbCMC:p(1),thumbIP:p(3),thumbTip:p(4),
                indexMCP:p(5),indexPIP:p(6),indexTip:p(8),middleMCP:p(9),middlePIP:p(10),middleTip:p(12),
                ringMCP:p(13),ringPIP:p(14),ringTip:p(16),littleMCP:p(17),littlePIP:p(18),littleTip:p(20))
            reportRaw(raw+"\n"+pose.fingerReadout,points:points,size:size)
            let candidate=pose.command(canned:rawLabel,score:rawScore)
            let stable=filter.update(label:candidate.0,score:candidate.1,at:now)
            publish(stable.0,stable.1,"MediaPipe · recognizing on this iPhone")
        }catch{
            filter.reset()
            reportRaw("Inference failed",points:[],size:nil)
            publish("None",0,"MediaPipe error: \(error.localizedDescription)")
        }
    }
    private func reportRaw(_ text:String,points:[CGPoint],size:CGSize?) { DispatchQueue.main.async { [weak self] in
        guard let self else{return};rawStatus=text;handLandmarks=points
        if let size, size.width>0, size.height>0 {frameSize=size}
    }}
    private func publish(_ label:String,_ score:Double,_ message:String){DispatchQueue.main.async{[weak self] in self?.gesture=label;self?.confidence=label=="None" ? 0:score;self?.status=message}}
}
#else
final class GestureCamera:ObservableObject {
    @Published private(set) var gesture="None";@Published private(set) var confidence=0.0;@Published private(set) var status="Camera unavailable in protocol test"
    var rawStatus="Camera unavailable in protocol test"
    func start(){};func stop(){gesture="None";confidence=0}
}
#endif
