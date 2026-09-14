"""Server-only quote review. Access is through the operator's server account, never a public API."""
import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'show', 'status', 'cleanup', 'backup'])
    parser.add_argument('reference', nargs='?')
    parser.add_argument('--set', dest='status', choices=['new', 'reviewing', 'quoted', 'closed'])
    args = parser.parse_args()
    root = Path(os.environ.get('APF_DATA_DIR', Path(__file__).resolve().parent.parent / 'data/website')).resolve()
    if root.is_relative_to(Path(__file__).resolve().parent.parent / 'docs'):
        parser.error('Private quote data cannot be stored inside docs.')
    path = root / 'quotes.sqlite3'
    if not path.is_file():
        parser.error('No quote database exists yet.')
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        if args.action == 'list':
            for row in db.execute('SELECT reference, status, created FROM quotes ORDER BY created DESC LIMIT 100'):
                print(json.dumps(dict(row)))
        elif args.action == 'show':
            row = db.execute('SELECT reference,status,created,contact,items FROM quotes WHERE reference=?', (args.reference,)).fetchone()
            if not row:
                parser.error('Quote not found.')
            record = dict(row)
            for key in ['contact', 'items']:
                record[key] = json.loads(record[key])
            # Explicit operator action. JSON escaping prevents terminal control injection.
            print(json.dumps(record, indent=2))
        elif args.action == 'status':
            if not args.reference or not args.status:
                parser.error('Provide a reference and --set status.')
            with db:
                changed = db.execute('UPDATE quotes SET status=? WHERE reference=?', (args.status, args.reference)).rowcount
            if not changed:
                parser.error('Quote not found.')
            print('Quote status updated.')
        elif args.action == 'backup':
            from .backup import create_backup
            destination = create_backup(db, root)
            print('Verified private database backup: ' + destination.name)
        elif args.action == 'cleanup':
            cutoff = time.time()-86400
            with db:
                for row in db.execute('SELECT path FROM media WHERE created<?', (cutoff,)):
                    media = Path(row['path']).resolve()
                    if media.is_relative_to(root / 'media'):
                        media.unlink(missing_ok=True)
                for table in ['media', 'listings', 'jobs']:
                    db.execute(f'DELETE FROM {table} WHERE created<?', (cutoff,))
            # Old interrupted worker directories contain private supplier metadata; retain no raw results.
            for child in root.iterdir():
                if child.is_dir() and len(child.name) == 32 and all(c in '0123456789abcdef' for c in child.name) and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child)
            print('Expired search data removed. Quote records retained for operator review.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
