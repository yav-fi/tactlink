# Text flight commands

Run with Python 3.10+; no additional dependencies:

```sh
python src/flight_language.py
python src/flight_language.py "take off to 10 meters, orbit home at a radius of 20 meters clockwise once, then land"
python -m unittest discover -s tests -p test_flight_language.py -v
```

This standalone CLI parses and validates text, then prints JSON. It does not
connect to a vehicle, generate sampled waypoints, or execute simulator controls.
Each input describes a new mission starting grounded at home.

| Action | Accepted example |
| --- | --- |
| Takeoff | `take off to 10 meters` |
| Move | `fly north 20 meters` |
| Altitude | `climb to 15 meters` |
| Hover | `hover for 5 seconds` |
| Return | `return home` |
| Land | `land here` |
| Orbit | `orbit home at a radius of 20 meters clockwise once` |

Commands can be separated by commas, semicolons, `and`, or `then`.
Case and repeated whitespace are ignored. Numbers must be numeric, such as `10`.
Units are required: meters/metres/m and seconds/s. Moves also accept `move` and
south/east/west. Altitude accepts `descend to` and `change altitude to`; these
always set an absolute altitude above home, regardless of the verb used.
There is no terrain-relative altitude calculation.

Orbit requires clockwise/counterclockwise (viewed from above) and once/twice/N laps.
Use `home` or `point NORTH EAST` for its center, e.g.
`orbit point 30 -10 at radius 5 m clockwise 2 laps`. Point coordinates are meters
north/east relative to home. Orbit maintains altitude, enters at the nearest
circle point, and finishes there after whole laps. Starting at the center selects
the due-north entry. Heading/camera orientation is not specified by orbit.
Return home maintains altitude and does not land.

JSON commands use a `type` discriminator and action-specific fields:
takeoff/change_altitude have `altitude_m`; move has `direction` and `distance_m`;
hover has `duration_s`; return_home/land have no arguments. Orbit has `center`,
`radius_m`, `direction`, and integer `laps`. Center is `{ "reference": "home" }`
or `{ "reference": "local", "north_m": 30, "east_m": -10 }`.
The result also reports `altitude_reference` and the calculated `end_state`.

Default limits: 50 m altitude, 200 m horizontal distance from home, 100 m per
move, 300 s hover, and 10 orbit laps. Pass a `Limits` object to `parse_mission`
to configure these demo limits. Validation checks state, positive finite values,
straight path endpoints, and the entire orbit circle. Errors identify the
command number and reject the whole mission without producing a partial result.
Missing arguments are never filled in silently.

These checks do not establish obstacle clearance, terrain clearance, dynamic
feasibility, or suitability for real flight. Next integration steps are a map
preview, waypoint generation, and an ArduPilot SITL adapter.
