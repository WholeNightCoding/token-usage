#!/usr/bin/env python3
"""Analyze token-usage transcripts and render terminal/markdown/HTML/JSON reports."""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# Ensure both `token_stats` (sibling) and `analysis.*` (parent package) import.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, ".."))

import token_stats as ts  # noqa: E402

from analysis import report  # noqa: E402


def parse_bucket(s: str) -> int:
    """Accept '30m', '60s', '1h', or bare seconds like '1800'."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    m = re.fullmatch(r"(\d+)\s*([smh])", s)
    if not m:
        raise ValueError(f"Unrecognized bucket size: {s!r}. Use e.g. 30m, 60s, 1h, or bare seconds")
    n, unit = int(m.group(1)), m.group(2)
    return {"s": n, "m": n * 60, "h": n * 3600}[unit]


def densify_buckets(
    bucketed: list[dict],
    start_local: datetime,
    end_local: datetime,
    bucket_sec: int,
) -> tuple[list[int], list[tuple[datetime, int]]]:
    """Expand sparse aggregate_by_bucket output into dense (values, ts_values).

    Returns:
        values: list[int] one per bucket starting at floor(start_local)
        ts_values: list[(local_dt, int)] same length
    """
    tz = ts.local_tz()
    floor_start = ts.floor_to_bucket(start_local.astimezone(tz), bucket_sec)
    floor_end = ts.floor_to_bucket(end_local.astimezone(tz), bucket_sec)

    # Build a dict of bucket-ISO -> tokens
    by_iso: dict[str, int] = {}
    for row in bucketed:
        by_iso[row["bucket"]] = int(row.get("tokens", 0))

    values: list[int] = []
    ts_values: list[tuple[datetime, int]] = []
    cur = floor_start
    step = timedelta(seconds=bucket_sec)
    # Inclusive of floor_end bucket
    while cur <= floor_end:
        iso = cur.isoformat(timespec="minutes")
        v = by_iso.get(iso, 0)
        values.append(v)
        ts_values.append((cur, v))
        cur = cur + step
    return values, ts_values


def densify_daily(
    by_day: list[dict],
    start_local: datetime,
    end_local: datetime,
) -> list[tuple[str, int]]:
    """Expand sparse aggregate_by_day rows into dense [(date_str, total)]."""
    tz = ts.local_tz()
    by_date: dict[str, int] = {row["date"]: int(row.get("total", 0)) for row in by_day}
    out: list[tuple[str, int]] = []
    d = start_local.astimezone(tz).date()
    end_d = end_local.astimezone(tz).date()
    one = timedelta(days=1)
    while d <= end_d:
        iso = d.isoformat()
        out.append((iso, by_date.get(iso, 0)))
        d = d + one
    return out


def main():
    p = argparse.ArgumentParser(
        description="Analyze Claude Code token usage with time-series patterns."
    )
    p.add_argument("--days", type=int, default=7,
                   help="Window size (days) for features/seasonal/markov (default 7)")
    p.add_argument("--bucket", default="30m",
                   help="Bucket size: 30m, 60s, 1h, or bare seconds (default 30m)")
    p.add_argument("--cp-days", dest="cp_days", type=int, default=30,
                   help="Window size (days) for change-point detection (default 30)")
    p.add_argument("--projects-dir", dest="projects_dir", default=ts.PROJECTS_DIR,
                   help="Override transcripts directory")
    p.add_argument("--html", help="Write HTML report to this path")
    p.add_argument("--markdown", help="Write Markdown report to this path")
    p.add_argument("--json", dest="json_path", help="Write JSON to this path")
    args = p.parse_args()

    bucket_sec = parse_bucket(args.bucket)

    tz = ts.local_tz()
    now_local = datetime.now(tz)
    start_local = now_local - timedelta(days=args.days)
    cp_start_local = now_local - timedelta(days=args.cp_days)

    files = ts.list_transcript_files(args.projects_dir)
    if not files:
        print(f"No transcript files found under {args.projects_dir}", file=sys.stderr)
        sys.exit(1)

    # Scan once for the larger of the two windows; reuse for the smaller window.
    cp_records = ts.scan_records(
        cp_start_local.astimezone(timezone.utc),
        now_local.astimezone(timezone.utc),
        projects_dir=args.projects_dir,
        files=files,
    )
    if args.days <= args.cp_days:
        start_utc = start_local.astimezone(timezone.utc)
        records = [r for r in cp_records if r.t_utc >= start_utc]
    else:
        records = ts.scan_records(
            start_local.astimezone(timezone.utc),
            now_local.astimezone(timezone.utc),
            projects_dir=args.projects_dir,
            files=files,
        )

    bucketed = ts.aggregate_by_bucket(records, bucket_sec)
    values, ts_values = densify_buckets(bucketed, start_local, now_local, bucket_sec)

    by_day = ts.aggregate_by_day(cp_records)
    daily_totals = densify_daily(by_day, cp_start_local, now_local)

    patterns = report.build_patterns(
        records=records,
        days=args.days,
        bucket_sec=bucket_sec,
        cp_days=args.cp_days,
        daily_totals_30d=daily_totals,
        ts_values=ts_values,
        values=values,
    )

    wrote_anything = False
    if args.html:
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(report.render_html(patterns))
        print(f"HTML written: {args.html}")
        wrote_anything = True
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(report.render_markdown(patterns))
        print(f"Markdown written: {args.markdown}")
        wrote_anything = True
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            fh.write(report.render_json(patterns))
        print(f"JSON written: {args.json_path}")
        wrote_anything = True

    if not wrote_anything:
        print(report.render_terminal(patterns))


if __name__ == "__main__":
    main()
