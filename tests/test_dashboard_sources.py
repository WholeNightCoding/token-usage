import importlib.util
import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler
from http.server import ThreadingHTTPServer
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_codex_usage as fixture
from test_codex_usage import context, counter, meta, response, usage

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usage_dashboard', ROOT / 'dashboard/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class DashboardSourceTests(unittest.TestCase):
    write = fixture.CodexUsageTests.write

    def setUp(self):
        fixture.CodexUsageTests.setUp(self)
        self.write('sessions/a.jsonl', [meta(), context(), response(), counter(usage())])
        self.write('one/a.jsonl', [{'timestamp': '2026-10-01T10:00:00Z', 'message': {
            'id': 'a', 'model': 'claude-sonnet-4-6',
            'usage': {'input_tokens': 10, 'output_tokens': 2}}}], codex=False)
        server.PROJECTS_ROOT = str(self.claude)
        server.CODEX_ROOT = str(self.codex)
        server._records_cache.update(signature=None, records=[])
        server._dashboard_cache.clear()
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()

    def get(self, route, source):
        url = f'http://127.0.0.1:{self.srv.server_port}/api/{route}?from=2026-10-01&to=2026-10-01&source={source}'
        with build_opener(ProxyHandler({})).open(url) as res:
            return json.load(res)

    def test_real_http_dashboard_switches_sources_without_leaking_cached_results(self):
        self.assertEqual(self.get('dashboard', 'codex')['summary']['total'], 120)
        self.assertEqual(self.get('dashboard', 'claude')['summary']['total'], 12)
        self.assertEqual(self.get('dashboard', 'all')['summary']['total'], 132)
        self.assertEqual(self.get('dashboard', 'codex')['summary']['total'], 120)

    def test_every_analysis_endpoint_obeys_the_selected_source(self):
        self.assertEqual(self.get('by-model', 'codex')['rows'][0]['model'], 'gpt-example')
        self.assertEqual(self.get('by-project', 'codex')['rows'][0]['total'], 120)
        self.assertEqual(self.get('detail', 'claude')['rows'][0]['total'], 12)
        self.assertEqual(self.get('by-day', 'codex')['rows'][0]['total'], 120)
        self.assertEqual(self.get('efficiency', 'codex')['summary']['total_tokens'], 120)
        self.assertEqual(sum(r['total'] for r in self.get('patterns', 'codex')
                             ['changepoint']['daily_totals']), 120)

    def test_billing_metadata_does_not_quote_claude_prices_for_codex(self):
        data = self.get('dashboard', 'codex')
        self.assertEqual(data['summary']['unpriced_tokens'], 120)
        self.assertEqual(data['by_source']['claude']['all'], 0)
        self.assertEqual(data['by_source']['codex']['all'], 120)
        self.assertFalse(self.get('efficiency', 'codex')['billing_supported'])

    def test_invalid_source_returns_bad_request(self):
        with self.assertRaises(HTTPError) as caught:
            self.get('dashboard', 'other')
        self.addCleanup(caught.exception.close)
        self.assertEqual(caught.exception.code, 400)

    def test_realtime_uses_source_even_though_it_has_a_separate_time_window(self):
        stamp = datetime.now(timezone.utc).isoformat()
        live = response('live-codex')
        live['timestamp'] = stamp
        self.write('sessions/live.jsonl', [meta(), context(), live])
        self.write('one/live.jsonl', [{'timestamp': stamp, 'message': {
            'id': 'live-claude', 'model': 'claude-sonnet-4-6',
            'usage': {'input_tokens': 10, 'output_tokens': 2}}}], codex=False)
        self.assertEqual(sum(r['tokens'] for r in self.get('realtime', 'codex')['rows']), 120)
        self.assertEqual(sum(r['tokens'] for r in self.get('realtime', 'claude')['rows']), 12)

    def test_dashboard_total_and_hour_rate_share_one_snapshot_while_logs_append(self):
        stamp = datetime.now(timezone.utc).isoformat()
        live = response('live-codex')
        live['timestamp'] = stamp
        self.write('sessions/live.jsonl', [meta(), context(), live])
        read = server.source_records
        def read_then_append(query):
            result = read(query)
            extra = response('arrives-after-snapshot', usage(50, 20, 5))
            extra['timestamp'] = stamp
            self.write('sessions/extra.jsonl', [meta(), context(), extra])
            return result
        # Only timing is controlled: all parsing, files and HTTP remain real.
        with patch.object(server, 'source_records', side_effect=read_then_append):
            data = self.get('dashboard', 'codex')
        self.assertEqual(data['summary']['total'], 240)
        self.assertEqual(data['summary']['rate_1h_tokens'], 120)

    def test_concurrent_requests_share_a_scan_when_logs_change_while_waiting(self):
        entered = threading.Event()
        waiter_ready = threading.Event()
        release = threading.Event()
        scan = server.ts.scan_records
        signature = server._corpus_signature
        results, errors = [], []

        def blocked_scan(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('scan was not released')
            return scan(*args, **kwargs)

        def observed_signature():
            result = signature()
            if threading.current_thread().name == 'waiting-reader':
                waiter_ready.set()
            return result

        def read():
            try:
                results.append(server.get_all_records())
            except Exception as error:
                errors.append(error)

        with patch.object(server.ts, 'scan_records', side_effect=blocked_scan) as scans, \
                patch.object(server, '_corpus_signature', side_effect=observed_signature):
            first = threading.Thread(target=read, name='first-reader')
            second = threading.Thread(target=read, name='waiting-reader')
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                self.write('sessions/extra.jsonl', [meta(), context(), response('extra')])
                second.start()
                self.assertTrue(waiter_ready.wait(5))
            finally:
                release.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(scans.call_count, 1)
            self.assertIs(results[0], results[1])
            self.assertEqual(sum(r.total for r in results[0]), 252)

        # A later request must still discover new activity, not freeze the cache.
        self.write('sessions/later.jsonl', [meta(), context(), response('later')])
        self.assertEqual(sum(r.total for r in server.get_all_records()), 372)


if __name__ == '__main__':
    unittest.main()
