"""Admin control center API tests. No supplier connections; admin auth is a bearer token, not a client secret."""
import os
import time
import unittest
from unittest.mock import patch
from test_quote_api import APITestCase, PARAMS, request, wait_job


class AdminAPI(APITestCase):
    def login(self):
        with patch.dict(os.environ, {'APF_ADMIN_PASSWORD': 'unit-test-only-password'}):
            status, data, _ = request(self.app, '/api/admin/login', {'password': 'unit-test-only-password'})
        self.assertEqual(status, 200)
        return data['token']

    def test_requires_configured_password_and_rejects_wrong_password(self):
        self.assertEqual(request(self.app, '/api/admin/login', {'password': 'x'})[0], 503)
        with patch.dict(os.environ, {'APF_ADMIN_PASSWORD': 'unit-test-only-password'}):
            self.assertEqual(request(self.app, '/api/admin/login', {'password': 'wrong'})[0], 401)
            status, data, _ = request(self.app, '/api/admin/login', {'password': 'unit-test-only-password'})
        self.assertEqual(status, 200)
        self.assertRegex(data['token'], r'^[a-f0-9]{64}$')

    def test_endpoints_require_a_valid_session(self):
        self.assertEqual(request(self.app, '/api/admin/dashboard')[0], 401)
        token = self.login()
        environ_extra = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        status, data, _ = self._auth_request('/api/admin/dashboard', environ_extra)
        self.assertEqual(status, 200)
        self.assertIn('orders_today', data)
        self.assertEqual(self._auth_request('/api/admin/dashboard', {'HTTP_AUTHORIZATION': 'Bearer wrong'})[0], 401)

    def test_pricing_rule_lifecycle_and_listing_override(self):
        token = self.login()
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        status, data, _ = self._auth_request('/api/admin/pricing-rules', auth, method='POST',
                                              data={'scope_type': 'global', 'scope_value': '', 'markup_type': 'percent', 'markup_value': 20})
        self.assertEqual(status, 200)
        rule_id = data['id']
        listing = self.listing()
        status, data, _ = self._auth_request(f"/api/admin/listings/{listing['id']}", auth, method='POST',
                                              data={'manual_price': 329.5, 'admin_notes': 'Confirmed with yard'})
        self.assertEqual(status, 200)
        status, data, _ = self._auth_request(f"/api/admin/listings/{listing['id']}", auth)
        self.assertEqual(data['override']['manual_price'], 329.5)
        status, data, _ = self._auth_request(f'/api/admin/pricing-rules/{rule_id}', auth, method='DELETE')
        self.assertEqual(status, 200)

    def test_hidden_listing_disappears_from_customer_results(self):
        listing = self.listing()
        token = self.login()
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        status, _, _ = self._auth_request(f"/api/admin/listings/{listing['id']}", auth, method='POST', data={'hidden': True})
        self.assertEqual(status, 200)
        _, started, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        result = wait_job(self.app, started['search_id'])
        self.assertEqual(result['listings'], [])

    def test_settings_and_maintenance_mode(self):
        token = self.login()
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        self.assertEqual(request(self.app, '/api/settings/public')[1], {'maintenance_mode': False, 'announcement': ''})
        status, _, _ = self._auth_request('/api/admin/settings', auth, method='POST',
                                           data={'maintenance_mode': True, 'announcement': 'Back soon.'})
        self.assertEqual(status, 200)
        self.assertEqual(request(self.app, '/api/settings/public')[1], {'maintenance_mode': True, 'announcement': 'Back soon.'})
        status, data, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        self.assertEqual(status, 503)
        self.assertEqual(data['message'], 'Back soon.')

    def test_place_supplier_order_is_disabled(self):
        listing = self.listing()
        reference = request(self.app, '/api/quotes', self.quote(listing))[1]['reference']
        token = self.login()
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        status, _, _ = self._auth_request(f'/api/admin/orders/{reference}/place-supplier-order', auth, method='POST', data={})
        self.assertEqual(status, 501)
        status, data, _ = self._auth_request('/api/admin/orders', auth)
        self.assertEqual(status, 200)
        self.assertTrue(any(o['reference'] == reference for o in data['orders']))

    def _auth_request(self, path, extra_environ, method=None, data=None):
        import io
        import json
        body = json.dumps(data).encode() if data is not None else b''
        environ = {'REQUEST_METHOD': method or ('POST' if data is not None else 'GET'), 'PATH_INFO': path,
                   'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body),
                   'REMOTE_ADDR': '127.0.0.1', 'HTTP_ORIGIN': 'http://127.0.0.1:8080', **extra_environ}
        metadata = []
        output = b''.join(self.app(environ, lambda status, headers: metadata.extend([int(status.split()[0]), dict(headers)])))
        if metadata[1].get('Content-Type') == 'application/json':
            output = json.loads(output)
        return metadata[0], output, metadata[1]


if __name__ == '__main__':
    unittest.main()
