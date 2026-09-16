"""Production boundaries: durable exact sources, enforced checkout, cache and privileged routes."""
import json
import time
from unittest.mock import Mock
from backend.api import Application
from backend.search import canonical_cache_key
from backend.domain import APIError
import test_admin_api
from test_quote_api import APITestCase, PARAMS, request, wait_job


class ProductionPath(APITestCase):
    login = test_admin_api.AdminAPI.login
    _auth_request = test_admin_api.AdminAPI._auth_request

    def test_checkout_snapshots_exact_source_and_current_price(self):
        item = self.listing()
        self.app.store.save_pricing_rule('global', '', 'percent', 20, None)
        data = self.quote(item)
        data['customer_price'] = .01
        reference = request(self.app, '/api/quotes', data)[1]['reference']
        order = self.app.store.order(reference)
        self.assertEqual(order['status'], 'needs_review')
        snapshot = json.loads(order['items'])[0]
        self.assertEqual(snapshot['customer_price'], 150)
        self.assertEqual(snapshot['configuration']['interchange'], 'Front left')
        self.assertNotIn('PRIVATE_METADATA_CANARY', json.dumps(snapshot))
        with self.app.store.connect() as db:
            source = json.loads(db.execute('SELECT metadata FROM order_sources WHERE reference=?', (reference,)).fetchone()[0])
            db.execute('DELETE FROM fulfillment_sources')
            db.execute('DELETE FROM listing_private')
            db.execute('DELETE FROM listings')
        self.assertEqual(source['order_action']['token'], 'PRIVATE_METADATA_CANARY')
        self.assertEqual(source['supplier_price'], 125)
        restarted = Application(self.root, [], False)
        with restarted.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM order_sources').fetchone()[0], 1)
        self.assertEqual(restarted.store.order(reference)['status'], 'needs_review')

    def test_hidden_or_incomplete_source_cannot_checkout(self):
        item = self.listing()
        self.app.store.save_listing_override(item['id'], hidden=True)
        self.assertEqual(request(self.app, '/api/quotes', self.quote(item))[0], 409)
        self.app.store.save_listing_override(item['id'], hidden=False)
        with self.app.store.connect() as db:
            db.execute('DELETE FROM fulfillment_sources')
        self.assertEqual(request(self.app, '/api/quotes', self.quote(item))[0], 409)

    def test_supplier_priority_and_manual_override(self):
        item = self.listing()
        store = self.app.store
        store.save_pricing_rule('part', 'Spindle', 'fixed', 10, None)
        store.save_pricing_rule('supplier', 'Fixture Yard', 'fixed', 30, None)
        result = self.app.search._priced({'status': 'ok', 'listings': [item]})
        self.assertEqual(result['listings'][0]['customer_price'], 155)
        store.save_listing_override(item['id'], manual_price=199)
        self.assertEqual(self.app.search._priced(result)['listings'][0]['price'], 199)
        self.assertEqual(store.listing_private(item['id'])['supplier_price'], 125)

    def test_order_transitions_and_preparation_never_execute_supplier(self):
        item = self.listing()
        reference = request(self.app, '/api/quotes', self.quote(item))[1]['reference']
        auth = {'HTTP_AUTHORIZATION': 'Bearer '+self.login()}
        path = '/api/admin/orders/'+reference
        self.assertEqual(self._auth_request(path+'/status', auth, data={'status': 'supplier_ordered'})[0], 409)
        self.assertEqual(self._auth_request(path+'/prepare-supplier-order', auth, data={})[0], 409)
        for status in ['customer_confirmed', 'ready_for_supplier_order']:
            self.assertEqual(self._auth_request(path+'/status', auth, data={'status': status})[0], 200)
        code, prepared, _ = self._auth_request(path+'/prepare-supplier-order', auth, data={})
        self.assertEqual(code, 200)
        self.assertFalse(prepared['supplier_submitted'])
        self.assertNotIn('PRIVATE_METADATA_CANARY', json.dumps(prepared))
        self.assertEqual(self._auth_request(path+'/place-supplier-order', auth, data={})[0], 501)
        self.assertIn('supplier_handoff_prepared', [a['action'] for a in self.app.store.admin_audit()])

    def test_admin_routes_cache_lifecycle_and_logout(self):
        item = self.listing()
        key = canonical_cache_key({**PARAMS, 'interchange': 'Front left'})
        auth = {'HTTP_AUTHORIZATION': 'Bearer '+self.login()}
        routes = ['/session', '/dashboard', '/cache', '/cache/'+key, '/pricing-rules', '/listings/'+item['id'], '/orders', '/settings', '/audit']
        for path in routes:
            self.assertEqual(self._auth_request('/api/admin'+path, {})[0], 401)
            code, payload, _ = self._auth_request('/api/admin'+path, auth)
            self.assertEqual(code, 200, path)
            self.assertNotIn('PRIVATE_METADATA_CANARY', json.dumps(payload))
        for action in ['expire', 'refresh']:
            self.assertEqual(self._auth_request('/api/admin/cache/'+key+'/'+action, {}, data={})[0], 401)
            code, result, _ = self._auth_request('/api/admin/cache/'+key+'/'+action, auth, data={})
            self.assertEqual(code, 200)
            if 'search_id' in result:
                wait_job(self.app, result['search_id'])
        self.assertEqual(self._auth_request('/api/admin/cache/'+key, auth, method='DELETE')[0], 200)
        self.assertEqual(self._auth_request('/api/admin/cache/'+key, auth)[0], 404)
        self.assertEqual(self._auth_request('/api/admin/logout', auth, data={})[0], 200)
        self.assertEqual(self._auth_request('/api/admin/session', auth)[0], 401)

    def test_restart_cache_and_popularity(self):
        item = self.listing()
        key = canonical_cache_key({**PARAMS, 'interchange': 'Front left'})
        restarted = Application(self.root, ['http://127.0.0.1:8080'], True, Mock(side_effect=AssertionError('supplier must not run')))
        _, job, _ = request(restarted, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        result = wait_job(restarted, job['search_id'])
        self.assertTrue(result['cache']['hit'])
        self.assertEqual(result['listings'][0]['id'], item['id'])
        self.assertEqual(restarted.store.cache_get(key)['search_count'], 2)

    def test_no_photo_does_not_call_worker(self):
        item = self.listing()
        self.app.photos.runner = Mock(side_effect=AssertionError('no gallery'))
        _, job, _ = request(self.app, '/api/listing/'+item['id']+'/photos', {})
        result = wait_job(self.app, job['job_id'])
        self.assertEqual(result['images'], [])
        self.app.photos.runner.assert_not_called()

    def test_non_orderable_rows_never_become_customer_listings(self):
        self.app.search.runner = lambda p, d: {'status': 'ok', 'results': [
            {**PARAMS, 'orderable': False}, {**PARAMS, 'orderable': True, 'source_results_url': 'https://private.invalid'}]}
        _, job, _ = request(self.app, '/api/search', PARAMS)
        self.assertEqual(wait_job(self.app, job['search_id'])['listings'], [])

    def test_nonfinite_markup_rejected(self):
        auth = {'HTTP_AUTHORIZATION': 'Bearer '+self.login()}
        for value in ['NaN', 'Infinity', -1]:
            self.assertEqual(self._auth_request('/api/admin/pricing-rules', auth, data={
                'scope_type': 'global', 'markup_type': 'percent', 'markup_value': value})[0], 400)

    def test_storage_initialization_fails_clearly_for_non_directory(self):
        path = self.root / 'not-a-directory'
        path.write_text('fixture')
        with self.assertRaisesRegex(RuntimeError, 'Persistent storage initialization failed'):
            Application(path, [], False)

    def test_unknown_price_stays_null_without_manual_override(self):
        from backend.pricing import price_for
        self.assertIsNone(price_for(None, {}, [], None))
        self.assertEqual(price_for(None, {}, [], {'manual_price': 80}), 80)
