"""Fake several SignalMap phones streaming into ``src/main.py --phone-feed``.

    # terminal 1 - the sim, operators driven by the feed
    python src/main.py --phone-feed 9870 --drones 3

    # terminal 2 - three fake phones walking a slow circle
    python scripts/mock_phone_feed.py --port 9870 --phones 3

Each fake phone walks a circle facing its direction of travel, so the operators
move in the visualizer and a three-finger "forward" dash follows whoever has
control. No iPhone, UWB, or Wi-Fi needed. ``--still`` parks them in a ring
facing inward instead.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time

_NAMES = ["Ian", "Alvan", "Yavin", "Thomas", "Sam", "Max", "Uma", "Leo"]


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
                heading = ang + math.pi if args.still else ang + math.pi / 2
                msg = {
                    "v": 1, "id": f"phone-{i + 1}", "name": _NAMES[i % len(_NAMES)],
                    "room": "mock", "t": round(t, 3), "cycle": int(t),
                    "pos": [round(x, 3), round(y, 3)], "z": 0.0,
                    "heading": round(heading % (2 * math.pi), 4),
                    "moving": not args.still,
                    "speed": 0.0 if args.still else args.radius * args.speed,
                    "headingReady": True, "gesture": "None", "flat": True,
                }
                sock.sendto(json.dumps(msg).encode("utf-8"), dst)
            time.sleep(period)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
