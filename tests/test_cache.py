"""Search cache (stale-while-revalidate) tests. No supplier connections; a counting fixture stands in for the engine."""
import time
import unittest
from test_quote_api import APITestCase, PARAMS, request, wait_job


class SearchCache(APITestCase):
    def counting_runner(self):
        calls = []

        def runner(params, directory):
            calls.append(params)
            directory.mkdir()
            return {'status': 'ok', 'results': [{**PARAMS, 'orderable': True, 'order_action': {'button_id': 'fixture', 'token': 'fixture'}, 'source_results_url': 'https://private.invalid/?x', 'price': '$100.00'}]}
        return calls, runner

    def test_repeat_search_within_fresh_window_does_not_rerun_supplier(self):
        calls, runner = self.counting_runner()
        self.app.search.runner = runner
        _, started, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        first = wait_job(self.app, started['search_id'])
        self.assertEqual(first['cache']['status'], 'fresh')
        self.assertEqual(len(calls), 1)
        _, started2, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        second = wait_job(self.app, started2['search_id'])
        self.assertEqual(second['cache']['status'], 'fresh')
        self.assertEqual(len(calls), 1, 'A second identical search within the fresh window must reuse the cache.')

    def test_stale_cache_serves_immediately_and_refreshes_in_background(self):
        calls, runner = self.counting_runner()
        self.app.search.runner = runner
        _, started, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        wait_job(self.app, started['search_id'])
        self.assertEqual(len(calls), 1)
        from backend.search import canonical_cache_key
        key = canonical_cache_key({**PARAMS, 'interchange': 'Front left'})
        with self.app.store.connect() as db:
            db.execute('UPDATE search_cache SET last_refreshed_at=? WHERE cache_key=?', (time.time() - 3600, key))
        _, started2, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        second = wait_job(self.app, started2['search_id'])
        self.assertIn(second['cache']['status'], ('stale', 'refreshing'))
        for _ in range(200):
            with self.app.store.connect() as db:
                refreshing = db.execute('SELECT refreshing FROM search_cache WHERE cache_key=?', (key,)).fetchone()[0]
            if not refreshing:
                break
            time.sleep(.01)
        self.assertEqual(len(calls), 2, 'The stale cache must trigger exactly one background refresh.')

    def test_force_refresh_bypasses_fresh_cache(self):
        calls, runner = self.counting_runner()
        self.app.search.runner = runner
        _, started, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        wait_job(self.app, started['search_id'])
        self.assertEqual(len(calls), 1)
        status, started2, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left', 'force_refresh': True})
        self.assertEqual(status, 202)
        wait_job(self.app, started2['search_id'])
        self.assertEqual(len(calls), 2)

    def test_supplier_price_is_default_without_pricing_rule(self):
        calls, runner = self.counting_runner()
        self.app.search.runner = runner
        _, started, _ = request(self.app, '/api/search', {**PARAMS, 'interchange': 'Front left'})
        result = wait_job(self.app, started['search_id'])
        self.assertEqual(result['listings'][0]['price'], 100.0)


if __name__ == '__main__':
    unittest.main()
