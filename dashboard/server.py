#!/usr/bin/env python3
"""Local web dashboard for Claude Code token usage."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, os.path.dirname(HERE))
import token_stats as ts  # noqa: E402
import work_efficiency as we  # noqa: E402
from analysis.features import time_series_features  # noqa: E402
from analysis.seasonal import hour_of_day, day_of_week  # noqa: E402
from analysis.changepoint import detect as cp_detect  # noqa: E402
from analysis.markov import two_state, three_state  # noqa: E402

STATIC_DIR = os.path.join(HERE, "static")
DEFAULT_PORT = 8787

# Whole-corpus records cache: one scan feeds every endpoint. Invalidated by a
# signature over (path, mtime, size) of all JSONL files — so new activity reloads
# automatically, but switching ranges never rescans disk.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FAR_FUTURE = datetime(9999, 1, 1, tzinfo=timezone.utc)

_records_lock = threading.Lock()
_records_cache: dict = {"signature": None, "records": []}
# Separate lock so concurrent cold-cache callers don't all rescan in parallel
# (thundering herd). With the GIL, parallel scan_records calls serialize anyway
# but waste CPU repeating the same work — the page issues 3 fetches at once on
# load, which without single-flight took ~57s to render vs ~6s warm.
_scan_lock = threading.Lock()

# Per-range result cache — avoids re-aggregating when the user clicks back
# to a range they just viewed. Keyed by (corpus_signature, range_key), so
# it auto-invalidates when new transcript activity lands.
_dashboard_lock = threading.Lock()
_dashboard_cache: dict = {}


def _corpus_signature():
    """Cheap fingerprint of all transcripts — just stat calls, no reads."""
    out = []
    for f in ts.list_transcript_files():
        try:
            st = os.stat(f)
            out.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    out.sort()
    return tuple(out)


def get_all_records():
    """Return the full record list, scanning disk only when files change."""
    sig = _corpus_signature()
    # Fast path: cache hit, no scan needed.
    with _records_lock:
        if _records_cache["signature"] == sig:
            return _records_cache["records"]
    # Cache miss: serialize on _scan_lock so only one thread scans; others
    # wait, then see the populated cache via the inner re-check.
    with _scan_lock:
        with _records_lock:
            if _records_cache["signature"] == sig:
                return _records_cache["records"]
        records = ts.scan_records(_EPOCH, _FAR_FUTURE)
        with _records_lock:
            _records_cache["records"] = records
            _records_cache["signature"] = sig
        return records


def _range_cache_key(q: dict):
    """Stable key for the current query's range.

    Fixed-boundary ranges (today/yesterday/this-week/this-month, or explicit
    from/to) share a single key. Rolling ranges (7d, 12h, ...) bucket to the
    current minute, so repeated clicks within a minute share a cache entry
    but the cache naturally refreshes as time advances.
    """
    ROLLING = False
    if "range" in q:
        name = q["range"][0]
        # "all" ends at now, but records never carry future timestamps, so its
        # result only changes when new activity lands — which already flips the
        # corpus signature. Treat it as fixed.
        fixed = {"today", "yesterday", "this-week", "this-month", "all"}
        if name not in fixed:
            ROLLING = True
        key = ("named", name)
    else:
        key = ("custom", q.get("from", [""])[0], q.get("to", [""])[0])
    minute = int(time.time()) // 60 if ROLLING else None
    return key + (minute,)


def resolve_qs_range(q: dict):
    """Parse from/to/range query params. Returns (start_local, end_local, label)."""
    if "range" in q:
        name = q["range"][0]
        start, end, label = ts.resolve_named_range(name)
        if name == "all":
            # Clamp to the first day with any activity — otherwise by_day /
            # efficiency iterate dense days from 1970 (20k+ empty rows, and
            # active-vs-wall-clock percentages lose all meaning).
            records = get_all_records()
            if records:
                first = min(r.t_utc for r in records).astimezone(ts.local_tz())
                start = first.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, end, label
    fr = q.get("from", [None])[0]
    to = q.get("to", [None])[0]
    if fr or to:
        now = datetime.now(ts.local_tz())
        today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = ts.parse_datetime(fr) if fr else today0
        end = ts.parse_datetime(to, end_of_day=True) if to else now
        return start, end, f"{start.isoformat()} to {end.isoformat()}"
    return ts.resolve_named_range("this-week")


def _filter_by_window(records, start_utc: datetime, end_utc: datetime):
    return [r for r in records if start_utc <= r.t_utc < end_utc]


def records_for(q: dict):
    start, end, label = resolve_qs_range(q)
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    records = _filter_by_window(get_all_records(), start_utc, end_utc)
    return records, {"from": start.isoformat(), "to": end.isoformat(), "label": label}


# --- handlers ---

def h_summary(q):
    records, rng = records_for(q)
    totals = ts.aggregate_totals(records)
    usd = ts.usd_estimate(records)

    now_local = datetime.now(ts.local_tz())
    rt_start_utc = (now_local - timedelta(hours=1)).astimezone(timezone.utc)
    now_utc = now_local.astimezone(timezone.utc)
    rt_records = _filter_by_window(get_all_records(), rt_start_utc, now_utc)
    rt_tokens = sum(r.total for r in rt_records)

    return {
        "range": rng,
        "total": totals["all"],
        "billing_equiv": totals["billing_equiv"],
        "est_usd": round(usd, 2),
        "rate_1h_tokens": rt_tokens,
        "rate_per_min": int(rt_tokens / 60) if rt_tokens else 0,
    }


def h_by_day(q):
    records, _ = records_for(q)
    return {"rows": ts.aggregate_by_day(records)}


def h_by_model(q):
    records, _ = records_for(q)
    return {"rows": ts.aggregate_by_model(records)}


def h_by_project(q):
    records, _ = records_for(q)
    limit = int(q.get("limit", ["10"])[0])
    return {"rows": ts.aggregate_by_project(records, limit=limit)}


def h_realtime(q):
    # Ignore 'from/to'; always "last N ending now".
    now_local = datetime.now(ts.local_tz())
    window = q.get("window", ["1h"])[0]
    bucket_sec = int(q.get("bucket", ["60"])[0])
    delta = ts.parse_duration(window)
    start = now_local - delta
    records = _filter_by_window(
        get_all_records(),
        start.astimezone(timezone.utc),
        now_local.astimezone(timezone.utc),
    )
    sparse = {r["bucket"]: r["tokens"] for r in ts.aggregate_by_bucket(records, bucket_sec)}

    # Dense series: every bucket between floor(start) and floor(now), inclusive,
    # filled with 0 when there's no activity. Gives a continuous line.
    cursor = ts.floor_to_bucket(start, bucket_sec)
    end_floor = ts.floor_to_bucket(now_local, bucket_sec)
    step = timedelta(seconds=bucket_sec)
    dense = []
    while cursor <= end_floor:
        key = cursor.isoformat(timespec="minutes")
        dense.append({"bucket": key, "tokens": sparse.get(key, 0)})
        cursor += step

    return {
        "rows": dense,
        "bucket_sec": bucket_sec,
        "window": window,
        "from": start.isoformat(),
        "to": now_local.isoformat(),
    }


def h_detail(q):
    records, _ = records_for(q)
    return {"rows": ts.aggregate_detail(records)}


def h_efficiency(q):
    """Work-efficiency view: tokens-per-active-minute, not tokens-per-wall-hour.

    Reuses the cached records corpus + the same range resolver as other endpoints
    (range=7d / range=this-week / from=…&to=…).
    """
    start, end, label = resolve_qs_range(q)
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    records = _filter_by_window(get_all_records(), start_utc, end_utc)
    result = we.compute_efficiency(records, start, end)
    result["range"]["label"] = label
    return result


def h_dashboard(q):
    """One-shot endpoint returning every range-scoped panel in a single pass.

    Cached by (corpus_signature, range_key): re-clicking a range served within
    the last minute returns instantly without re-aggregating.
    """
    sig = _corpus_signature()
    rkey = _range_cache_key(q)
    full_key = (sig, rkey)
    with _dashboard_lock:
        hit = _dashboard_cache.get(full_key)
        if hit is not None:
            return hit

    records, rng = records_for(q)
    totals = ts.aggregate_totals(records)
    usd = ts.usd_estimate(records)

    now_local = datetime.now(ts.local_tz())
    rt_start_utc = (now_local - timedelta(hours=1)).astimezone(timezone.utc)
    rt_records = _filter_by_window(
        get_all_records(),
        rt_start_utc,
        now_local.astimezone(timezone.utc),
    )
    rt_tokens = sum(r.total for r in rt_records)

    limit = int(q.get("project_limit", ["10"])[0])

    result = {
        "range": rng,
        "generated_at": now_local.isoformat(timespec="seconds"),
        "summary": {
            "total": totals["all"],
            "billing_equiv": totals["billing_equiv"],
            "est_usd": round(usd, 2),
            "rate_1h_tokens": rt_tokens,
            "rate_per_min": int(rt_tokens / 60) if rt_tokens else 0,
        },
        "by_day":     ts.aggregate_by_day(records),
        "by_model":   ts.aggregate_by_model(records),
        "by_project": ts.aggregate_by_project(records, limit=limit),
        "detail":     ts.aggregate_detail(records),
    }

    with _dashboard_lock:
        # Evict entries from stale corpus signatures and older than 5 minutes.
        cutoff = int(time.time()) // 60 - 5
        for k in list(_dashboard_cache):
            if k[0] != sig:
                _dashboard_cache.pop(k, None); continue
            minute = k[1][-1]
            if minute is not None and minute < cutoff:
                _dashboard_cache.pop(k, None)
        _dashboard_cache[full_key] = result
    return result


def _json_safe(o):
    """Recursively replace non-finite floats (inf/-inf/nan) with the string 'inf'/'-inf'/null.

    Standard json.dumps emits 'Infinity' / 'NaN' which are invalid JSON for browsers.
    The changepoint module can produce inf in pct_change when the before-segment mean is 0.
    """
    if isinstance(o, float):
        if math.isnan(o):
            return None
        if math.isinf(o):
            return "inf" if o > 0 else "-inf"
        return o
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


class HTTPError(Exception):
    """Raised by handlers to return non-200 JSON responses."""
    def __init__(self, code: int, payload: dict):
        super().__init__(payload.get("error", "http error"))
        self.code = code
        self.payload = payload


def _compute_patterns(q):
    """Resolve params + assemble the patterns dict. Shared by /api/patterns and /api/interpret."""
    # --- bucket size (always present) ---
    try:
        bucket_sec = int(q.get("bucket", ["1800"])[0])
    except (TypeError, ValueError):
        bucket_sec = 1800
    if bucket_sec <= 0:
        bucket_sec = 1800
    elif bucket_sec < 3600 and 3600 % bucket_sec != 0:
        bucket_sec = 1800
    elif bucket_sec >= 3600 and bucket_sec % 3600 != 0:
        bucket_sec = 3600

    now_local = datetime.now(ts.local_tz())

    # --- resolve time window ---
    # from/to override days. Both are local-tz YYYY-MM-DD.
    from_str = (q.get("from", [""])[0] or "").strip()
    to_str = (q.get("to", [""])[0] or "").strip()

    custom_range = bool(from_str)
    if custom_range:
        try:
            start_local = ts.parse_datetime(from_str)
        except ValueError as e:
            raise HTTPError(400, {"error": f"bad 'from' date: {e}"})
        if to_str:
            try:
                # End of day so the chosen 'to' day is inclusive
                end_local = ts.parse_datetime(to_str, end_of_day=True)
            except ValueError as e:
                raise HTTPError(400, {"error": f"bad 'to' date: {e}"})
        else:
            end_local = now_local
        if end_local < start_local:
            raise HTTPError(400, {"error": "'to' must be >= 'from'"})
        if (end_local - start_local).total_seconds() < 3600:
            raise HTTPError(400, {"error": "window must span at least 1 hour"})
        # Derived day count (rounded up). Used to label the result; also drives changepoint.
        days = max(1, math.ceil((end_local - start_local).total_seconds() / 86400))
        cp_days_eff = days
    else:
        try:
            days = int(q.get("days", ["7"])[0])
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(90, days))
        try:
            cp_days_eff = int(q.get("cp_days", ["30"])[0])
        except (TypeError, ValueError):
            cp_days_eff = 30
        cp_days_eff = max(3, min(365, cp_days_eff))
        end_local = now_local
        start_local = now_local - timedelta(days=days)

    # --- features / markov window (uses user-chosen bucket) ---
    recs = _filter_by_window(
        get_all_records(),
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )
    buckets = ts.aggregate_by_bucket(recs, bucket_sec)
    sparse = {b["bucket"]: b["tokens"] for b in buckets}
    cur = ts.floor_to_bucket(start_local, bucket_sec)
    end = ts.floor_to_bucket(end_local, bucket_sec)
    step = timedelta(seconds=bucket_sec)
    ts_values = []
    while cur <= end:
        key = cur.isoformat(timespec="minutes")
        ts_values.append((cur, sparse.get(key, 0)))
        cur += step
    values = [v for _, v in ts_values]

    # --- seasonal (hour-of-day / day-of-week): independent of user bucket choice ---
    # When bucket >= 1h, all tokens collapse onto the bucket's start hour, leaving
    # other hours falsely empty. Always re-bucket at <=1h granularity for hour-of-day
    # so the 24-hour bars reflect true time-of-day distribution.
    seasonal_bucket_sec = min(bucket_sec, 3600)
    if seasonal_bucket_sec == bucket_sec:
        seasonal_ts_values = ts_values
    else:
        sb = ts.aggregate_by_bucket(recs, seasonal_bucket_sec)
        sb_sparse = {b["bucket"]: b["tokens"] for b in sb}
        s_cur = ts.floor_to_bucket(start_local, seasonal_bucket_sec)
        s_end = ts.floor_to_bucket(end_local, seasonal_bucket_sec)
        s_step = timedelta(seconds=seasonal_bucket_sec)
        seasonal_ts_values = []
        while s_cur <= s_end:
            key = s_cur.isoformat(timespec="minutes")
            seasonal_ts_values.append((s_cur, sb_sparse.get(key, 0)))
            s_cur += s_step

    # --- changepoint: daily totals, dense, over cp_days_eff ending at end_local ---
    cp_start = end_local - timedelta(days=cp_days_eff)
    recs_cp = _filter_by_window(
        get_all_records(),
        cp_start.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )
    day_buckets = ts.aggregate_by_day(recs_cp)
    day_map = {d["date"]: d["total"] for d in day_buckets}
    cur_d = (end_local - timedelta(days=cp_days_eff - 1)).date()
    end_d = end_local.date()
    daily = []
    while cur_d <= end_d:
        daily.append((cur_d.isoformat(), day_map.get(cur_d.isoformat(), 0)))
        cur_d += timedelta(days=1)

    params = {
        "days": days,
        "bucket_sec": bucket_sec,
        "cp_days": cp_days_eff,
        "tz": now_local.strftime("%z"),
    }
    if custom_range:
        params["from"] = start_local.date().isoformat()
        params["to"] = end_local.date().isoformat()

    return {
        "computed_at": now_local.isoformat(),
        "params": params,
        "features": time_series_features(values, bucket_sec),
        "seasonal": {
            "hour": hour_of_day(seasonal_ts_values),
            "dow": day_of_week(seasonal_ts_values),
        },
        "markov_2": two_state(values, bucket_sec),
        "markov_3": three_state(values, bucket_sec),
        "changepoint": cp_detect(daily),
    }


def h_patterns(q):
    """Pattern analysis: features, seasonal cycles, Markov chains, change points."""
    return _json_safe(_compute_patterns(q))


# Note: /api/interpret blocks the single (or limited-thread) HTTP server for 5-30s
# while the local `claude` CLI runs. This is acceptable for a single-user dashboard.
def h_interpret(q):
    """Compute patterns then ask the local `claude` CLI to write a Chinese interpretation."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise HTTPError(503, {
            "error": "claude CLI not found in PATH",
            "hint": "Install Claude Code (https://claude.com/claude-code) so the dashboard can call it.",
        })

    patterns = _compute_patterns(q)  # may raise HTTPError(400, ...)
    safe = _json_safe(patterns)
    patterns_json = json.dumps(safe, ensure_ascii=False, indent=2)

    model = os.environ.get("TOKEN_USAGE_LLM_MODEL", "claude-sonnet-4-6")
    prompt = (
        "你是一个数据分析师。下面是某 Claude Code 用户最近一段时间的 token 用量时序分析结果"
        "（包含描述统计、突发性、自相关、马尔可夫状态转移、变化点检测等指标）。\n\n"
        "请用中文写一份简洁的解读报告（500-800 字），结构如下：\n\n"
        "## TL;DR\n一句话总结用户画像（如「晚高峰个人项目型 / 高黏性 / 强 24h 周期」等）。\n\n"
        "## 关键发现\n3-5 个最值得关注的具体发现（要带数字）。\n\n"
        "## 优化建议\n2-3 条可执行的成本/效率建议（基于数据，不要泛泛而谈）。\n\n"
        "数据如下（JSON）：\n```json\n" + patterns_json + "\n```\n\n"
        "只输出 markdown 报告本身，不要前置说明，不要包裹在 ```markdown 块里。"
    )

    started = time.time()
    try:
        result = subprocess.run(
            [claude_bin, "--model", model, "--print", prompt],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise HTTPError(504, {"error": "claude CLI timed out (>120s)"})
    elapsed_ms = int((time.time() - started) * 1000)

    if result.returncode != 0:
        raise HTTPError(500, {
            "error": f"claude CLI failed (exit {result.returncode})",
            "stderr": (result.stderr or "").strip()[:500],
        })

    interpretation = (result.stdout or "").strip()
    if not interpretation:
        raise HTTPError(500, {"error": "claude CLI returned empty output"})

    return _json_safe({
        "interpretation": interpretation,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "computed_at": patterns["computed_at"],
        "params": patterns["params"],
    })


ROUTES = {
    "/api/summary":    h_summary,
    "/api/by-day":     h_by_day,
    "/api/by-model":   h_by_model,
    "/api/by-project": h_by_project,
    "/api/realtime":   h_realtime,
    "/api/detail":     h_detail,
    "/api/efficiency": h_efficiency,
    "/api/dashboard":  h_dashboard,
    "/api/patterns":   h_patterns,
    "/api/interpret":  h_interpret,
}


CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except CLIENT_GONE:
            # Browser cancelled the request (common with rapid UI clicks / auto-refresh).
            self.close_connection = True

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except CLIENT_GONE:
            self.close_connection = True

    def _send_static(self, rel_path: str):
        if rel_path in ("", "/"):
            rel_path = "/index.html"
        safe = os.path.normpath(rel_path.lstrip("/"))
        if safe.startswith(".."):
            self.send_error(404); return
        full = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(full):
            self.send_error(404); return
        ext = os.path.splitext(full)[1].lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
        }.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except CLIENT_GONE:
            self.close_connection = True

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ROUTES:
            try:
                result = ROUTES[url.path](parse_qs(url.query))
            except CLIENT_GONE:
                self.close_connection = True
                return
            except HTTPError as e:
                try:
                    self._send_json(e.payload, code=e.code)
                except CLIENT_GONE:
                    self.close_connection = True
                return
            except Exception as e:
                try:
                    self._send_json({"error": str(e)}, code=500)
                except CLIENT_GONE:
                    self.close_connection = True
                return
            self._send_json(result)
            return
        self._send_static(url.path)


