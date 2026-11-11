"""Back up the database to a timestamped file and delete old backups.

    python manage.py backup_db                  # write a backup to BACKUP_DIR (default ./backups)
    python manage.py backup_db --keep 30        # keep the newest 30 instead of 14
    python manage.py backup_db --dest /mnt/backups

SQLite databases are written as ``ibsbill-<time>.sqlite3.gz``. PostgreSQL databases
are written with ``pg_dump`` as ``ibsbill-<time>.dump`` (restore with ``pg_restore``).
Backup files are readable only by the user running the command: they hold every
payment and the application keys.

A backup kept on the same disk does not protect against losing the server, so copy
the folder somewhere else too.
"""
import gzip
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

PREFIX = 'ibsbill-'


class Command(BaseCommand):
    help = 'Back up the database and remove old backups.'

    def add_arguments(self, parser):
        parser.add_argument('--dest', default=None,
                            help='Folder for the backups (default: the BACKUP_DIR setting).')
        parser.add_argument('--keep', type=int, default=None,
                            help='How many of the newest backups to keep (default: the BACKUP_KEEP setting).')

    def handle(self, *args, **options):
        dest = Path(options['dest'] or settings.BACKUP_DIR)
        keep = options['keep'] if options['keep'] is not None else settings.BACKUP_KEEP
        if keep < 1:
            raise CommandError('--keep must be at least 1.')

        dest.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(dest, 0o700)
        except OSError:
            pass  # not supported on every platform

        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        vendor = connection.vendor
        if vendor == 'sqlite':
            path = self._sqlite(dest, stamp)
        elif vendor == 'postgresql':
            path = self._postgres(dest, stamp)
        else:
            raise CommandError(f'Backups are not supported for {vendor} databases.')

        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

        removed = self._prune(dest, keep, newest=path)
        size_kb = max(1, path.stat().st_size // 1024)
        self.stdout.write(self.style.SUCCESS(f'Backup written: {path} ({size_kb} KB)'))
        if removed:
            self.stdout.write(f'Removed {len(removed)} old backup(s); keeping the newest {keep}.')

    def _sqlite(self, dest, stamp):
        """Copy the live database with SQLite's own backup call (safe while the site is in use)."""
        raw = dest / f'{PREFIX}{stamp}.sqlite3'
        final = dest / f'{PREFIX}{stamp}.sqlite3.gz'
        try:
            connection.ensure_connection()
            target = sqlite3.connect(str(raw))
            try:
                connection.connection.backup(target)
            finally:
                target.close()
            with open(raw, 'rb') as src, gzip.open(final, 'wb') as out:
                shutil.copyfileobj(src, out)
        except Exception as exc:
            final.unlink(missing_ok=True)
            raise CommandError(f'Backup failed: {exc}')
        finally:
            raw.unlink(missing_ok=True)
        return final

    def _postgres(self, dest, stamp):
        db = settings.DATABASES['default']
        final = dest / f'{PREFIX}{stamp}.dump'
        env = dict(os.environ)
        if db.get('PASSWORD'):
            env['PGPASSWORD'] = db['PASSWORD']
        cmd = ['pg_dump', '--format=custom', '--no-owner', '--file', str(final),
               '--host', db.get('HOST') or 'localhost', '--port', str(db.get('PORT') or 5432),
               '--username', db['USER'], db['NAME']]
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
        except FileNotFoundError:
            raise CommandError('pg_dump was not found. Install the PostgreSQL client tools '
                               '(for example: sudo apt install postgresql-client).')
        except subprocess.TimeoutExpired:
            final.unlink(missing_ok=True)
            raise CommandError('pg_dump took longer than 30 minutes and was stopped.')
        if result.returncode != 0:
            final.unlink(missing_ok=True)
            raise CommandError(f'pg_dump failed: {result.stderr.strip() or result.returncode}')
        return final

    def _prune(self, dest, keep, newest):
        """Delete all but the `keep` most recent backups. The file just written is never removed."""
        files = sorted((f for f in dest.glob(f'{PREFIX}*') if f.is_file()),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        removed = []
        for old in files[keep:]:
            if old == newest:
                continue
            old.unlink(missing_ok=True)
            removed.append(old)
        return removed
