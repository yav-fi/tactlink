import io
import unittest
import wave
from unittest.mock import Mock

from fastapi.testclient import TestClient

from integrations.flight_bridge import create_app
from integrations.flight_path import build_preview
from integrations.speech_input import LocalTranscriber, normalize_instruction
from src.flight_language import MissionError, instruction_lines, parse_mission


class WaypointTests(unittest.TestCase):
    def test_clicked_waypoint_is_one_direct_segment(self):
        path = build_preview('take off to 10 m, go to point 30 -40 at altitude 15 meters, land')
        segment = path['segments'][1]
        self.assertEqual(segment['start'], dict(x=0, y=0, z=10))
        self.assertEqual(segment['end'], dict(x=-40, y=30, z=15))
        self.assertEqual(path['commands'][1]['type'], 'waypoint')

    def test_click_and_text_share_state_and_limits(self):
        for text in ['go to point 10 20 at altitude 10 m',
                     'take off to 10 m, go to point 100 100 at altitude 10 m',
                     'take off to 10 m, go to point 20 0 at altitude -1 m',
                     'take off to 10 m, go to point 20 0 at altitude 51 m',
                     'take off to 10 m, land, go to point 10 20 at altitude 10 m']:
            with self.subTest(text=text), self.assertRaises(MissionError):
                parse_mission(text)

    def test_spoken_numbers_normalized_without_misreading_point(self):
        self.assertEqual(normalize_instruction('Take off to ten meters. Fly north twenty-five meters, then land.'),
                         'take off to 10 meters, fly north 25 meters, then land')
        self.assertEqual(normalize_instruction('hover for one point five seconds'), 'hover for 1.5 seconds')
        self.assertEqual(normalize_instruction('fly north one hundred and twenty meters'), 'fly north 120 meters')
        self.assertEqual(normalize_instruction('go to point 30 -20 at altitude ten meters'), 'go to point 30 -20 at altitude 10 meters')
        self.assertEqual(normalize_instruction('fly north twenty ten meters'), 'fly north twenty ten meters')
        self.assertEqual(normalize_instruction('hover for one and two seconds'), 'hover for one and two seconds')
        self.assertEqual(instruction_lines('hover for 5 seconds'), ['hover for 5 seconds'])


class PlannerAPITests(unittest.TestCase):
    def test_static_ui_parse_preview_export(self):
        with TestClient(create_app()) as client:
            self.assertIn('Waypoint planner', client.get('/planner/').text)
            self.assertEqual(client.get('/planner/planner.js').status_code, 200)
            result = client.post('/api/planner/parse', json={'text':'take off to ten meters, then hover for five seconds'}).json()
            self.assertEqual(result['lines'], ['take off to 10 meters', 'hover for 5 seconds'])
            draft = dict(text=', '.join(result['lines']) + ', land',
                         origin=dict(latitude_deg=38, longitude_deg=-77, altitude_msl_m=42))
            response=client.post('/api/planner/export', json=draft)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.text.startswith('QGC WPL 110'))
            self.assertIn('\t42.0\t',response.text)
            self.assertEqual(client.post('/api/planner/export',json={'text':draft['text']}).status_code,422)

    def test_audio_endpoint_injection_and_size_limit(self):
        speech=Mock()
        speech.transcribe.return_value={'transcript':'take off to ten meters', 'normalized_text':'take off to 10 meters'}
        speech.status.return_value={'available':True}
        with TestClient(create_app(transcriber=speech)) as client:
            self.assertTrue(client.get('/api/speech/status').json()['available'])
            self.assertEqual(client.post('/api/speech/transcribe',content=b'fake-wav').json()['normalized_text'],'take off to 10 meters')
            speech.transcribe.assert_called_once_with(b'fake-wav')
            self.assertEqual(client.post('/api/speech/transcribe',content=b'x'*1100001).status_code,413)
            speech.transcribe.side_effect=ValueError('No speech detected')
            self.assertEqual(client.post('/api/speech/transcribe',content=b'fake-wav').status_code,422)

    def test_local_audio_validation_before_model_download(self):
        # This checks decoding and rejection only; no model download is required.
        try:
            import faster_whisper  # noqa
        except ImportError:
            self.skipTest('optional speech dependencies not installed')
        speech=LocalTranscriber()
        with self.assertRaises(ValueError):
            speech.transcribe(b'not audio')
        data=io.BytesIO()
        with wave.open(data,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0'*32000)
        with self.assertRaisesRegex(ValueError,'audible'):
            speech.transcribe(data.getvalue())
        self.assertIsNone(speech.model)


if __name__=='__main__':
    unittest.main()
