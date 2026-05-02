"""Seasonal aggregation of token usage.

Pure-stdlib helpers that bucket pre-densified ``(datetime, tokens)`` samples
by hour-of-day or day-of-week. Inputs are assumed to already be in the user's
local timezone; we just read ``dt.hour`` / ``dt.weekday()`` directly.
"""

from datetime import datetime

_DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def hour_of_day(ts_values: list[tuple[datetime, int]]) -> list[dict]:
    """Aggregate token counts by hour of day (0-23).

    Returns 24-element list, one per hour, sorted by hour ascending.
    """
    sums = [0] * 24
    counts = [0] * 24
    for dt, tokens in ts_values:
        h = dt.hour
        sums[h] += tokens
        counts[h] += 1
    return [
        {
            "hour": h,
            "mean": (sums[h] / counts[h]) if counts[h] else 0.0,
            "sum": sums[h],
            "count": counts[h],
        }
        for h in range(24)
    ]


def day_of_week(ts_values: list[tuple[datetime, int]]) -> list[dict]:
    """Aggregate token counts by day of week (Mon=0 ... Sun=6).

    Returns 7-element list, one per dow, sorted Mon to Sun.
    """
    sums = [0] * 7
    counts = [0] * 7
    for dt, tokens in ts_values:
        d = dt.weekday()
        sums[d] += tokens
        counts[d] += 1
    return [
        {
            "dow": d,
            "name": _DOW_NAMES[d],
            "mean": (sums[d] / counts[d]) if counts[d] else 0.0,
            "sum": sums[d],
            "count": counts[d],
        }
        for d in range(7)
    ]
