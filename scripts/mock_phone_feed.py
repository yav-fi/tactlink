"""Fake several SignalMap phones streaming into ``src/main.py --phone-feed``.

    # terminal 1 - the sim, operators driven by the feed
    python src/main.py --phone-feed 9870 --drones 3

    # terminal 2 - three fake phones walking a slow circle
    python scripts/mock_phone_feed.py --port 9870 --phones 3

Each fake phone walks a circle facing its direction of travel, so the operators
move in the visualizer and a three-finger "forward" dash follows whoever has
control. No iPhone, UWB, or Wi-Fi needed. ``--still`` parks them in a ring
facing inward instead. ``--script`` makes phone 1 run a gesture timeline
(take off, orbit, halt, land) on a loop so you can watch the drone respond to a
"phone" before on-device recognition exists.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time

_NAMES = ["Ian", "Alvan", "Yavin", "Thomas", "Sam", "Max", "Uma", "Leo"]

# (start, end) seconds within a 20 s loop -> gesture phone 1 holds. Mirrors the
# --demo timeline in src/main.py.
_SCRIPT = [(2.0, 3.0, "Thumb_Up"), (5.0, 6.0, "Thumb_Up"), (8.0, 9.0, "Pointing_Up"),
           (11.0, 12.0, "Dash_Left"), (14.0, 15.0, "Dash_Right"),
           (17.0, 18.0, "Three_Finger_Forward"), (20.0, 21.0, "ILoveYou"),
           (23.0, 24.0, "Open_Palm"), (26.0, 27.5, "Thumb_Down")]
_SCRIPT_LOOP = 30.0


def _scripted_gesture(t: float) -> str:
    phase = t % _SCRIPT_LOOP
    for a, b, g in _SCRIPT:
        if a <= phase < b:
            return g
    return "None"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9870)
    p.add_argument("--phones", type=int, default=3)
    p.add_argument("--rate", type=float, default=12.0, help="datagrams per phone per second")
    p.add_argument("--radius", type=float, default=5.0, help="circle radius, metres")
    p.add_argument("--speed", type=float, default=0.3, help="angular speed, rad/s")
    p.add_argument("--still", action="store_true", help="stand in a ring, don't walk")
    p.add_argument("--script", action="store_true",
                   help="phone 1 runs a looping gesture timeline (take off, orbit, halt, land)")
    p.add_argument("--no-compass", action="store_true",
                   help="omit the absolute compass field (test the motion-heading fallback)")
    args = p.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dst = (args.host, args.port)
    period = 1.0 / max(1.0, args.rate)
    start = time.monotonic()
    print(f"sending {args.phones} phones -> udp://{args.host}:{args.port} "
          f"at {args.rate:g} Hz  (ctrl-c to stop)")
    try:
        while True:
            t = time.monotonic() - start
            for i in range(args.phones):
                phase = 2 * math.pi * i / max(1, args.phones)
                ang = phase + (0.0 if args.still else args.speed * t)
                x = args.radius * math.cos(ang)
                y = args.radius * math.sin(ang)
                if args.phones == 2:
                    x, y = 0.0, (-1 if i == 0 else 1) * args.radius
                heading = ang + math.pi if args.still else ang + math.pi / 2
                gesture = _scripted_gesture(t) if (args.script and i == 0) else "None"
                # sim-frame facing (radians) -> compass degrees CW from north.
                compass = (90.0 - math.degrees(heading)) % 360.0
                msg = {
                    "v": 1, "id": f"phone-{i + 1}", "name": _NAMES[i % len(_NAMES)],
                    "room": "mock", "t": round(t, 3), "cycle": int(t),
                    "pos": [round(x, 3), round(y, 3)], "z": 0.0,
                    "heading": round(heading % (2 * math.pi), 4),
                    "moving": not args.still,
                    "speed": 0.0 if args.still else args.radius * args.speed,
                    "headingReady": True,
                    "compass": round(compass, 2),
                    "compassValid": not args.no_compass,
                    "gesture": gesture,
                    "gestureConfidence": 0.9 if gesture != "None" else 0.0,
                    "gestureSource": "mock" if gesture != "None" else "none",
                    "flat": args.phones in (2, 3),
                    "twoPhone": args.phones == 2,
                    "members": args.phones,
                    "geometryAge": 0.0,
                }
                sock.sendto(json.dumps(msg).encode("utf-8"), dst)
            time.sleep(period)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
