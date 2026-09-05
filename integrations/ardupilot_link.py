"""Single-vehicle MAVLink telemetry adapter. No arming, mode or mission writes."""

import time

from .flight_path import global_to_local


class ArduPilotLink:
    def __init__(self, endpoint, baud=115200, connection=None, clock=time.monotonic):
        from pymavlink import mavutil
        self.clock = clock
        self.mavutil = mavutil
        self.connection = connection or mavutil.mavlink_connection(
            endpoint, baud=baud, source_system=255, source_component=190)
        self.endpoint = endpoint
        self.target = None
        self.heartbeat_at = None
        self.position_at = None
        self.home = None
        self.global_position = None
        self.mode = None
        self.armed = None
        self.error = None

    def poll(self):
        """Bounded nonblocking drain; run from one owner, not concurrent threads."""
        for _ in range(100):
            message = self.connection.recv_match(blocking=False)
            if message is None:
                break
            source = (message.get_srcSystem(), message.get_srcComponent())
            kind = message.get_type()
            if kind == 'HEARTBEAT' and self.target is None:
                if message.autopilot != self.mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
                    continue
                self.target = source
                # Requests only: GLOBAL_POSITION_INT at 5 Hz, HOME_POSITION at 1 Hz.
                for message_id, interval in ((33, 200000), (242, 1000000)):
                    self.connection.mav.command_long_send(*source, 511, 0,
                                                         message_id, interval, 0, 0, 0, 0, 0)
            if source != self.target:
                continue
            if kind == 'HEARTBEAT':
                self.heartbeat_at = self.clock()
                self.mode = self.mavutil.mode_string_v10(message)
                self.armed = bool(message.base_mode & 128)
            elif kind == 'HOME_POSITION':
                self.home = dict(latitude_deg=message.latitude / 1e7,
                                 longitude_deg=message.longitude / 1e7,
                                 altitude_msl_m=message.altitude / 1000)
            elif kind == 'GLOBAL_POSITION_INT':
                self.position_at = self.clock()
                self.global_position = dict(latitude_deg=message.lat / 1e7,
                                            longitude_deg=message.lon / 1e7,
                                            relative_altitude_m=message.relative_alt / 1000)

    def snapshot(self):
        now = self.clock()
        age = None if self.heartbeat_at is None else now - self.heartbeat_at
        position_age = None if self.position_at is None else now - self.position_at
        local = None
        if self.home is not None and self.global_position is not None:
            gps = self.global_position
            local = global_to_local(gps['latitude_deg'], gps['longitude_deg'],
                                    gps['relative_altitude_m'], self.home)
        return dict(type='ardupilot_telemetry', frame='ENU', units='meters',
                    connected=age is not None and age < 3 and self.error is None,
                    endpoint=self.endpoint, target=self.target, heartbeat_age_s=age,
                    position_age_s=position_age,
                    position_stale=position_age is None or position_age >= 3,
                    home=self.home, position=local, global_position=self.global_position,
                    mode=self.mode, armed=self.armed, error=self.error)

    def close(self):
        self.connection.close()
