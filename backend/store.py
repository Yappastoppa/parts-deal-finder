"""Private persistent search snapshots and a quote queue; never served as static files."""
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .domain import APIError

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY, created REAL NOT NULL, public TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, path TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS quotes (reference TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
 digest TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, contact TEXT NOT NULL, items TEXT NOT NULL);
'''


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'quotes.sqlite3'
        with self.connect() as db:
            db.executescript(SCHEMA)
        self.path.chmod(0o600)
        # Searches interrupted by a process restart are retryable, never left polling forever.
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed', payload=? WHERE status='pending'", (json.dumps({'status': 'failed', 'message': 'Search interrupted. Please try again.'}),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_job(self):
        job_id = secrets.token_hex(16)
        with self.connect() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?)', (job_id, time.time(), 'pending', '{}'))
        return job_id

    def finish_job(self, job_id, result):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?, payload=? WHERE id=?', (result['status'], json.dumps(result), job_id))

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND created>?', (job_id, time.time()-86400)).fetchone()
        if not row:
            raise APIError(404, 'Search expired. Please search again.')
        if row['status'] == 'pending' and row['created'] < time.time()-300:
            return {'status': 'failed', 'message': 'Search was interrupted. Please try again.'}
        return {'status': 'pending'} if row['status'] == 'pending' else json.loads(row['payload'])

    def save_listing(self, item):
        with self.connect() as db:
            db.execute('INSERT INTO listings VALUES (?,?,?)', (item['id'], time.time(), json.dumps(item)))

    def save_media(self, path):
        media_id = secrets.token_hex(16)
        with self.connect() as db:
            db.execute('INSERT INTO media VALUES (?,?,?)', (media_id, str(path), time.time()))
        return f'/api/media/{media_id}'

    def media(self, media_id):
        with self.connect() as db:
            row = db.execute('SELECT path FROM media WHERE id=? AND created>?', (media_id, time.time()-86400)).fetchone()
        if not row:
            raise APIError(404, 'Photo unavailable.')
        path = Path(row['path']).resolve()
        if not path.is_relative_to(self.directory / 'media') or path.suffix.lower() != '.jpg' or not path.is_file():
            raise APIError(404, 'Photo unavailable.')
        return path

    def submit_quote(self, values):
        digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT reference,digest FROM quotes WHERE request_key=?', (values['request_key'],)).fetchone()
            if previous:
                if previous['digest'] != digest:
                    raise APIError(409, 'This request identifier was already used. Start a new request.')
                return previous['reference']
            items = []
            for listing_id in values['listing_ids']:
                row = db.execute('SELECT public FROM listings WHERE id=? AND created>?', (listing_id, time.time()-86400)).fetchone()
                if not row:
                    raise APIError(409, 'A selected part has expired. Search again before requesting a quote.')
                items.append(json.loads(row['public']))
            reference = 'APF-' + secrets.token_hex(6).upper()
            contact = {k: v for k, v in values.items() if k not in ['listing_ids', 'request_key']}
            contact['contact_consent'] = True
            contact['consent_version'] = 'quote-contact-2026-09-12'
            db.execute('INSERT INTO quotes VALUES (?,?,?,?,?,?,?)', (reference, values['request_key'], digest, time.time(), 'new', json.dumps(contact), json.dumps(items)))
        return reference
