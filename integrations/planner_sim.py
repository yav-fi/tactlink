"""Independent Python 3D mission preview. Never connects to an aircraft.

Run: python -m integrations.planner_sim --url http://127.0.0.1:8766
"""
import argparse
import math
from pathlib import Path
import sys
import time
import threading

import httpx
import numpy as np

# Existing src modules use sibling imports. Import them without modifying src.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from control_types import ControlInput
from simulator import QuadSimulator, MAX_SPEED, MAX_CLIMB

from integrations.flight_path import build_preview


def load_preview(url):
    response = httpx.get(url.rstrip('/') + '/api/preview', timeout=5)
    response.raise_for_status()
    data = response.json()
    if data.get('frame') != 'ENU' or data.get('units') != 'meters':
        raise ValueError('Expected an ENU preview in meters.')
    if not data.get('segments') or sum(len(s['points']) for s in data['segments']) > 20000:
        raise ValueError('Preview is empty or too large.')
    for segment in data['segments']:
        if segment['type'] not in ('line', 'vertical', 'hold', 'arc') or not segment['points']:
            raise ValueError('Unsupported segment.')
        for point in segment['points']:
            if not all(math.isfinite(point[k]) for k in ('x', 'y', 'z')):
                raise ValueError('Nonfinite path coordinate.')
    return data


