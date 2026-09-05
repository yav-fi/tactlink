# Flight bridge and Cesium integration

The bridge connects our seven-command text parser to Thomas's `3d-mapping`
Cesium simulator. Speech recognition can later supply the same text field.
It also reads ArduPilot telemetry and exports mission files for Mission Planner.
It never arms, changes flight mode, uploads a mission, or starts a vehicle.

## Run on this Windows computer

From the repository root, install the small bridge dependency set once:

```powershell
& 'C:/Users/Iholl/AppData/Local/Programs/Python/Python311/python.exe' -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-flight.txt
.venv/Scripts/python.exe -m integrations.flight_bridge
```

Open `http://127.0.0.1:8765/docs` for the interactive API. In a second terminal:

```powershell
cd 3d-mapping
npm.cmd ci
npm.cmd run dev
```

Open the Vite URL, deploy and select a drone, enter a flight instruction, and
click **Preview text path**. A cyan route appears and the mission JSON is filled
in. **Run mission** animates that mission inside the browser. The preview uses
the selected drone's deployment point as local home; landing returns to that
deployment height, which may be above terrain. If the drone has already moved,
the generated animation first returns to its deployment point.

The existing renderer's native orbit starts from a different center convention.
The adapter therefore expands our arc samples into `goto` steps, preserving
direction and laps, and uses `hover` steps for timed holds. Movement uses the
selected drone's speed when the preview is generated. The preview is geometric;
neither acceleration nor obstacle clearance is guaranteed.

## ArduPilot connection

To listen to a separate MAVProxy/SITL output, restart the bridge with:

```powershell
.venv/Scripts/python.exe -m integrations.flight_bridge --connect udpin:127.0.0.1:14551
```

In MAVProxy's console, add `output add 127.0.0.1:14551`. Keep Mission Planner on
its own UDP output/port. The bridge also accepts pymavlink TCP and serial
endpoints, e.g. `--connect tcp:127.0.0.1:5760` or `--connect COM5 --baud 115200`.
No connection is opened without `--connect`.

The adapter selects the first ArduPilot heartbeat source and filters subsequent
messages by its system and component IDs. It requests GLOBAL_POSITION_INT at
5 Hz and HOME_POSITION at 1 Hz using SET_MESSAGE_INTERVAL. `connected` becomes
false after three seconds without a heartbeat. Position has its own age and
staleness flag; a live heartbeat does not imply fresh coordinates. Missing data
stays null. If the link reports an error, restart the bridge to reconnect.

Live telemetry is available through HTTP and WebSocket for a renderer adapter;
the Cesium UI added here previews planned paths, not a live aircraft marker.
Telemetry ENU zero is the aircraft's reported home, which is independent of a
simulated drone's deployment point. Do not overlay them without aligning those
origins and handling ellipsoid versus mean-sea-level heights.

## Interface for the visualizer

- `POST /api/preview`: `{ "text": "take off to 10 m, orbit home at radius 20 m clockwise once, land" }`
- `GET /api/preview`: latest successful preview; 404 before the first one.
- `GET /api/telemetry`: latest ArduPilot state, including connection/data ages.
- `WS /ws/flight`: sends the current `flight_preview` on connection and when it
  changes; sends `ardupilot_telemetry` snapshots at 5 Hz.
- `GET /api/preview/{mission_id}/mission.waypoints`: export the current preview;
  an outdated mission ID returns 404. A geographic origin is required.

Preview JSON has `schema_version: "1.0"`, `mission_id`, `frame: "ENU"`,
`units: "meters"`, `altitude_reference: "home"`, commands, segments and end state.
Every segment has `type` (line/vertical/arc/hold), zero-based `command_index`,
`start`, `end`, and ordered `points`. All positions are `{x: east, y: north,
z: up}`. Arc segments also include center, radius_m, direction, and laps. Holds
include duration_s. Straight segments contain endpoints; arc samples are at
most roughly 2 m apart by default (sample_spacing_m can be 0.5 through 10).
Samples are geometric positions, not a time-indexed or dynamically feasible
trajectory. Invalid requests return 422 and preserve the last successful preview.

The service binds to loopback on port 8765. Localhost web origins are allowed.
For a teammate on the LAN, explicitly use `--host 0.0.0.0 --allow-origin
http://TEAMMATE-IP:5173`, and set their simulator's `VITE_FLIGHT_BRIDGE_URL` to
`http://YOUR-IP:8765`. This development service has no authentication; use only
a trusted development network. It stores only the latest preview in memory.

## Mission Planner export

Include the explicit aircraft home in a preview request:

```json
{
  "text": "take off to 10 meters, orbit home at radius 20 m clockwise once, land",
  "origin": { "latitude_deg": 38.8895, "longitude_deg": -77.0353, "altitude_msl_m": 10 }
}
```

Coordinates above are an example, not an assumed aircraft location. Download
the mission.waypoints endpoint using the returned mission_id and load the file
in Mission Planner's flight-plan view. The export uses QGC WPL 110: row zero is
home in the global frame, remaining altitudes are relative to home. Takeoff,
land, and timed loiter use their mission commands; return-home is a waypoint
at home at the current planned altitude. Orbits become polygonal waypoint paths,
not native circle commands; actual Copter navigation may round corners.
Use SITL to inspect actual behavior before considering a physical flight.

The geographic conversion is a small-area tangent approximation suitable for
the parser's default 200 m boundary, restricted to latitudes within +/-85.
Mission file import and live ArduPilot/SITL behavior require separate validation;
mocked protocol tests and localhost packets are not an autopilot flight test.

## Verification commands

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_flight*.py' -v
cd 3d-mapping
npm.cmd test
npm.cmd run build
```

Protocol references:
[ArduPilot message rates](https://ardupilot.org/dev/docs/mavlink-requesting-data.html),
[mission file format](https://mavlink.io/en/file_formats/).
