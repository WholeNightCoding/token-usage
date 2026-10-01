#!/usr/bin/env python3
"""Shared parsing and aggregation for Claude Code and Codex token usage."""
from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Tuple, Union
from codex_records import scan_events

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
CODEX_HOME = os.path.expanduser(os.environ.get('CODEX_HOME') or '~/.codex')
SOURCES = ('all', 'claude', 'codex')


def local_tz():
    return datetime.now(timezone.utc).astimezone().tzinfo


def parse_datetime(s: str, end_of_day: bool = False) -> datetime:
    """Accepts YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS], interpreted in local tz."""
    s = s.strip()
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M", "%Y-%m-%d"]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d" and end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            return dt.replace(tzinfo=local_tz())
        except ValueError:
            continue
    raise ValueError(f"Unrecognized datetime: {s!r}")


def parse_duration(s: str) -> timedelta:
    m = re.fullmatch(r"(\d+)\s*([dhm])", s.strip())
    if not m:
        raise ValueError(f"Unrecognized duration: {s!r}. Use e.g. 7d, 12h, 30m")
    n, unit = int(m.group(1)), m.group(2)
    return {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]


def resolve_named_range(name: str, now: Optional[datetime] = None) -> Tuple[datetime, datetime, str]:
    """name in: today, yesterday, this-week, this-month, 7d, 30d, 1h, etc.
    Returns (start_local, end_local, label). Durations use 'last N'."""
    now = now or datetime.now(local_tz())
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if name == "today":
        return today0, today0 + timedelta(days=1), "today"
    if name == "yesterday":
        return today0 - timedelta(days=1), today0, "yesterday"
    if name == "this-week":
        start = today0 - timedelta(days=today0.weekday())
        return start, start + timedelta(days=7), "this-week"
    if name == "this-month":
        start = today0.replace(day=1)
        end = start.replace(year=start.year + 1, month=1) if start.month == 12 \
            else start.replace(month=start.month + 1)
        return start, end, "this-month"
    if name == "all":
        return datetime(1970, 1, 1, tzinfo=local_tz()), now, "all-time"
    # "7d" / "30d" / "1h" style -> rolling window ending now
    return now - parse_duration(name), now, f"last {name}"


@dataclass
class Record:
    t_utc: datetime       # event timestamp
    model: str
    project: str          # slug from path under PROJECTS_DIR
    input_: int
    output: int
    cache_read: int
    cache_create: int             # total cache_creation (5m + 1h)
    cache_create_1h: int = 0      # subset of cache_create that is 1h ephemeral
    source: str = 'claude'
    reasoning_output: int = 0   # included in output, never add to total

    @property
    def total(self) -> int:
        return self.input_ + self.output + self.cache_read + self.cache_create


def _project_slug(path: str, projects_dir: str) -> str:
    rel = os.path.relpath(path, projects_dir)
    parts = rel.split(os.sep)
    return parts[0] if parts and parts[0] != "." else "(root)"


def read_claude_file(path: str, projects_dir: str,
                     start_utc: Optional[datetime] = None,
                     end_utc: Optional[datetime] = None) -> List[Tuple[tuple | None, Record]]:
    """Parse one file; keep IDs for cross-file dedup in scan_records."""
    rows = []
    slug = _project_slug(path, projects_dir)
    try:
        fp = open(path, "r", encoding="utf-8", errors="ignore")
    except OSError:
        return []
    with fp:
        for line in fp:
            if '"usage"' not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            ts = obj.get("timestamp", "")
            if not isinstance(ts, str) or not ts:
                continue
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if start_utc is not None and not (start_utc <= t < end_utc):
                continue
            msg = obj.get("message") or {}
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage") or obj.get("usage")
            if not isinstance(usage, dict) or not usage:
                continue
            model = msg.get("model") or obj.get("model") or "unknown"
            cc_breakdown = usage.get("cache_creation") or {}
            rec = Record(
                t_utc=t, model=model, project=slug,
                input_=usage.get("input_tokens", 0) or 0,
                output=usage.get("output_tokens", 0) or 0,
                cache_read=usage.get("cache_read_input_tokens", 0) or 0,
                cache_create=usage.get("cache_creation_input_tokens", 0) or 0,
                cache_create_1h=cc_breakdown.get("ephemeral_1h_input_tokens", 0) or 0,
            )
            mid = msg.get("id") or obj.get("uuid")
            mid = ('claude', mid) if mid else None
            rows.append((mid, rec))
    return rows


def scan_records(
    start_utc: datetime,
    end_utc: datetime,
    projects_dir: str = PROJECTS_DIR,
    files: Optional[List[str]] = None,
    *,
    source: str = 'all',
    codex_home: str = CODEX_HOME,
    file_cache=None,
) -> List[Record]:
    """Scan all JSONL transcripts, dedupe by message id, filter by time window.

    Dedup strategy: when the same message.id appears in multiple transcripts
    (e.g. parent + subagent files, or streaming snapshots), merge by taking the
    field-wise max. The Anthropic API writes streaming partials with
    output_tokens=1 to disk before the final value lands — first-seen-wins
    would discard the real count.
    """
    if source not in SOURCES:
        raise ValueError(f'Unknown source: {source}')
    if files is None:
        files = list_transcript_files(projects_dir, source=source, codex_home=codex_home)
    codex_root = os.path.realpath(codex_home) + os.sep
    codex_files = [f for f in files if os.path.realpath(f).startswith(codex_root)
                   or os.path.basename(f).startswith('rollout-')]
    by_id: dict[str, Record] = {}
    no_id: List[Record] = []
    for f in files:
        if source == 'codex' or f in codex_files:
            continue
        entries = (file_cache.read_claude(f, projects_dir) if file_cache is not None
                   else read_claude_file(f, projects_dir, start_utc, end_utc))
        for mid, rec in entries:
            if not (start_utc <= rec.t_utc < end_utc):
                continue
            if not mid:
                no_id.append(rec)
                continue
            prior = by_id.get(mid)
            if prior is None:
                by_id[mid] = rec
            else:
                by_id[mid] = Record(
                    t_utc=prior.t_utc, model=prior.model, project=prior.project,
                    input_=max(prior.input_, rec.input_),
                    output=max(prior.output, rec.output),
                    cache_read=max(prior.cache_read, rec.cache_read),
                    cache_create=max(prior.cache_create, rec.cache_create),
                    cache_create_1h=max(prior.cache_create_1h, rec.cache_create_1h),
                )
    if source != 'claude':
        reader = file_cache.read_codex if file_cache is not None else None
        for key, values in scan_events(codex_files, read_file=reader):
            rec = Record(**values)
            if not (start_utc <= rec.t_utc < end_utc):
                continue
            key = ('codex', key)
            prior = by_id.get(key)
            if prior is None:
                by_id[key] = rec
            else:
                by_id[key] = Record(
                    t_utc=prior.t_utc, model=prior.model, project=prior.project,
                    source='codex', input_=max(prior.input_, rec.input_),
                    output=max(prior.output, rec.output), cache_read=max(prior.cache_read, rec.cache_read),
                    cache_create=max(prior.cache_create, rec.cache_create),
                    reasoning_output=max(prior.reasoning_output, rec.reasoning_output))
    return list(by_id.values()) + no_id


def list_transcript_files(projects_dir: str = PROJECTS_DIR, *, source: str = 'all',
                          codex_home: str = CODEX_HOME) -> List[str]:
    if source not in SOURCES:
        raise ValueError(f'Unknown source: {source}')
    files = []
    if source != 'codex':
        files.extend(glob.glob(os.path.join(projects_dir, '**', '*.jsonl'), recursive=True))
    if source != 'claude':
        for name in ('sessions', 'archived_sessions'):
            files.extend(glob.glob(os.path.join(codex_home, name, '**', '*.jsonl'), recursive=True))
    return sorted(set(files))


