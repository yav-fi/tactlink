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
