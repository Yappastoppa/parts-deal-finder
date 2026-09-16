"""Private persistent search snapshots and a quote queue; never served as static files."""
import hashlib
import json
import logging
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .domain import APIError

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY, created REAL NOT NULL, public TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS listing_private (listing_id TEXT PRIMARY KEY, created REAL NOT NULL,
 supplier_price REAL, source_results_url TEXT NOT NULL, seller TEXT NOT NULL DEFAULT '',
 gallery_url TEXT, gallery_trigger TEXT);
CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, path TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS search_cache (
 cache_key TEXT PRIMARY KEY, year TEXT NOT NULL, make TEXT NOT NULL, model TEXT NOT NULL,
 part TEXT NOT NULL, interchange TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
 partial INTEGER NOT NULL DEFAULT 0, result_count INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL,
 searched_at REAL NOT NULL, last_refreshed_at REAL NOT NULL, refresh_count INTEGER NOT NULL DEFAULT 0,
 search_count INTEGER NOT NULL DEFAULT 1, refreshing INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pricing_rules (
 id INTEGER PRIMARY KEY, scope_type TEXT NOT NULL, scope_value TEXT NOT NULL DEFAULT '',
 markup_type TEXT NOT NULL, markup_value REAL NOT NULL, min_margin REAL, active INTEGER NOT NULL DEFAULT 1,
 updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS listing_overrides (
 listing_id TEXT PRIMARY KEY, hidden INTEGER NOT NULL DEFAULT 0, manual_price REAL,
 admin_notes TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_sessions (token TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL,
 old_value TEXT, new_value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes (reference TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
 digest TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, contact TEXT NOT NULL, items TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fulfillment_sources (listing_id TEXT PRIMARY KEY, created REAL NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS order_sources (reference TEXT NOT NULL, listing_id TEXT NOT NULL, metadata TEXT NOT NULL,
 PRIMARY KEY(reference, listing_id));
CREATE TABLE IF NOT EXISTS photo_cache (listing_id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL);
'''


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'quotes.sqlite3'
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('schema_version', '2')")
        self.path.chmod(0o600)
        # Searches interrupted by a process restart are retryable, never left polling forever.
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed', payload=? WHERE status='pending'", (json.dumps({'status': 'failed', 'message': 'Search interrupted. Please try again.'}),))
            db.execute('UPDATE search_cache SET refreshing=0')
        logging.getLogger(__name__).info('Persistent data directory: %s', self.directory)

    def save_source(self, listing_id, metadata):
        with self.connect() as db:
            db.execute('INSERT INTO fulfillment_sources VALUES (?,?,?)',
                       (listing_id, time.time(), json.dumps(metadata)))

    def source(self, listing_id):
        with self.connect() as db:
            row = db.execute('SELECT metadata FROM fulfillment_sources WHERE listing_id=?', (listing_id,)).fetchone()
        return json.loads(row['metadata']) if row else None

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

    def save_listing_private(self, listing_id, supplier_price, source_results_url, seller='', gallery_url=None, gallery_trigger=None):
        # Supplier price and the source results URL never leave this table; the public listing has neither.
        if not source_results_url:
            return
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO listing_private VALUES (?,?,?,?,?,?,?)',
                       (listing_id, time.time(), supplier_price, source_results_url, seller,
                        gallery_url, json.dumps(gallery_trigger) if gallery_trigger else None))

    def listing_private(self, listing_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM listing_private WHERE listing_id=? AND created>?', (listing_id, time.time()-86400)).fetchone()
        if not row:
            return None
        item = dict(row)
        item['gallery_trigger'] = json.loads(item['gallery_trigger']) if item['gallery_trigger'] else None
        return item

    def listing_public(self, listing_id):
        with self.connect() as db:
            row = db.execute('SELECT public FROM listings WHERE id=? AND created>?', (listing_id, time.time()-86400)).fetchone()
        return json.loads(row['public']) if row else None

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
            from .pricing import price_for
            rules = self.pricing_rules()
            items, sources = [], []
            for listing_id in values['listing_ids']:
                row = db.execute('SELECT public FROM listings WHERE id=? AND created>?', (listing_id, time.time()-86400)).fetchone()
                if not row:
                    raise APIError(409, 'A selected part has expired. Search again before requesting a quote.')
                item = json.loads(row['public'])
                override_row = db.execute('SELECT * FROM listing_overrides WHERE listing_id=?', (listing_id,)).fetchone()
                override = dict(override_row) if override_row else {}
                source_row = db.execute('SELECT metadata FROM fulfillment_sources WHERE listing_id=?', (listing_id,)).fetchone()
                if override.get('hidden') or not item.get('orderable') or not source_row:
                    raise APIError(409, 'A selected part is no longer available. Search again.')
                source = json.loads(source_row['metadata'])
                customer_price = price_for(source.get('supplier_price'), item, rules, override)
                items.append({**item, 'price': customer_price, 'customer_price': customer_price,
                              'configuration': source['search']})
                sources.append((listing_id, source_row['metadata']))
            reference = 'APF-' + secrets.token_hex(6).upper()
            contact = {k: v for k, v in values.items() if k not in ['listing_ids', 'request_key']}
            contact['contact_consent'] = True
            contact['consent_version'] = 'quote-contact-2026-09-12'
            db.execute('INSERT INTO quotes VALUES (?,?,?,?,?,?,?)', (reference, values['request_key'], digest, time.time(), 'needs_review', json.dumps(contact), json.dumps(items)))
            db.executemany('INSERT INTO order_sources VALUES (?,?,?)', [(reference, lid, metadata) for lid, metadata in sources])
            db.execute('INSERT INTO admin_audit (at,action,target,new_value) VALUES (?,?,?,?)',
                       (time.time(), 'order_created', reference, json.dumps({'status': 'needs_review'})))
        return reference

    def order_status(self, reference, status):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM quotes WHERE reference=?', (reference,)).fetchone()
            if not row:
                raise APIError(404, 'Order not found.')
            from .pricing import ORDER_TRANSITIONS
            if status != row['status'] and status not in ORDER_TRANSITIONS.get(row['status'], ()):
                raise APIError(409, 'Order status transition is not allowed.')
            changed = db.execute('UPDATE quotes SET status=? WHERE reference=?', (status, reference)).rowcount
        if not changed:
            raise APIError(404, 'Order not found.')

    def order(self, reference):
        with self.connect() as db:
            row = db.execute('SELECT * FROM quotes WHERE reference=?', (reference,)).fetchone()
        return dict(row) if row else None

    def orders(self, status=None, limit=200):
        with self.connect() as db:
            if status:
                rows = db.execute('SELECT reference,created,status,contact,items FROM quotes WHERE status=? ORDER BY created DESC LIMIT ?', (status, limit)).fetchall()
            else:
                rows = db.execute('SELECT reference,created,status,contact,items FROM quotes ORDER BY created DESC LIMIT ?', (limit,)).fetchall()
        return [dict(row) for row in rows]

    # --- Search cache: persistent, stale-while-revalidate results built up from real searches only. ---
    def cache_get(self, cache_key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM search_cache WHERE cache_key=?', (cache_key,)).fetchone()
        return dict(row) if row else None

    def cache_put(self, cache_key, year, make, model, part, interchange, result):
        now = time.time()
        payload = json.dumps(result)
        count = len(result.get('listings', []))
        with self.connect() as db:
            existing = db.execute('SELECT search_count, refresh_count FROM search_cache WHERE cache_key=?', (cache_key,)).fetchone()
            searches = (existing['search_count'] if existing else 0) + 1
            refreshes = (existing['refresh_count'] if existing else 0) + (1 if existing else 0)
            db.execute('''INSERT OR REPLACE INTO search_cache
                (cache_key, year, make, model, part, interchange, status, partial, result_count, payload,
                 searched_at, last_refreshed_at, refresh_count, search_count, refreshing)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
                (cache_key, year, make, model, part, interchange, result.get('status', 'ok'),
                 int(bool(result.get('partial'))), count, payload, now, now, refreshes, searches))
        from .search import cache_diagnostic
        cache_diagnostic('CACHE_WRITE', cache_key, results=count)

    def cache_mark_refreshing(self, cache_key, refreshing):
        with self.connect() as db:
            db.execute('UPDATE search_cache SET refreshing=? WHERE cache_key=?', (int(refreshing), cache_key))

    def cache_hit(self, cache_key):
        with self.connect() as db:
            db.execute('UPDATE search_cache SET search_count=search_count+1, searched_at=? WHERE cache_key=?', (time.time(), cache_key))

    def cache_expire(self, cache_key):
        with self.connect() as db:
            db.execute('UPDATE search_cache SET last_refreshed_at=0 WHERE cache_key=?', (cache_key,))

    def cache_delete(self, cache_key):
        with self.connect() as db:
            db.execute('DELETE FROM search_cache WHERE cache_key=?', (cache_key,))

    def cache_list(self, limit=100):
        with self.connect() as db:
            rows = db.execute('''SELECT cache_key, year, make, model, part, interchange, status, result_count,
                searched_at, last_refreshed_at, refresh_count, search_count, refreshing
                FROM search_cache ORDER BY last_refreshed_at DESC LIMIT ?''', (limit,)).fetchall()
        return [dict(row) for row in rows]

    # --- Pricing rules: supplier price is preserved separately; only a matching rule produces a customer price. ---
    def pricing_rules(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM pricing_rules WHERE active=1').fetchall()
        from .pricing import rule_priority
        return sorted((dict(row) for row in rows), key=rule_priority)

    def pricing_rules_all(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM pricing_rules ORDER BY updated_at DESC').fetchall()
        return [dict(row) for row in rows]

    def save_pricing_rule(self, scope_type, scope_value, markup_type, markup_value, min_margin, rule_id=None):
        now = time.time()
        with self.connect() as db:
            if rule_id:
                db.execute('''UPDATE pricing_rules SET scope_type=?, scope_value=?, markup_type=?, markup_value=?,
                    min_margin=?, updated_at=? WHERE id=?''', (scope_type, scope_value, markup_type, markup_value, min_margin, now, rule_id))
                return rule_id
            cursor = db.execute('''INSERT INTO pricing_rules (scope_type, scope_value, markup_type, markup_value, min_margin, active, updated_at)
                VALUES (?,?,?,?,?,1,?)''', (scope_type, scope_value, markup_type, markup_value, min_margin, now))
            return cursor.lastrowid

    def delete_pricing_rule(self, rule_id):
        with self.connect() as db:
            db.execute('DELETE FROM pricing_rules WHERE id=?', (rule_id,))

    # --- Per-listing storefront overrides: hide, manual price, notes. Scoped to that cached listing instance. ---
    def listing_override(self, listing_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM listing_overrides WHERE listing_id=?', (listing_id,)).fetchone()
        return dict(row) if row else None

    def save_listing_override(self, listing_id, hidden=None, manual_price=None, admin_notes=None, clear_manual_price=False):
        existing = self.listing_override(listing_id) or {'hidden': 0, 'manual_price': None, 'admin_notes': ''}
        with self.connect() as db:
            db.execute('''INSERT OR REPLACE INTO listing_overrides (listing_id, hidden, manual_price, admin_notes, updated_at)
                VALUES (?,?,?,?,?)''', (
                listing_id,
                int(hidden) if hidden is not None else existing['hidden'],
                None if clear_manual_price else (manual_price if manual_price is not None else existing['manual_price']),
                admin_notes if admin_notes is not None else existing['admin_notes'],
                time.time()))

    # --- Admin sessions: opaque server-side tokens, never a client-guessable secret. ---
    def create_admin_session(self, ttl=28800):
        token = secrets.token_hex(32)
        now = time.time()
        with self.connect() as db:
            db.execute('DELETE FROM admin_sessions WHERE expires<?', (now,))
            db.execute('INSERT INTO admin_sessions VALUES (?,?,?)', (token, now, now + ttl))
        return token

    def admin_session_valid(self, token):
        if not token:
            return False
        with self.connect() as db:
            row = db.execute('SELECT 1 FROM admin_sessions WHERE token=? AND expires>?', (token, time.time())).fetchone()
        return bool(row)

    def delete_admin_session(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM admin_sessions WHERE token=?', (token,))

    def log_admin_action(self, action, target, old_value=None, new_value=None):
        with self.connect() as db:
            db.execute('INSERT INTO admin_audit (at, action, target, old_value, new_value) VALUES (?,?,?,?,?)',
                       (time.time(), action, target, json.dumps(old_value) if old_value is not None else None,
                        json.dumps(new_value) if new_value is not None else None))

    def admin_audit(self, limit=200):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM admin_audit ORDER BY at DESC LIMIT ?', (limit,)).fetchall()
        return [dict(row) for row in rows]

    # --- Storefront settings: cache TTLs, maintenance mode, announcement text. Not code, editable at runtime. ---
    def get_setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def all_settings(self):
        with self.connect() as db:
            rows = db.execute('SELECT key, value FROM settings').fetchall()
        return {row['key']: json.loads(row['value']) for row in rows}

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def dashboard_stats(self):
        day_ago = time.time() - 86400
        with self.connect() as db:
            searches_today = db.execute('SELECT COUNT(*) FROM search_cache WHERE searched_at>?', (day_ago,)).fetchone()[0]
            cached_searches = db.execute('SELECT COUNT(*) FROM search_cache').fetchone()[0]
            live_listings = db.execute('SELECT COUNT(*) FROM listings WHERE created>?', (day_ago,)).fetchone()[0]
            orders_today = db.execute('SELECT COUNT(*) FROM quotes WHERE created>?', (day_ago,)).fetchone()[0]
            failed_recent = db.execute("SELECT COUNT(*) FROM jobs WHERE status='failed' AND created>?", (day_ago,)).fetchone()[0]
            order_counts = {row['status']: row['n'] for row in db.execute('SELECT status, COUNT(*) AS n FROM quotes GROUP BY status').fetchall()}
        return {
            'searches_today': searches_today, 'cached_searches': cached_searches,
            'live_listings_today': live_listings, 'orders_today': orders_today,
            'recent_failed_searches': failed_recent, 'orders_by_status': order_counts,
        }
