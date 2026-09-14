"""Consistent, private SQLite snapshots using the SQLite online backup API."""
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


def create_backup(source, directory):
    backups = directory / 'backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    # Resolve symlinks before writing: a private backup must never escape its runtime directory.
    if not backups.resolve().is_relative_to(directory.resolve()):
        raise ValueError('Backup directory must remain inside the private runtime directory.')
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(6) + '.sqlite3'
    destination = backups / name
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup verification failed.')
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise
