import json
import os
from pathlib import Path
import subprocess
import tempfile
import sqlite3
import types
import unittest
from unittest.mock import Mock, patch

from backend.search import run_engine
from backend.worker import main as worker_main
from backend.review import main as review_main
from test_quote_api import APITestCase, PARAMS, request, wait_job


class SearchAdapter(unittest.TestCase):
    def test_worker_calls_existing_search_with_isolated_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'request.json').write_text(json.dumps({**PARAMS, 'make': 'CHEVROLET', 'interchange': 'Front left'}))
            engine = types.SimpleNamespace(search_parts=Mock(return_value={'status': 'ok', 'results': []}))
            old_umask = os.umask(0o077)
            try:
                with patch.dict('sys.modules', {'carpart_engine': engine}):
                    worker_main(root)
            finally:
                os.umask(old_umask)
            engine.search_parts.assert_called_once_with('2021 Chevy M4 Spindle', capture_galleries=False, requested_interchange='Front left')
            self.assertEqual(engine.ROOT, root)
            self.assertEqual(engine.DATA_DIR, root/'data')
            self.assertEqual(json.loads((root/'result.json').read_text())['status'], 'ok')

    def test_timeout_terminates_private_browser_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = Mock(pid=999999, returncode=None)
            process.wait.side_effect = [subprocess.TimeoutExpired('worker', 1), 0]
            with patch('backend.search.subprocess.Popen', return_value=process) as launch, patch('backend.search.os.killpg') as kill:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_engine(PARAMS, Path(tmp)/'job', timeout=1)
            self.assertEqual(launch.call_args.kwargs['stdout'], subprocess.DEVNULL)
            self.assertEqual(launch.call_args.kwargs['stderr'], subprocess.DEVNULL)
            self.assertTrue(launch.call_args.kwargs['start_new_session'])
            kill.assert_called_once()


class MediaAndReview(APITestCase):
    def test_consistent_private_backup_can_be_restored(self):
        listing = self.listing()
        reference = request(self.app, '/api/quotes', self.quote(listing))[1]['reference']
        with patch.dict(os.environ, {'APF_DATA_DIR': str(self.root)}), patch('sys.argv', ['review', 'backup']), patch('builtins.print'):
            review_main()
        paths = list((self.root/'backups').glob('*.sqlite3'))
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)
        # Restore the snapshot into an independent connection; do not touch the live database.
        with sqlite3.connect(paths[0]) as backup, sqlite3.connect(':memory:') as restored:
            backup.backup(restored)
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            row = restored.execute('SELECT reference,contact FROM quotes').fetchone()
            self.assertEqual(row[0], reference)
            self.assertEqual(json.loads(row[1])['email'], 'test@example.invalid')

    def test_backup_does_not_follow_an_external_directory_symlink(self):
        from backend.backup import create_backup
        with tempfile.TemporaryDirectory() as outside:
            (self.root/'backups').symlink_to(outside, target_is_directory=True)
            with self.app.store.connect() as db:
                with self.assertRaises(ValueError):
                    create_backup(db, self.root)
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_media_allowlist_and_review_queue(self):
        def runner(params, directory):
            directory.mkdir()
            return {'status': 'ok', 'results': [{**PARAMS, 'orderable': True, 'order_action': {'button_id': 'fixture', 'token': 'fixture'}, 'gallery_url': 'https://private.invalid/gallery', 'source_results_url': 'https://private.invalid/?token=x'}]}
        self.app.search.runner = runner
        listing = self.listing()
        self.assertEqual(listing['images'], [])

        def photo_runner(request, directory):
            directory.mkdir()
            (directory/'images').mkdir()
            (directory/'images/part.jpg').write_bytes(b'\xff\xd8\xfffixture')
            (directory/'images/not-photo.svg').write_text('<svg>private</svg>')
            return {'images': ['images/part.jpg', 'images/not-photo.svg', '/etc/passwd']}
        self.app.photos.runner = photo_runner
        _, started, _ = request(self.app, f"/api/listing/{listing['id']}/photos", method='POST')
        photo_result = wait_job(self.app, started['job_id'])
        self.assertEqual(len(photo_result['images']), 1)
        status, image, headers = request(self.app, photo_result['images'][0])
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/jpeg')
        self.assertEqual(image[:3], b'\xff\xd8\xff')
        response = request(self.app, '/api/quotes', self.quote(listing))[1]
        with patch.dict(os.environ, {'APF_DATA_DIR': str(self.root)}), patch('sys.argv', ['review', 'status', response['reference'], '--set', 'reviewing']), patch('builtins.print'):
            review_main()
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM quotes').fetchone()[0], 'reviewing')
            for table in ['listings','media','jobs','listing_private']:
                db.execute(f'UPDATE {table} SET created=0')
        with patch.dict(os.environ, {'APF_DATA_DIR': str(self.root)}), patch('sys.argv', ['review', 'cleanup']), patch('builtins.print'):
            review_main()
        self.assertEqual(request(self.app, photo_result['images'][0])[0], 404)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quotes').fetchone()[0], 1)
        self.assertFalse(list((self.root/'media').iterdir()))
