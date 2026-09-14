"""Isolated adapter. Called only with a private job directory; never imports the bot."""
import json
import os
import sys
from pathlib import Path


def main(directory):
    os.umask(0o077)
    root = Path(directory).resolve()
    params = json.loads((root / 'request.json').read_text())
    import carpart_engine as engine
    # Change module globals in this child only. Legacy engine/bot files and runtime stay untouched.
    engine.ROOT, engine.DATA_DIR, engine.IMAGE_DIR = root, root / 'data', root / 'images'
    engine.DATA_DIR.mkdir(exist_ok=True)
    engine.IMAGE_DIR.mkdir(exist_ok=True)
    make = {'CHEVROLET': 'Chevy', 'LAND ROVER': 'LandRover', 'MERCEDES-BENZ': 'Mercedes'}.get(params['make'].upper(), params['make'])
    prompt = f"{params['year']} {make} {params['model']} {params['part']}"
    result = engine.search_parts(prompt, capture_galleries=True, requested_interchange=params.get('interchange') or None)
    (root / 'result.json').write_text(json.dumps(result))


if __name__ == '__main__':
    main(sys.argv[1])