RELOAD_WATCH_FILES = [
    os.path.abspath(__file__),
    os.path.join(os.path.dirname(HERE), "scripts", "token_stats.py"),
    os.path.join(os.path.dirname(HERE), "scripts", "work_efficiency.py"),
]


def _reload_watcher(poll_sec: float = 1.0):
    """Watch source files; re-exec the server when any of them change.

    Uses os.execv to replace the running process in-place — the PID stays the
    same, so any parent (e.g. the menubar app's subprocess.Popen) keeps a valid
    handle. The listening socket is closed on exec; allow_reuse_address (set by
    HTTPServer) lets the new image rebind the port immediately.
    """
    snapshot = {}
    for f in RELOAD_WATCH_FILES:
        try:
            snapshot[f] = os.stat(f).st_mtime_ns
        except OSError:
            continue
    while True:
        time.sleep(poll_sec)
        for f, prev in snapshot.items():
            try:
                cur = os.stat(f).st_mtime_ns
            except OSError:
                continue
            if cur != prev:
                sys.stderr.write(f"[reload] {os.path.basename(f)} changed → restarting\n")
                sys.stderr.flush()
                # Brief wait so an editor mid-save doesn't trigger a half-written reload.
                time.sleep(0.15)
                os.execv(sys.executable, [sys.executable] + sys.argv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-reload", action="store_true",
                    help="disable auto-reload on Python source changes")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Token Usage Dashboard → {url}")
    if not args.no_reload:
        threading.Thread(target=_reload_watcher, daemon=True).start()
        print("Auto-reload: ON  (--no-reload to disable)")
    print("Ctrl-C to stop.")
    if not args.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")


if __name__ == "__main__":
    main()
