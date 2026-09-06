# Planner to Python 3D preview

Run from the repository after validating a mission in the waypoint planner:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-flight.txt opencv-python numpy
.venv/Scripts/python.exe -m integrations.planner_sim --url http://127.0.0.1:8766
```

The separate OpenCV window starts paused. Focus that window to use:

- Space: play/pause
- R: restart the loaded mission, paused
- L: fetch the latest validated bridge preview, paused
- Q: close

Edit the mission in the browser, wait for validation, then press L in the 3D window.
The browser and Python playback controls are independent. The bridge exposes the most
recent validated preview, potentially from another tab; invalid edits do not replace it.
Review the mission before playback. Loading failures preserve the previous mission paused.

The adapter imports the existing src simulator, control types and renderer without editing
them. East/north/up maps to x/y/z. A damped position controller feeds simulated stick inputs;
orbit samples are followed in sequence, hover timing starts after arrival, and landing
finishes on the ground. Intermediate points are approached slowly, so this is not a
flight-dynamics or timing match to browser playback. No aircraft or webcam connection.

Offline verification:

```powershell
.venv/Scripts/python.exe -m integrations.planner_sim --demo --headless
```

The demo includes takeoff, movement, orbit, hover, return home and landing. Headless mode
fails if a mission does not finish within 1200 simulated seconds.

## Hand gestures

Press G in the Python preview to open webcam 0 and enter gesture mode. Mission playback stops advancing. Space freezes either mode. M closes the camera and returns to mission mode paused; Space then resumes toward the next pending waypoint from the drone's current position. R restarts the mission. Use --camera 1 for another camera.

Current config: thumbs up takes off/steps altitude, thumbs down lands, pointing up orbits, I-love-you returns home, open palm halts. The existing interpreter also supports its geometric directional gestures. The adapter uses a fixed home anchor, not multi-operator tracking. Missing hands cancel maneuvers; a camera failure pauses simulation. Position updates still drive the world-map overlay. Gesture movement does not edit the saved route. Install mediapipe for camera mode.

Forward in this standalone adapter follows the drone heading at gesture trigger, rather than operator facing. Brief hand-tracking gaps under 350ms do not cancel a maneuver.
