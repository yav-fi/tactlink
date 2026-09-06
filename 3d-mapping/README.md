# TactLink 3D Mission Control

CesiumJS operator interface for the TactLink distributed runtime, with the
original deterministic browser simulator preserved as a separate local mode.

## Run it

```sh
cd 3d-mapping
cp .env.example .env
npm install
npm run dev
```

Start `uvicorn server.main:app --reload` from the repository root, then open
<http://127.0.0.1:5173/?mode=runtime>. The Vite development server is pinned to port
5173 so it can run alongside the distributed runtime and its control console on
port 8000.

To load Google Photorealistic 3D Tiles, create a Cesium ion token with that dataset enabled and assign it to `VITE_CESIUM_ION_ACCESS_TOKEN` in `.env`. Without a token, the app still runs on Cesium's fallback globe.

## Backend runtime mode

The WebSocket snapshot is authoritative. The browser creates/upgrades Cesium
entities from backend truth and does not run `DroneController.update` for them.
It renders estimated position separately, uncertainty ellipses, current plan,
mission points, usable/unavailable links, relay roles, and topology metrics.
It also renders the backend's SEARCH grid as unknown, fresh, or stale cells;
shows coverage, observation confidence, capability and outcome effectiveness;
and includes a filtered event timeline plus compact network-health history.
Selecting a drone shows how many observations and cells that node knows, making
belief divergence during a partition visible without exposing truth to node
autonomy.
Backend local metres are converted in one helper using Cesium's east-north-up
fixed frame around the snapshot's `origin_lat`, `origin_lon`, and `origin_alt`.

Use the panel to change GPS/network/sensor interference, node failure rate, or
scenario preset; fail a random drone or simulated mission control; pause; and
reset. These are local HTTP calls to port 8000.

## Local sandbox mission contract

Open <http://127.0.0.1:5173/?mode=local> for the original browser simulator.

All four propeller assemblies spin continuously, with adjacent rotors turning in opposite directions. The cinematic camera begins by circling the Washington Monument. Once drones exist, it smoothly follows their collective center and adjusts its range to keep the full fleet visible; it circles the fleet while motion is minimal. First-person remains available as an advanced pilot option.

Live flight markers and playback bodies use a compact quadcopter: a 36 × 24 × 12 cm center body, four arms and four crossed-blade rotor assemblies, under one meter across. Parts follow the drone's pose and color for both Normal and Survey types. Automatic framing stays close for one drone, expands with fleet spread, and includes playback bodies. Movement leaves a short segmented streak that fades fully in 1.3 seconds; complete route history remains internal for replay instead of cluttering the map. Start/release prisms remain route markers.

Survey visuals weaken with distance: full base opacity through 100 m, 60% through 250 m, 30% through 500 m and 12% beyond that. Cone faces are split into those range bands. Coverage triangles use their farthest sampled vertex's range when recorded, and keep that strength through group color changes and undo. This is visual attenuation, not a calibrated detection probability.

**Undo** restores up to 20 recent deployment, speed, group, mission, reset or manual-flight edits. A flight session is one undo action; restored drones are parked, with their paths, release markers and Survey patches preserved. Undo does not resume an interrupted mission or playback. **Pause playback / Resume playback** freezes and continues both recorded-path playback and destination-command flights without counting paused time.

Survey flights sample the center and twelve edge rays against loaded map surfaces, recording coverage triangles only where all three sampled vertices hit surfaces. Cone edges extend to their first detected surface; unmatched rays display up to 10 km and create no coverage. Sampling refreshes at most twice per second per drone, staggered across the fleet. Up to 500 surface triangles are retained per drone, sampled after 10 m of movement. This is approximate: small obstacles between rays, surface discontinuities and unloaded tiles can leave errors. The Fleet overview lists type, state, speed, route distance, estimated duration and coverage triangle count.

Choose **Normal** or **Survey** during single or bulk deployment. Normal drones retain the existing controls. Survey drones add a translucent camera cone mounted 2 m underneath the drone, pitched 60° down in level flight and adjusted for ascent/descent (limited to 30–85° down) (approximately 41° full field of view, extending to sampled surfaces) that follows heading, movement, playback and drone color. It uses sampled surface intersections, not object detection or exhaustive visibility analysis. Both types automatically test progressively wider side paths and then a climb when the direct path meets an obstacle. The HUD briefly shows **AUTO-AVOIDING** while taking a detour; **NO SAFE ROUTE** appears only when every candidate is blocked.

Photorealistic-map collisions use the tile mesh, not the hidden fallback globe: valid flight positions can have negative ellipsoid heights. Failed geometry queries display a warning rather than freezing movement; building protection is unavailable while those queries fail. Fallback-globe ground checks still run independently, and drones starting inside the clearance margin can climb out.

The local screen uses only one short translucent text command line centered at the bottom. Type `/` to see direct commands such as `/deploy`, `/fly`, `/speed`, `/goto`, `/return`, and `/reset`. Plain phrases execute too: “go to the Washington Monument, wait 5 seconds, then go forward 50 meters and return home” compiles into one ordered mission on the selected drone. The built-in DC landmark catalog uses safe nearby approach points for the Washington Monument, Lincoln Memorial, White House, U.S. Capitol, Jefferson Memorial, MLK Memorial, World War II Memorial, and Smithsonian Castle. With a Cesium ion token, other named DC places use ion's Google place search. Other open-ended instructions go through the backend's validated local-LLM mission compiler. Command feedback becomes the empty input's placeholder instead of occupying another panel. WASD pilots relative to the screen, Q/E turns, R/F or Space/Shift changes altitude, and Escape releases.

