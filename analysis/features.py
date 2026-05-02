"""Time-series feature extraction for bucketed token-count series.

Pure stdlib. Operates on pre-densified lists of integer token counts per
consecutive equal-sized bucket; no project imports.
"""

from __future__ import annotations

import math
import statistics


# ACF lag time intervals (seconds) and their human labels.
_ACF_INTERVALS: list[tuple[str, int]] = [
    ("30min", 30 * 60),
    ("1h", 60 * 60),
    ("2h", 2 * 60 * 60),
    ("4h", 4 * 60 * 60),
    ("12h", 12 * 60 * 60),
    ("24h", 24 * 60 * 60),
    ("7d", 7 * 24 * 60 * 60),
]


def _empty_result() -> dict:
    """Return the result shape filled with zeros for an empty input."""
    return {
        "n_buckets": 0,
        "active_pct": 0.0,
        "stats": {
            "mean": 0.0,
            "median": 0.0,
            "std": 0.0,
            "max": 0,
            "p50": 0,
            "p75": 0,
            "p90": 0,
            "p95": 0,
            "p99": 0,
        },
        "stats_active_only": {"mean": 0.0, "median": 0.0, "std": 0.0},
        "burstiness": {"goh_barabasi_B": 0.0, "fano_factor": 0.0, "cv": 0.0},
        "concentration": {"gini": 0.0, "top5pct": 0.0, "top10pct": 0.0},
        "acf": [],
        "runs": {
            "active": {"count": 0, "median_h": 0.0, "max_h": 0.0, "mean_h": 0.0},
            "idle": {"count": 0, "median_h": 0.0, "max_h": 0.0, "mean_h": 0.0},
        },
        "entropy_bits": 0.0,
    }


def _gini(x: list[int]) -> float:
    if not x:
        return 0.0
    s = sum(x)
    if s == 0:
        return 0.0
    n = len(x)
    xs = sorted(x)
    return (sum((i + 1) * v for i, v in enumerate(xs)) / (n * s)) * 2 - (n + 1) / n


def _autocorr(x: list[int], lag: int, mean: float, denom: float) -> float:
    """Sample autocorrelation at the given lag.

    `mean` and `denom` (sum of squared deviations) are pre-computed so we
    don't recompute them per lag.
    """
    if denom <= 0 or lag <= 0 or lag >= len(x):
        return 0.0
    num = 0.0
    for i in range(len(x) - lag):
        num += (x[i] - mean) * (x[i + lag] - mean)
    return num / denom


def _runs(values: list[int], predicate) -> list[int]:
    """Return lengths (in buckets) of maximal consecutive runs satisfying `predicate`."""
    out: list[int] = []
    cur = 0
    for v in values:
        if predicate(v):
            cur += 1
        else:
            if cur > 0:
                out.append(cur)
            cur = 0
    if cur > 0:
        out.append(cur)
    return out


def _runs_summary(run_lengths: list[int], bucket_sec: int) -> dict:
    """Convert bucket-count run lengths to hour-denominated summary stats."""
    if not run_lengths:
        return {"count": 0, "median_h": 0.0, "max_h": 0.0, "mean_h": 0.0}
    hours = [r * bucket_sec / 3600.0 for r in run_lengths]
    return {
        "count": len(hours),
        "median_h": float(statistics.median(hours)),
        "max_h": float(max(hours)),
        "mean_h": float(sum(hours) / len(hours)),
    }


