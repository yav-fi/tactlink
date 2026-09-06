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
    let i=finger(0.42,fingers.1),m=finger(0.50,fingers.2)
    let r=finger(0.58,fingers.3),l=finger(0.66,fingers.4)
    let thumbTip = fingers.0 ? point(0.82,0.48):point(0.59,0.24)
    var result=PhoneHandPose(wrist:wrist,thumbCMC:point(0.56,0.22),thumbIP:point(0.68,0.34),thumbTip:thumbTip,
                 indexMCP:i.0,indexPIP:i.1,indexTip:i.2,middleMCP:m.0,middlePIP:m.1,middleTip:m.2,
                 ringMCP:r.0,ringPIP:r.1,ringTip:r.2,littleMCP:l.0,littlePIP:l.1,littleTip:l.2)
    let angle=atan2(vector.1,vector.0)-Double.pi/2
    let keys:[WritableKeyPath<PhoneHandPose,P>]=[\.wrist,\.thumbCMC,\.thumbIP,\.thumbTip,\.indexMCP,\.indexPIP,\.indexTip,\.middleMCP,\.middlePIP,\.middleTip,\.ringMCP,\.ringPIP,\.ringTip,\.littleMCP,\.littlePIP,\.littleTip]
    for key in keys {
        let old=result[keyPath:key], x=old.x-wrist.x, y=old.y-wrist.y
        result[keyPath:key]=point(wrist.x+cos(angle)*x-sin(angle)*y,wrist.y+sin(angle)*x+cos(angle)*y)
    }
    return result
}

for vector in [(0.0,1.0),(1.0,0.0),(0.0,-1.0),(-1.0,0.0)] {
    check(pose((false,true,true,true,false),vector:vector).command(canned:"None",score:0).0=="Three_Finger_Orbit","orbit works in every hand orientation")
    check(pose((true,true,true,true,true),vector:vector).command(canned:"Open_Palm",score:0.9).0=="Four_Finger_Hover","hover ignores thumb and hand orientation")
    check(pose((false,false,false,false,false),vector:vector).command(canned:"None",score:0).0=="Closed_Fist","curled landmarks recognize fist even when canned is None")
    check(pose((false,true,false,false,false),vector:vector).command(canned:"None",score:0).0=="One_Finger_Up","one index finger climbs regardless of direction")
    check(pose((false,true,true,false,false),vector:vector).command(canned:"Victory",score:0.9).0=="Two_Fingers_Down","index and middle descend regardless of direction")
}
check(pose((true,false,false,false,false)).command(canned:"Thumb_Up",score:0.9).0=="Closed_Fist","thumb position does not interfere with curled four fingers")
check(pose((false,true,true,true,true)).command(canned:"Open_Palm",score:0.9).0=="Four_Finger_Hover","four fingers hover overhead")
check(pose((false,true,true,true,false)).command(canned:"None",score:0).0=="Three_Finger_Orbit","three fingers orbit")
check(pose((false,false,true,false,false)).command(canned:"None",score:0).0=="None","middle finger alone has no command")
var depthPose=pose((false,true,false,false,false))
// Rotate its index finger from the image plane into depth: 2D would collapse it.
let jointKeys:[WritableKeyPath<PhoneHandPose,P>]=[\.indexMCP,\.indexPIP,\.indexTip]
for key in jointKeys { let point=depthPose[keyPath:key]; depthPose[keyPath:key] = .init(x:point.x,y:0,z:point.y,confidence:1) }
check(depthPose.command(canned:"None",score:0).0=="One_Finger_Up","3D extension survives foreshortening")
var ambiguous=pose((false,true,false,false,false))
ambiguous.indexTip=ambiguous.indexPIP
check(ambiguous.command(canned:"None",score:0).0=="None","degenerate joint geometry is not a fist")

var filter=PhoneGestureFilter()
check(filter.update(label:"Closed_Fist",score:0.9,at:0).0=="None","one frame cannot command")
_=filter.update(label:"Closed_Fist",score:0.9,at:0.07)
check(filter.update(label:"Closed_Fist",score:0.9,at:0.14).0=="Closed_Fist","stable fist accepted")
check(filter.update(label:"Thumb_Up",score:0.9,at:0.21).0=="Closed_Fist","one wrong frame cannot flip command")
check(filter.update(label:"Closed_Fist",score:0.9,at:0.28).0=="Closed_Fist","recover from flicker")
_=filter.update(label:"None",score:0,at:0.35)
check(filter.update(label:"None",score:0,at:0.5).0=="None","lost hand clears command")
filter.reset()
check(filter.update(label:"Thumb_Down",score:0.99,at:1).0=="None","reset discards previous votes")

print("Five-gesture and temporal filter tests passed")