The command line now sits in an edge-to-edge bottom bar with selected-drone latitude, longitude, altitude, configured speed, flight state, and fleet count. Telemetry refreshes at 10 Hz while drone physics and camera movement continue at the display frame rate. Quadcopter parts share one cached pose per render frame, terrain and photogrammetry use performance-oriented screen-space error settings, and Cesium uses FXAA instead of multisample antialiasing.

Collision checks protect movement against loaded buildings and terrain with a compact 0.5 m gameplay clearance (smaller than the enlarged display prisms). Manual flight, missions, destination commands, and playback first try lateral detours and then a climb; movement stops only when no candidate segment is safe. Manual batches stop together if any member has no safe route. Checks sweep movement segments, including segments crossed by delayed playback frames. Deployment requires at least 15 m above the picked surface. This is reactive local avoidance, not global route planning or drone-to-drone deconfliction. Cesium's internal `pickFromRay` query only sees geometry rendered in the current view: unloaded/offscreen buildings and gaps in photogrammetry remain limitations. GPU ray queries per moving drone may affect large-fleet performance.

Open **More → Batches**, select drones or recall a saved group, set **Batch speed (mph)**, and click **Control selected batch**. WASD moves all members relative to the current screen view, Q/E turns their shared heading, and R/F or Space/Shift changes height. The automatic camera frames the entire batch; first-person uses its lead drone. **Apply speed to selection** changes every selected member's mph, including during batch control. **Release batch** or Escape parks the drones, leaves individual release prisms and a clickable **PICK UP BATCH** marker. Clicking that marker resumes those drones at their current positions and retains their recorded paths. Historical pickup markers recall membership, without rewinding positions. Reset all clears pickup markers too.

Use **Deploy a new drone** for one drone or **Deploy a batch** for a grid (default ten, configurable count and spacing). Bulk deployment records no paths. Select drones using the group checkboxes, Select all, or map-marker clicks. Save the selection with a group name to recall it later. **Choose destination** opens surface picking with altitude and spacing; dashed routes preview distinct arrival slots. **Send selected drones** replaces the selected drones' routes and launches them together at their individual speeds. They share one color across bodies, paths and markers, and retain their IDs. Destination commands update actual drone positions, so subsequent manual control starts at the arrival point. Arrival count and elapsed time use the playback display; Stop playback stops group movement too. Groups are session-local and Reset all clears them. Individual drone controls remain available. Routes use reactive obstacle avoidance but do not enforce formation spacing in transit.

**Run all paths** replays the manually recorded trails with colored prisms, launching every routed drone together at its currently assigned mph. The elapsed counter stops at the last arrival; drones without a recorded route stay parked. Playback preserves routes and release markers, and can be repeated. Speeds and route-editing controls are locked during playback; automatic framing follows every playback body. Text-only previews must be flown/recorded before they are included in this recorded-trail replay.

The scene starts with an empty fleet and the camera circling the Washington Monument. Enter `/deploy` (or “deploy a drone”), then click one visible map surface: a normal drone is placed there immediately at the safe 20 m default height. `/deploy 5 survey` places a five-drone survey batch with one map click. Enter `/fly 1` to pilot drone 1. Colors cycle through eight choices; `/reset all` removes the fleet and its routes. Release markers are prisms.

Each drone starts at **60 mph (26.8224 m/s)**. Type a positive decimal into **Selected drone speed (mph)** to change its cruising speed. Manual movement uses local east/north/up meters on the globe with smooth acceleration and normalized diagonal inputs. The speed also supplies the default for goto/return-home missions; an explicit `speed_mps` in a mission overrides it. Orbit timing remains governed by radius and duration. Altitudes are ellipsoid heights; placement adds the chosen height to the picked surface.

Run `npm test` to verify fleet isolation, colors, cleanup and physical speed at multiple frame rates.

```json
{
  "drone_id": "drone_1",
  "mission": [
    { "action": "goto", "latitude": 38.8895, "longitude": -77.0353, "altitude": 120, "speed_mps": 35 },
    { "action": "orbit", "radius_m": 70, "duration_s": 18 },
    { "action": "return_home", "speed_mps": 35 }
  ]
}
```

Supported actions are `goto`, `hover`, `orbit`, and `return_home`. This is a
local browser-simulator contract, not the canonical distributed-runtime
`simulation.models.MissionCommand` contract; movement remains deterministic in
the browser.

## Text flight preview

Start the Python flight bridge from the repository root with
`.venv/Scripts/python.exe -m integrations.flight_bridge` after installing
`requirements-flight.txt`. Deploy a drone, enter an instruction, and click
**Preview text path** to draw its validated route and populate the mission editor.
**Run mission** animates it locally. Preview zero is the selected drone's
deployment height; landing in this preview returns to that height.

The default bridge URL is `http://127.0.0.1:8765`; override it with
`VITE_FLIGHT_BRIDGE_URL` in your local environment. See
[the bridge guide](../docs/flight-bridge.md) for HTTP/WebSocket contracts,
ArduPilot telemetry setup, and Mission Planner export.
