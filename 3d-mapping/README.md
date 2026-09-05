# 3D mapping

CesiumJS operator interface for the DNHacks distributed runtime, with the
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
Backend local metres are converted in one helper using Cesium's east-north-up
fixed frame around the snapshot's `origin_lat`, `origin_lon`, and `origin_alt`.

Use the panel to change GPS/network/sensor interference, node failure rate, or
scenario preset; fail a random drone or simulated mission control; pause; and
reset. These are local HTTP calls to port 8000.

## Local sandbox mission contract

Open <http://127.0.0.1:5173/?mode=local> for the original browser simulator.

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
