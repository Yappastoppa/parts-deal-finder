"""AI help boundary tests; provider calls use mocked responses only."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.api import Application
from backend.help import HelpService
from backend.domain import APIError
from test_quote_api import request, ORIGIN


class HelpAPI(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'APF_AI_HELP':'0', 'OPENAI_API_KEY':'', 'APF_HELP_DAILY_LIMIT':'100'})
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, [ORIGIN], False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_and_context_validation(self):
        self.assertEqual(request(self.app, '/api/help/status')[1], {'enabled': False})
        self.assertEqual(request(self.app, '/api/help', {'message':'How do I search?'})[0], 503)
        for data in [{'message':''}, {'message':'x'*601}, {'message':'Hi','page':'private'}, {'message':'Hi','key':'private'}, {'message':'Hi','mode':'admin'}, {'message':['bad']}]:
            self.assertEqual(request(self.app, '/api/help', data)[0], 400)
        self.assertEqual(request(self.app, '/api/help', {'message':'Hi'}, origin='')[0],403)
        self.assertEqual(request(self.app, '/api/help', {'message':'Hi'}, origin='https://invalid.example')[0],403)
        self.assertFalse((self.root/'help_usage.sqlite3').exists())

    def test_server_context_and_ip_limits(self):
        with patch.object(self.app.help, 'answer', return_value='Select Compare on two parts.') as answer:
            status, data, headers = request(self.app, '/api/help', {'message':'How to compare?','page':'results','mode':'live'})
            self.assertEqual(status,200)
            self.assertEqual(data['answer'],'Select Compare on two parts.')
            answer.assert_called_once_with('How to compare?','results','demo')
            self.assertEqual(headers['Cache-Control'],'no-store')
            for _ in range(9): self.assertEqual(request(self.app,'/api/help',{'message':'Hi'})[0],200)
            self.assertEqual(request(self.app,'/api/help',{'message':'Hi'})[0],429)

    def test_provider_payload_budget_persists_and_no_chat_storage(self):
        service=HelpService(self.root,key='unit-only-private-key',enabled=True,daily_limit=2)
        payload={'status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Use Part Search.'}]}]}
        def respond(req,timeout):
            self.assertEqual(req.full_url,'https://api.openai.com/v1/responses')
            self.assertEqual(timeout,15)
            body=json.loads(req.data)
            self.assertFalse(body['store'])
            self.assertEqual(body['max_output_tokens'],280)
            self.assertNotIn('tools',body)
            self.assertNotIn('unit-only-private-key',req.data.decode())
            self.assertIn('No real inventory',body['instructions'])
            return io.BytesIO(json.dumps(payload).encode())
        with patch('backend.help.urlopen',side_effect=respond) as remote:
            self.assertEqual(service.answer('QUESTION_STORAGE_CANARY','home','demo'),'Use Part Search.')
            # Recreating the service does not reset the persisted budget.
            service=HelpService(self.root,key='unit-only-private-key',enabled=True,daily_limit=2)
            service.answer('How to compare?','results','demo')
            with self.assertRaises(APIError) as failure: service.answer('Again','home','demo')
            self.assertEqual(failure.exception.status,429)
            self.assertEqual(remote.call_count,2)
        self.assertNotIn(b'QUESTION_STORAGE_CANARY',service.path.read_bytes())
        self.assertEqual(service.path.stat().st_mode & 0o777,0o600)

    def test_provider_failure_incomplete_and_concurrency(self):
        service=HelpService(self.root,key='unit-only-private-key',enabled=True)
        with patch('backend.help.urlopen',side_effect=RuntimeError('PRIVATE_PROVIDER_ERROR')):
            with self.assertRaises(APIError) as failure:service.answer('Hi','home','demo')
            self.assertNotIn('PRIVATE_PROVIDER_ERROR',failure.exception.message)
            self.assertEqual(failure.exception.status,503)
        with patch('backend.help.urlopen',return_value=io.BytesIO(b'{"status":"incomplete","output":[]}')):
            with self.assertRaises(APIError):service.answer('Hi','home','demo')
        self.assertTrue(service.slots.acquire(blocking=False));self.assertTrue(service.slots.acquire(blocking=False))
        with self.assertRaises(APIError) as failure:service.answer('Hi','home','demo')
        self.assertEqual(failure.exception.status,429)
        service.slots.release();service.slots.release()


if __name__=='__main__':unittest.main()
