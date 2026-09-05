# 3D mapping

Browser-based geospatial drone simulator for the DNHacks mission-control project. It uses CesiumJS, with no game engine, and accepts deterministic mission JSON for a simulated drone.

## Run it

```sh
cd 3d-mapping
cp .env.example .env
npm install
npm run dev
```

To load Google Photorealistic 3D Tiles, create a Cesium ion token with that dataset enabled and assign it to `VITE_CESIUM_ION_ACCESS_TOKEN` in `.env`. Without a token, the app still runs on Cesium's fallback globe.

## Mission contract

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

Supported actions are `goto`, `hover`, `orbit`, and `return_home`. This is the renderer-facing contract for the future backend/LLM; movement remains deterministic in the browser.
