"""Gesture takeover adapter for the standalone simulated mission viewer."""
from control_types import HandState, GestureState
from controls import GestureController
from gestures import GestureInterpreter
import time
import math


class GestureTakeover:
    def __init__(self, state):
        self.interpreter = GestureInterpreter()
        self.controller = GestureController()
        # Existing controller has no public state-adoption API. Seed only here,
        # without changing src or triggering an unintended takeoff maneuver.
        self.controller._armed = state.armed or not state.on_ground
        self.controller._alt_target = float(state.pos[2])
        self.controller._cmd.armed = self.controller._armed
        self.last_gesture = 'None'
        self.last_seen = None
        self.feedback = ''

    def update(self, hand, state, now=None):
        now = time.monotonic() if now is None else now
        if hand.present:
            self.last_seen = now
        self.last_gesture = hand.gesture if hand.present else 'No hand - hold'
        gestures = self.interpreter.update(hand.gesture if hand.present else 'None',
                                           hand.gesture_source, hand=hand, now=now)
        # The standalone preview has no operator-facing tracker. Forward means
        # the simulated drone's heading at the instant the gesture fires.
        gestures.events = [f'fly_bearing:{state.yaw + math.pi/2}' if event == 'fly_forward' else event
                           for event in gestures.events]
        if not hand.present:
            # One missed detection should not cancel orbit. Sustained loss halts.
            gestures.events = ['halt'] if self.last_seen is None or now-self.last_seen >= .35 else []
        command = self.controller.update(hand, gestures, state)
        self.feedback = f'{self.last_gesture} | {self.controller.maneuver or "hold"} | hold {gestures.hold_progress:.0%}'
        return command


class GestureCamera:
    def __init__(self, state, camera=0):
        import cv2
        from hand_tracker import HandTracker
        self.capture = cv2.VideoCapture(camera)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH,640)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
        self.capture.set(cv2.CAP_PROP_FPS,30)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE,1)
        self.tracker = None
        try:
            if not self.capture.isOpened():
                raise RuntimeError('Cannot open webcam. Close other camera apps.')
            self.tracker = HandTracker()
        except Exception:
            self.close()
            raise
        self.takeover = GestureTakeover(state)

    def read(self, state):
        import cv2
        ok, frame = self.capture.read()
        if not ok:
            raise RuntimeError('Webcam frame unavailable. Simulation paused.')
        frame = cv2.flip(frame, 1)
        if frame.shape[1] > 640:
            frame = cv2.resize(frame,(640,round(frame.shape[0]*640/frame.shape[1])))
        hand = self.tracker.process(frame)
        self.tracker.draw(frame)
        return self.takeover.update(hand, state), frame

    def close(self):
        self.capture.release()
        if self.tracker:
            self.tracker.close()
