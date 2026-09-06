# Local world-map / Python simulation link

1. Start the bridge on 8766 and validate a mission in `/planner/`. Set its home
   latitude and longitude; export altitude is optional for the map overlay.
2. Run `python -m integrations.planner_sim --url http://127.0.0.1:8766`.
3. Open the normal world-map app (not `?mode=runtime`) and click **Connect waypoint planner**.
4. In the Python window use Space to play/pause; the map follows its position.
   After editing the planner mission, press L in Python to load it again.

The map polls `/api/simulation`; Python publishes local coordinates four times per
second on a background thread. The marker is hidden after three seconds without an
update or when mission IDs differ. The overlay does not drive any fleet drone.
The map and Python controls are independent; use the Python window to control this marker.

The bridge defaults to localhost:8766 in the map adapter; override with
VITE_FLIGHT_BRIDGE_URL. Python and map must use the same bridge. Multiple simulator
publishers are not supported: the latest update for the mission wins.

The route anchors to planner latitude/longitude, with map terrain height as local zero.
Without terrain availability, zero is the ellipsoid surface. ArduPilot MSL altitude is
not treated as ellipsoid height. This is a terrain-relative visual preview, not an
absolute-altitude flight or obstacle-clearance check.

The latest validated preview is shared across planner tabs. Invalid drafts do not
replace it. The 3D overlay fetches only; no aircraft commands are sent.
