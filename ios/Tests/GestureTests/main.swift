import Foundation

func check(_ value:@autoclosure()->Bool,_ message:String){if !value(){print("FAIL: \(message)");exit(1)}}
typealias P=PhoneHandPose.Point
func point(_ x:Double,_ y:Double)->P{.init(x:x,y:y,confidence:0.9)}

func pose(_ fingers:(Bool,Bool,Bool,Bool,Bool), vector:(Double,Double)=(0,1))->PhoneHandPose {
    let wrist=point(0.5,0.1)
    func finger(_ x:Double,_ up:Bool,_ dx:Double=0,_ dy:Double=0.5)->(P,P,P){
        let m=point(x,0.3), p=point(x,up ? 0.55:0.45)
        return (m,p,point(up ? x+dx:x,up ? 0.3+dy:0.32))
    }
    let i=finger(0.42,fingers.1,vector.0/2,vector.1/2),m=finger(0.50,fingers.2,vector.0/2,vector.1/2)
    let r=finger(0.58,fingers.3),l=finger(0.66,fingers.4)
    let thumbTip = fingers.0 ? point(0.82,0.48):point(0.59,0.24)
    return .init(wrist:wrist,thumbCMC:point(0.56,0.22),thumbIP:point(0.68,0.34),thumbTip:thumbTip,
                 indexMCP:i.0,indexPIP:i.1,indexTip:i.2,middleMCP:m.0,middlePIP:m.1,middleTip:m.2,
                 ringMCP:r.0,ringPIP:r.1,ringTip:r.2,littleMCP:l.0,littlePIP:l.1,littleTip:l.2)
}

check(pose((false,true,true,true,false)).cannedLabel()=="Three_Finger_Forward","three-finger pose")
check(pose((true,true,false,false,true)).cannedLabel()=="ILoveYou","I-love-you pose")
check(pose((false,true,false,false,false)).cannedLabel()=="Pointing_Up","point pose")
check(pose((false,true,true,true,true)).cannedLabel()=="Open_Palm","open palm")

var swing=PhoneFingerSwing();var result:String?
let sequence=[pose((false,true,true,false,false),vector:(0,1)),pose((false,true,true,false,false),vector:(-1,0)),pose((false,true,true,false,false),vector:(0,1)),pose((false,true,true,false,false),vector:(-1,0))]
var t=0.0
for item in sequence { _=swing.update(pose:item,at:t);t+=0.13;result=swing.update(pose:item,at:t);t+=0.02 }
check(result=="Dash_Left","mirrored V-H-V-H resolves left")
print("Gesture geometry tests passed")
