"""Metadata-first photos and cross-listing dialog ownership, with browser-only fixtures."""
import base64
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent / 'docs'
BASE = 'http://127.0.0.1:8080'
A, B, JOB, SEARCH = 'a'*32, 'b'*32, 'c'*32, 'd'*32
items = [dict(id=lid, mode='live', year='2019', make='BMW', model='540i', part='Spindle',
              stock=stock, seller='Fixture supplier', city='Fixture city', location='Front left',
              price=100, mileage=None, condition='Not provided', images=[], orderable=True, has_gallery=gallery)
         for lid, stock, gallery in [(A, 'PHOTO-A', True), (B, 'NO-PHOTO-B', False)]]
polls = 0
photo_starts = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1200, 'height': 900})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    def route(r):
        global polls
        path = urlsplit(r.request.url).path
        data = None
        if path == '/scripts/config.js':
            r.fulfill(content_type='text/javascript', body=f'export const API_BASE_URL={json.dumps(BASE)}; export const HELP_API_BASE_URL="";'); return
        if path == '/api/settings/public': data = {'maintenance_mode': False, 'announcement': ''}
        elif path == '/api/search': data = {'status': 'pending', 'search_id': SEARCH}
        elif path == '/api/search/'+SEARCH: data = {'status': 'ok', 'listings': items}
        elif path == '/api/listing/'+A+'/photos':
            photo_starts.append(A); data = {'status': 'pending', 'job_id': JOB}
        elif path == '/api/photos/'+JOB:
            polls += 1
            data = {'status': 'pending'} if polls < 4 else {'status': 'ok', 'listing_id': A, 'images': ['/api/media/'+A], 'gallery_status': 'loaded'}
        elif path == '/api/media/'+A:
            r.fulfill(content_type='image/png', body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aO6kAAAAASUVORK5CYII=')); return
        if data is not None:
            r.fulfill(content_type='application/json', body=json.dumps(data)); return
        file = ROOT / path.lstrip('/')
        if not file.is_file(): r.fulfill(status=404); return
        r.fulfill(path=str(file), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
    page.route(BASE+'/**', route)
    page.goto(BASE+'/results.html?year=2019&make=BMW&model=540i&part=Spindle&interchange=AWD%2C+LH')
    page.wait_for_selector('.listing')
    assert page.locator('.listing').count() == 2
    assert polls < 4, 'Metadata must render before gallery completion'
    page.locator('.listing').nth(0).get_by_role('button', name='View Photos').click()
    page.locator('#gallery-close').click()
    page.locator('.listing').nth(1).get_by_role('button', name='View Photos').click()
    page.wait_for_function("document.querySelector('.listing-photo img').src.includes('/api/media/')")
    assert 'NO-PHOTO-B' in page.locator('#gallery-title').inner_text()
    assert 'No supplier photo' in page.locator('#gallery .muted').inner_text()
    assert '/api/media/' not in page.locator('#gallery-image').get_attribute('src')
    assert photo_starts == [A], 'Visible thumbnail and full gallery must share one request'
    assert not errors, errors
    browser.close()
print('PASS: metadata before gallery; visible card photo; deduplicated gallery; no-photo listing never borrows another listing photo.')
