"""WSGI customer API. No legacy checkout or public admin endpoint without a session; no supplier ordering."""
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs
from .admin import AdminAPI, public_settings
from .domain import APIError, quote_input, search_input
from .search import PhotoService, SearchService
from .store import Store
from .help import HelpService, help_input

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')


class RateLimit:
    def __init__(self):
        self.entries = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, ip, kind, maximum):
        now = time.monotonic()
        with self.lock:
            for key in list(self.entries):
                if not self.entries[key] or self.entries[key][-1] < now - 600:
                    del self.entries[key]
            key = (ip, kind)
            if key not in self.entries and len(self.entries) >= 10000:
                raise APIError(429, 'Service is busy. Please try again later.')
            queue = self.entries[key]
            while queue and queue[0] < now - 600:
                queue.popleft()
            if len(queue) >= maximum:
                raise APIError(429, 'Too many requests. Please try again in a few minutes.')
            queue.append(now)


class Application:
    def __init__(self, directory=None, origins=None, live=None, runner=None):
        self.live = os.environ.get('APF_LIVE_SEARCH') == '1' if live is None else live
        directory = Path(directory or os.environ.get('APF_DATA_DIR', ROOT / 'data' / 'website')).resolve()
        if directory.is_relative_to(ROOT / 'docs'):
            raise RuntimeError('Private API data cannot be stored inside docs')
        try:
            self.store = Store(directory)
        except (OSError, sqlite3.Error):
            logging.getLogger(__name__).critical('Persistent storage initialization failed; verify APF_DATA_DIR is a writable private directory')
            raise RuntimeError('Persistent storage initialization failed') from None
        self.help = HelpService(directory)
        self.search = SearchService(self.store, runner) if runner else SearchService(self.store)
        self.photos = PhotoService(self.store)
        self.admin = AdminAPI(self.store, RateLimit(), self.search)
        self.origins = set(origins if origins is not None else filter(None, os.environ.get('APF_ALLOWED_ORIGINS', '').split(',')))
        self.rate = RateLimit()

    def __call__(self, environ, start_response):
        origin = environ.get('HTTP_ORIGIN', '')
        headers = [('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'no-store'),
                   ('Referrer-Policy', 'no-referrer'), ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")]
        try:
            if origin and origin not in self.origins:
                raise APIError(403, 'This website is not allowed to use this API.')
            if origin:
                headers.extend([('Access-Control-Allow-Origin', origin), ('Vary', 'Origin')])
            method, path = environ['REQUEST_METHOD'], environ.get('PATH_INFO', '')
            if method == 'OPTIONS':
                if not path.startswith('/api/'):
                    raise APIError(404, 'Not found.')
                headers.extend([('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS'),
                                 ('Access-Control-Allow-Headers', 'Content-Type, Authorization'), ('Access-Control-Max-Age', '600')])
                return self.respond(start_response, 204, b'', headers, 'text/plain')
            if method == 'GET' and path == '/api/health':
                return self.respond(start_response, 200, {'status': 'ok', 'live_search': self.live}, headers)
            if method == 'GET' and path == '/api/help/status':
                return self.respond(start_response, 200, {'enabled': self.help.enabled}, headers)
            if method == 'GET' and path == '/api/settings/public':
                return self.respond(start_response, 200, public_settings(self.store), headers)
            if method == 'GET' and path == '/api/search-cache':
                query = parse_qs(environ.get('QUERY_STRING', ''))
                params = search_input({k: (v[0] if v else '') for k, v in query.items() if k in ['year', 'make', 'model', 'part', 'interchange']})
                status = self.search.cache_status(params)
                return self.respond(start_response, 200, {'cache': status}, headers)
            if method == 'GET' and re.fullmatch('/api/search/[a-f0-9]{32}', path):
                return self.respond(start_response, 200, self.search._priced(self.store.job(path.rsplit('/', 1)[1])), headers)
            if method == 'GET' and re.fullmatch('/api/photos/[a-f0-9]{32}', path):
                return self.respond(start_response, 200, self.store.job(path.rsplit('/', 1)[1]), headers)
            if method == 'GET' and re.fullmatch('/api/media/[a-f0-9]{32}', path):
                media = self.store.media(path.rsplit('/', 1)[1])
                return self.respond(start_response, 200, media.read_bytes(), headers, 'image/jpeg')
            if path.startswith('/api/admin/'):
                return self._admin(start_response, method, path[len('/api/admin'):], environ, headers)
            if method == 'POST' and re.fullmatch('/api/listing/[a-f0-9]{32}/photos', path):
                if not self.live:
                    raise APIError(503, 'Live inventory is not connected yet.')
                ip = environ.get('REMOTE_ADDR', 'unknown')
                self.rate.check(ip, 'photos', 20)
                listing_id = path.split('/')[3]
                job_id = self.photos.start(listing_id)
                return self.respond(start_response, 202, {'status': 'pending', 'job_id': job_id}, headers)
            if method != 'POST' or path not in ['/api/search', '/api/quotes', '/api/help']:
                raise APIError(404, 'Not found.')
            if environ.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
                raise APIError(415, 'Send JSON data.')
            try:
                length = int(environ.get('CONTENT_LENGTH', '0'))
            except ValueError:
                raise APIError(400, 'Invalid request length.')
            if not 0 < length <= 16384:
                raise APIError(413, 'Request is too large or empty.')
            try:
                data = json.loads(environ['wsgi.input'].read(length))
            except (ValueError, UnicodeDecodeError):
                raise APIError(400, 'Invalid JSON request.')
            if not isinstance(data, dict):
                raise APIError(400, 'Expected a JSON object.')
            ip = environ.get('REMOTE_ADDR', 'unknown')  # Do not trust client-supplied forwarded headers.
            if path == '/api/help':
                if not origin or origin not in self.origins:
                    raise APIError(403, 'Open help from the website.')
                message, page, mode = help_input(data)
                if not self.live:
                    mode = 'demo'
                self.rate.check(ip, 'help', 10)
                answer = self.help.answer(message, page, mode)
                return self.respond(start_response, 200, {'answer': answer}, headers)
            if path == '/api/search':
                params = search_input(data)
                if not self.live:
                    raise APIError(503, 'Live inventory is not connected yet.')
                if self.store.get_setting('maintenance_mode', False):
                    raise APIError(503, self.store.get_setting('announcement') or 'Live inventory is temporarily paused for maintenance. Please try again soon.')
                self.rate.check(ip, 'search', 10)
                force_refresh = params.pop('force_refresh', False)
                job_id = self.search.start(params, force_refresh=force_refresh)
                return self.respond(start_response, 202, {'status': 'pending', 'search_id': job_id}, headers)
            values = quote_input(data)
            self.rate.check(ip, 'quote', 10)
            reference = self.store.submit_quote(values)
            return self.respond(start_response, 201, {'status': 'received', 'reference': reference}, headers)
        except APIError as error:
            return self.respond(start_response, error.status, {'message': error.message}, headers)
        except Exception:
            # Neither exception strings nor customer request bodies enter responses or logs.
            return self.respond(start_response, 500, {'message': 'Request could not be completed. Please try again.'}, headers)

    def _admin(self, start_response, method, rest, environ, headers):
        if rest == '/login' and method == 'POST':
            try:
                length = int(environ.get('CONTENT_LENGTH', '0'))
            except ValueError:
                raise APIError(400, 'Invalid request length.')
            if not 0 < length <= 16384:
                raise APIError(413, 'Request is too large or empty.')
            try:
                data = json.loads(environ['wsgi.input'].read(length))
            except (ValueError, UnicodeDecodeError):
                raise APIError(400, 'Invalid JSON request.')
            ip = environ.get('REMOTE_ADDR', 'unknown')
            result = self.admin.login(data if isinstance(data, dict) else {}, ip)
            return self.respond(start_response, 200, result, headers)
        self.admin.require_session(environ)
        data = {}
        if method in ('POST', 'DELETE'):
            try:
                length = int(environ.get('CONTENT_LENGTH', '0'))
            except ValueError:
                length = 0
            if length:
                if length > 65536:
                    raise APIError(413, 'Request is too large.')
                try:
                    data = json.loads(environ['wsgi.input'].read(length))
                except (ValueError, UnicodeDecodeError):
                    raise APIError(400, 'Invalid JSON request.')
                if not isinstance(data, dict):
                    raise APIError(400, 'Expected a JSON object.')
        result = self.admin.handle(method, rest, data, environ)
        return self.respond(start_response, 200, result, headers)

    @staticmethod
    def respond(start_response, status, data, headers, content_type='application/json'):
        payload = data if isinstance(data, bytes) else json.dumps(data).encode()
        start_response(f'{status} {HTTPStatus(status).phrase}', headers + [('Content-Type', content_type), ('Content-Length', str(len(payload)))])
        return [payload]


# Lazy construction allows importing test utilities without opening production data.
_instance = None
_instance_lock = threading.Lock()


def application(environ, start_response):
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Application()
    return _instance(environ, start_response)
