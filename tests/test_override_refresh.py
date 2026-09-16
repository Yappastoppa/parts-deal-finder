"""Manual prices follow supplier inventory identity, never scraper session IDs or prices."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from backend.api import Application
from backend.search import canonical_cache_key
from test_quote_api import APITestCase, request, wait_job, ORIGIN

PARAMS = {'year': '2019', 'make': 'BMW', 'model': '540i', 'part': 'Spindle', 'interchange': 'AWD, LH'}


class OverrideRefresh(APITestCase):
    def setUp(self):
        super().setUp()
        self.supplier_price = 49
        self.calls = 0
        self.supplier = 'Milford Motors LLC'
        self.stock = 'DONOR-123'
        self.app.search.runner = self.runner

    def runner(self, params, directory):
        self.calls += 1
        directory.mkdir()
        return {'status': 'ok', 'results': [{**PARAMS, 'stock': self.stock,
            'supplier': self.supplier, 'price': f'${self.supplier_price}', 'orderable': True,
            'source_results_url': f'https://supplier.invalid/results?session={self.calls}',
            'order_action': {'button_id': f'row_{self.calls}', 'token': f'private-token-{self.calls}'}}]}

    def search(self, **extra):
        code, job, _ = request(self.app, '/api/search', {**PARAMS, **extra})
        self.assertEqual(code, 202)
        result = wait_job(self.app, job['search_id'])
        self.assertEqual(result['status'], 'ok')
        return result['listings'][0]

    def override(self):
        item = self.search()
        self.assertEqual(item['price'], 49)
        self.app.store.save_listing_override(item['id'], manual_price=99)
        return item['id']

    def assert_override(self, item, listing_id):
        self.assertEqual(item['id'], listing_id)
        self.assertEqual(item['price'], 99)
        self.assertEqual(item['customer_price'], 99)
        self.assertEqual(self.app.store.listing_private(listing_id)['supplier_price'], self.supplier_price)
        self.assertEqual(self.app.store.source(listing_id)['supplier_price'], self.supplier_price)

    def test_manual_price_survives_forced_refresh_and_supplier_price_change(self):
        lid = self.override()
        self.supplier_price = 65
        self.assert_override(self.search(force_refresh=True), lid)
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.app.store.source(lid)['order_action']['token'], 'private-token-2')
        self.assert_override(self.search(), lid)

    def test_manual_price_survives_background_refresh(self):
        lid = self.override()
        key = canonical_cache_key(PARAMS)
        with self.app.store.connect() as db:
            db.execute('UPDATE search_cache SET last_refreshed_at=? WHERE cache_key=?', (time.time()-3600, key))
        entered, release = threading.Event(), threading.Event()
        def delayed(params, directory):
            entered.set()
            if not release.wait(5): raise AssertionError('refresh release timed out')
            return self.runner(params, directory)
        self.app.search.runner = delayed
        try:
            self.assertEqual(self.search()['price'], 99)
            self.assertTrue(entered.wait(2))
            self.supplier_price = 72
        finally:
            release.set()
        self.assertTrue(self.app.search.slot.acquire(timeout=5))
        self.app.search.slot.release()
        self.assert_override(self.search(), lid)
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.app.store.cache_get(key)['refresh_count'], 1)

    def test_override_survives_new_python_process(self):
        lid = self.override()
        script = '''import json,sys
from backend.api import Application
app=Application(sys.argv[1], [], True)
key=sys.argv[2]
payload=json.loads(app.store.cache_get(key)['payload'])
print(json.dumps(app.search._priced(payload)['listings'][0]))
'''
        environment = {k:v for k,v in os.environ.items() if not k.startswith(('CARPART_', 'TELEGRAM_', 'APF_', 'OPENAI_'))}
        output = subprocess.run([sys.executable, '-c', script, str(self.root), canonical_cache_key(PARAMS)],
                                cwd=Path(__file__).resolve().parent.parent, env=environment,
                                text=True, capture_output=True, check=True)
        self.assert_override(json.loads(output.stdout), lid)
        self.app = Application(self.root, [ORIGIN], True, self.runner)
        self.supplier_price = 61
        self.assert_override(self.search(force_refresh=True), lid)

    def test_clear_override_uses_current_rules_then_supplier(self):
        lid = self.override()
        self.supplier_price = 60
        self.search(force_refresh=True)
        rule = self.app.store.save_pricing_rule('supplier', self.supplier, 'fixed', 15, None)
        self.app.store.save_listing_override(lid, clear_manual_price=True)
        self.assertEqual(self.search()['price'], 75)
        self.app.store.delete_pricing_rule(rule)
        self.assertEqual(self.search()['price'], 60)
        self.assertEqual(self.search(force_refresh=True)['price'], 60)

    def test_cache_is_unpriced_and_old_priced_payload_cannot_win(self):
        lid = self.override()
        key = canonical_cache_key(PARAMS)
        cached = json.loads(self.app.store.cache_get(key)['payload'])
        self.assertIsNone(cached['listings'][0]['price'])
        self.assertIsNone(cached['listings'][0]['customer_price'])
        cached['listings'][0].update(price=1, customer_price=1)
        with self.app.store.connect() as db:
            db.execute('UPDATE search_cache SET payload=? WHERE cache_key=?', (json.dumps(cached),key))
        self.assert_override(self.search(), lid)
        self.app.store.save_listing_override(lid, manual_price=109)
        self.assertEqual(self.search()['price'],109)

    def test_identity_does_not_transfer_to_other_supplier_stock_or_configuration(self):
        lid = self.override()
        for field, value in [('supplier','Another supplier'),('stock','ANOTHER-STOCK')]:
            old = getattr(self, field)
            setattr(self, field, value)
            item = self.search(force_refresh=True)
            self.assertNotEqual(item['id'],lid)
            self.assertEqual(item['price'],49)
            setattr(self, field, old)
        item = self.search(interchange='AWD, RH',force_refresh=True)
        self.assertNotEqual(item['id'],lid)
        self.assertEqual(item['price'],49)

    def test_existing_random_id_override_migrates_and_survives_cleanup(self):
        lid = self.override()
        # Simulate a pre-patch database with an existing override, but no identity mapping.
        with self.app.store.connect() as db:
            db.execute('DELETE FROM inventory_identities')
            db.execute('DELETE FROM listing_identity_aliases')
        self.app = Application(self.root, [ORIGIN], True, self.runner)
        self.assert_override(self.search(force_refresh=True), lid)

        with self.app.store.connect() as db:
            for table in ['listings','fulfillment_sources','listing_private']:
                db.execute(f'DELETE FROM {table}')
        self.app = Application(self.root, [ORIGIN], True, self.runner)
        self.assert_override(self.search(force_refresh=True), lid)

    def test_ambiguous_rows_do_not_merge_sources_or_replace_last_good_cache(self):
        lid = self.override()
        original = self.app.store.source(lid)
        def ambiguous(params, directory):
            result = self.runner(params, directory)
            result['results'].append({**result['results'][0], 'order_action': {'button_id':'other-row','token':'other-token'}})
            return result
        self.app.search.runner = ambiguous
        _, job, _ = request(self.app, '/api/search', {**PARAMS, 'force_refresh': True})
        self.assertEqual(wait_job(self.app, job['search_id'])['status'], 'failed')
        self.assertEqual(self.app.store.source(lid), original)
        self.assert_override(self.search(), lid)

    def test_legacy_alias_resolves_override_and_clear_to_one_identity(self):
        lid = self.override()
        alias = 'f'*32
        public = {**self.app.store.listing_public(lid), 'id':alias}
        self.app.store.save_listing(public)
        self.app.store.save_source(alias,self.app.store.source(lid))
        self.app.store.save_listing_private(alias,49,'https://supplier.invalid/results',self.supplier)
        with self.app.store.connect() as db:
            db.execute('DELETE FROM inventory_identities')
            db.execute('DELETE FROM listing_identity_aliases')
        self.app = Application(self.root,[ORIGIN],True,self.runner)
        self.assertEqual(self.app.store.listing_override(alias)['manual_price'],99)
        self.app.store.save_listing_override(alias,clear_manual_price=True)
        self.assertEqual(self.search()['price'],49)
        self.assertEqual(self.search(force_refresh=True)['id'],lid)
