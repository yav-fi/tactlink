import math
import unittest
import time
from unittest.mock import Mock

from fastapi.testclient import TestClient
from pymavlink.dialects.v20 import ardupilotmega as mav

from integrations.ardupilot_link import ArduPilotLink
from integrations.flight_bridge import create_app
from integrations.flight_path import build_preview, global_to_local, local_to_global, mission_planner_file
from src.flight_language import MissionError

TEXT = 'take off to 10 m, orbit home at radius 20 m clockwise once, hover for 5 s, return home, land'
ORIGIN = dict(latitude_deg=40, longitude_deg=-74, altitude_msl_m=100)


class PathTests(unittest.TestCase):
    def test_orbit_geometry_and_continuity(self):
        path = build_preview(TEXT)
        arc = next(s for s in path['segments'] if s['type'] == 'arc')
        self.assertGreater(arc['points'][1]['x'], arc['points'][0]['x'])
        for point in arc['points']:
            self.assertAlmostEqual(math.hypot(point['x'], point['y']), 20)
            self.assertEqual(point['z'], 10)
        for a, b in zip(path['segments'], path['segments'][1:]):
            self.assertEqual(a['end'], b['start'])
        self.assertEqual(arc['points'][0], arc['points'][-1])
        self.assertEqual(path['segments'][-1]['end'], dict(x=0, y=0, z=0))

    def test_counterclockwise_local_center(self):
        path = build_preview('take off to 10 m, orbit point 30 10 at radius 5 m counterclockwise twice')
        arc = path['segments'][-1]
        self.assertEqual(arc['center'], dict(x=10, y=30, z=10))
        a, b = arc['points'][:2]
        cross = (a['x']-10)*(b['y']-30) - (a['y']-30)*(b['x']-10)
        self.assertGreater(cross, 0)
        self.assertEqual(len(arc['points']), 73)

    def test_coordinate_round_trip(self):
        point = dict(x=50, y=-30, z=10)
        lat, lon = local_to_global(point, ORIGIN)
        result = global_to_local(lat, lon, 10, ORIGIN)
        for axis in point:
            self.assertAlmostEqual(point[axis], result[axis], places=6)

    def test_export_semantics(self):
        rows = mission_planner_file(build_preview(TEXT, ORIGIN)).splitlines()
        self.assertEqual(rows[0], 'QGC WPL 110')
        items = [r.split('\t') for r in rows[1:]]
        self.assertTrue(all(len(r) == 12 for r in items))
        self.assertEqual(items[0][2], '0')
        self.assertEqual(items[0][10], '100')
        self.assertTrue(all(r[2] == '3' for r in items[1:]))
        self.assertEqual(items[1][3], '22')
        self.assertEqual(items[-1][3], '21')
        self.assertEqual(next(r for r in items if r[3] == '19')[4], '5.0')
        with self.assertRaises(MissionError):
            mission_planner_file(build_preview(TEXT))

    def test_resource_limit(self):
        with self.assertRaises(MissionError):
            build_preview('take off to 10 m, orbit home at radius 200 m clockwise 10 laps', sample_spacing_m=0.5)


def sourced(message, system=1, component=1):
    message._header.srcSystem = system
    message._header.srcComponent = component
    return message


class LinkTests(unittest.TestCase):
    def test_real_mavlink_udp_transport(self):
        from pymavlink import mavutil
        receiver = mavutil.mavlink_connection('udpin:127.0.0.1:0')
        port = receiver.port.getsockname()[1]
        sender = mavutil.mavlink_connection(f'udpout:127.0.0.1:{port}', source_system=1, source_component=1)
        link = ArduPilotLink('loopback-test', connection=receiver)
        try:
            sender.mav.heartbeat_send(2, 3, 0, 0, 3)
            sender.mav.home_position_send(400000000, -740000000, 100000, 0, 0, 0, [1,0,0,0], 0,0,0)
            sender.mav.global_position_int_send(0, 400001000, -739999000, 110000, 10000, 0,0,0,0)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                link.poll()
                if link.snapshot()['position'] is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(link.snapshot()['connected'])
            self.assertEqual(link.snapshot()['position']['z'], 10)
        finally:
            link.close()
            sender.close()

    def test_filter_conversion_staleness_and_requests_only(self):
        connection = Mock()
        heartbeat = sourced(mav.MAVLink_heartbeat_message(2, 3, 0, 0, 3, 3))
        wrong = sourced(mav.MAVLink_global_position_int_message(0, 0, 0, 0, 0, 0, 0, 0, 0), 2)
        home = sourced(mav.MAVLink_home_position_message(400000000, -740000000, 100000, 0, 0, 0, [1,0,0,0], 0,0,0))
        gps = sourced(mav.MAVLink_global_position_int_message(0, 400001000, -739999000, 110000, 10000, 0,0,0,0))
        connection.recv_match.side_effect = [heartbeat, home, gps, wrong, None]
        now = [0.0]
        link = ArduPilotLink('test', connection=connection, clock=lambda: now[0])
        link.poll()
        state = link.snapshot()
        self.assertTrue(state['connected'])
        self.assertEqual(state['position']['z'], 10)
        self.assertGreater(state['position']['x'], 0)
        self.assertGreater(state['position']['y'], 0)
        self.assertEqual(len(connection.mav.mock_calls), 2)
        for call in connection.mav.command_long_send.call_args_list:
            self.assertEqual(call.args[2], 511)  # SET_MESSAGE_INTERVAL only
        now[0] = 4
        self.assertFalse(link.snapshot()['connected'])
        self.assertTrue(link.snapshot()['position_stale'])
        link.close()
        connection.close.assert_called_once()


class APITests(unittest.TestCase):
    def test_preview_export_errors_and_websocket(self):
        with TestClient(create_app()) as client:
            self.assertEqual(client.get('/api/preview').status_code, 404)
            self.assertFalse(client.get('/api/telemetry').json()['connected'])
            response = client.post('/api/preview', json={'text': TEXT, 'origin': ORIGIN})
            self.assertEqual(response.status_code, 200)
            preview = response.json()
            export = client.get(f"/api/preview/{preview['mission_id']}/mission.waypoints")
            self.assertEqual(export.status_code, 200)
            with client.websocket_connect('/ws/flight') as ws:
                self.assertEqual(ws.receive_json()['mission_id'], preview['mission_id'])
                self.assertEqual(ws.receive_json()['type'], 'ardupilot_telemetry')
            self.assertEqual(client.post('/api/preview', json={'text': 'fly north 10 m'}).status_code, 422)
            self.assertEqual(client.get('/api/preview').json()['mission_id'], preview['mission_id'])
            self.assertEqual(client.post('/api/preview', json={'text': TEXT, 'origin': {**ORIGIN, 'latitude_deg': 90}}).status_code, 422)
            updated = client.post('/api/preview', json={'text': 'take off to 5 m'}).json()
            self.assertEqual(client.get(f"/api/preview/{preview['mission_id']}/mission.waypoints").status_code, 404)
            self.assertEqual(client.get(f"/api/preview/{updated['mission_id']}/mission.waypoints").status_code, 422)


if __name__ == '__main__':
    unittest.main()
