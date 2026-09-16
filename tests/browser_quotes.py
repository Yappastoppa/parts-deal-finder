"""Real HTTP/browser quote flow against an injected supplier fixture. No external search or notifications."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from wsgiref.simple_server import make_server
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.api import Application
from backend.__main__ import QuietHandler, Server
from test_quote_api import fixture_runner

base = os.environ.get('APF_PREVIEW_URL', 'http://127.0.0.1:8080/')
origin = '/'.join(base.split('/')[:3])
with tempfile.TemporaryDirectory() as tmp:
    calls = []
    def counted_runner(params, directory):
        calls.append(dict(params))
        return fixture_runner(params, directory)
    app = Application(tmp, [origin], True, counted_runner)
    server = make_server('127.0.0.1', 0, app, server_class=Server, handler_class=QuietHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    api = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1200, 'height': 900})
            errors, failures = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            page.on('response', lambda response: failures.append(response.url) if response.status >= 400 else None)
            # The committed config stays in demo mode; only this test browser sees an API origin.
            page.route('**/scripts/config.js', lambda route: route.fulfill(content_type='text/javascript', body=f'export const API_BASE_URL = {json.dumps(api)};'))
            page.goto(base)
            page.locator('#query').fill('2021 BMW M4 Spindle')
            page.locator('#search-form button').click()
            page.get_by_role('button', name='Front left', exact=True).click()
            page.wait_for_selector('.listing')
            assert 'interchange=Front+left' in page.url
            page.reload()
            page.wait_for_selector('.listing')
            assert len(calls) == 2, 'Refreshing started an unnecessary supplier search'
            assert page.locator('#search-summary span').count() == 4
            assert page.locator('.price').inner_text() == '$125.00'
            page.locator('#sort-parts').select_option('price-low')
            assert page.locator('.listing').count() == 1
            page.locator('#filter-price').fill('100')
            assert page.locator('.listing').count() == 0
            assert page.get_by_role('heading', name='Try a wider search').is_visible()
            page.get_by_role('button', name='Clear filters', exact=True).click()
            assert page.locator('.price').inner_text() == '$125.00'
            assert 'supplier listings' in page.locator('#result-count').inner_text()
            assert 'PRIVATE_METADATA_CANARY' not in page.locator('body').inner_text()
            page.get_by_role('button', name='View Photos').click()
            assert 'No supplier photo' in page.locator('#gallery .muted').inner_text()
            page.locator('#gallery-close').click()
            page.get_by_role('button', name='Choose This Part').click()
            page.get_by_role('button', name='Save Part', exact=True).click()
            page.get_by_role('link', name='Cart 1', exact=True).click()
            page.reload()
            assert page.locator('.cart-listing').count() == 1
            page.get_by_role('link', name='Request a Quote').click()
            page.locator('[name=name]').fill('Test Customer')
            page.locator('[name=email]').fill('browser@example.invalid')
            page.locator('[name=postal_code]').fill('12345')
            page.locator('[name=consent]').check()
            page.set_viewport_size({'width': 375, 'height': 812})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path='/tmp/apf-quote-mobile.png', full_page=True)
            page.get_by_role('button', name='Send Quote Request').click()
            page.wait_for_function("document.querySelector('#quote-feedback').textContent.includes('Request received')")
            assert page.locator('#quote-form').is_hidden()
            assert 'APF-' in page.locator('#quote-feedback').inner_text()
            reference = page.locator('#quote-feedback').inner_text().split('Your reference is ')[1].split('.')[0]
            page.reload()
            page.wait_for_function("document.querySelector('#quote-feedback').textContent.includes('Request received')")
            assert reference in page.locator('#quote-feedback').inner_text()
            assert page.locator('#quote-form').is_hidden()
            assert 'browser@example.invalid' not in page.evaluate('JSON.stringify([localStorage,sessionStorage])')
            with app.store.connect() as db:
                records = db.execute('SELECT * FROM quotes').fetchall()
            assert len(records) == 1
            assert json.loads(records[0]['contact'])['email'] == 'browser@example.invalid'
            assert not errors, errors
            assert not failures, failures
            # Expired server snapshots reject quote requests rather than accepting browser data.
            page.goto(base+'quote.html')
            page.get_by_role('button', name='Start another quote', exact=True).click()
            with app.store.connect() as db:
                db.execute('UPDATE listings SET created=0')
            page.locator('[name=name]').fill('Test Customer')
            page.locator('[name=email]').fill('browser@example.invalid')
            page.locator('[name=postal_code]').fill('12345')
            page.locator('[name=consent]').check()
            page.get_by_role('button', name='Send Quote Request').click()
            page.wait_for_function("document.querySelector('#quote-feedback').textContent.includes('expired')")
            assert page.locator('#quote-submit').is_enabled()
            # Verify the demo fallback, independent of whatever backend the committed config.js points to in production.
            page.unroute('**/scripts/config.js')
            page.route('**/scripts/config.js', lambda route: route.fulfill(content_type='text/javascript', body='export const API_BASE_URL = ""; export const HELP_API_BASE_URL = "";'))
            page.goto(base+'quote.html')
            assert page.locator('#quote-form').is_hidden()
            assert 'not sent' in page.locator('#quote-mode').inner_text()
            browser.close()
            print('PASS: real HTTP API + browser fixture search, configuration choice, live cart persistence, saved parts, search resume without repeat supplier calls, quote submission/storage and receipt refresh, mobile form, no contact browser storage, expiration rejection, demo form disabled, no normal-flow console/network errors.')
    finally:
        server.shutdown()
        server.server_close()
        app.search.slot.acquire(timeout=5)
