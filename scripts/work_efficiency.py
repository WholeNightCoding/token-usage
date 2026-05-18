#!/usr/bin/env python3
"""Work-efficiency analysis: token throughput while you're actually working.

Distinguishes wall-clock time from "active time" (minutes that actually consumed
tokens). Reports throughput as tokens-per-active-minute, not tokens-per-wall-hour,
so a 20-minute work block isn't averaged against the 40 idle minutes around it.

Minimum aggregation granularity is a 30-min slot.

Usable two ways:
  - As a library: `compute_efficiency(records, start, end)` returns a dict
  - As a CLI:     `python3 work_efficiency.py [days]`
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_stats as ts  # noqa: E402


def _percentile(sorted_vals: list, p: float) -> float:
    """Linear-interpolation percentile (numpy-style). p in [0, 1]."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _pct_dict(sorted_vals: list) -> dict:
    return {
        "min": _percentile(sorted_vals, 0.0),
        "p10": _percentile(sorted_vals, 0.10),
        "p25": _percentile(sorted_vals, 0.25),
        "median": _percentile(sorted_vals, 0.50),
        "p75": _percentile(sorted_vals, 0.75),
        "p90": _percentile(sorted_vals, 0.90),
        "max": _percentile(sorted_vals, 1.0),
    }


