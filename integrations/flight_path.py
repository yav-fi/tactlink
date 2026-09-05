"""Validated flight language -> ENU preview geometry and Mission Planner files."""

import math
from uuid import uuid4

from src.flight_language import MissionError, parse_mission

EARTH_RADIUS_M = 6378137.0


def local_to_global(point, origin):
    """Small-area tangent approximation; caller supplies the vehicle's home."""
    lat, lon = origin['latitude_deg'], origin['longitude_deg']
    latitude = lat + math.degrees(point['y'] / EARTH_RADIUS_M)
    longitude = lon + math.degrees(point['x'] / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return latitude, (longitude + 180) % 360 - 180


def global_to_local(latitude, longitude, altitude, origin):
    delta_lon = (longitude - origin['longitude_deg'] + 180) % 360 - 180
    return dict(x=math.radians(delta_lon) * EARTH_RADIUS_M * math.cos(math.radians(origin['latitude_deg'])),
                y=math.radians(latitude - origin['latitude_deg']) * EARTH_RADIUS_M, z=altitude)


def build_preview(text, origin=None, sample_spacing_m=2.0):
    if not math.isfinite(sample_spacing_m) or not 0.5 <= sample_spacing_m <= 10:
        raise MissionError('Sample spacing must be between 0.5 and 10 meters.')
    if origin is not None:
        origin = dict(origin)
        if (not all(math.isfinite(origin[k]) for k in ('latitude_deg', 'longitude_deg', 'altitude_msl_m'))
                or abs(origin['latitude_deg']) > 85 or abs(origin['longitude_deg']) > 180):
            raise MissionError('Origin must be finite, within latitude +/-85 and longitude +/-180.')
    mission = parse_mission(text)
    position = dict(x=0.0, y=0.0, z=0.0)
    segments = []
    point_count = 0

    def add(kind, destination, command_index, **extra):
        nonlocal position, point_count
        start, end = dict(position), dict(destination)
        points = extra.pop('points', [start, end])
        point_count += len(points)
        if point_count > 20000:
            raise MissionError('Preview exceeds 20,000 points; shorten the mission.')
        segments.append(dict(type=kind, command_index=command_index, start=start,
                             end=end, points=points, **extra))
        position = end

    for index, command in enumerate(mission['commands']):
        kind = command['type']
        end = dict(position)
        if kind in ('takeoff', 'change_altitude', 'land'):
            end['z'] = 0.0 if kind == 'land' else command['altitude_m']
            add('vertical', end, index)
        elif kind == 'move':
            axis, sign = {'north': ('y', 1), 'south': ('y', -1),
                          'east': ('x', 1), 'west': ('x', -1)}[command['direction']]
            end[axis] += sign * command['distance_m']
            add('line', end, index)
        elif kind == 'return_home':
            end.update(x=0.0, y=0.0)
            add('line', end, index)
        elif kind == 'waypoint':
            end.update(x=command['east_m'], y=command['north_m'], z=command['altitude_m'])
            add('line', end, index)
        elif kind == 'hover':
            add('hold', end, index, duration_s=command['duration_s'])
        elif kind == 'orbit':
            center = dict(x=command['center'].get('east_m', 0.0),
                          y=command['center'].get('north_m', 0.0), z=position['z'])
            dx, dy = position['x'] - center['x'], position['y'] - center['y']
            angle = math.atan2(dy, dx) if dx or dy else math.pi / 2
            radius = command['radius_m']
            entry = dict(x=center['x'] + radius * math.cos(angle),
                         y=center['y'] + radius * math.sin(angle), z=position['z'])
            add('line', entry, index, role='orbit_entry')
            per_lap = max(36, math.ceil(2 * math.pi * radius / sample_spacing_m))
            count = per_lap * command['laps']
            if point_count + count + 1 > 20000:
                raise MissionError('Preview exceeds 20,000 points; shorten the mission.')
            sign = -1 if command['direction'] == 'clockwise' else 1
            points = [dict(x=center['x'] + radius * math.cos(angle + sign * 2 * math.pi * i / per_lap),
                           y=center['y'] + radius * math.sin(angle + sign * 2 * math.pi * i / per_lap),
                           z=position['z']) for i in range(count + 1)]
            points[0] = points[-1] = dict(entry)
            add('arc', entry, index, points=points, center=center, radius_m=radius,
                direction=command['direction'], laps=command['laps'])
    return dict(schema_version='1.0', type='flight_preview', mission_id=str(uuid4()),
                frame='ENU', units='meters', altitude_reference='home', origin=origin,
                commands=mission['commands'], segments=segments, end_state=mission['end_state'])


def mission_planner_file(preview):
    """Export an approximate Copter waypoint mission, never upload or execute it."""
    origin = preview['origin']
    if origin is None:
        raise MissionError('Set an explicit geographic home origin before exporting.')
    rows = ['QGC WPL 110']

    def row(point, command=16, duration=0, home=False):
        lat, lon = local_to_global(point, origin)
        seq = len(rows) - 1
        values = [seq, int(home), 0 if home else 3, command, duration, 0, 0, 0,
                  f'{lat:.8f}', f'{lon:.8f}', origin['altitude_msl_m'] if home else point['z'], 1]
        rows.append('\t'.join(map(str, values)))

    row(dict(x=0, y=0, z=0), home=True)
    for segment in preview['segments']:
        command = preview['commands'][segment['command_index']]['type']
        if segment['type'] == 'arc':
            for point in segment['points'][1:]:
                row(point)
        else:
            code = {'takeoff': 22, 'land': 21, 'hover': 19}.get(command, 16)
            row(segment['end'], code, segment.get('duration_s', 0))
    return '\n'.join(rows) + '\n'
