"""WSGI customer API. No legacy checkout, supplier ordering or public admin endpoints."""
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from pathlib import Path
from .domain import APIError, quote_input, search_input
from .search import SearchService
from .store import Store
from .help import HelpService, help_input

ROOT = Path(__file__).resolve().parent.parent


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
        self.store = Store(directory)
        self.help = HelpService(directory)
        self.search = SearchService(self.store, runner) if runner else SearchService(self.store)
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
                headers.extend([('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'), ('Access-Control-Allow-Headers', 'Content-Type'), ('Access-Control-Max-Age', '600')])
                return self.respond(start_response, 204, b'', headers, 'text/plain')
            if method == 'GET' and path == '/api/health':
                return self.respond(start_response, 200, {'status': 'ok', 'live_search': self.live}, headers)
            if method == 'GET' and path == '/api/help/status':
                return self.respond(start_response, 200, {'enabled': self.help.enabled}, headers)
            if method == 'GET' and re.fullmatch('/api/search/[a-f0-9]{32}', path):
                return self.respond(start_response, 200, self.store.job(path.rsplit('/', 1)[1]), headers)
            if method == 'GET' and re.fullmatch('/api/media/[a-f0-9]{32}', path):
                media = self.store.media(path.rsplit('/', 1)[1])
                return self.respond(start_response, 200, media.read_bytes(), headers, 'image/jpeg')
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
                self.rate.check(ip, 'search', 10)
                job_id = self.search.start(params)
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
