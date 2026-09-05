# Waypoint planner

Open **http://127.0.0.1:8766/planner/**. This is a separate 2D application; the
Cesium simulator and its token are not needed.

## Start

From the repository root in PowerShell:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-flight.txt -r requirements-speech.txt
.venv/Scripts/python.exe scripts/setup_speech.py
.venv/Scripts/python.exe -m integrations.flight_bridge --port 8766
```

The model setup runs once and caches base.en under the ignored models/speech
directory. Speech runs on the local CPU with INT8 inference. If Python is not
on PATH, the existing project .venv/Scripts/python.exe is the intended runtime.
Keep the last command running while using the page. For a fresh checkout, create
the environment with Python 3.11 first. Speech dependencies are optional for
clicking/typing; install only requirements-flight.txt to use those features.

## Three inputs, one command list

- **Click:** select Take off and Add command first. Select Waypoint and click a
  destination, or select Orbit and click its center. A waypoint includes its
  altitude; an orbit keeps the current planned altitude. Other commands use
  the Add command button. Drag the map to pan and use +/− to zoom.
- **Type:** enter one or several commands and click Add to mission. Typed and
  transcribed numbers such as "twenty-five" become numeric values. The text
  field shows the normalized instruction for review.
- **Speak:** hold the microphone button (or hold Space/Enter while it is focused),
  speak, and release. Allow microphone access in the browser when prompted.
  The transcript appears in the editable text field. Review it and click Add to
  mission. Recording never adds a command automatically. Microphone capture
  needs localhost or HTTPS and a browser with AudioWorklet support.

Edit command rows, reorder them with arrows, or delete them. Every change
invalidates the old preview and export until it is validated again. Drafts are
saved in this browser's local storage; recordings are not saved. A draft always
starts grounded at local home. Additive commands after Land need a new Takeoff.

Use the **Add commands** selector to insert typed/spoken commands before an
existing row (for example before Land). Add validates the entire proposed
mission before changing the draft. Errors appear beside the input, and **Show
command N** jumps to the invalid row. A successful addition clears the submitted
transcript to avoid accidentally adding it again. An already-invalid draft must
be corrected before further text/speech additions can pass validation.

Example:

```text
take off to 10 meters, fly north 20 meters,
orbit home at radius 20 meters clockwise once,
hover for 5 seconds, return home, land
```

Clicking a waypoint generates `go to point NORTH EAST at altitude ALT meters`.
This is one direct 3D segment, not separate north/east legs. Orbit point
coordinates also use north then east in meters. The map uses x=east/y=north;
JSON path segments retain ENU x/east, y/north, z/up.

## Map and export

The default map is an offline 50 m reference grid at an example home location.
Optional Street map imagery requests only visible OpenStreetMap tiles using
normal browser caching and attribution. It requires internet; no tile
prefetching or offline download is performed. The reference grid and waypoint
editing work without those tiles.

Enter the intended home latitude, longitude, and altitude above mean sea level,
then mark those as the intended home coordinates to enable export. Changing
home relocates the local route and clears that checkbox. The default geographic
location is an example, and no home altitude is assumed.

Download .waypoints exports the exact current validated draft as QGC WPL 110.
Open it in Mission Planner's flight-plan view to inspect it. The browser does
not upload, arm, change flight mode, or start an aircraft. Orbits export as
sampled waypoint polygons. Actual autopilot navigation can round corners.
Use ArduPilot SITL to check execution before a physical flight.

Defaults remain: 50 m altitude, 100 m per move/waypoint leg, 200 m horizontal
boundary from home, 300 s hover, 10 orbit laps. The map is a top-down route view;
vertical segments can overlap on the map. Altitudes remain visible in command
rows. These are geometry/state checks, not obstacle or terrain clearance checks.

## Interfaces and limits

- `POST /api/planner/parse`: text to normalized command lines, without requiring
  a complete mission. Full state validation occurs at preview/export time.
- `POST /api/preview`: existing text-to-validated-segments API.
- `POST /api/planner/export`: revalidates and exports the submitted text and
  geographic origin; it does not depend on the last preview from another tab.
- `GET /api/speech/status`: local dependency/model status.
- `POST /api/speech/transcribe`: raw mono PCM16 WAV at 16 kHz, 0.25–30 seconds,
  maximum 1.1 MB. Browser audio is resampled before submission. Audio stays on
  this computer, apart from initial model downloads which send no recording.

## Checks

```powershell
.venv/Scripts/python.exe -m unittest tests.test_flight_language tests.test_flight_bridge tests.test_waypoint_planner -v
node --test tests/map-math.test.mjs
node --check integrations/waypoint_ui/planner.js
```

Browser tests exercised typed instructions and actual clicked waypoint/orbit
geometry. A locally synthesized WAV was transcribed correctly through the real
HTTP endpoint. Physical microphone/browser permission behavior still needs a
user recording; synthetic audio does not test microphone quality or accuracy in
ambient noise. No live ArduPilot flight is claimed.
