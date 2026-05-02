"""Binary segmentation change-point detection on a daily token-total time series.

Pure stdlib. No numpy/scipy/pandas. No project imports.
"""

from __future__ import annotations

import math
import statistics


def _sse(seg: list[float]) -> float:
    """Sum of squared errors of a segment around its mean."""
    if not seg:
        return 0.0
    m = sum(seg) / len(seg)
    return sum((v - m) ** 2 for v in seg)


def _best_single_split(
    y: list[float], lo: int, hi: int, min_seg: int
) -> tuple[int | None, float]:
    """Return (best_split_index, gain) for splitting y[lo:hi]."""
    base = _sse(y[lo:hi])
    best: tuple[int | None, float] = (None, 0.0)
    for k in range(lo + min_seg, hi - min_seg + 1):
        cost = _sse(y[lo:k]) + _sse(y[k:hi])
        gain = base - cost
        if gain > best[1]:
            best = (k, gain)
    return best


def _segment_mean(y: list[float], lo: int, hi: int) -> float:
    if hi <= lo:
        return 0.0
    return sum(y[lo:hi]) / (hi - lo)


def detect(
    daily_totals: list[tuple[str, int]],
    alpha: float = 2.0,
    min_seg_days: int = 2,
) -> dict:
    """Binary segmentation change-point detection on a daily time series.

    Args:
        daily_totals: list of (date_str_iso, daily_total_tokens), e.g.
            [("2026-04-03", 4719560), ("2026-04-04", 0), ...]
            Assumed to be sorted by date ascending and dense (no gaps).
        alpha: BIC penalty multiplier (higher = fewer change points).
        min_seg_days: minimum segment length in days.
    """
    n = len(daily_totals)
    dates = [d for d, _ in daily_totals]
    y: list[float] = [float(v) for _, v in daily_totals]

    # Compute penalty.
    if n == 0:
        sigma2 = 0.0
        penalty = float("inf")
    else:
        sigma2 = statistics.pvariance(y)
        if sigma2 == 0:
            penalty = float("inf")
        else:
            penalty = alpha * sigma2 * math.log(n)

    # Recursive binary segmentation.
    cps: list[int] = []

    def segment(lo: int, hi: int) -> None:
        if hi - lo < 2 * min_seg_days:
            return
        k, gain = _best_single_split(y, lo, hi, min_seg_days)
        if k is None:
            return
        if gain > penalty:
            cps.append(k)
            segment(lo, k)
            segment(k, hi)

    segment(0, n)
    cps.sort()

    # Build segments (chronological, split on cps).
    boundaries = [0] + cps + [n]
    segments = []
    for i in range(len(boundaries) - 1):
        lo, hi = boundaries[i], boundaries[i + 1]
        if hi <= lo:
            continue
        segments.append(
            {
                "start_date": dates[lo],
                "end_date": dates[hi - 1],
                "n_days": hi - lo,
                "mean": _segment_mean(y, lo, hi),
            }
        )

    # Build change-point records. before-segment is from previous CP (or 0)
    # up to k; after-segment is from k up to next CP (or n).
    cp_records = []
    for i, k in enumerate(cps):
        prev_cp = cps[i - 1] if i > 0 else 0
        next_cp = cps[i + 1] if i + 1 < len(cps) else n
        before_mean = _segment_mean(y, prev_cp, k)
        after_mean = _segment_mean(y, k, next_cp)
        if before_mean == 0:
            pct_change: float = math.inf
        else:
            pct_change = (after_mean - before_mean) / before_mean * 100.0
        cp_records.append(
            {
                "date": dates[k],
                "index": k,
                "before_mean": before_mean,
                "after_mean": after_mean,
                "pct_change": pct_change,
            }
        )

    return {
        "alpha": alpha,
        "min_seg_days": min_seg_days,
        "penalty": penalty,
        "n_days": n,
        "changepoints": cp_records,
        "segments": segments,
        "daily_totals": [{"date": d, "total": int(v)} for d, v in daily_totals],
    }
