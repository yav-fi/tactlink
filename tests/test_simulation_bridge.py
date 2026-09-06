import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from integrations.flight_bridge import create_app

class SimulationBridgeTests(unittest.TestCase):
    def test_dry_validation_preserves_geographic_mission(self):
        with TestClient(create_app()) as client:
            saved=client.post('/api/preview',json={'text':'take off to 10 m','map_origin':{'latitude_deg':38.8895,'longitude_deg':-77.0353}}).json()
            self.assertEqual(saved['map_origin']['latitude_deg'],38.8895)
            client.post('/api/preview',json={'text':'take off to 5 m','publish':False})
            self.assertEqual(client.get('/api/preview').json()['mission_id'],saved['mission_id'])

    def test_matching_mission_and_staleness(self):
        with TestClient(create_app()) as client:
            preview=client.post('/api/preview',json={'text':'take off to 10 m'}).json()
            position=dict(mission_id=preview['mission_id'],x=0,y=0,z=3,paused=False,complete=False)
            with patch('integrations.flight_bridge.time.monotonic',return_value=10):
                self.assertEqual(client.post('/api/simulation',json=position).status_code,200)
                self.assertFalse(client.get('/api/simulation').json()['stale'])
            with patch('integrations.flight_bridge.time.monotonic',return_value=14):
                self.assertTrue(client.get('/api/simulation').json()['stale'])
            client.post('/api/preview',json={'text':'take off to 5 m'})
            self.assertEqual(client.post('/api/simulation',json=position).status_code,409)
            self.assertTrue(client.get('/api/simulation').json()['stale'])