def compute_efficiency(
    records: List[ts.Record],
    start_local: datetime,
    end_local: datetime,
) -> dict:
    """Pure-data computation. Returns a JSON-able dict.

    Definitions:
      - "active minute": a clock minute (local tz) that has any token usage.
      - "active 30-min slot": a half-hour slot with at least one active minute.
      - rate = total_tokens_in_slot / active_minutes_in_slot * 60 (i.e. tok/hr
        extrapolated from the minutes you actually worked, NOT from 60 wall
        minutes — so a 20-min burst isn't diluted by the 40 idle minutes
        around it).
    """
    tz = ts.local_tz()

    # Bucket tokens (raw + billing-equiv) by exact minute, local tz.
    per_min_raw: dict = defaultdict(int)
    per_min_bill: dict = defaultdict(float)
    for r in records:
        m = r.t_utc.astimezone(tz).replace(second=0, microsecond=0)
        per_min_raw[m] += r.total
        per_min_bill[m] += ts.billing_equiv_tokens(r)

    total_tokens = sum(per_min_raw.values())
    total_bill = sum(per_min_bill.values())
    active_minutes = len(per_min_raw)
    active_hours = active_minutes / 60.0

    # Wall-clock window length.
    wall_seconds = (end_local - start_local).total_seconds()
    wall_hours = wall_seconds / 3600.0

    # 30-minute slot rollup.
    slot_raw: dict = defaultdict(int)
    slot_bill: dict = defaultdict(float)
    slot_active: dict = defaultdict(int)
    for m, v in per_min_raw.items():
        slot = m.replace(minute=(m.minute // 30) * 30)
        slot_raw[slot] += v
        slot_bill[slot] += per_min_bill[m]
        slot_active[slot] += 1

    # Total possible 30-min slots in the window (for occupancy %).
    total_slots = max(1, int(wall_seconds // 1800))

    # Per-slot rate distribution (tok/hr, extrapolated from active minutes).
    slot_rates_raw = sorted(slot_raw[s] / slot_active[s] * 60 for s in slot_raw)
    slot_rates_bill = sorted(slot_bill[s] / slot_active[s] * 60 for s in slot_raw)

    # Hour-of-day rollup (across whole window).
    hod_tokens = defaultdict(int)
    hod_bill = defaultdict(float)
    hod_active_min = defaultdict(int)
    for m, v in per_min_raw.items():
        h = m.hour
        hod_tokens[h] += v
        hod_bill[h] += per_min_bill[m]
        hod_active_min[h] += 1
    hour_of_day = []
    for h in range(24):
        am = hod_active_min.get(h, 0)
        tok = hod_tokens.get(h, 0)
        bill = hod_bill.get(h, 0.0)
        hour_of_day.append({
            "hour": h,
            "active_min": am,
            "tokens": tok,
            "billing_equiv": int(bill),
            "rate_per_hr": (tok / am * 60.0) if am else 0.0,
        })

    # Per-day rollup. Includes ALL days in window (zero-filled).
    day_tokens = defaultdict(int)
    day_bill = defaultdict(float)
    day_active = defaultdict(int)
    for m, v in per_min_raw.items():
        d = m.date()
        day_tokens[d] += v
        day_bill[d] += per_min_bill[m]
        day_active[d] += 1
    per_day = []
    cur = start_local.date()
    end_d = end_local.date()
    while cur <= end_d:
        am = day_active.get(cur, 0)
        tok = day_tokens.get(cur, 0)
        bill = day_bill.get(cur, 0.0)
        per_day.append({
            "date": cur.isoformat(),
            "active_min": am,
            "tokens": tok,
            "billing_equiv": int(bill),
            "rate_per_hr": (tok / am * 60.0) if am else 0.0,
        })
        cur += timedelta(days=1)

    avg_rate_raw_per_hr = (total_tokens / active_minutes * 60.0) if active_minutes else 0.0
    avg_rate_bill_per_hr = (total_bill / active_minutes * 60.0) if active_minutes else 0.0

    return {
        "range": {
            "from": start_local.isoformat(),
            "to": end_local.isoformat(),
            "wall_hours": round(wall_hours, 2),
        },
        "summary": {
            "active_minutes": active_minutes,
            "active_hours": round(active_hours, 2),
            "active_pct": round(active_minutes / 60.0 / wall_hours, 4) if wall_hours else 0,
            "total_tokens": total_tokens,
            "billing_equiv": int(total_bill),
            "avg_rate_raw_per_min": int(total_tokens / active_minutes) if active_minutes else 0,
            "avg_rate_raw_per_hr": int(avg_rate_raw_per_hr),
            "avg_rate_bill_per_hr": int(avg_rate_bill_per_hr),
        },
        "slot_distribution": {
            "slot_seconds": 1800,
            "total_slots": total_slots,
            "active_slots": len(slot_raw),
            "active_slot_pct": round(len(slot_raw) / total_slots, 4) if total_slots else 0,
            "raw_per_hr": {k: int(v) for k, v in _pct_dict(slot_rates_raw).items()},
            "bill_per_hr": {k: int(v) for k, v in _pct_dict(slot_rates_bill).items()},
            # Sorted raw rate arrays — let the client draw CDF + KDE without
            # re-aggregating. ~600 floats for a 30d window, payload is tiny.
            "raw_rates_sorted": [int(v) for v in slot_rates_raw],
            "bill_rates_sorted": [int(v) for v in slot_rates_bill],
        },
        "hour_of_day": hour_of_day,
        "per_day": per_day,
    }


# ---------- CLI ----------

def _fmt_short(n) -> str:
    n = float(n)
    if n >= 1e9: return f"{n/1e9:.2f}B"
    if n >= 1e6: return f"{n/1e6:.2f}M"
    if n >= 1e3: return f"{n/1e3:.1f}K"
    return f"{n:.0f}"


def _cli(days: int = 30) -> None:
    from datetime import timezone
    tz = ts.local_tz()
    end = datetime.now(tz)
    start = end - timedelta(days=days)
    print(f"Scanning transcripts {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} "
          f"(last {days}d, local tz)\n")
    records = ts.scan_records(start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    if not records:
        print("No records in range.")
        return

    data = compute_efficiency(records, start, end)
    s = data["summary"]
    sd = data["slot_distribution"]

    print("=" * 70)
    print(f"OVERVIEW — last {days} days")
    print("=" * 70)
    wall_h = data["range"]["wall_hours"]
    print(f"  Wall-clock hours in range:      {wall_h:>10,.0f} h")
    print(f"  Active minutes (any tokens):    {s['active_minutes']:>10,} min "
          f"= {s['active_hours']:>6.1f} h ({s['active_pct']*100:>4.1f}% of wall clock)")
    print(f"  Total tokens (raw):             {s['total_tokens']:>15,}  ({_fmt_short(s['total_tokens'])})")
    print(f"  Total tokens (billing-equiv):   {s['billing_equiv']:>15,}  ({_fmt_short(s['billing_equiv'])})")
    print()
    print("THROUGHPUT (rate while actually working, NOT wall clock)")
    print(f"  Avg raw tokens / active minute: {_fmt_short(s['avg_rate_raw_per_min']):>10}  "
          f"= {_fmt_short(s['avg_rate_raw_per_hr'])}/hr")
    print(f"  Avg bill tokens / active hour:  {_fmt_short(s['avg_rate_bill_per_hr']):>10}/hr")
    print()

    print("=" * 70)
    print("30-MIN SLOT DISTRIBUTION (rate = tokens ÷ active min in slot × 60)")
    print("=" * 70)
    print(f"  Total active 30-min slots: {sd['active_slots']} "
          f"(of {sd['total_slots']} possible, {sd['active_slot_pct']*100:.1f}%)")
    print()
    raw_max = sd["raw_per_hr"]["max"] or 1
    print("  Raw tokens/hr (extrapolated from active minutes in each 30-min slot):")
    for label in ["min", "p10", "p25", "median", "p75", "p90", "max"]:
        v = sd["raw_per_hr"][label]
        bar = "█" * int(v / raw_max * 40)
        print(f"    {label:>6}: {_fmt_short(v):>8}/hr  {bar}")
    print()
    bill_max = sd["bill_per_hr"]["max"] or 1
    print("  Billing-equiv tokens/hr:")
    for label in ["min", "p10", "p25", "median", "p75", "p90", "max"]:
        v = sd["bill_per_hr"][label]
        bar = "█" * int(v / bill_max * 40)
        print(f"    {label:>6}: {_fmt_short(v):>8}/hr  {bar}")
    print()

    print("=" * 70)
    print("HOUR-OF-DAY PATTERN — when in the day do you work?")
    print("=" * 70)
    print(f"  Hour   Active-min   Tokens(raw)   Tokens(bill)   Rate/hr-while-active")
    max_am = max(h["active_min"] for h in data["hour_of_day"]) or 1
    for h in data["hour_of_day"]:
        bar = "█" * int(h["active_min"] / max_am * 30)
        marker = "  ←busiest" if h["active_min"] == max_am and h["active_min"] > 0 else ""
        print(f"  {h['hour']:02d}:00  {h['active_min']:>7}      "
              f"{_fmt_short(h['tokens']):>8}     "
              f"{_fmt_short(h['billing_equiv']):>8}      "
              f"{_fmt_short(h['rate_per_hr']):>7}/hr  {bar}{marker}")
    print()

    print("=" * 70)
    print("PER-DAY BREAKDOWN")
    print("=" * 70)
    print(f"  {'Date':10}  {'Active':>8}  {'Tokens(raw)':>11}  {'Tokens(bill)':>12}  {'Rate/hr':>10}")
    nonzero = [d for d in data["per_day"] if d["active_min"] > 0]
    for d in nonzero:
        hrs = d["active_min"] / 60
        print(f"  {d['date']:10}  {hrs:>5.1f}h    "
              f"{_fmt_short(d['tokens']):>8}      "
              f"{_fmt_short(d['billing_equiv']):>8}      "
              f"{_fmt_short(d['rate_per_hr']):>7}/hr")
    if nonzero:
        total_am = sum(d["active_min"] for d in nonzero)
        total_tok = sum(d["tokens"] for d in nonzero)
        total_bill = sum(d["billing_equiv"] for d in nonzero)
        rate = (total_tok / total_am * 60) if total_am else 0
        print(f"  {'─'*10}  {'─'*8}  {'─'*11}  {'─'*12}  {'─'*10}")
        print(f"  {'TOTAL':10}  {total_am/60:>5.1f}h    "
              f"{_fmt_short(total_tok):>8}      "
              f"{_fmt_short(total_bill):>8}      "
              f"{_fmt_short(rate):>7}/hr")
    print()
    print(f"  ↑ days with NO record in range are skipped (idle days)")


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    _cli(d)
