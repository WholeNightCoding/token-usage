"""Cache per-file parsing while retaining cross-file accounting in token_stats."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
import sqlite3
import sys
import warnings
import zlib

import token_stats as ts
import codex_records


def default_cache_dir(projects_dir: str, codex_home: str) -> Path:
    if sys.platform == 'darwin':
        base = Path.home() / 'Library/Caches'
    elif os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    roots = [str(Path(root).resolve()) for root in (projects_dir, codex_home)]
    scope = hashlib.sha256(json.dumps(roots).encode()).hexdigest()[:16]
    return base / 'token-usage' / scope


def _revision():
    digest = hashlib.sha256()
    for path in (__file__, ts.__file__, codex_records.__file__):
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def _freeze(value):
    return tuple(_freeze(v) for v in value) if isinstance(value, list) else value


def _record_row(record):
    if isinstance(record, dict):
        record = ts.Record(**record)
    return [record.t_utc.isoformat(), record.model, record.project, record.input_,
            record.output, record.cache_read, record.cache_create,
            record.cache_create_1h, record.source, record.reasoning_output]


def _decode_record(row):
    return ts.Record(datetime.fromisoformat(row[0]), *row[1:])


def _encode(scope, data):
    if scope.startswith('claude:'):
        rows = [[key, _record_row(rec)] for key, rec in data]
    else:
        rows = [[[key, _record_row(rec), checkpoint,
                  [mirror[0], mirror[1].isoformat(), mirror[2]], counts]
                 for key, rec, checkpoint, mirror, counts in group] for group in data]
    return zlib.compress(json.dumps(rows, separators=(',', ':')).encode(), level=1)


def _decode(scope, payload):
    rows = json.loads(zlib.decompress(payload))
    if scope.startswith('claude:'):
        return [(_freeze(key), _decode_record(rec)) for key, rec in rows]
    return tuple([(_freeze(key), vars(_decode_record(rec)), _freeze(checkpoint),
                   (mirror[0], datetime.fromisoformat(mirror[1]), _freeze(mirror[2])),
                   _freeze(counts))
                  for key, rec, checkpoint, mirror, counts in group] for group in rows)


class TranscriptIndex:
    """Disposable statistics only; callers serialize access around a scan."""
    def __init__(self, cache_dir: str | Path | None = None):
        self.entries = {}
        self.disk_entries = {}
        self.dirty = set()
        self.db = None
        if cache_dir is not None:
            try:
                directory = Path(cache_dir)
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != 'nt':
                    directory.chmod(0o700)
                self.db = sqlite3.connect(directory / 'transcripts.sqlite3',
                                          check_same_thread=False, timeout=2)
                self.db.execute('CREATE TABLE IF NOT EXISTS meta (revision TEXT)')
                self.db.execute('CREATE TABLE IF NOT EXISTS files ('
                                'path TEXT PRIMARY KEY, scope TEXT, signature TEXT, '
                                'payload BLOB, checksum TEXT)')
                revision = _revision()
                prior = self.db.execute('SELECT revision FROM meta').fetchone()
                if prior != (revision,):
                    with self.db:
                        self.db.execute('DELETE FROM files')
                        self.db.execute('DELETE FROM meta')
                        self.db.execute('INSERT INTO meta VALUES (?)', (revision,))
                self.disk_entries = {
                    path: (scope, tuple(json.loads(signature)))
                    for path, scope, signature in self.db.execute(
                        'SELECT path, scope, signature FROM files')}
            except (OSError, sqlite3.Error, ValueError, TypeError) as error:
                self._disable_disk(error)

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def _disable_disk(self, error):
        self.close()
        self.disk_entries.clear()
        warnings.warn(f'Token usage disk cache unavailable; using memory cache: {error}',
                      RuntimeWarning, stacklevel=2)

    def _read(self, path, scope, read):
        try:
            st = os.stat(path)
        except OSError:
            self.entries.pop(path, None)
            return [] if scope.startswith('claude:') else ([], [])
        signature = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        prior = self.entries.get(path)
        if prior and prior[:2] == (scope, signature):
            return prior[2]
        if self.db is not None and self.disk_entries.get(path) == (scope, signature):
            try:
                row = self.db.execute('SELECT payload, checksum FROM files WHERE path = ?',
                                      (path,)).fetchone()
                if row and hashlib.sha256(row[0]).hexdigest() == row[1]:
                    data = _decode(scope, row[0])
                    self.entries[path] = (scope, signature, data)
                    return data
            except sqlite3.Error as error:
                self._disable_disk(error)
            except (ValueError, TypeError, KeyError, IndexError, zlib.error):
                pass  # A damaged derived entry is rebuilt from the transcript.
        data = read()
        self.entries[path] = (scope, signature, data)
        self.dirty.add(path)
        return data

    def read_claude(self, path: str, projects_dir: str) -> list:
        return self._read(path, 'claude:' + str(Path(projects_dir).resolve()),
                          lambda: ts.read_claude_file(path, projects_dir))

    def read_codex(self, path: str) -> tuple:
        return self._read(path, 'codex', lambda: codex_records._read_file(path))

    def prune(self, paths: list[str]) -> None:
        present = set(paths)
        for path in list(self.entries):
            if path not in present:
                del self.entries[path]
                self.dirty.discard(path)
        if self.db is not None:
            obsolete = set(self.disk_entries) - present
            try:
                with self.db:
                    self.db.executemany('DELETE FROM files WHERE path = ?',
                                        [(path,) for path in obsolete])
                    for path in self.dirty:
                        scope, signature, data = self.entries[path]
                        payload = _encode(scope, data)
                        self.db.execute('INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)',
                                        (path, scope, json.dumps(signature), payload,
                                         hashlib.sha256(payload).hexdigest()))
                for path in obsolete:
                    self.disk_entries.pop(path, None)
                for path in self.dirty:
                    self.disk_entries[path] = self.entries[path][:2]
            except (OSError, sqlite3.Error) as error:
                self._disable_disk(error)
        self.dirty.clear()
