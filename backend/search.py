"""One bounded supplier search at a time, in a disposable child process. Now cache-aware (stale-while-revalidate)."""
import json
import hashlib
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from . import pricing
from .domain import APIError, public_listing, public_text

ROOT = Path(__file__).resolve().parent.parent

FRESH_SECONDS = 15 * 60
BACKGROUND_REFRESH_SECONDS = 24 * 60 * 60
log = logging.getLogger(__name__)


def cache_diagnostic(event, key, **fields):
    log.info('%s key=%s %s', event, hashlib.sha256(key.encode()).hexdigest(),
             ' '.join(f'{name}={value}' for name, value in fields.items()))


def run_engine(params, directory, timeout=240):
    directory.mkdir(mode=0o700)
    (directory / 'request.json').write_text(json.dumps(params))
    # stdout/stderr may contain supplier information; neither is sent to logs or customers.
    process = subprocess.Popen([sys.executable, '-m', 'backend.worker', str(directory)], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        process.wait(timeout=timeout)
        if process.returncode:
            raise RuntimeError('Search process failed')
        result_path = directory / 'result.json'
        if result_path.stat().st_size > 10_000_000:
            raise RuntimeError('Search response too large')
        return json.loads(result_path.read_text())
    finally:
        # Kill the complete private process group, including any orphaned Chromium descendants.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_photo_worker(request, directory, timeout=60):
    directory.mkdir(mode=0o700)
    (directory / 'request.json').write_text(json.dumps(request))
    process = subprocess.Popen([sys.executable, '-m', 'backend.photo_worker', str(directory)], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        process.wait(timeout=timeout)
        if process.returncode:
            raise RuntimeError('Photo fetch process failed')
        return json.loads((directory / 'result.json').read_text())
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def canonical_cache_key(params):
    return '|'.join([
        params['year'].strip(), params['make'].strip().upper(), params['model'].strip().upper(),
        params['part'].strip().lower(), (params.get('interchange') or '').strip().lower(),
    ])


class SearchService:
    """Real searches are never blocked by galleries; page-visible photos load lazily via PhotoService."""

    def __init__(self, store, runner=run_engine):
        self.store, self.runner = store, runner
        self.slot = threading.BoundedSemaphore(1)

    def start(self, params, force_refresh=False):
        cache_key = canonical_cache_key(params)
        cached = None if force_refresh else self.store.cache_get(cache_key)
        now = time.time()
        cache_diagnostic('CACHE_LOOKUP', cache_key, forced=bool(force_refresh))
        age = now - cached['last_refreshed_at'] if cached else None
        cache_diagnostic('CACHE_HIT', cache_key, hit='yes' if cached and age < BACKGROUND_REFRESH_SECONDS else 'no')
        cache_diagnostic('CACHE_AGE', cache_key, seconds=round(age, 3) if age is not None else 'none')
        if cached:
            age = now - cached['last_refreshed_at']
            payload = self._priced(json.loads(cached['payload']))
            if age < FRESH_SECONDS:
                self.store.cache_hit(cache_key)
                return self._finished_job(payload, cached, 'fresh')
            if age < BACKGROUND_REFRESH_SECONDS:
                self.store.cache_hit(cache_key)
                started = self._maybe_start_background_refresh(cache_key, params)
                return self._finished_job(payload, cached, 'refreshing' if started else 'stale')
        if not self.slot.acquire(blocking=False):
            raise APIError(429, 'Another inventory search is running. Please try again shortly.')
        try:
            job_id = self.store.create_job()
            threading.Thread(target=self.run, args=(job_id, params, cache_key), daemon=True).start()
            return job_id
        except Exception:
            self.slot.release()
            raise

    def cache_status(self, params):
        cached = self.store.cache_get(canonical_cache_key(params))
        if not cached:
            return None
        return {'searched_at': cached['searched_at'], 'last_refreshed_at': cached['last_refreshed_at'],
                'result_count': cached['result_count'], 'refreshing': bool(cached['refreshing'])}

    def _finished_job(self, payload, cached, cache_status):
        job_id = self.store.create_job()
        response = {**payload, 'cache': {
            'status': cache_status, 'hit': True, 'searched_at': cached['searched_at'],
            'last_refreshed_at': cached['last_refreshed_at'], 'result_count': cached['result_count'],
        }}
        self.store.finish_job(job_id, response)
        return job_id

    def _maybe_start_background_refresh(self, cache_key, params):
        if not self.slot.acquire(blocking=False):
            return  # A live search is already running; the customer keeps seeing the last known-good result.
        self.store.cache_mark_refreshing(cache_key, True)
        cache_diagnostic('CACHE_REFRESH_STARTED', cache_key)
        threading.Thread(target=self._background_refresh, args=(cache_key, params), daemon=True).start()
        return True

    def _background_refresh(self, cache_key, params):
        directory = self.store.directory / secrets.token_hex(16)
        try:
            result = self._execute(params, directory)
            if result.get('status') in ('ok', 'no_inventory'):
                self.store.cache_put(cache_key, params['year'], params['make'], params['model'], params['part'],
                                      params.get('interchange') or '', result)
        except Exception:
            pass
        finally:
            self.store.cache_mark_refreshing(cache_key, False)
            self.slot.release()

    def run(self, job_id, params, cache_key=None):
        directory = self.store.directory / job_id
        try:
            result = self._execute(params, directory)
            if cache_key and result.get('status') in ('ok', 'no_inventory'):
                self.store.cache_put(cache_key, params['year'], params['make'], params['model'], params['part'],
                                      params.get('interchange') or '', result)
                now = time.time()
                result = {**self._priced(result), 'cache': {'status': 'fresh', 'hit': False, 'searched_at': now,
                                                              'last_refreshed_at': now, 'result_count': len(result.get('listings', []))}}
        except Exception:
            result = {'status': 'failed', 'message': 'Search could not finish in time. Please try again.'}
        finally:
            try:
                self.store.finish_job(job_id, result)
            except Exception:
                # Storage failures must not trigger a thread traceback containing supplier exceptions.
                import logging
                logging.error('Search storage unavailable; operator attention required')
            finally:
                self.slot.release()

    def _execute(self, params, directory):
        # Fast path: metadata only. Photos load lazily per listing through PhotoService.
        raw = self.runner({**params, 'capture_galleries': False}, directory)
        status = raw.get('status')
        try:
            if status in ['needs_interchange_choice', 'choice_not_found']:
                choices = raw.get('choices') or raw.get('available_choices') or []
                labels = [public_text(c.get('label') if isinstance(c, dict) else c, 240) for c in choices[:30]]
                return {'status': 'needs_interchange_choice', 'choices': [x for x in labels if x]}
            if status in ['ok', 'no_inventory']:
                items = []
                for entry in raw.get('results', [])[:100]:
                    action = entry.get('order_action') or {}
                    if (not entry.get('orderable') or not entry.get('source_results_url')
                            or not action.get('button_id') or not action.get('token')):
                        continue
                    listing_id = secrets.token_hex(16)
                    item = public_listing(entry, listing_id, [])
                    item['configuration'] = {k: public_text(params.get(k), 240) for k in ('year', 'make', 'model', 'part', 'interchange')}
                    supplier_price = pricing.parse_supplier_price(entry.get('price'))
                    self.store.save_source(listing_id, {
                        'source_results_url': entry['source_results_url'], 'order_action': action,
                        'supplier_price': supplier_price, 'stock': entry.get('stock'),
                        'search': item['configuration'],
                    })
                    self.store.save_listing_private(
                        listing_id, supplier_price, entry.get('source_results_url', ''),
                        public_text(entry.get('supplier'), 180), entry.get('gallery_url'), entry.get('gallery_trigger'))
                    self.store.save_listing(item)
                    items.append(item)
                return {'status': 'ok', 'listings': items, 'partial': bool(raw.get('partial')) or len(raw.get('results', [])) > 100}
            return {'status': 'failed', 'message': 'The supplier could not complete this search. Check the vehicle and part, then try again.'}
        finally:
            if directory.is_dir():
                # This directory was generated solely for this job; never retain raw source metadata.
                shutil.rmtree(directory)

    def _priced(self, result):
        """Recompute customer prices and overrides fresh every time a result is served, cached or not,
        so admin pricing/hide changes apply immediately without a new supplier search."""
        if result.get('status') not in ('ok', 'no_inventory') or not result.get('listings'):
            return result
        rules = self.store.pricing_rules()
        priced = []
        for item in result['listings']:
            override = self.store.listing_override(item['id'])
            if override and override.get('hidden'):
                continue
            private = self.store.listing_private(item['id'])
            supplier_price = private['supplier_price'] if private else None
            customer_price = pricing.price_for(supplier_price, item, rules, override)
            item = {**item, 'price': customer_price, 'customer_price': customer_price}
            priced.append(item)
        return {**result, 'listings': priced}


class PhotoService:
    """Bounded, on-demand full-gallery fetch for exactly one listing. Independent of the search slot."""

    def __init__(self, store, runner=run_photo_worker, concurrency=2):
        self.store, self.runner = store, runner
        self.slot = threading.BoundedSemaphore(concurrency)

    def start(self, listing_id):
        private = self.store.listing_private(listing_id)
        if not private:
            raise APIError(404, 'Photos unavailable for this listing.')
        with self.store.connect() as db:
            cached = db.execute('SELECT payload FROM photo_cache WHERE listing_id=? AND created>?',
                                (listing_id, time.time()-86400)).fetchone()
        if cached or not (private.get('gallery_url') or private.get('gallery_trigger')):
            job = self.store.create_job()
            result = json.loads(cached['payload']) if cached else {'status': 'ok', 'listing_id': listing_id, 'images': [], 'gallery_status': 'none'}
            self.store.finish_job(job, result)
            return job
        if not self.slot.acquire(blocking=False):
            raise APIError(429, 'Too many photo requests right now. Please try again shortly.')
        try:
            job_id = self.store.create_job()
            threading.Thread(target=self.run, args=(job_id, listing_id, private), daemon=True).start()
            return job_id
        except Exception:
            self.slot.release()
            raise

    def run(self, job_id, listing_id, private):
        directory = self.store.directory / job_id
        try:
            request = {'source_results_url': private['source_results_url'], 'gallery_url': private.get('gallery_url'),
                       'gallery_trigger': private.get('gallery_trigger')}
            raw = self.runner(request, directory)
            images = []
            for reference in (raw.get('images') or [])[:12]:
                path = (directory / reference).resolve()
                if not path.is_relative_to(directory / 'images') or not path.is_file() or path.stat().st_size > 10_000_000:
                    continue
                if path.read_bytes()[:3] == b'\xff\xd8\xff':
                    target = self.store.directory / 'media' / (secrets.token_hex(16) + '.jpg')
                    target.parent.mkdir(mode=0o700, exist_ok=True)
                    shutil.copyfile(path, target)
                    images.append(self.store.save_media(target))
            result = {'status': 'ok', 'listing_id': listing_id, 'images': images,
                      'gallery_status': 'loaded' if images else 'none'}
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO photo_cache VALUES (?,?,?)', (listing_id, time.time(), json.dumps(result)))
        except Exception:
            result = {'status': 'ok', 'listing_id': listing_id, 'images': [], 'gallery_status': 'error'}
        finally:
            try:
                if directory.is_dir():
                    shutil.rmtree(directory)
                self.store.finish_job(job_id, result)
            except Exception:
                import logging
                logging.error('Photo storage unavailable; operator attention required')
            finally:
                self.slot.release()