class MissionFollower:
    def __init__(self, preview):
        self.preview = preview
        self.sim = QuadSimulator()
        self.targets = []
        for segment in preview['segments']:
            command = preview['commands'][segment['command_index']]['type']
            points = segment['points'][1:] or segment['points']
            if segment['type'] == 'hold':
                points = [segment['end']]
            for point in points:
                self.targets.append((np.array([point[k] for k in ('x','y','z')]),
                    segment.get('duration_s', 0), segment['command_index'], command))
        self.index = 0
        self.hold = 0
        self.done = False
        self.cmd = ControlInput()

    def step(self, dt=1/60):
        if self.done:
            return self.sim.state
        target, duration, _, kind = self.targets[self.index]
        state = self.sim.state
        delta = target-state.pos
        # A damped position controller drives the existing velocity/stick model.
        velocity = np.clip(delta * 1.5 - state.vel * .7, -3, 3)
        cy, sy = math.cos(state.yaw), math.sin(state.yaw)
        self.cmd = ControlInput(armed=True,
            roll=float(np.clip((cy*velocity[0]+sy*velocity[1])/MAX_SPEED,-1,1)),
            pitch=float(np.clip((-sy*velocity[0]+cy*velocity[1])/MAX_SPEED,-1,1)),
            throttle=float(np.clip(velocity[2]/MAX_CLIMB,-1,1)))
        if kind == 'land' and state.pos[2] < .08:
            self.cmd.armed = False
        self.sim.step(self.cmd,dt)
        reached = np.linalg.norm(target-state.pos) < .12 and np.linalg.norm(state.vel) < .2
        if kind == 'land':
            reached = reached and state.on_ground
        self.hold = self.hold + dt if reached else 0
        if reached and self.hold >= duration:
            self.index += 1
            self.hold = 0
            if self.index == len(self.targets):
                self.done = True
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8766')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--camera', type=int, default=0)
    args = parser.parse_args()
    demo = 'take off to 3 m, fly north 5 m, orbit home at radius 5 m clockwise once, hover for 2 seconds, return home, land'
    try:
        preview = build_preview(demo) if args.demo else load_preview(args.url)
    except (ValueError, KeyError, httpx.HTTPError) as error:
        parser.exit(1, f'Cannot load mission: {error}. Validate a mission in the planner first.\n')
    follower = MissionFollower(preview)
    if args.headless:
        for _ in range(60*1200):
            follower.step()
            if follower.done:
                print('Completed simulation:', follower.sim.state.pos.tolist())
                return
        parser.exit(1, 'Simulation did not finish within 1200 simulated seconds.\n')
    import cv2
    from visualizer import Visualizer
    visualizer = Visualizer(960,720)
    paused = True
    gesture_camera = None
    def close_gestures():
        nonlocal gesture_camera
        if gesture_camera:
            gesture_camera.close()
            gesture_camera = None
    publish_stop = threading.Event()
    # Network waits run off the rendering thread. Snapshot updates never drive flight.
    def publish():
        with httpx.Client(timeout=1) as client:
            while not publish_stop.wait(.25):
                active = follower
                point = active.sim.state.pos.copy()
                try:
                    client.post(args.url.rstrip('/')+'/api/simulation', json={
                        'mission_id': active.preview['mission_id'],
                        'x':float(point[0]),'y':float(point[1]),'z':float(point[2]),
                        'paused':paused,'complete':active.done and gesture_camera is None})
                except httpx.HTTPError:
                    pass
    if not args.demo:
        threading.Thread(target=publish,daemon=True).start()
    status = 'SPACE play/pause | G gestures | M mission (paused) | R restart | L load | Q quit'
    window = 'Planner mission - simulation only'
    previous_frame = time.perf_counter()
    try:
        while True:
            start = time.perf_counter()
            dt = min(.1, max(.001,start-previous_frame))
            previous_frame = start
            camera_frame = None
            if gesture_camera and not paused:
                try:
                    follower.cmd, camera_frame = gesture_camera.read(follower.sim.state)
                    follower.sim.step(follower.cmd,dt)
                except Exception as error:
                    paused = True
                    close_gestures()
                    status = f'Gesture mode stopped: {error}'[:110]
            elif not paused:
                follower.step(dt)
            image = visualizer.render(follower.sim.state, follower.cmd, trails=[follower.sim.trail])
            index = min(follower.index, len(follower.targets)-1)
            label = f'{"Paused" if paused else "Gestures" if gesture_camera else "Complete" if follower.done else "Playing"} | Mission command {follower.targets[index][2]+1}'
            cv2.putText(image,label,(20,640),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,220,255),1)
            cv2.putText(image,status,(20,680),cv2.FONT_HERSHEY_SIMPLEX,.45,(220,220,220),1)
            if gesture_camera:
                cv2.putText(image,gesture_camera.takeover.feedback,(20,610),cv2.FONT_HERSHEY_SIMPLEX,.5,(80,240,140),1)
            cv2.putText(image,f'{1/dt:.0f} FPS',(840,610),cv2.FONT_HERSHEY_SIMPLEX,.5,(80,240,140),1)
            if camera_frame is not None:
                image = np.hstack((cv2.resize(camera_frame,(640,720)),image))
            cv2.imshow(window,image)
            key = cv2.waitKey(max(1,int(1000*(1/60-(time.perf_counter()-start))))) & 255
            if key == ord('q') or cv2.getWindowProperty(window,cv2.WND_PROP_VISIBLE)<1:
                break
            if key == ord(' '):
                paused = not paused
            if key == ord('g'):
                paused = True
                close_gestures()
                try:
                    from integrations.gesture_preview import GestureCamera
                    gesture_camera = GestureCamera(follower.sim.state,args.camera)
                    paused = False
                    status = 'GESTURES active | SPACE freeze | M mission paused | G reconnect | Q quit'
                except Exception as error:
                    status = f'Cannot start gestures: {error}'[:110]
            if key == ord('m'):
                close_gestures(); paused = True; follower.hold = 0
                status = 'MISSION paused: SPACE resumes toward next pending waypoint | G gestures'
            if key == ord('r'):
                close_gestures()
                follower = MissionFollower(preview); paused = True
            if key == ord('l'):
                close_gestures()
                paused = True
                try:
                    replacement = load_preview(args.url)
                    follower = MissionFollower(replacement); preview = replacement
                    status = 'Latest mission loaded. SPACE play | R restart | L reload | Q quit'
                except (ValueError, KeyError, httpx.HTTPError):
                    status = 'Load failed; previous mission kept and paused. Check planner server.'
    finally:
        close_gestures()
        publish_stop.set()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
