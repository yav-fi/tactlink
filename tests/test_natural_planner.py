import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from integrations.flight_bridge import create_app
from integrations.natural_planner import NaturalPlanner, Proposal, interpret


class NaturalPlannerTests(unittest.TestCase):
    def test_proposal_validates_without_mutation(self):
        planner = Mock()
        planner.generate.return_value = Proposal(explanation='North', question='', lines=['fly north 10 meters'])
        lines = ['take off to 10 meters', 'land']
        result = interpret('head north ten meters', lines, 1, planner)
        self.assertEqual(lines, ['take off to 10 meters', 'land'])
        self.assertEqual(result['preview']['segments'][1]['end']['y'], 10)
        self.assertEqual(planner.generate.call_args.args[0]['state_at_insertion']['altitude_m'], 10)

    def test_question_discards_commands(self):
        planner = Mock()
        planner.generate.return_value = Proposal(explanation='', question='How far?', lines=['land'])
        result = interpret('go north a little', ['take off to 10 meters'], 1, planner)
        self.assertEqual(result['lines'], [])
        self.assertIsNone(result['preview'])

    def test_invalid_model_commands_rejected(self):
        for line in ['fly north 500 meters', 'arm aircraft', 'take off to 60 meters']:
            planner = Mock()
            planner.generate.return_value = Proposal(explanation='', question='', lines=[line])
            with self.subTest(line=line), self.assertRaises(ValueError):
                interpret('request', ['take off to 10 meters'], 1, planner)

    def test_missing_key_and_no_shared_preview_write(self):
        with patch.dict('os.environ', {}, clear=True), TestClient(create_app()) as client:
            self.assertFalse(client.get('/api/planner/ai/status').json()['available'])
            response = client.post('/api/planner/interpret', json={'text':'go up', 'lines':[], 'position':0})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(client.get('/api/preview').status_code, 404)

    def test_transport_format_and_redacted_failure(self):
        body = {'status':'completed', 'output':[{'type':'message','content':[{'type':'output_text',
            'text':'{"explanation":"Need distance","question":"How far?","lines":[]}'}]}]}
        with patch.dict('os.environ', {'OPENAI_API_KEY':'test-key'}), patch('integrations.natural_planner.httpx.post') as post:
            post.return_value.json.return_value = body
            self.assertEqual(NaturalPlanner().generate({}).question, 'How far?')
            self.assertFalse(post.call_args.kwargs['json']['store'])
            self.assertTrue(post.call_args.kwargs['json']['text']['format']['strict'])

if __name__ == '__main__':
    unittest.main()
