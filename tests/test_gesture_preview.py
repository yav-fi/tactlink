import unittest
from integrations.planner_sim import MissionFollower
from integrations.flight_path import build_preview
from integrations.gesture_preview import GestureTakeover
from control_types import HandState

class GesturePreviewTests(unittest.TestCase):
    def test_directional_gestures_move_simulator(self):
        import math
        for direction in ('left','right','forward'):
            with self.subTest(direction=direction):
                follower=MissionFollower(build_preview('take off to 10 m'))
                state=follower.sim.state
                state.pos[2]=10;state.armed=True;state.on_ground=False
                if direction=='forward': state.yaw=math.pi/2
                adapter=GestureTakeover(state)
                now=10
                poses=([(0,-1),(1 if direction=='right' else -1,0),(0,-1),(1 if direction=='right' else -1,0)]
                       if direction!='forward' else [(0,-1)]*4)
                for point in poses:
                    hand=HandState(present=True,gesture='None',fingers=(False,True,True,direction=='forward',False),point_dir=point)
                    for _ in range(8):
                        now+=.05
                        cmd=adapter.update(hand,state,now=now);follower.sim.step(cmd,.05)
                for _ in range(100):
                    now+=.05
                    cmd=adapter.update(HandState(present=True,gesture='None'),state,now=now)
                    follower.sim.step(cmd,.05)
                self.assertGreater(state.pos[0] if direction=='right' else -state.pos[0],4)
                self.assertLess(abs(state.pos[2]-10),.5)

    def test_pointing_up_orbits_and_tracking_loss_halts(self):
        follower=MissionFollower(build_preview('take off to 10 m'))
        state=follower.sim.state
        state.pos[2]=10; state.armed=True; state.on_ground=False
        adapter=GestureTakeover(state)
        hand=HandState(present=True,gesture='Pointing_Up',gesture_source='canned')
        for i in range(30):
            cmd=adapter.update(hand,state,now=10+i*.1)
            follower.sim.step(cmd,.1)
        self.assertEqual(adapter.controller.maneuver,'orbit')
        self.assertGreater(float(abs(state.pos[0])+abs(state.pos[1])),1)
        adapter.update(HandState(),state,now=13)
        self.assertEqual(adapter.controller.maneuver,'orbit')
        adapter.update(HandState(),state,now=13.4)
        self.assertEqual(adapter.controller.maneuver,'')

    def test_takeover_preserves_airborne_state_and_mission_cursor(self):
        follower=MissionFollower(build_preview('take off to 10 m, fly north 10 m'))
        for _ in range(300): follower.step()
        cursor=follower.index
        altitude=follower.sim.state.pos[2]
        controller=GestureTakeover(follower.sim.state)
        for _ in range(60):
            cmd=controller.update(HandState(present=False),follower.sim.state)
            follower.sim.step(cmd,1/60)
        self.assertTrue(cmd.armed)
        self.assertEqual(follower.index,cursor)
        self.assertLess(abs(follower.sim.state.pos[2]-altitude),.5)

    def test_no_hand_does_not_arm_grounded_drone(self):
        follower=MissionFollower(build_preview('take off to 10 m'))
        cmd=GestureTakeover(follower.sim.state).update(HandState(),follower.sim.state)
        self.assertFalse(cmd.armed)
