"""Isolated API tests. No supplier connections, real credentials, orders or messages."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from backend.api import Application
from backend.domain import APIError, public_listing

PARAMS = {'year': '2021', 'make': 'BMW', 'model': 'M4', 'part': 'Spindle'}
ORIGIN = 'http://127.0.0.1:8080'


def fixture_runner(params, directory):
    directory.mkdir()
    (directory / 'private.json').write_text('{"token":"PRIVATE_METADATA_CANARY"}')
    if params.get('interchange') != 'Front left':
        return {'status': 'needs_interchange_choice', 'choices': [{'label': 'Front left', 'value': 'PRIVATE_METADATA_CANARY'}]}
    return {'status': 'ok', 'results': [{
        **PARAMS, 'price': '$125.00', 'stock': 'TEST-001', 'supplier': 'Fixture Yard', 'grade': 'A',
        'images': [], 'orderable': True, 'order_action': {'button_id': 'fixture-button', 'token': 'PRIVATE_METADATA_CANARY'},
        'source_results_url': 'https://private.invalid/?token=PRIVATE_METADATA_CANARY',
    }]}


def request(app, path, data=None, method=None, origin=ORIGIN, raw=None, content_type='application/json'):
    body = raw if raw is not None else json.dumps(data).encode() if data is not None else b''
    environ = {'REQUEST_METHOD': method or ('POST' if data is not None or raw else 'GET'), 'PATH_INFO': path,
               'CONTENT_TYPE': content_type, 'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body),
               'REMOTE_ADDR': '127.0.0.1', 'HTTP_ORIGIN': origin}
    metadata = []
    output = b''.join(app(environ, lambda status, headers: metadata.extend([int(status.split()[0]), dict(headers)])))
    if metadata[1].get('Content-Type') == 'application/json':
        output = json.loads(output)
    return metadata[0], output, metadata[1]


def wait_job(app, job_id):
    for _ in range(200):
        result = request(app, f'/api/search/{job_id}')[1]
        if result['status'] != 'pending':
            return result
        time.sleep(.01)
    raise AssertionError('Fixture job did not finish')


class APITestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, [ORIGIN], True, fixture_runner)

    def tearDown(self):
        self.assertTrue(self.app.search.slot.acquire(timeout=5))
        self.app.search.slot.release()
        self.tmp.cleanup()

    def listing(self):
        status, started, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        self.assertEqual(status, 202)
        result = wait_job(self.app, started['search_id'])
        self.assertEqual(result['status'], 'ok')
        return result['listings'][0]

    def quote(self, listing):
        return {'listing_ids': [listing['id']], 'name': 'Test Customer', 'email': 'test@example.invalid',
                'phone': '', 'postal_code': '12345', 'notes': '', 'consent': True, 'request_key': 'a'*32}


class QuoteAPI(APITestCase):
    def test_refinement_and_no_supplier_secrets(self):
        _, started, _ = request(self.app, '/api/search', PARAMS)
        result = wait_job(self.app, started['search_id'])
        self.assertEqual(result, {'status': 'needs_interchange_choice', 'choices': ['Front left']})
        listing = self.listing()
        self.assertEqual(listing['price'], 125.0)
        self.assertIsNone(listing['mileage'])
        self.assertNotIn('PRIVATE_METADATA_CANARY', json.dumps(listing))
        self.assertNotIn('source_results_url', listing)
        self.assertFalse(list(self.root.glob('*/private.json')))

    def test_quote_persistence_idempotency_and_trusted_snapshot(self):
        listing = self.listing()
        payload = self.quote(listing)
        payload['price'] = .01
        status, first, _ = request(self.app, '/api/quotes', payload)
        self.assertEqual(status, 201)
        second = request(self.app, '/api/quotes', payload)[1]
        self.assertEqual(first, second)
        restart = Application(self.root, [ORIGIN], False, fixture_runner)
        self.assertEqual(request(restart, '/api/quotes', payload)[1], first)
        with restart.store.connect() as db:
            rows = db.execute('SELECT * FROM quotes').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]['items'])[0]['price'], 125.0)
        self.assertNotIn('email', first)
        self.assertEqual(request(self.app, '/api/quotes', {**payload, 'email': 'different@example.invalid'})[0], 409)

    def test_rejects_demo_forged_expired_and_bad_contact(self):
        listing = self.listing()
        data = self.quote(listing)
        for changes, code in [({'listing_ids': ['demo-m4-1']}, 400), ({'listing_ids': ['f'*32]}, 409),
                              ({'consent': False}, 400), ({'email': 'invalid'}, 400), ({'name': 'a\nb'}, 400),
                              ({'notes': 'x'*1001}, 400)]:
            self.assertEqual(request(self.app, '/api/quotes', {**data, **changes})[0], code)
        with self.app.store.connect() as db:
            db.execute('UPDATE listings SET created=0')
        self.assertEqual(request(self.app, '/api/quotes', data)[0], 409)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quotes').fetchone()[0], 0)

    def test_interrupted_job_and_private_database_cannot_be_served(self):
        job = self.app.store.create_job()
        with self.app.store.connect() as db:
            db.execute('UPDATE jobs SET created=? WHERE id=?', (time.time()-301, job))
            db.execute('INSERT INTO media VALUES (?,?,?)', ('b'*32, str(self.app.store.path), time.time()))
        self.assertEqual(request(self.app, '/api/search/'+job)[1]['status'], 'failed')
        self.assertEqual(request(self.app, '/api/media/'+'b'*32)[0], 404)

    def test_origin_payload_routes_and_headers(self):
        self.assertEqual(request(self.app, '/api/search', PARAMS, origin='https://untrusted.invalid')[0], 403)
        self.assertEqual(request(self.app, '/api/search', PARAMS, content_type='text/plain')[0], 415)
        self.assertEqual(request(self.app, '/api/search', raw=b'{')[0], 400)
        self.assertEqual(request(self.app, '/api/search', raw=b'x'*17000)[0], 413)
        self.assertEqual(request(self.app, '/api/search', [1])[0], 400)
        for path in ['/api/quotes', '/api/checkout', '/api/admin', '/data/quotes.sqlite3', '/api/media/../../.env']:
            self.assertEqual(request(self.app, path)[0], 404)
        status, _, headers = request(self.app, '/api/search', method='OPTIONS')
        self.assertEqual(status, 204)
        self.assertEqual(headers['Access-Control-Allow-Origin'], ORIGIN)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Set-Cookie', headers)

    def test_disabled_busy_failed_and_rate_limited_search(self):
        self.app.live = False
        self.assertEqual(request(self.app, '/api/search', PARAMS)[0], 503)
        self.app.live = True
        self.app.search.slot.acquire()
        self.assertEqual(request(self.app, '/api/search', PARAMS)[0], 429)
        self.app.search.slot.release()
        def failure(*args):
            raise RuntimeError('PRIVATE_METADATA_CANARY')
        self.app.search.runner = failure
        _, started, _ = request(self.app, '/api/search', PARAMS)
        result = wait_job(self.app, started['search_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('PRIVATE_METADATA_CANARY', json.dumps(result))
        for _ in range(11):
            status, _, _ = request(self.app, '/api/search', PARAMS)
        self.assertEqual(status, 429)

    def test_public_text_and_media_path_boundary(self):
        with patch.dict(os.environ, {'CARPART_PASSWORD': 'PRIVATE_PASSWORD_CANARY'}):
            listing = public_listing({'supplier': 'PRIVATE_PASSWORD_CANARY https://private.invalid/token', 'stock': 'token=hidden'}, 'a'*32, [])
        self.assertNotIn('PRIVATE_PASSWORD_CANARY', json.dumps(listing))
        self.assertNotIn('private.invalid', json.dumps(listing))
        self.assertNotIn('hidden', json.dumps(listing))
        with self.app.store.connect() as db:
            db.execute('INSERT INTO media VALUES (?,?,?)', ('a'*32, '/etc/passwd', time.time()))
        self.assertEqual(request(self.app, '/api/media/'+'a'*32)[0], 404)


if __name__ == '__main__':
    unittest.main()
