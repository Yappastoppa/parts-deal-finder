"""Run isolated API checks and both browser suites at a temporary repository subpath."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parent.parent


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    # Tests have no reason to inherit real supplier or messaging credentials.
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('CARPART_', 'TELEGRAM_', 'APF_', 'OPENAI_'))}
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'], cwd=ROOT, env=environment, check=True)
    if importlib.util.find_spec('playwright') is None:
        raise SystemExit('API tests passed. Install development Playwright and Chromium to run browser checks.')
    with tempfile.TemporaryDirectory(prefix='apf-checks-') as tmp:
        (Path(tmp)/'parts-deal-finder').symlink_to(ROOT/'docs', target_is_directory=True)
        handler = partial(QuietStaticHandler, directory=tmp)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        environment['APF_PREVIEW_URL'] = f'http://127.0.0.1:{server.server_port}/parts-deal-finder/'
        try:
            for script in ['browser_preview.py', 'browser_quotes.py', 'browser_help.py', 'browser_admin.py', 'browser_photos.py']:
                subprocess.run([sys.executable, 'tests/'+script], cwd=ROOT, env=environment, check=True, timeout=240)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print('PASS: API, demo, quote, help, admin and photo browser checks. Temporary servers stopped.')


if __name__ == '__main__':
    main()
