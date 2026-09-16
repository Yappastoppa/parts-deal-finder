"""Isolated adapter for a single on-demand gallery fetch. Never imports the bot; never places an order."""
import json
import os
import sys
from pathlib import Path


def main(directory):
    os.umask(0o077)
    root = Path(directory).resolve()
    request = json.loads((root / 'request.json').read_text())
    import carpart_engine as engine
    # Change module globals in this child only. Legacy engine/bot files and runtime stay untouched.
    engine.ROOT, engine.DATA_DIR, engine.IMAGE_DIR = root, root / 'data', root / 'images'
    engine.DATA_DIR.mkdir(exist_ok=True)
    engine.IMAGE_DIR.mkdir(exist_ok=True)
    listing = {
        'source_results_url': request['source_results_url'],
        'gallery_url': request.get('gallery_url'),
        'gallery_trigger': request.get('gallery_trigger'),
    }
    result = engine.capture_listing_gallery(listing)
    (root / 'result.json').write_text(json.dumps({
        'status': 'ok', 'images': result.get('images', []),
        'gallery_status': result.get('gallery_status', 'none'),
    }))


if __name__ == '__main__':
    main(sys.argv[1])
