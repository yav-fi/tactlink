# Editable real-world drone speed

Completed: 2026-09-05 13:29:14 EDT (America/New_York).

Request: default to 60 mph and allow typing a freely chosen speed that matches real-world movement on the map.

Added per-drone positive numeric mph setting defaulting to 60. Conversion uses 0.44704 meters per second per mph. Local east/north/up displacement replaces approximate degrees-per-meter motion. Smooth acceleration remains; normalized diagonal controls do not increase the requested cruise speed. Goto/return missions use the configured default unless speed_mps is explicit. Orbit uses its existing radius/duration contract. Fixed mission validation that rejected valid hover/orbit steps and rejects invalid speed overrides. Simulation animation is enabled for mission execution. Removed the arbitrary positive ellipsoid-height clamp, which could move a deployed drone from its chosen altitude on takeover.

Verification: five npm tests passed, including measured 26.8224 meters traveled over one second at steady cruise at 30/60/144 fps with straight and diagonal inputs. Custom decimal speed and per-drone isolation checks passed. Production build and whitespace checks passed. No building/terrain collision avoidance is implemented.
