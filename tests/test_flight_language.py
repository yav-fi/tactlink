import unittest

from src.flight_language import Limits, MissionError, parse_mission


class FlightLanguageTests(unittest.TestCase):
    def test_complete_mission(self):
        result = parse_mission("Take off to 10 meters, fly north 20 meters, hover for 5 seconds, then land.")
        self.assertEqual([c['type'] for c in result['commands']], ['takeoff', 'move', 'hover', 'land'])
        self.assertEqual(result['end_state'], dict(north_m=20, east_m=0, altitude_m=0, airborne=False))

    def test_altitude_and_home(self):
        result = parse_mission("takeoff to 10 m and move west 5 m; climb to 15 m then return home")
        self.assertEqual(result['end_state'], dict(north_m=0, east_m=0, altitude_m=15, airborne=True))

    def test_orbit_at_center_enters_north(self):
        result = parse_mission("take off to 10 m, orbit home at a radius of 20 meters clockwise once")
        self.assertEqual(result['end_state']['north_m'], 20)
        self.assertEqual(result['end_state']['east_m'], 0)
        self.assertEqual(result['commands'][1]['laps'], 1)

    def test_orbit_nearest_entry(self):
        result = parse_mission("take off to 10 m, move east 30 m, orbit home at radius 20 m counterclockwise twice")
        self.assertEqual(result['end_state']['east_m'], 20)
        self.assertEqual(result['end_state']['north_m'], 0)

    def test_explicit_center(self):
        result = parse_mission("take off to 10 m, orbit point 30 0 at radius 10 m clockwise 2 laps")
        self.assertEqual(result['end_state']['north_m'], 20)

    def test_rejects_invalid_or_ambiguous_input(self):
        for text in ['', 'take off', 'fly north 10 m', 'take off to -1 m',
                     'take off to 10 m, move left 5 m', 'take off to 10 m, hover for 5',
                     'take off to 10 m, land, move north 2 m',
                     'take off to 10 m, take off to 20 m',
                     'take off to 10 m, orbit home at radius 5 m clockwise 0 laps',
                     'take off to 10 m, orbit home at radius 5 m clockwise',
                     'take off to 10 m,,land', 'take off to 10 m, do something']:
            with self.subTest(text=text), self.assertRaises(MissionError):
                parse_mission(text)

    def test_limits(self):
        for tail in ['move north 101 m', 'climb to 51 m', 'hover for 301 seconds',
                     'orbit home at radius 10 m clockwise 11 laps',
                     'orbit point 195 0 at radius 10 m clockwise once',
                     'move north 100 m, move north 100 m, move east 1 m']:
            with self.subTest(tail=tail), self.assertRaises(MissionError):
                parse_mission('take off to 10 m, ' + tail)

    def test_configurable_boundary(self):
        with self.assertRaises(MissionError):
            parse_mission('take off to 10 m, move east 21 m', Limits(max_distance_from_home_m=20))


if __name__ == '__main__':
    unittest.main()
