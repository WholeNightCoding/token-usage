"""Markov chain analysis for bucketed token-usage time series.

Pure stdlib. Takes a pre-densified list of token counts (one per bucket)
and computes transition matrices, stationary distributions, and dwell
times for 2-state (Idle/Active) and 3-state (Idle/Low/High) chains.
"""

from __future__ import annotations

import statistics


# ---------------------------------------------------------------------------
# Labelers
# ---------------------------------------------------------------------------

def _label2(v: int) -> str:
    return "I" if v == 0 else "A"


def _label3(v: int, thr: int) -> str:
    if v == 0:
        return "I"
    return "L" if v < thr else "H"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _empty_counts(states: list[str]) -> dict[str, dict[str, int]]:
    return {s: {s2: 0 for s2 in states} for s in states}


def _count_transitions(seq: list[str], states: list[str]) -> dict[str, dict[str, int]]:
    counts = _empty_counts(states)
    for a, b in zip(seq, seq[1:]):
        counts[a][b] += 1
    return counts


def _normalize_row(row: dict[str, int]) -> dict[str, float]:
    s = sum(row.values())
    if s == 0:
        return {k: 0.0 for k in row}
    return {k: v / s for k, v in row.items()}


def _build_P(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    return {s: _normalize_row(row) for s, row in counts.items()}


def _stationary_2x2(P: dict[str, dict[str, float]]) -> dict[str, float]:
    a = P["I"]["A"]
    b = P["A"]["I"]
    if a + b == 0:
        return {"I": 0.5, "A": 0.5}
    return {"I": b / (a + b), "A": a / (a + b)}


def _stationary_n(
    P: dict[str, dict[str, float]],
    states: list[str],
    iters: int = 500,
) -> dict[str, float]:
    n = len(states)
    pi = {s: 1.0 / n for s in states}
    for _ in range(iters):
        new = {s: 0.0 for s in states}
        for s in states:
            for s2 in states:
                new[s2] += pi[s] * P[s][s2]
        pi = new
    # Renormalize defensively against drift.
    total = sum(pi.values())
    if total > 0:
        pi = {s: v / total for s, v in pi.items()}
    return pi


def _dwell_hours(
    P: dict[str, dict[str, float]],
    s: str,
    bucket_sec: int,
) -> float:
    self_p = P[s][s]
    if self_p >= 1.0:
        return float("inf")
    return (1.0 / (1.0 - self_p)) * bucket_sec / 3600.0


def _uniform(states: list[str]) -> dict[str, float]:
    n = len(states)
    return {s: 1.0 / n for s in states}


def _zero_P(states: list[str]) -> dict[str, dict[str, float]]:
    return {s: {s2: 0.0 for s2 in states} for s in states}


def _zero_dwell(states: list[str]) -> dict[str, float]:
    return {s: 0.0 for s in states}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def two_state(values: list[int], bucket_sec: int = 1800) -> dict:
    """Binary Markov chain on idle (0) vs active (>0) buckets."""
    states = ["I", "A"]

    if len(values) < 2:
        return {
            "states": states,
            "P": _zero_P(states),
            "counts": _empty_counts(states),
            "stationary": _uniform(states),
            "dwell_h": _zero_dwell(states),
            "stickiness": 0.0,
        }

    seq = [_label2(v) for v in values]
    counts = _count_transitions(seq, states)
    P = _build_P(counts)

    stationary = _stationary_2x2(P)
    dwell_h = {s: _dwell_hours(P, s, bucket_sec) for s in states}
    stickiness = P["A"]["A"] - stationary["A"]

    return {
        "states": states,
        "P": P,
        "counts": counts,
        "stationary": stationary,
        "dwell_h": dwell_h,
        "stickiness": stickiness,
    }


def three_state(
    values: list[int],
    bucket_sec: int = 1800,
    threshold: int | None = None,
) -> dict:
    """3-state Markov chain on Idle / Low / High buckets.

    threshold defaults to the median of the active (non-zero) values.
    Buckets with v < threshold are 'L' (Low); v >= threshold are 'H' (High).
    Buckets with v == 0 are 'I' (Idle).
    """
    states = ["I", "L", "H"]

    # Resolve threshold (always emit a concrete int, even on degenerate input).
    if threshold is None:
        nonzero = [v for v in values if v > 0]
        if nonzero:
            thr = int(statistics.median(nonzero))
        else:
            thr = 1
    else:
        thr = int(threshold)

    if len(values) < 2:
        return {
            "states": states,
            "threshold": thr,
            "P": _zero_P(states),
            "counts": _empty_counts(states),
            "stationary": _uniform(states),
            "dwell_h": _zero_dwell(states),
            "n_per_state": {s: 0 for s in states},
        }

    seq = [_label3(v, thr) for v in values]
    counts = _count_transitions(seq, states)
    P = _build_P(counts)

    stationary = _stationary_n(P, states)
    dwell_h = {s: _dwell_hours(P, s, bucket_sec) for s in states}

    n_per_state = {s: 0 for s in states}
    for s in seq:
        n_per_state[s] += 1

    return {
        "states": states,
        "threshold": thr,
        "P": P,
        "counts": counts,
        "stationary": stationary,
        "dwell_h": dwell_h,
        "n_per_state": n_per_state,
    }
