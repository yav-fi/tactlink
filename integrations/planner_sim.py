"""Independent Python 3D mission preview. Never connects to an aircraft.

Run: python -m integrations.planner_sim --url http://127.0.0.1:8766
"""
import argparse
import math
from pathlib import Path
import sys
import time

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
    status = 'Loaded. SPACE play/pause | R restart | L load latest | Q quit'
    window = 'Planner mission - simulation only'
    try:
        while True:
            start = time.perf_counter()
            if not paused:
                follower.step()
            image = visualizer.render(follower.sim.state, follower.cmd, trails=[follower.sim.trail])
            index = min(follower.index, len(follower.targets)-1)
            label = f'{"Complete" if follower.done else "Paused" if paused else "Playing"} | Command {follower.targets[index][2]+1}'
            cv2.putText(image,label,(20,640),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,220,255),1)
            cv2.putText(image,status,(20,680),cv2.FONT_HERSHEY_SIMPLEX,.45,(220,220,220),1)
            cv2.imshow(window,image)
            key = cv2.waitKey(max(1,int(1000*(1/60-(time.perf_counter()-start))))) & 255
            if key == ord('q') or cv2.getWindowProperty(window,cv2.WND_PROP_VISIBLE)<1:
                break
            if key == ord(' '):
                paused = not paused
            if key == ord('r'):
                follower = MissionFollower(preview); paused = True
            if key == ord('l'):
                paused = True
                try:
                    replacement = load_preview(args.url)
                    follower = MissionFollower(replacement); preview = replacement
                    status = 'Latest mission loaded. SPACE play | R restart | L reload | Q quit'
                except (ValueError, KeyError, httpx.HTTPError):
                    status = 'Load failed; previous mission kept and paused. Check planner server.'
    finally:
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
