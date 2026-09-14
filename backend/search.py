"""One bounded supplier search at a time, in a disposable child process."""
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from .domain import APIError, public_listing, public_text

ROOT = Path(__file__).resolve().parent.parent


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


class SearchService:
    def __init__(self, store, runner=run_engine):
        self.store, self.runner = store, runner
        self.slot = threading.BoundedSemaphore(1)

    def start(self, params):
        if not self.slot.acquire(blocking=False):
            raise APIError(429, 'Another inventory search is running. Please try again shortly.')
        try:
            job_id = self.store.create_job()
            threading.Thread(target=self.run, args=(job_id, params), daemon=True).start()
            return job_id
        except Exception:
            self.slot.release()
            raise

    def run(self, job_id, params):
        directory = self.store.directory / job_id
        try:
            raw = self.runner(params, directory)
            status = raw.get('status')
            if status in ['needs_interchange_choice', 'choice_not_found']:
                choices = raw.get('choices') or raw.get('available_choices') or []
                labels = [public_text(c.get('label') if isinstance(c, dict) else c, 240) for c in choices[:30]]
                result = {'status': 'needs_interchange_choice', 'choices': [x for x in labels if x]}
            elif status in ['ok', 'no_inventory']:
                items = []
                for entry in raw.get('results', [])[:100]:
                    images = []
                    for reference in (entry.get('images') or [])[:12]:
                        path = (directory / reference).resolve()
                        if not path.is_relative_to(directory / 'images') or not path.is_file() or path.stat().st_size > 10_000_000:
                            continue
                        if path.read_bytes()[:3] == b'\xff\xd8\xff':
                            # Copy only verified raster bytes to a private media area; no arbitrary source URLs.
                            target = self.store.directory / 'media' / (secrets.token_hex(16) + '.jpg')
                            target.parent.mkdir(mode=0o700, exist_ok=True)
                            shutil.copyfile(path, target)
                            images.append(self.store.save_media(target))
                    item = public_listing(entry, secrets.token_hex(16), images)
                    self.store.save_listing(item)
                    items.append(item)
                result = {'status': 'ok', 'listings': items, 'partial': bool(raw.get('partial')) or len(raw.get('results', [])) > 100}
            else:
                result = {'status': 'failed', 'message': 'The supplier could not complete this search. Check the vehicle and part, then try again.'}
        except Exception:
            result = {'status': 'failed', 'message': 'Search could not finish in time. Please try again.'}
        finally:
            try:
                # This directory was generated solely for this job; never retain raw source metadata.
                if directory.is_dir():
                    shutil.rmtree(directory)
                self.store.finish_job(job_id, result)
            except Exception:
                # Storage failures must not trigger a thread traceback containing supplier exceptions.
                import logging
                logging.error('Search storage unavailable; operator attention required')
            finally:
                self.slot.release()
