"""Deterministic text-to-mission playground. No vehicle connection or execution."""

import argparse
import json
import math
import re
from dataclasses import dataclass


class MissionError(ValueError):
    """An instruction needs correction before it can become a mission."""


@dataclass(frozen=True)
class Limits:
    max_altitude_m: float = 50
    max_distance_from_home_m: float = 200
    max_move_m: float = 100
    max_hover_s: float = 300
    max_laps: int = 10


NUMBER = r"(-?\d{1,8}(?:\.\d{1,8})?)"
METERS = r"(?:meters?|metres?|m)"


def _parse(text):
    patterns = [
        (rf"take\s?off to {NUMBER} {METERS}", "takeoff", "altitude_m"),
        (rf"(?:climb|descend|change altitude) to {NUMBER} {METERS}", "change_altitude", "altitude_m"),
        (rf"hover for {NUMBER} (?:seconds?|s)", "hover", "duration_s"),
    ]
    for pattern, kind, field in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return {"type": kind, field: float(match[1])}
    match = re.fullmatch(rf"(?:fly|move) (north|south|east|west) {NUMBER} {METERS}", text)
    if match:
        return {"type": "move", "direction": match[1], "distance_m": float(match[2])}
    match = re.fullmatch(rf"(?:go|fly) to point {NUMBER} {NUMBER} at altitude {NUMBER} {METERS}", text)
    if match:
        return {"type": "waypoint", "north_m": float(match[1]), "east_m": float(match[2]),
                "altitude_m": float(match[3])}
    if text == "return home":
        return {"type": "return_home"}
    if text in ("land", "land here"):
        return {"type": "land"}
    match = re.fullmatch(
        rf"orbit (home|point {NUMBER} {NUMBER}) at (?:a )?radius (?:of )?{NUMBER} {METERS} "
        r"(clockwise|counterclockwise) (once|twice|\d{1,3} laps?)", text)
    if match:
        center = ({"reference": "home"} if match[1] == "home" else
                  {"reference": "local", "north_m": float(match[2]), "east_m": float(match[3])})
        laps = {"once": 1, "twice": 2}.get(match[6])
        if laps is None:
            laps = int(match[6].split()[0])
        return {"type": "orbit", "center": center, "radius_m": float(match[4]),
                "direction": match[5], "laps": laps}
    raise MissionError(
        f"Unsupported or incomplete instruction: {text!r}. "
        "Use explicit meters/seconds; orbit requires center, radius, direction, and laps. "
        "See docs/flight-language.md for examples.")


def instruction_lines(text):
    """Split syntax without requiring a complete grounded-to-airborne mission."""
    if not isinstance(text, str) or len(text) > 8000:
        raise MissionError('Instructions must be text of at most 8,000 characters.')
    normalized = re.sub(r"\s+", " ", text.strip().lower()).rstrip('.')
    parts = re.split(r"\s*(?:[,;]\s*(?:(?:and\s+)?then\s+|and\s+)?|\band then\b|\bthen\b|\band\b)\s*", normalized)
    if len(parts) > 100:
        raise MissionError('Use at most 100 commands per mission.')
    for index, part in enumerate(parts, 1):
        try:
            if not part:
                raise MissionError('Provide a command between separators.')
            _parse(part)
        except MissionError as error:
            raise MissionError(f'Command {index}: {error}') from error
    return parts


def parse_mission(text, limits=None):
    """Parse and validate a mission starting grounded at home (north=0, east=0).

    The circular horizontal boundary is convex, so checking segment endpoints
    checks straight entry/move paths too. Orbit checks use the entire circle.
    This is geometric validation, not obstacle or flight-dynamics validation.
    """
    limits = limits or Limits()
    for value in vars(limits).values():
        if not math.isfinite(value) or value <= 0:
            raise MissionError("All configured limits must be finite and positive.")
    parts = instruction_lines(text)
    commands = []
    north = east = altitude = 0.0
    airborne = False
    for index, part in enumerate(parts, 1):
        try:
            if not part:
                raise MissionError("Provide a command between separators.")
            command = _parse(part)
            kind = command["type"]
            if kind == "takeoff":
                if airborne:
                    raise MissionError("Already airborne; use change altitude to ... meters.")
                airborne = True
            elif not airborne:
                raise MissionError("Take off before issuing airborne commands.")
            for field in ("altitude_m", "distance_m", "duration_s", "radius_m", "laps"):
                if field in command and (not math.isfinite(command[field]) or command[field] <= 0):
                    raise MissionError(f"{field} must be finite and positive.")
            if kind in ("takeoff", "change_altitude", "waypoint"):
                altitude = command["altitude_m"]
                if altitude > limits.max_altitude_m:
                    raise MissionError("Altitude exceeds configured maximum.")
                if kind == 'waypoint':
                    if math.hypot(command['north_m'] - north, command['east_m'] - east) > limits.max_move_m:
                        raise MissionError('Waypoint leg exceeds configured maximum distance.')
                    north, east = command['north_m'], command['east_m']
            elif kind == "move":
                distance = command["distance_m"]
                if distance > limits.max_move_m:
                    raise MissionError("Move exceeds configured maximum distance.")
                dn, de = {"north": (1, 0), "south": (-1, 0), "east": (0, 1), "west": (0, -1)}[command["direction"]]
                north += dn * distance
                east += de * distance
            elif kind == "hover" and command["duration_s"] > limits.max_hover_s:
                raise MissionError("Hover exceeds configured maximum duration.")
            elif kind == "return_home":
                north = east = 0.0
            elif kind == "land":
                altitude = 0.0
                airborne = False
            elif kind == "orbit":
                if command["laps"] > limits.max_laps:
                    raise MissionError("Orbit exceeds configured maximum laps.")
                center = command["center"]
                cn, ce = center.get("north_m", 0.0), center.get("east_m", 0.0)
                radius = command["radius_m"]
                if not all(math.isfinite(v) for v in (cn, ce)):
                    raise MissionError("Orbit center must be finite.")
                if math.hypot(cn, ce) + radius > limits.max_distance_from_home_m:
                    raise MissionError("Orbit circle exceeds configured flight boundary.")
                dn, de = north - cn, east - ce
                distance = math.hypot(dn, de)
                north = cn + radius * dn / distance if distance else cn + radius
                east = ce + radius * de / distance if distance else ce
            if math.hypot(north, east) > limits.max_distance_from_home_m:
                raise MissionError("Path exceeds configured flight boundary.")
            commands.append(command)
        except MissionError as error:
            raise MissionError(f"Command {index}: {error}") from error
    return {"altitude_reference": "home", "commands": commands,
            "end_state": {"north_m": north, "east_m": east,
                          "altitude_m": altitude, "airborne": airborne}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="?", help="Mission text; omit for interactive mode")
    args = parser.parse_args()
    if args.text is not None:
        try:
            print(json.dumps(parse_mission(args.text), indent=2))
        except MissionError as error:
            parser.exit(2, f"{error}\n")
        return
    print("Text mission playground. Each mission starts grounded at home. Type quit to exit.")
    while True:
        try:
            text = input("mission> ")
        except (EOFError, KeyboardInterrupt):
            break
        if text.strip().lower() in ("quit", "exit"):
            break
        try:
            print(json.dumps(parse_mission(text), indent=2))
        except MissionError as error:
            print(error)


if __name__ == "__main__":
    main()
