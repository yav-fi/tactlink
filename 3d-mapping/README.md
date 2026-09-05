# 3D mapping

Browser-based geospatial drone simulator for the DNHacks mission-control project. It uses CesiumJS, with no game engine, and accepts deterministic mission JSON for a simulated drone.

## Run it

```sh
cd 3d-mapping
cp .env.example .env
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. The Vite development server is pinned to port
5173 so it can run alongside the distributed runtime and its control console on
port 8000.

To load Google Photorealistic 3D Tiles, create a Cesium ion token with that dataset enabled and assign it to `VITE_CESIUM_ION_ACCESS_TOKEN` in `.env`. Without a token, the app still runs on Cesium's fallback globe.

## Mission contract

Photorealistic-map collisions use the tile mesh, not the hidden fallback globe: valid flight positions can have negative ellipsoid heights. Failed geometry queries display a warning rather than freezing movement; building protection is unavailable while those queries fail. Fallback-globe ground checks still run independently, and drones starting inside the clearance margin can climb out.

The left panel has **Individual drone** and **Batches** tabs. Single deployment, selected-drone speed and mission editing live in the first; bulk deployment, batch piloting/speed, selection and saved groups live in the second. Placement controls appear in the matching tab. Fleet playback, reset-all, camera controls and drone state are shared. Switching tabs preserves selection and flight state; picking up a batch opens its tab automatically.

Collision checks stop movement against loaded buildings and terrain with a conservative 10 m clearance around the displayed prism. Manual batches stop together if any member is blocked; steer away to continue. Missions stop on collision, and playback counts blocked drones separately from arrivals. Checks sweep movement segments, including segments crossed by delayed playback frames. Deployment requires at least 15 m above the picked surface. These are collision stops, not automatic rerouting or drone-to-drone collision handling. Cesium's internal `pickFromRay` query only sees geometry rendered in the current view: unloaded/offscreen buildings and gaps in photogrammetry remain limitations. GPU ray queries per moving drone may affect large-fleet performance.

Select drones or recall a saved group, set **Batch speed (mph)**, and click **Control selected batch**. WASD moves all members together, Q/E turns their shared heading, and R/F or Space/Shift changes height. The third-person camera frames the batch; first-person uses its lead drone. **Apply speed to selection** changes every selected member's mph, including during batch control. **Release batch** or Escape parks the drones, leaves individual release prisms and a clickable **PICK UP BATCH** marker. Clicking that marker resumes those drones at their current positions and retains their recorded paths. Historical pickup markers recall membership, without rewinding positions. Reset all clears pickup markers too.

Use **Deploy a new drone** for one drone or **Deploy a batch** for a grid (default ten, configurable count and spacing). Bulk deployment records no paths. Select drones using the group checkboxes, Select all, or map-marker clicks. Save the selection with a group name to recall it later. **Choose destination** opens surface picking with altitude and spacing; dashed routes preview distinct arrival slots. **Send selected drones** replaces the selected drones' routes and launches them together at their individual speeds. They share one color across bodies, paths and markers, and retain their IDs. Destination commands update actual drone positions, so subsequent manual control starts at the arrival point. Arrival count and elapsed time use the playback display; Stop playback stops group movement too. Groups are session-local and Reset all clears them. Individual drone controls remain available. Routes do not perform obstacle avoidance or enforce formation spacing in transit.

**Run all paths** replays the manually recorded trails with colored prisms, launching every routed drone together at its currently assigned mph. The elapsed counter stops at the last arrival; drones without a recorded route stay parked. Playback preserves routes and release markers, and can be repeated. Speeds and route-editing controls are locked during playback; the free camera and Stop playback remain available. Text-only previews must be flown/recorded before they are included in this recorded-trail replay.

The scene starts with an empty fleet. Click **Deploy a new drone**, navigate with the free camera, click a surface, choose height above that surface and confirm **Deploy here**. Placement records no route. Select a drone from the fleet list to pilot it. Colors cycle through eight choices; Reset all drones removes the fleet and its routes. Release markers are prisms and the live arrow is three meters long.

Each drone starts at **60 mph (26.8224 m/s)**. Type a positive decimal into **Selected drone speed (mph)** to change its cruising speed. Manual movement uses local east/north/up meters on the globe with smooth acceleration and normalized diagonal inputs. The speed also supplies the default for goto/return-home missions; an explicit `speed_mps` in a mission overrides it. Orbit timing remains governed by radius and duration. Altitudes are ellipsoid heights; placement adds the chosen height to the picked surface. No collision avoidance is implemented.

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