# USD per 1M tokens — editable
PRICING_USD_PER_MTOK = {
    "claude-opus-4-7":   {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in":  3.0, "out": 15.0},
    "claude-haiku-4-5":  {"in":  1.0, "out":  5.0},
}
CACHE_READ_MULT = 0.1
CACHE_WRITE_MULT = 1.25       # 5-minute ephemeral cache write
CACHE_WRITE_1H_MULT = 2.0     # 1-hour ephemeral cache write
OUTPUT_BILLING_MULT = 5  # output tokens are ~5x input in billing weight
UNKNOWN_MODEL_FALLBACK = "claude-opus-4-7"  # pessimistic


def _price_for(model: str) -> dict:
    """Match by prefix so 'claude-opus-4-7-max' and dated variants resolve."""
    for key, price in PRICING_USD_PER_MTOK.items():
        if model.startswith(key):
            return price
    return PRICING_USD_PER_MTOK[UNKNOWN_MODEL_FALLBACK]


def billing_equiv_tokens(u: Union["Record", dict]) -> float:
    """Rough billing-equivalent input tokens. Accepts Record or totals dict.

    cache_create is split into 5m (1.25x) and 1h (2.0x) ephemeral writes.
    Falls back to all-5m when the 1h breakdown is unavailable (older records).
    """
    if isinstance(u, Record):
        if u.source != 'claude':
            return 0.0
        i, o, cr, cc, cc1h = u.input_, u.output, u.cache_read, u.cache_create, u.cache_create_1h
    else:
        i, o, cr, cc = u["in"], u["out"], u["cr"], u["cc"]
        cc1h = u.get("cc1h", 0)
    cc5m = max(0, cc - cc1h)
    return (i + cr * CACHE_READ_MULT
            + cc5m * CACHE_WRITE_MULT + cc1h * CACHE_WRITE_1H_MULT
            + o * OUTPUT_BILLING_MULT)


def usd_estimate(records: Iterable[Record]) -> float:
    """Sum USD across records using per-model pricing.

    cache_create_1h is priced at 2.0x input rate; cache_create_5m at 1.25x.
    """
    total = 0.0
    for r in records:
        if r.source != 'claude':
            continue
        p = _price_for(r.model)
        cc5m = max(0, r.cache_create - r.cache_create_1h)
        in_mtok = (r.input_
                   + r.cache_read * CACHE_READ_MULT
                   + cc5m * CACHE_WRITE_MULT
                   + r.cache_create_1h * CACHE_WRITE_1H_MULT) / 1_000_000
        out_mtok = r.output / 1_000_000
        total += in_mtok * p["in"] + out_mtok * p["out"]
    return total


def _empty_totals() -> dict:
    return {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc1h": 0, "count": 0,
            "reasoning_output": 0}


def _add(acc: dict, r: Record) -> None:
    acc["in"] += r.input_
    acc["out"] += r.output
    acc["cr"] += r.cache_read
    acc["cc"] += r.cache_create
    acc["cc1h"] += r.cache_create_1h
    acc["count"] += 1
    acc['reasoning_output'] += r.reasoning_output


def aggregate_totals(records: List[Record]) -> dict:
    acc = _empty_totals()
    for r in records:
        _add(acc, r)
    acc["all"] = acc["in"] + acc["out"] + acc["cr"] + acc["cc"]
    acc["billing_equiv"] = int(sum(billing_equiv_tokens(r) for r in records))
    acc['unpriced_tokens'] = sum(r.total for r in records if r.source != 'claude')
    acc['billing_supported'] = all(r.source == 'claude' for r in records)
    return acc


def aggregate_by_source(records: List[Record]) -> dict:
    return {s: aggregate_totals([r for r in records if r.source == s])
            for s in ('claude', 'codex')}


def aggregate_by_model(records: List[Record]) -> List[dict]:
    buckets: dict = defaultdict(_empty_totals)
    for r in records:
        _add(buckets[r.model], r)
    out = []
    for m, v in buckets.items():
        v["model"] = m
        v["total"] = v["in"] + v["out"] + v["cr"] + v["cc"]
        out.append(dict(v))
    out.sort(key=lambda x: -x["total"])
    return out


def aggregate_by_project(records: List[Record], limit: Optional[int] = None) -> List[dict]:
    buckets: dict = defaultdict(_empty_totals)
    for r in records:
        _add(buckets[r.project], r)
    out = []
    for p, v in buckets.items():
        v["project"] = p
        v["total"] = v["in"] + v["out"] + v["cr"] + v["cc"]
        out.append(dict(v))
    out.sort(key=lambda x: -x["total"])
    return out[:limit] if limit else out


def aggregate_by_day(records: List[Record]) -> List[dict]:
    """Returns rows: {date, per_model: {model: total, ...}, total}."""
    buckets: dict = defaultdict(lambda: defaultdict(int))
    tz = local_tz()
    for r in records:
        day = r.t_utc.astimezone(tz).date().isoformat()
        buckets[day][r.model] += r.total
    out = []
    for day in sorted(buckets.keys()):
        per_model = dict(buckets[day])
        out.append({
            "date": day,
            "per_model": per_model,
            "total": sum(per_model.values()),
        })
    return out


def aggregate_by_minute(records: List[Record]) -> List[dict]:
    """Returns rows: {minute: 'YYYY-MM-DDTHH:MM', tokens}. Local tz. Used for realtime."""
    buckets: dict = defaultdict(int)
    tz = local_tz()
    for r in records:
        local = r.t_utc.astimezone(tz).replace(second=0, microsecond=0)
        buckets[local.isoformat(timespec="minutes")] += r.total
    return [{"minute": k, "tokens": v} for k, v in sorted(buckets.items())]


def floor_to_bucket(dt: datetime, bucket_sec: int) -> datetime:
    """Floor a datetime to the start of its bucket, in its current tz.
    bucket_sec must divide 3600 (60, 300, 600, 1800) or be a whole-hour multiple."""
    if bucket_sec >= 3600:
        step = bucket_sec // 3600
        return dt.replace(hour=(dt.hour // step) * step,
                          minute=0, second=0, microsecond=0)
    step_min = bucket_sec // 60
    return dt.replace(minute=(dt.minute // step_min) * step_min,
                      second=0, microsecond=0)


def aggregate_by_bucket(records: List[Record], bucket_sec: int) -> List[dict]:
    """Bucket records by fixed-size time buckets in local tz.
    Returns rows: {bucket: 'YYYY-MM-DDTHH:MM', tokens}.
    bucket_sec must divide 3600 (1/5/10/15/30/60 minutes, or 1/2/3/4/6/12 hours)."""
    buckets: dict = defaultdict(int)
    tz = local_tz()
    for r in records:
        local = r.t_utc.astimezone(tz)
        floored = floor_to_bucket(local, bucket_sec)
        buckets[floored.isoformat(timespec="minutes")] += r.total
    return [{"bucket": k, "tokens": v} for k, v in sorted(buckets.items())]


def aggregate_detail(records: List[Record]) -> List[dict]:
    """Per (model, project) row for the detail table."""
    buckets: dict = defaultdict(_empty_totals)
    for r in records:
        _add(buckets[(r.model, r.project)], r)
    out = []
    for (m, p), v in buckets.items():
        v["model"] = m
        v["project"] = p
        v["total"] = v["in"] + v["out"] + v["cr"] + v["cc"]
        out.append(dict(v))
    out.sort(key=lambda x: -x["total"])
    return out
