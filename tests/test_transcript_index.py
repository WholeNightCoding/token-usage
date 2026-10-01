import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_codex_usage as fixture
from test_codex_usage import context, counter, meta, response, usage
from transcript_index import TranscriptIndex
import token_stats as ts


class TranscriptIndexTests(unittest.TestCase):
    write = fixture.CodexUsageTests.write

    def setUp(self):
        fixture.CodexUsageTests.setUp(self)
        self.cache_dir = self.root / 'cache'
        self.write('sessions/a.jsonl', [meta(), context(), response(), counter(usage())])
        self.write('one/a.jsonl', [{'timestamp': '2026-10-01T10:00:00Z', 'message': {
            'id': 'claude-a', 'model': 'claude-sonnet-4-6', 'usage': {
                'input_tokens': 10, 'output_tokens': 2}}}], codex=False)

    def scan(self, index):
        files = ts.list_transcript_files(str(self.claude), codex_home=str(self.codex))
        records = ts.scan_records(datetime(2026, 10, 1, tzinfo=timezone.utc),
                                  datetime(2026, 10, 2, tzinfo=timezone.utc),
                                  str(self.claude), files=files,
                                  codex_home=str(self.codex), file_cache=index)
        if index is not None:
            index.prune(files)
        return records

    def test_restart_loads_real_sqlite_index_without_opening_transcripts(self):
        index = TranscriptIndex(self.cache_dir)
        self.addCleanup(index.close)
        self.assertEqual(sum(r.total for r in self.scan(index)), 132)
        index.close()
        restarted = TranscriptIndex(self.cache_dir)
        self.addCleanup(restarted.close)
        from builtins import open as real_open
        opened = []
        def observe(path, *args, **kwargs):
            if str(path).endswith('.jsonl'):
                opened.append(path)
            return real_open(path, *args, **kwargs)
        with patch('builtins.open', side_effect=observe):
            records = self.scan(restarted)
        self.assertEqual(sum(r.total for r in records), 132)
        self.assertEqual(opened, [])
        self.assertEqual(records, self.scan(None))

    def test_rewrite_with_same_size_and_restored_mtime_is_detected(self):
        index = TranscriptIndex(self.cache_dir)
        self.addCleanup(index.close)
        self.scan(index)
        path = self.claude / 'one/a.jsonl'
        before = path.stat()
        path.write_text(path.read_text().replace('10,', '20,'))
        self.assertEqual(path.stat().st_size, before.st_size)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(sum(r.total for r in self.scan(index)), 142)

    def test_delete_and_archive_copies_keep_cross_file_dedup_correct(self):
        index = TranscriptIndex(self.cache_dir)
        self.addCleanup(index.close)
        self.scan(index)
        self.write('archived_sessions/copy.jsonl', [meta(), context(), response()])
        self.assertEqual(sum(r.total for r in self.scan(index)), 132)
        (self.codex / 'sessions/a.jsonl').unlink()
        self.assertEqual(sum(r.total for r in self.scan(index)), 132)
        (self.codex / 'archived_sessions/copy.jsonl').unlink()
        self.assertEqual(sum(r.total for r in self.scan(index)), 12)

    def test_partial_last_line_is_reparsed_when_completed(self):
        index = TranscriptIndex(self.cache_dir)
        self.addCleanup(index.close)
        path = self.codex / 'sessions/a.jsonl'
        extra = json.dumps(response('extra')) + '\n'
        with path.open('a') as fp:
            fp.write(extra[:40])
        self.assertEqual(sum(r.total for r in self.scan(index)), 132)
        with path.open('a') as fp:
            fp.write(extra[40:])
        self.assertEqual(sum(r.total for r in self.scan(index)), 252)

    def test_bad_cached_payload_is_rebuilt_from_the_original_file(self):
        index = TranscriptIndex(self.cache_dir)
        self.addCleanup(index.close)
        self.scan(index)
        index.close()
        with sqlite3.connect(self.cache_dir / 'transcripts.sqlite3') as db:
            db.execute('UPDATE files SET payload = ?', (b'invalid JSON',))
        restarted = TranscriptIndex(self.cache_dir)
        self.addCleanup(restarted.close)
        self.assertEqual(sum(r.total for r in self.scan(restarted)), 132)

    def test_unwritable_cache_falls_back_to_memory_and_keeps_statistics(self):
        blocked = self.root / 'not-a-directory'
        blocked.write_text('keep this user file')
        with self.assertWarns(RuntimeWarning):
            index = TranscriptIndex(blocked)
        self.addCleanup(index.close)
        self.assertEqual(sum(r.total for r in self.scan(index)), 132)
        self.assertEqual(blocked.read_text(), 'keep this user file')

    def test_corrupt_database_falls_back_without_touching_transcripts(self):
        self.cache_dir.mkdir()
        (self.cache_dir / 'transcripts.sqlite3').write_bytes(b'not a sqlite database')
        with self.assertWarns(RuntimeWarning):
            index = TranscriptIndex(self.cache_dir)
        self.addCleanup(index.close)
        self.assertEqual(sum(r.total for r in self.scan(index)), 132)

    def test_parser_revision_change_invalidates_persisted_entries(self):
        index = TranscriptIndex(self.cache_dir)
        self.addCleanup(index.close)
        self.scan(index)
        index.close()
        with sqlite3.connect(self.cache_dir / 'transcripts.sqlite3') as db:
            db.execute('UPDATE meta SET revision = ?', ('old parser',))
        restarted = TranscriptIndex(self.cache_dir)
        self.addCleanup(restarted.close)
        self.assertEqual(sum(r.total for r in self.scan(restarted)), 132)
        with sqlite3.connect(self.cache_dir / 'transcripts.sqlite3') as db:
            self.assertNotEqual(db.execute('SELECT revision FROM meta').fetchone()[0],
                                'old parser')


class CachedAccountingTests(fixture.CodexUsageTests):
    """Run the same independent accounting expectations through file caching."""
    def setUp(self):
        super().setUp()
        self.index = TranscriptIndex(self.root / 'index')
        self.addCleanup(self.index.close)

    def scan(self, source='all', start=fixture.START):
        files = ts.list_transcript_files(str(self.claude), source=source,
                                         codex_home=str(self.codex))
        records = ts.scan_records(start, fixture.END, str(self.claude), files=files,
                                  source=source, codex_home=str(self.codex),
                                  file_cache=self.index)
        self.index.prune(files)
        return records


if __name__ == '__main__':
    unittest.main()