def time_series_features(values: list[int], bucket_sec: int = 1800) -> dict:
    """Compute time-series features over a dense list of bucketed token counts.

    Args:
        values: dense list of token counts per consecutive equal-sized bucket
            (zeros included for idle buckets).
        bucket_sec: bucket size in seconds; controls ACF lag labeling and the
            run-length-to-hours conversion.

    Returns:
        A dict with the schema documented in the module docstring's spec.
    """
    n = len(values)
    if n == 0:
        return _empty_result()

    total = sum(values)
    nonzero = [v for v in values if v > 0]
    n_nonzero = len(nonzero)

    mean = total / n
    stdev = float(statistics.pstdev(values))  # population std
    max_v = max(values)

    cv = stdev / mean if mean else 0.0
    B = (stdev - mean) / (stdev + mean) if (stdev + mean) > 0 else 0.0
    fano = (stdev ** 2) / mean if mean > 0 else 0.0

    sv = sorted(values)
    # Index-based quantiles (no interpolation), matching count_tokens.py style.
    # `median` uses median_low semantics for integer stability; the verification
    # spec's assertion `stats.median == 0` on a 50/50 split list confirms this
    # (statistics.median would return 500.0 instead).
    median = sv[(n - 1) // 2]
    p50 = sv[n // 2]
    p75 = sv[(3 * n) // 4]
    p90 = sv[int(n * 0.9)]
    p95 = sv[int(n * 0.95)]
    p99 = sv[int(n * 0.99)]

    # Active-only stats.
    if n_nonzero > 0:
        nz_sorted = sorted(nonzero)
        nz_mean = sum(nonzero) / n_nonzero
        nz_median = float(nz_sorted[(n_nonzero - 1) // 2])
        nz_std = float(statistics.pstdev(nonzero))
    else:
        nz_mean = 0.0
        nz_median = 0.0
        nz_std = 0.0

    # Concentration.
    gini_v = _gini(values)
    if total > 0:
        # Top-N% of buckets by count, using floor(N/k) like the spec snippet.
        top5_k = n // 20
        top10_k = n // 10
        top5pct = (sum(sv[-top5_k:]) / total * 100) if top5_k > 0 else 0.0
        top10pct = (sum(sv[-top10_k:]) / total * 100) if top10_k > 0 else 0.0
    else:
        top5pct = 0.0
        top10pct = 0.0

    # ACF — pre-compute mean and total squared deviation once.
    acf_mean = mean
    acf_denom = sum((v - acf_mean) ** 2 for v in values)
    acf: list[dict] = []
    for label, interval_sec in _ACF_INTERVALS:
        lag_buckets = round(interval_sec / bucket_sec)
        if lag_buckets < 1 or lag_buckets >= n:
            continue
        acf.append(
            {
                "lag_buckets": int(lag_buckets),
                "lag_label": label,
                "value": float(_autocorr(values, lag_buckets, acf_mean, acf_denom)),
            }
        )

    # Runs.
    active_runs = _runs(values, lambda v: v > 0)
    idle_runs = _runs(values, lambda v: v == 0)

    # Binary entropy (active vs idle), in bits.
    nz_frac = n_nonzero / n
    if 0 < nz_frac < 1:
        entropy_bits = -(
            nz_frac * math.log2(nz_frac) + (1 - nz_frac) * math.log2(1 - nz_frac)
        )
    else:
        entropy_bits = 0.0

    return {
        "n_buckets": n,
        "active_pct": float(n_nonzero / n * 100),
        "stats": {
            "mean": float(mean),
            "median": float(median),
            "std": stdev,
            "max": int(max_v),
            "p50": int(p50),
            "p75": int(p75),
            "p90": int(p90),
            "p95": int(p95),
            "p99": int(p99),
        },
        "stats_active_only": {
            "mean": float(nz_mean),
            "median": nz_median,
            "std": nz_std,
        },
        "burstiness": {
            "goh_barabasi_B": float(B),
            "fano_factor": float(fano),
            "cv": float(cv),
        },
        "concentration": {
            "gini": float(gini_v),
            "top5pct": float(top5pct),
            "top10pct": float(top10pct),
        },
        "acf": acf,
        "runs": {
            "active": _runs_summary(active_runs, bucket_sec),
            "idle": _runs_summary(idle_runs, bucket_sec),
        },
        "entropy_bits": float(entropy_bits),
    }
