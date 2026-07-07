#!/usr/bin/env python3
"""Aggregate Claude Code token usage from local transcripts for an arbitrary time range."""
import argparse
import json
import os
import sys
from datetime import timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_stats as ts  # noqa: E402


def fmt_int(n: int) -> str:
    return f"{n:,}"


def fmt_short(n: int) -> str:
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:     return f"{n/1_000_000:.2f}M"
    if n >= 1_000:         return f"{n/1_000:.2f}K"
    return str(n)


def resolve_range(args):
    """Returns (start_local, end_local, label) matching original format."""
    if args.today:
        start, end, _ = ts.resolve_named_range("today")
        return start, end, "today"
    if args.yesterday:
        start, end, _ = ts.resolve_named_range("yesterday")
        return start, end, "yesterday"
    if args.this_week:
        start, end, _ = ts.resolve_named_range("this-week")
        return start, end, "this-week (Mon-Sun)"
    if args.this_month:
        start, end, _ = ts.resolve_named_range("this-month")
        return start, end, f"this-month ({start.strftime('%Y-%m')})"
    if args.all:
        start, end, _ = ts.resolve_named_range("all")
        return start, end, "all-time"
    if args.last:
        start, end, _ = ts.resolve_named_range(args.last)
        return start, end, f"last {args.last}"
    if args.date:
        d = ts.parse_datetime(args.date)
        d0 = d.replace(hour=0, minute=0, second=0, microsecond=0)
        return d0, d0 + timedelta(days=1), f"date {args.date}"
    if args.from_ or args.to:
        from datetime import datetime
        now = datetime.now(ts.local_tz())
        today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = ts.parse_datetime(args.from_) if args.from_ else today0
        end = ts.parse_datetime(args.to, end_of_day=True) if args.to else now
        return start, end, f"{start.isoformat()} to {end.isoformat()}"
    # default
    return ts.resolve_named_range("today")[0:2] + ("today (default)",)


def main():
    p = argparse.ArgumentParser(description="Aggregate Claude Code token usage from local transcripts.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--today", action="store_true")
    g.add_argument("--yesterday", action="store_true")
    g.add_argument("--this-week", dest="this_week", action="store_true")
    g.add_argument("--this-month", dest="this_month", action="store_true")
    g.add_argument("--all", action="store_true", help="Entire history (all transcripts)")
    g.add_argument("--date", help="Single day, YYYY-MM-DD")
    g.add_argument("--last", help="Rolling window, e.g. 7d / 12h / 30m")
    p.add_argument("--from", dest="from_")
    p.add_argument("--to")
    p.add_argument("--by-day", action="store_true")
    p.add_argument("--projects-dir", default=ts.PROJECTS_DIR)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    start, end, label = resolve_range(args)
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)

    files = ts.list_transcript_files(args.projects_dir)
    if not files:
        print(f"No transcript files found under {args.projects_dir}", file=sys.stderr)
        sys.exit(1)

    records = ts.scan_records(start_utc, end_utc, projects_dir=args.projects_dir, files=files)
    by_model = ts.aggregate_by_model(records)
    totals = ts.aggregate_totals(records)
    by_day = ts.aggregate_by_day(records) if args.by_day else None

    if args.json:
        # Compute billing_equiv as float (not int) to match original output
        billing_equiv_val = ts.billing_equiv_tokens(totals)
        out = {
            "range": {"start": start.isoformat(), "end": end.isoformat(), "label": label},
            "by_model": {m["model"]: {k: m[k] for k in ("in","out","cr","cc","count")} for m in by_model},
            "total": {**{k: totals[k] for k in ("in","out","cr","cc","count")},
                      "all": totals["all"], "billing_equiv_input": billing_equiv_val},
        }
        if by_day:
            out["by_day"] = {d["date"]: d["per_model"] for d in by_day}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    print(f"Range: {label}")
    print(f"       {start.isoformat()}  ->  {end.isoformat()}")
    print(f"Scanned {len(files)} transcript files under {args.projects_dir}")
    print()
    hdr = f"{'MODEL':<38} {'MSGS':>6} {'INPUT':>10} {'OUTPUT':>10} {'CACHE_READ':>14} {'CACHE_CREATE':>14} {'TOTAL':>14}"
    print(hdr)
    print("-" * len(hdr))
    for m in by_model:
        print(f"{m['model']:<38} {m['count']:>6} {fmt_int(m['in']):>10} {fmt_int(m['out']):>10} {fmt_int(m['cr']):>14} {fmt_int(m['cc']):>14} {fmt_int(m['total']):>14}")
    print("-" * len(hdr))
    print(f"{'TOTAL':<38} {totals['count']:>6} {fmt_int(totals['in']):>10} {fmt_int(totals['out']):>10} {fmt_int(totals['cr']):>14} {fmt_int(totals['cc']):>14} {fmt_int(totals['all']):>14}")
    print()
    print(f"Grand total tokens: {fmt_int(totals['all'])}  ({fmt_short(totals['all'])})")
    billing_equiv_val = ts.billing_equiv_tokens(totals)
    print(f"Billing-equiv input tokens (rough): {fmt_int(int(billing_equiv_val))}  ({fmt_short(int(billing_equiv_val))})")
    print("  weights: input=1x, cache_read=0.1x, cache_create_5m=1.25x, cache_create_1h=2x, output=5x")

    if by_day:
        print()
        print("Per-day breakdown:")
        print(f"{'DATE':<12} {'MSGS':>6} {'INPUT':>10} {'OUTPUT':>10} {'CACHE_READ':>14} {'CACHE_CREATE':>14} {'TOTAL':>14}")
        print("-" * 80)
        for d in by_day:
            # Re-aggregate per-day totals across models (not just token sum)
            day_records = [r for r in records if r.t_utc.astimezone(ts.local_tz()).date().isoformat() == d["date"]]
            dt = ts.aggregate_totals(day_records)
            print(f"{d['date']:<12} {dt['count']:>6} {fmt_int(dt['in']):>10} {fmt_int(dt['out']):>10} {fmt_int(dt['cr']):>14} {fmt_int(dt['cc']):>14} {fmt_int(dt['all']):>14}")


if __name__ == "__main__":
    main()
