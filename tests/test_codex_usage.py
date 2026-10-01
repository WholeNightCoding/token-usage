import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import token_stats as ts

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 2, tzinfo=timezone.utc)


def event(kind, payload, stamp='2026-10-01T10:00:00Z'):
    return {'timestamp': stamp, 'type': kind, 'payload': payload}


def usage(i=100, cached=60, out=20, reasoning=8, write=0):
    return {'input_tokens': i, 'cached_input_tokens': cached,
            'cache_write_input_tokens': write, 'output_tokens': out,
            'reasoning_output_tokens': reasoning, 'total_tokens': i + out}


def meta(thread='thread-a', **extra):
    return event('session_meta', {'id': thread, 'cwd': '/projects/one', **extra},
                 '2026-10-01T09:00:00Z')


def context(model='gpt-example', cwd='/projects/one'):
    return event('turn_context', {'model': model, 'cwd': cwd, 'turn_id': 'turn-a'})


def response(response_id='response-a', counts=None, thread='thread-a', totals=None):
    counts = counts or usage()
    return event('token_usage_record', {'thread_id': thread, 'response_id': response_id,
                 'usage': counts, 'thread_token_usage': totals or counts})


def counter(counts, last=None, stamp='2026-10-01T10:00:00.001Z'):
    return event('event_msg', {'type': 'token_count', 'info': {
        'total_token_usage': counts, 'last_token_usage': last or counts}}, stamp)


class CodexUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.claude = self.root / 'claude'
        self.codex = self.root / 'codex'

    def write(self, name, rows, codex=True):
        p = (self.codex if codex else self.claude) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return str(p)

    def scan(self, source='all', start=START):
        return ts.scan_records(start, END, str(self.claude),
                               codex_home=str(self.codex), source=source)

    def test_reads_modern_codex_cache_and_reasoning_without_double_counting(self):
        self.write('sessions/a.jsonl', [meta(), context(), response()])
        records = self.scan()
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual((r.input_, r.cache_read, r.output, r.total), (40, 60, 20, 120))
        self.assertEqual((r.source, r.model, r.project, r.reasoning_output),
                         ('codex', 'gpt-example', '/projects/one', 8))

    def test_dedupes_modern_response_copies_and_does_not_add_legacy_mirror(self):
        rows = [meta(), context(), response(), counter(usage())]
        self.write('sessions/a.jsonl', rows)
        self.write('archived_sessions/a-copy.jsonl', rows)
        self.assertEqual(sum(r.total for r in self.scan()), 120)
        self.assertEqual(len(self.scan()), 1)

    def test_partial_cached_input_snapshots_do_not_inflate_request_total(self):
        self.write('sessions/a.jsonl', [meta(), context(),
            response(counts=usage(100, 20, 1)), response(counts=usage(100, 60, 20))])
        r = self.scan()[0]
        self.assertEqual((r.input_, r.cache_read, r.output, r.total), (40, 60, 20, 120))

    def test_first_legacy_counter_does_not_attribute_missing_history_to_today(self):
        self.write('sessions/a.jsonl', [meta(), context(),
            counter(usage(1000, 600, 200), usage(100, 60, 20))])
        self.assertEqual(sum(r.total for r in self.scan()), 120)

    def test_later_reset_can_reuse_a_checkpoint_from_an_older_modern_request(self):
        self.write('sessions/a.jsonl', [meta(), context(), response(), counter(usage()),
            counter(usage(50, 20, 5), stamp='2026-10-01T10:01:00Z'),
            counter(usage(), usage(50, 40, 15), '2026-10-01T10:02:00Z')])
        self.assertEqual(sum(r.total for r in self.scan()), 240)

    def test_legacy_counters_use_deltas_skip_repeated_snapshots_and_survive_reset(self):
        self.write('sessions/a.jsonl', [meta(), context(), counter(usage()),
                   counter(usage(), stamp='2026-10-01T10:01:00Z'),
                   counter(usage(180, 100, 30), usage(80, 40, 10), '2026-10-01T10:02:00Z'),
                   counter(usage(50, 20, 5), stamp='2026-10-01T10:03:00Z')])
        records = self.scan()
        self.assertEqual(sum(r.total for r in records), 265)
        self.assertEqual(len(records), 3)

    def test_reads_pre_window_counter_before_filtering(self):
        self.write('sessions/a.jsonl', [meta(), context(),
            counter(usage(), stamp='2026-09-30T23:59:00Z'),
            counter(usage(180, 100, 30), usage(80, 40, 10))])
        self.assertEqual(sum(r.total for r in self.scan()), 90)

    def test_legacy_fork_inherited_history_is_not_counted_again(self):
        inherited = counter(usage(), stamp='2026-10-01T08:00:00Z')
        self.write('sessions/parent.jsonl', [meta('parent'), context(), inherited])
        self.write('sessions/child.jsonl', [meta('child', forked_from_id='parent'),
            context(), inherited, counter(usage(150, 90, 30), usage(50, 30, 10))])
        self.assertEqual(sum(r.total for r in self.scan()), 180)

    def test_no_cumulative_totals_can_use_last_usage_and_timestamp_dedup(self):
        row = event('event_msg', {'type': 'token_count', 'info': {
            'last_token_usage': usage()}})
        self.write('sessions/a.jsonl', [meta(), context(), row, row])
        self.assertEqual(sum(r.total for r in self.scan()), 120)

    def test_context_model_and_project_change_only_affects_subsequent_usage(self):
        self.write('sessions/a.jsonl', [meta(), context(), response(),
            context('gpt-other', '/projects/two'), response('response-b')])
        self.assertEqual([(r.model, r.project) for r in self.scan()],
                         [('gpt-example', '/projects/one'), ('gpt-other', '/projects/two')])

    def test_claude_dedup_and_cache_write_split_are_preserved(self):
        row = {'timestamp': '2026-10-01T10:00:00Z', 'message': {
            'id': 'message-a', 'model': 'claude-sonnet-4-6', 'usage': {
                'input_tokens': 10, 'output_tokens': 1, 'cache_read_input_tokens': 20,
                'cache_creation_input_tokens': 8,
                'cache_creation': {'ephemeral_1h_input_tokens': 3}}}}
        self.write('one/a.jsonl', [row], codex=False)
        row['message']['usage']['output_tokens'] = 6
        self.write('one/subagents/b.jsonl', [row], codex=False)
        records = self.scan('claude')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].total, 44)
        self.assertEqual(ts.billing_equiv_tokens(records[0]), 54.25)

    def test_source_selection_and_unpriced_codex_are_explicit(self):
        self.write('sessions/a.jsonl', [meta(), context(), response()])
        self.write('one/a.jsonl', [{'timestamp': '2026-10-01T10:00:00Z', 'message': {
            'id': 'response-a', 'model': 'claude-sonnet-4-6',
            'usage': {'input_tokens': 10, 'output_tokens': 2}}}], codex=False)
        self.assertEqual(sum(r.total for r in self.scan('claude')), 12)
        self.assertEqual(sum(r.total for r in self.scan('codex')), 120)
        self.assertEqual(sum(r.total for r in self.scan()), 132)
        self.assertEqual(ts.usd_estimate(self.scan('codex')), 0)
        self.assertEqual(ts.billing_equiv_tokens(self.scan('codex')[0]), 0)
        self.assertEqual(ts.aggregate_totals(self.scan())['unpriced_tokens'], 120)

    def test_cli_uses_codex_home_and_keeps_json_total_shape(self):
        self.write('sessions/a.jsonl', [meta(), context(), response()])
        proc = subprocess.run([sys.executable, str(ROOT / 'scripts/count_tokens.py'),
            '--source', 'codex', '--date', '2026-10-01', '--json'],
            env={**os.environ, 'CODEX_HOME': str(self.codex), 'TZ': 'UTC'},
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out['total']['all'], 120)
        self.assertEqual(out['by_source']['codex']['all'], 120)
        self.assertEqual(out['total']['reasoning_output'], 8)

    def test_malformed_rows_and_partial_last_line_do_not_hide_valid_usage(self):
        p = Path(self.write('sessions/a.jsonl', [[], {'timestamp': 42}, meta(), context(),
            response(counts={'input_tokens': -3}), response()]))
        with p.open('a') as f:
            f.write('{"unfinished":')
        self.assertEqual(sum(r.total for r in self.scan()), 120)

    def test_reads_only_sessions_and_archive_not_unrelated_jsonl(self):
        self.write('sessions/a.jsonl', [meta(), context(), response()])
        self.write('unrelated/a.jsonl', [meta(), context(), response('extra')])
        self.assertEqual(sum(r.total for r in self.scan()), 120)

    def test_transcript_bytes_are_unchanged(self):
        p = Path(self.write('sessions/a.jsonl', [meta(), context(), response()]))
        before = p.read_bytes()
        self.scan()
        self.assertEqual(p.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
