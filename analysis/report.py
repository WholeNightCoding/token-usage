"""Render a token-usage `patterns` dict to terminal text, Markdown, HTML, or JSON.

Pure stdlib. The `patterns` dict shape is what `build_patterns()` produces:
top-level keys are computed_at, params, features, seasonal, markov_2,
markov_3, changepoint, profile.
"""

from __future__ import annotations

import html as _html
import json
import math
from datetime import datetime
from typing import Any

from analysis.changepoint import detect as _cp_detect
from analysis.features import time_series_features as _features
from analysis.markov import three_state as _markov3
from analysis.markov import two_state as _markov2
from analysis.seasonal import day_of_week as _dow
from analysis.seasonal import hour_of_day as _hod


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_short(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{sign}{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{sign}{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{sign}{n/1_000:.2f}K"
    if n == int(n):
        return f"{sign}{int(n)}"
    return f"{sign}{n:.2f}"


def _fmt_pct(x) -> str:
    try:
        return f"{float(x):.1f}%"
    except (TypeError, ValueError):
        return str(x)


def _fmt_signed(x, decimals: int = 3) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{v:+.{decimals}f}"


def _bar(value: float, max_value: float, width: int = 40, char: str = "█") -> str:
    if max_value <= 0 or value <= 0:
        return ""
    n = int(round(min(value, max_value) / max_value * width))
    return char * max(1, n) if value > 0 else ""


# ---------------------------------------------------------------------------
# Profile derivation
# ---------------------------------------------------------------------------

def _peak_hour(seasonal_hour: list[dict]) -> int | None:
    if not seasonal_hour:
        return None
    return max(seasonal_hour, key=lambda h: h.get("mean", 0)).get("hour")


def _acf_value(acf: list[dict], lag_label: str) -> float | None:
    for entry in acf or []:
        if entry.get("lag_label") == lag_label:
            return float(entry.get("value", 0.0))
    return None


def _build_profile(features: dict, seasonal: dict, m2: dict, m3: dict) -> dict:
    """Return {summary: str, bullets: [str, ...]} per spec."""
    tags: list[str] = []
    bullets: list[str] = []

    peak = _peak_hour(seasonal.get("hour", []))
    if peak is not None and 18 <= peak <= 21:
        tags.append("晚高峰")
        bullets.append(f"晚高峰用户：峰值小时 = {peak:02d}:00（18-21 点）")
    elif peak is not None:
        bullets.append(f"峰值小时 = {peak:02d}:00")

    B = features.get("burstiness", {}).get("goh_barabasi_B", 0.0)
    if B > 0.15:
        tags.append("突发型")
        bullets.append(f"突发型：Goh-Barabási B = {_fmt_signed(B)}（>0.15）")
    else:
        bullets.append(f"突发指标 B = {_fmt_signed(B)}")

    acf = features.get("acf", [])
    lag12 = _acf_value(acf, "12h")
    lag24 = _acf_value(acf, "24h")
    if (lag12 is not None and lag12 < -0.1) or (lag24 is not None and lag24 > 0.1):
        tags.append("强 24h 周期")
        parts = []
        if lag12 is not None:
            parts.append(f"lag-12h={_fmt_signed(lag12)}")
        if lag24 is not None:
            parts.append(f"lag-24h={_fmt_signed(lag24)}")
        bullets.append("强 24h 周期：" + ", ".join(parts))

    lag7d = _acf_value(acf, "7d")
    if lag7d is not None and abs(lag7d) < 0.1:
        tags.append("无周周期")
        bullets.append(f"无周周期：lag-7d = {_fmt_signed(lag7d)}（|·|<0.1）")

    stickiness = m2.get("stickiness", 0.0) or 0.0
    if stickiness > 0.3:
        tags.append("黏性强")
        bullets.append(f"Markov 黏性强：stickiness = {_fmt_signed(stickiness)}")
    else:
        bullets.append(f"Markov 黏性 = {_fmt_signed(stickiness)}")

    P3 = m3.get("P", {}) or {}
    hh = (P3.get("H") or {}).get("H", 0.0) or 0.0
    ll = (P3.get("L") or {}).get("L", 0.0) or 0.0
    if hh > ll:
        tags.append("high 沉浸")
        bullets.append(f"High 沉浸：P(H→H)={hh:.2f} > P(L→L)={ll:.2f}")
    else:
        bullets.append(f"P(H→H)={hh:.2f}, P(L→L)={ll:.2f}")

    if tags:
        summary = f"你是 {'、'.join(tags)} 的用户"
    else:
        summary = "用户画像：信号偏弱，未触发主要分类标签"

    return {"summary": summary, "bullets": bullets}


# ---------------------------------------------------------------------------
# build_patterns
# ---------------------------------------------------------------------------

def build_patterns(
    records,
    days: int,
    bucket_sec: int,
    cp_days: int,
    daily_totals_30d: list[tuple[str, int]],
    ts_values: list[tuple[datetime, int]],
    values: list[int],
) -> dict:
    """Assemble the full patterns dict from prepared inputs."""
    # Detect local tz string
    try:
        tz_str = datetime.now().astimezone().strftime("%z") or "local"
    except Exception:
        tz_str = "local"

    feats = _features(values, bucket_sec)
    seasonal = {"hour": _hod(ts_values), "dow": _dow(ts_values)}
    m2 = _markov2(values, bucket_sec)
    m3 = _markov3(values, bucket_sec)
    cp = _cp_detect(daily_totals_30d)

    profile = _build_profile(feats, seasonal, m2, m3)

    return {
        "computed_at": datetime.now().astimezone().isoformat(),
        "params": {
            "days": days,
            "bucket_sec": bucket_sec,
            "cp_days": cp_days,
            "tz": tz_str,
        },
        "features": feats,
        "seasonal": seasonal,
        "markov_2": m2,
        "markov_3": m3,
        "changepoint": cp,
        "profile": profile,
    }


# ---------------------------------------------------------------------------
# JSON renderer (sanitize inf/nan)
# ---------------------------------------------------------------------------

def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        if math.isnan(obj):
            return "nan"
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def render_json(patterns: dict, indent: int = 2) -> str:
    return json.dumps(_sanitize(patterns), indent=indent, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Terminal renderer
# ---------------------------------------------------------------------------

_DOW_CN = {"Mon": "周一", "Tue": "周二", "Wed": "周三",
           "Thu": "周四", "Fri": "周五", "Sat": "周六", "Sun": "周日"}


def render_terminal(patterns: dict) -> str:
    p = patterns
    params = p.get("params", {})
    feats = p.get("features", {})
    seasonal = p.get("seasonal", {})
    m2 = p.get("markov_2", {})
    m3 = p.get("markov_3", {})
    cp = p.get("changepoint", {})
    profile = p.get("profile", {})

    out: list[str] = []
    sep = "=" * 72
    sub = "-" * 72

    # ---- Header ----
    out.append(sep)
    out.append("Token 用量时序分析报告")
    out.append(sep)
    out.append(f"  生成时间   : {p.get('computed_at', '')}")
    out.append(f"  数据窗口   : 最近 {params.get('days', '?')} 天 "
               f"({params.get('bucket_sec', 0)//60} 分钟桶) + "
               f"最近 {params.get('cp_days', '?')} 天日总量")
    daily_totals = cp.get("daily_totals", []) or []
    total_tokens = sum(int(d.get("total", 0)) for d in daily_totals)
    out.append(f"  时区       : {params.get('tz', 'local')}")
    out.append(f"  总 token   : {_fmt_int(total_tokens)}  ({_fmt_short(total_tokens)})")
    out.append("")

    # ---- Profile ----
    out.append(sub)
    out.append("用户画像 (Profile)")
    out.append(sub)
    out.append(f"  >>> {profile.get('summary', '')}")
    for b in profile.get("bullets", []):
        out.append(f"   - {b}")
    out.append("")

    # ---- Section 1: features ----
    out.append(sub)
    out.append("1. 基础时序特征 (features)")
    out.append(sub)
    n_buckets = feats.get("n_buckets", 0)
    active_pct = feats.get("active_pct", 0.0)
    n_active = int(round(n_buckets * active_pct / 100)) if n_buckets else 0
    out.append(f"  桶数 = {n_buckets}    活跃 = {n_active} "
               f"({_fmt_pct(active_pct)})    "
               f"静默 = {n_buckets - n_active} ({_fmt_pct(100 - active_pct)})")
    stats = feats.get("stats", {})
    nz_stats = feats.get("stats_active_only", {})
    out.append("")
    out.append("  描述性统计")
    hdr = f"    {'指标':<10} {'含 0 桶':>20} {'仅活跃桶':>20}"
    out.append(hdr)
    out.append("    " + "-" * (len(hdr) - 4))
    for label, k1, k2 in (("mean", "mean", "mean"),
                          ("median", "median", "median"),
                          ("std", "std", "std")):
        v1 = stats.get(k1, 0)
        v2 = nz_stats.get(k2, 0)
        out.append(f"    {label:<10} {_fmt_int(int(v1)):>20} {_fmt_int(int(v2)):>20}")
    out.append(f"    {'max':<10} {_fmt_int(stats.get('max', 0)):>20} {'—':>20}")
    out.append("")
    out.append("  分位数 (含 0 桶):")
    for q in ("p50", "p75", "p90", "p95", "p99"):
        out.append(f"    {q:<5} = {_fmt_int(stats.get(q, 0)):>16}")
    out.append("")

    # Burstiness
    b = feats.get("burstiness", {})
    out.append("  突发性")
    out.append(f"    Goh-Barabási B = {_fmt_signed(b.get('goh_barabasi_B', 0))}  "
               f"(0=泊松, +1=极端突发, -1=完全均匀)")
    out.append(f"    Fano factor    = {_fmt_short(b.get('fano_factor', 0))}  "
               f"(=1 时为泊松)")
    out.append(f"    CV             = {b.get('cv', 0):.3f}")
    out.append("")

    # Concentration
    c = feats.get("concentration", {})
    out.append("  集中度")
    out.append(f"    Gini       = {c.get('gini', 0):.3f}")
    out.append(f"    top 5%     = {_fmt_pct(c.get('top5pct', 0))}")
    out.append(f"    top 10%    = {_fmt_pct(c.get('top10pct', 0))}")
    out.append("")

    # ACF as bars
    out.append("  自相关函数 (ACF)")
    acf = feats.get("acf", []) or []
    if acf:
        max_abs = max((abs(a.get("value", 0.0)) for a in acf), default=1.0) or 1.0
        for a in acf:
            v = a.get("value", 0.0)
            label = a.get("lag_label", "?")
            bar = _bar(abs(v), max_abs, 30)
            sign = " " if v >= 0 else "-"
            tag = "" if v >= 0 else "  (反相)"
            out.append(f"    lag {label:<6} {sign}{abs(v):.3f}  {bar}{tag}")
    out.append("")

    # Runs
    runs = feats.get("runs", {})
    a_run = runs.get("active", {})
    i_run = runs.get("idle", {})
    out.append("  Session / 静默期")
    out.append(f"    {'类型':<14} {'段数':>6} {'中位 (h)':>10} {'最长 (h)':>10} {'平均 (h)':>10}")
    out.append(f"    {'工作 session':<14} {a_run.get('count', 0):>6} "
               f"{a_run.get('median_h', 0):>10.2f} "
               f"{a_run.get('max_h', 0):>10.2f} "
               f"{a_run.get('mean_h', 0):>10.2f}")
    out.append(f"    {'静默期':<14} {i_run.get('count', 0):>6} "
               f"{i_run.get('median_h', 0):>10.2f} "
               f"{i_run.get('max_h', 0):>10.2f} "
               f"{i_run.get('mean_h', 0):>10.2f}")
    out.append("")
    out.append(f"  二元活跃熵 H = {feats.get('entropy_bits', 0):.4f} bits "
               f"(1.0 = 最大不确定)")
    out.append("")

    # ---- Section 2: seasonal ----
    out.append(sub)
    out.append("2. 季节性 (seasonal)")
    out.append(sub)
    hours = seasonal.get("hour", []) or []
    if hours:
        max_mean = max((h.get("mean", 0) for h in hours), default=1.0) or 1.0
        out.append("  小时画像 (mean per bucket, local tz):")
        for h in hours:
            mean = h.get("mean", 0.0)
            bar = _bar(mean, max_mean, 40)
            out.append(f"    {h.get('hour', 0):02d}:00 {bar:<40}  {_fmt_short(mean)}")
        out.append("")

    dows = seasonal.get("dow", []) or []
    if dows:
        max_mean = max((d.get("mean", 0) for d in dows), default=1.0) or 1.0
        out.append("  星期画像 (mean per bucket):")
        for d in dows:
            mean = d.get("mean", 0.0)
            bar = _bar(mean, max_mean, 40)
            name = _DOW_CN.get(d.get("name", ""), d.get("name", "?"))
            out.append(f"    {name}  {bar:<40}  {_fmt_short(mean)}")
        out.append("")

    # ---- Section 3: changepoint ----
    out.append(sub)
    out.append("3. 变化点 (changepoint)")
    out.append(sub)
    segs = cp.get("segments", []) or []
    if segs:
        out.append(f"  段数 = {len(segs)}    变化点数 = {len(cp.get('changepoints', []) or [])}")
        out.append(f"  {'段':<4} {'起':<12} {'止':<12} {'天数':>6} {'平均/天':>14}")
        for i, s in enumerate(segs, 1):
            out.append(f"  {i:<4} {s.get('start_date', ''):<12} "
                       f"{s.get('end_date', ''):<12} "
                       f"{s.get('n_days', 0):>6} "
                       f"{_fmt_short(s.get('mean', 0)):>14}")
        out.append("")

    cps = cp.get("changepoints", []) or []
    if cps:
        out.append("  检测到的变化点:")
        for c2 in cps:
            pct = c2.get("pct_change", 0.0)
            pct_str = "inf" if isinstance(pct, float) and math.isinf(pct) else f"{pct:+.1f}%"
            out.append(f"    {c2.get('date', '')}  "
                       f"前 mean={_fmt_short(c2.get('before_mean', 0))}  "
                       f"后 mean={_fmt_short(c2.get('after_mean', 0))}  "
                       f"({pct_str})")
        out.append("")

    daily = daily_totals
    if daily:
        out.append("  日总量时序:")
        max_total = max((d.get("total", 0) for d in daily), default=1) or 1
        cp_dates = {c2.get("date") for c2 in cps}
        for d in daily:
            total = d.get("total", 0)
            bar = _bar(total, max_total, 40)
            marker = "  <-- CP" if d.get("date") in cp_dates else ""
            out.append(f"    {d.get('date', ''):<12} "
                       f"{_fmt_short(total):>10}  {bar}{marker}")
        out.append("")

    # ---- Section 4: markov ----
    out.append(sub)
    out.append("4. 马尔可夫状态转移 (Markov)")
    out.append(sub)
    # 2-state
    if m2:
        out.append("  二态 (Idle / Active)")
        P = m2.get("P", {}) or {}
        states = m2.get("states", ["I", "A"])
        # Header
        out.append("            " + "  ".join(f"-> {s}" for s in states))
        for s in states:
            row = P.get(s, {})
            cells = "  ".join(f"{row.get(s2, 0):.4f}" for s2 in states)
            out.append(f"      {s}  [  {cells}  ]")
        st = m2.get("stationary", {}) or {}
        dw = m2.get("dwell_h", {}) or {}
        out.append(f"    稳态 π    : I={st.get('I', 0):.3f}  A={st.get('A', 0):.3f}")
        out.append(f"    平均停留  : I={dw.get('I', 0):.2f} h  A={dw.get('A', 0):.2f} h")
        out.append(f"    黏性指数  : {_fmt_signed(m2.get('stickiness', 0))}")
        out.append("")

    # 3-state
    if m3:
        out.append(f"  三态 (Idle / Low<{_fmt_short(m3.get('threshold', 0))} / High≥)")
        P = m3.get("P", {}) or {}
        states = m3.get("states", ["I", "L", "H"])
        nps = m3.get("n_per_state", {}) or {}
        out.append("            " + "  ".join(f"-> {s}" for s in states) + "    n")
        for s in states:
            row = P.get(s, {})
            cells = "  ".join(f"{row.get(s2, 0):.4f}" for s2 in states)
            out.append(f"      {s}  [  {cells}  ]   {nps.get(s, 0)}")
        st = m3.get("stationary", {}) or {}
        dw = m3.get("dwell_h", {}) or {}
        out.append(f"    稳态 π    : "
                   f"I={st.get('I', 0):.3f}  L={st.get('L', 0):.3f}  H={st.get('H', 0):.3f}")
        out.append(f"    平均停留  : "
                   f"I={dw.get('I', 0):.2f} h  "
                   f"L={dw.get('L', 0):.2f} h  "
                   f"H={dw.get('H', 0):.2f} h")
        out.append("")

    # ---- Footer ----
    out.append(sub)
    out.append("方法清单: 描述统计/分位数 · Goh-Barabási B · Fano · Gini · Top-X% · "
               "ACF · Hour-of-day / Day-of-week · Run-length · Shannon 熵 · "
               "Binary segmentation + BIC · Markov 链 + 稳态")
    out.append(sub)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _md_acf_bar(value: float, max_abs: float, width: int = 20) -> str:
    if max_abs <= 0:
        return ""
    n = int(round(min(abs(value), max_abs) / max_abs * width))
    return "█" * max(1, n) if abs(value) > 0 else ""


def render_markdown(patterns: dict) -> str:
    p = patterns
    params = p.get("params", {})
    feats = p.get("features", {})
    seasonal = p.get("seasonal", {})
    m2 = p.get("markov_2", {})
    m3 = p.get("markov_3", {})
    cp = p.get("changepoint", {})
    profile = p.get("profile", {})

    daily_totals = cp.get("daily_totals", []) or []
    total_tokens = sum(int(d.get("total", 0)) for d in daily_totals)

    out: list[str] = []
    out.append("# Token 用量时序分析报告")
    out.append("")
    out.append(f"**生成时间**：{p.get('computed_at', '')}")
    out.append(f"**数据窗口**：最近 {params.get('days', '?')} 天 "
               f"（{params.get('bucket_sec', 0)//60} 分钟桶）+ "
               f"最近 {params.get('cp_days', '?')} 天（日总量）")
    out.append(f"**时区**：{params.get('tz', 'local')}")
    out.append(f"**数据源**：`~/.claude/projects/**/*.jsonl`")
    out.append("")
    out.append("---")
    out.append("")

    # TL;DR / Profile
    out.append("## TL;DR — 用户画像")
    out.append("")
    out.append(f"> {profile.get('summary', '')}")
    out.append("")
    for b in profile.get("bullets", []):
        out.append(f"- {b}")
    out.append("")
    out.append("---")
    out.append("")

    # Section 1
    out.append(f"## 1. {params.get('days', '?')} 天 {params.get('bucket_sec', 0)//60} 分钟桶 — 基础时序特征")
    out.append("")
    n_buckets = feats.get("n_buckets", 0)
    active_pct = feats.get("active_pct", 0.0)
    n_active = int(round(n_buckets * active_pct / 100)) if n_buckets else 0
    out.append("### 1.1 总体强度")
    out.append("")
    out.append("| 指标 | 值 |")
    out.append("|---|---|")
    out.append(f"| 总 token | {_fmt_short(total_tokens)} |")
    out.append(f"| 桶数 | {n_buckets} |")
    out.append(f"| 活跃桶占比 | {_fmt_pct(active_pct)} ({n_active}/{n_buckets}) |")
    out.append(f"| 静默桶占比 | {_fmt_pct(100 - active_pct)} ({n_buckets - n_active}/{n_buckets}) |")
    out.append("")

    stats = feats.get("stats", {})
    nz = feats.get("stats_active_only", {})
    out.append("### 1.2 描述性统计")
    out.append("")
    out.append("| 维度 | 含 0 桶 | 仅活跃桶 |")
    out.append("|---|---|---|")
    out.append(f"| mean | {_fmt_int(int(stats.get('mean', 0)))} | "
               f"{_fmt_int(int(nz.get('mean', 0)))} |")
    out.append(f"| median | {_fmt_int(int(stats.get('median', 0)))} | "
               f"{_fmt_int(int(nz.get('median', 0)))} |")
    out.append(f"| std | {_fmt_int(int(stats.get('std', 0)))} | "
               f"{_fmt_int(int(nz.get('std', 0)))} |")
    cv = feats.get("burstiness", {}).get("cv", 0)
    out.append(f"| **CV (变异系数)** | **{cv:.2f}** | — |")
    out.append(f"| max | {_fmt_int(stats.get('max', 0))} | — |")
    out.append("")
    out.append("**分位数（含 0 桶）**：")
    out.append("")
    out.append("```")
    out.append(f"p50  = {_fmt_int(stats.get('p50', 0)):>14}")
    out.append(f"p75  = {_fmt_int(stats.get('p75', 0)):>14}")
    out.append(f"p90  = {_fmt_int(stats.get('p90', 0)):>14}")
    out.append(f"p95  = {_fmt_int(stats.get('p95', 0)):>14}")
    out.append(f"p99  = {_fmt_int(stats.get('p99', 0)):>14}")
    out.append("```")
    out.append("")

    # Burstiness
    b = feats.get("burstiness", {})
    out.append("### 1.3 突发性指标")
    out.append("")
    out.append("| 指标 | 值 | 含义 |")
    out.append("|---|---|---|")
    out.append(f"| Goh-Barabási B | **{_fmt_signed(b.get('goh_barabasi_B', 0))}** | "
               f"0=泊松，+1=极端突发，−1=完全均匀 |")
    out.append(f"| Fano factor | {_fmt_short(b.get('fano_factor', 0))} | =1 时为泊松 |")
    out.append(f"| CV | {b.get('cv', 0):.3f} | 远高于 1.0 即为突发 |")
    out.append("")

    # Concentration
    c = feats.get("concentration", {})
    out.append("### 1.4 集中度")
    out.append("")
    out.append("| 指标 | 值 |")
    out.append("|---|---|")
    out.append(f"| Gini 系数 | **{c.get('gini', 0):.3f}** |")
    out.append(f"| top 5% 桶占总量 | {_fmt_pct(c.get('top5pct', 0))} |")
    out.append(f"| top 10% 桶占总量 | {_fmt_pct(c.get('top10pct', 0))} |")
    out.append("")

    # ACF
    out.append("### 1.5 自相关函数（ACF）")
    out.append("")
    out.append("```")
    acf = feats.get("acf", []) or []
    if acf:
        max_abs = max((abs(a.get("value", 0.0)) for a in acf), default=1.0) or 1.0
        for a in acf:
            v = a.get("value", 0.0)
            label = a.get("lag_label", "?")
            bar = _md_acf_bar(v, max_abs, 20)
            sign = "+" if v >= 0 else "−"
            tag = "" if v >= 0 else "  (反相)"
            out.append(f"lag {label:<6} : {sign}{abs(v):.3f}  {bar}{tag}")
    out.append("```")
    out.append("")

    # Hour-of-day
    out.append("### 1.6 一天时段画像（local tz）")
    out.append("")
    out.append("```")
    hours = seasonal.get("hour", []) or []
    if hours:
        max_mean = max((h.get("mean", 0) for h in hours), default=1.0) or 1.0
        for h in hours:
            mean = h.get("mean", 0.0)
            bar = _bar(mean, max_mean, 40)
            out.append(f"{h.get('hour', 0):02d}:00 {bar:<40} {_fmt_short(mean)}")
    out.append("```")
    out.append("")

    # DOW
    out.append("### 1.7 一周分布")
    out.append("")
    out.append("```")
    dows = seasonal.get("dow", []) or []
    if dows:
        max_mean = max((d.get("mean", 0) for d in dows), default=1.0) or 1.0
        for d in dows:
            mean = d.get("mean", 0.0)
            bar = _bar(mean, max_mean, 40)
            name = _DOW_CN.get(d.get("name", ""), d.get("name", "?"))
            out.append(f"{name}  {bar:<40} {_fmt_short(mean)}")
    out.append("```")
    out.append("")

    # Runs
    runs = feats.get("runs", {})
    a_run = runs.get("active", {})
    i_run = runs.get("idle", {})
    out.append("### 1.8 Session 与静默期")
    out.append("")
    out.append("| 类型 | 段数 | 中位长度 | 最长 | 平均 |")
    out.append("|---|---|---|---|---|")
    out.append(f"| 工作 session | {a_run.get('count', 0)} | "
               f"**{a_run.get('median_h', 0):.1f} h** | "
               f"{a_run.get('max_h', 0):.1f} h | "
               f"{a_run.get('mean_h', 0):.1f} h |")
    out.append(f"| 静默期 | {i_run.get('count', 0)} | "
               f"{i_run.get('median_h', 0):.1f} h | "
               f"{i_run.get('max_h', 0):.1f} h | "
               f"{i_run.get('mean_h', 0):.1f} h |")
    out.append("")

    out.append("### 1.9 信息论")
    out.append("")
    out.append(f"- 二元活跃/闲熵 H = **{feats.get('entropy_bits', 0):.4f} bits**")
    out.append("")
    out.append("---")
    out.append("")

    # Section 2: changepoint
    out.append(f"## 2. 变化点检测（最近 {params.get('cp_days', '?')} 天）")
    out.append("")
    out.append("**方法**：日总量上跑 binary segmentation + BIC penalty。")
    out.append("")
    segs = cp.get("segments", []) or []
    out.append("### 2.1 检测结果")
    out.append("")
    out.append("```")
    for i, s in enumerate(segs, 1):
        out.append(f"段 {i} ({s.get('start_date', '')} – {s.get('end_date', '')})："
                   f"{s.get('n_days', 0)} 天平均 {_fmt_short(s.get('mean', 0))} / 天")
    out.append("```")
    out.append("")
    cps = cp.get("changepoints", []) or []
    if cps:
        out.append(f"识别到 {len(cps)} 个变化点：")
        out.append("")
        for c2 in cps:
            pct = c2.get("pct_change", 0.0)
            pct_str = "inf" if isinstance(pct, float) and math.isinf(pct) else f"{pct:+.1f}%"
            out.append(f"- **{c2.get('date', '')}**: "
                       f"前 {_fmt_short(c2.get('before_mean', 0))}/天 → "
                       f"后 {_fmt_short(c2.get('after_mean', 0))}/天 ({pct_str})")
        out.append("")
    else:
        out.append("无显著变化点。")
        out.append("")

    # Daily totals chart
    out.append("### 2.2 日总量时序")
    out.append("")
    out.append("```")
    if daily_totals:
        max_total = max((d.get("total", 0) for d in daily_totals), default=1) or 1
        cp_dates = {c2.get("date") for c2 in cps}
        for d in daily_totals:
            total = d.get("total", 0)
            bar = _bar(total, max_total, 40)
            marker = "  <-- CP" if d.get("date") in cp_dates else ""
            out.append(f"{d.get('date', '')}  {_fmt_short(total):>10}  {bar}{marker}")
    out.append("```")
    out.append("")
    out.append("---")
    out.append("")

    # Section 3: Markov
    out.append("## 3. 马尔可夫状态转移矩阵")
    out.append("")
    out.append("### 3.1 二态 Markov（Idle / Active）")
    out.append("")
    out.append("```")
    P = m2.get("P", {}) or {}
    states = m2.get("states", ["I", "A"])
    out.append("            " + "  ".join(f"→{s}    " for s in states))
    for s in states:
        row = P.get(s, {})
        cells = "  ".join(f"{row.get(s2, 0):.4f}" for s2 in states)
        out.append(f"      {s}    [  {cells}  ]")
    out.append("```")
    out.append("")
    st = m2.get("stationary", {}) or {}
    dw = m2.get("dwell_h", {}) or {}
    out.append("| 指标 | 值 |")
    out.append("|---|---|")
    out.append(f"| 稳态 π(I) | {_fmt_pct(st.get('I', 0) * 100)} |")
    out.append(f"| 稳态 π(A) | {_fmt_pct(st.get('A', 0) * 100)} |")
    out.append(f"| Idle 平均停留 | **{dw.get('I', 0):.2f} h** |")
    out.append(f"| Active 平均停留 | **{dw.get('A', 0):.2f} h** |")
    out.append(f"| 黏性指数 | **{_fmt_signed(m2.get('stickiness', 0))}** |")
    out.append("")

    out.append(f"### 3.2 三态 Markov（Idle / Low<{_fmt_short(m3.get('threshold', 0))} / High≥）")
    out.append("")
    out.append("```")
    P = m3.get("P", {}) or {}
    states = m3.get("states", ["I", "L", "H"])
    nps = m3.get("n_per_state", {}) or {}
    out.append("            " + "  ".join(f"→{s}    " for s in states) + "    n")
    for s in states:
        row = P.get(s, {})
        cells = "  ".join(f"{row.get(s2, 0):.4f}" for s2 in states)
        out.append(f"      {s}    [  {cells}  ]   {nps.get(s, 0)}")
    out.append("```")
    out.append("")
    st = m3.get("stationary", {}) or {}
    dw = m3.get("dwell_h", {}) or {}
    out.append("| 状态 | 稳态 π | 平均停留 |")
    out.append("|---|---|---|")
    for s in states:
        out.append(f"| {s} | {_fmt_pct(st.get(s, 0) * 100)} | {dw.get(s, 0):.2f} h |")
    out.append("")
    out.append("---")
    out.append("")

    # Appendix
    out.append("## 附录 A — 用到的方法清单")
    out.append("")
    out.append("| 方法 | 来源学科 | 用途 |")
    out.append("|---|---|---|")
    rows = [
        ("描述统计 + 分位数", "经典", "强度、分布形状"),
        ("Goh-Barabási burstiness B", "复杂网络", "比泊松突发多少"),
        ("Fano factor", "统计/物理", "方差/均值比"),
        ("Gini 系数", "经济学", "集中度（不平等）"),
        ("Top-X% 集中度", "Pareto 分析", "重尾程度"),
        ("ACF（多 lag 自相关）", "经典时序", "短期持续 + 周期性"),
        ("Hour-of-day / Day-of-week 分解", "季节性分解", "昼夜 + 周节律"),
        ("Run-length 分析", "可靠性/生存分析", "session 长度 + 静默长度"),
        ("Shannon 熵", "信息论", "二元状态可预测性"),
        ("Binary segmentation + BIC", "变化点检测", "等级/趋势突变"),
        ("离散马尔可夫链 + 稳态分布", "概率论", "状态转移 + 长期占比"),
    ]
    for r in rows:
        out.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

_HTML_STYLE = """
  :root {
    --bg: #fafaf7;
    --panel: #ffffff;
    --text: #2a2a2a;
    --muted: #6b6b6b;
    --accent: #c2410c;
    --accent2: #1d4ed8;
    --good: #15803d;
    --warn: #b45309;
    --bad: #b91c1c;
    --grid: #e5e5e0;
    --bar: #fb923c;
    --bar-dim: #fed7aa;
    --code-bg: #f4f4ee;
    --font-sans: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', 'PingFang SC', 'Hiragino Sans GB', sans-serif;
    --font-mono: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1a1a;
      --panel: #232323;
      --text: #e6e6e0;
      --muted: #999991;
      --grid: #383834;
      --code-bg: #2a2a2a;
      --bar-dim: #7c2d12;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-sans);
    line-height: 1.6;
    padding: 32px 16px;
  }
  .container { max-width: 920px; margin: 0 auto; }
  h1 { font-size: 28px; margin: 0 0 8px; border-bottom: 2px solid var(--accent); padding-bottom: 8px; }
  h2 { font-size: 22px; margin: 36px 0 12px; color: var(--accent2); border-bottom: 1px solid var(--grid); padding-bottom: 4px; }
  h3 { font-size: 17px; margin: 22px 0 8px; }
  h4 { font-size: 15px; margin: 16px 0 4px; color: var(--muted); }
  .meta { color: var(--muted); font-size: 14px; margin-bottom: 24px; }
  .meta span { margin-right: 16px; }
  .tldr {
    background: var(--panel);
    border-left: 4px solid var(--accent);
    padding: 16px 20px;
    margin: 16px 0 32px;
    border-radius: 4px;
  }
  .tldr ul { margin: 8px 0; padding-left: 22px; }
  .panel {
    background: var(--panel);
    border: 1px solid var(--grid);
    border-radius: 6px;
    padding: 16px 20px;
    margin: 12px 0;
  }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
  th, td { padding: 6px 12px; text-align: left; border-bottom: 1px solid var(--grid); }
  th { background: var(--code-bg); font-weight: 600; }
  td.num { text-align: right; font-family: var(--font-mono); }
  .strong { font-weight: 700; color: var(--accent); }
  pre {
    background: var(--code-bg);
    padding: 12px 16px;
    border-radius: 4px;
    overflow-x: auto;
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.4;
    margin: 8px 0;
  }
  code {
    background: var(--code-bg);
    padding: 1px 6px;
    border-radius: 3px;
    font-family: var(--font-mono);
    font-size: 13px;
  }
  .bar-row {
    display: flex;
    align-items: center;
    font-family: var(--font-mono);
    font-size: 13px;
    gap: 8px;
    margin: 1px 0;
  }
  .bar-row .label { width: 60px; color: var(--muted); }
  .bar-row .val { width: 90px; text-align: right; }
  .bar-row .bar {
    height: 14px;
    background: var(--bar);
    border-radius: 2px;
    flex-shrink: 0;
  }
  .bar-row .bar.dim { background: var(--bar-dim); }
  .markov-table td.num { width: 70px; }
  .markov-table .self { background: rgba(251, 146, 60, 0.18); font-weight: 700; }
  .ascii { white-space: pre; }
  .callout {
    background: rgba(251, 146, 60, 0.08);
    border-left: 3px solid var(--accent);
    padding: 8px 14px;
    margin: 10px 0;
    font-size: 14px;
    border-radius: 0 4px 4px 0;
  }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 720px) { .grid2 { grid-template-columns: 1fr; } }
  .footer {
    margin-top: 48px;
    padding-top: 16px;
    border-top: 1px solid var(--grid);
    color: var(--muted);
    font-size: 13px;
  }
  .arrow-flow {
    font-family: var(--font-mono);
    background: var(--code-bg);
    padding: 16px;
    text-align: center;
    border-radius: 4px;
    line-height: 1.6;
  }
  /* Help tooltips */
  .help {
    position: relative;
    display: inline-flex;
    vertical-align: middle;
    margin-left: 6px;
    font-weight: 400;
  }
  .help-icon {
    width: 16px; height: 16px;
    border-radius: 50%;
    background: var(--muted);
    color: var(--bg);
    font-size: 11px;
    font-weight: 700;
    line-height: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: help;
    user-select: none;
    font-family: var(--font-sans);
    transition: background 0.15s;
  }
  .help-icon:hover,
  .help:focus-within .help-icon { background: var(--accent); color: #fff; }
  .help-tip {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    transform: translateX(-50%);
    width: 300px;
    background: var(--panel);
    border: 1px solid var(--grid);
    padding: 12px 14px;
    border-radius: 6px;
    font-size: 12px;
    line-height: 1.55;
    color: var(--text);
    box-shadow: 0 6px 24px rgba(0,0,0,0.18);
    text-align: left;
    white-space: normal;
    visibility: hidden;
    opacity: 0;
    transition: opacity 0.15s, visibility 0.15s;
    z-index: 100;
    pointer-events: none;
    font-family: var(--font-sans);
  }
  .help-tip strong {
    color: var(--accent);
    display: block;
    margin-bottom: 4px;
    font-size: 13px;
  }
  .help-tip code {
    background: var(--code-bg);
    padding: 1px 5px;
    border-radius: 3px;
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .help-tip::after {
    content: '';
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: var(--grid);
  }
  .help:hover .help-tip,
  .help:focus-within .help-tip {
    visibility: visible;
    opacity: 1;
  }
  .help.tip-right .help-tip { left: auto; right: 0; transform: none; }
  .help.tip-right .help-tip::after { left: auto; right: 12px; transform: none; }
  h2 .help-icon, h3 .help-icon { vertical-align: middle; }
"""


def _esc(s) -> str:
    return _html.escape(str(s))


def _scaled_width(value: float, max_value: float, max_px: int) -> int:
    if max_value <= 0 or value <= 0:
        return 1
    return max(1, int(round(min(value, max_value) / max_value * max_px)))


def _help(title: str, body: str, side: str = "") -> str:
    """Render a small help (?) icon with a hover tooltip. body may contain HTML."""
    cls = "help" + (f" {side}" if side else "")
    return (f'<span class="{cls}"><span class="help-icon" tabindex="0">?</span>'
            f'<span class="help-tip"><strong>{title}</strong>{body}</span></span>')


# Tooltip content — defined once, reused across the report
_TIPS = {
    "profile": _help(
        "用法画像", (
            "根据下面 6 个信号自动给你贴标签：<br>"
            "• <b>晚高峰</b>：18-21 点是峰值<br>"
            "• <b>突发型</b>：Burstiness B &gt; 0.15<br>"
            "• <b>强 24h 周期</b>：lag-12h ACF &lt; -0.1 或 lag-24h &gt; 0.1<br>"
            "• <b>无周周期</b>：lag-7d ACF 接近 0<br>"
            "• <b>高黏性</b>：Stickiness &gt; 0.3<br>"
            "• <b>High 沉浸</b>：P(H→H) &gt; P(L→L)"
        )
    ),
    "burstiness": _help(
        "突发性指标", (
            "<b>Goh-Barabási B</b>：来自复杂网络。范围 [-1, +1]。"
            "+1=极端突发（少数大爆发 + 大量空闲）；0=泊松（随机均匀）；-1=完全均匀。"
            "B &gt; 0.15 算明显突发。<br><br>"
            "<b>Fano factor</b>：方差/均值。=1 时为泊松；远高于 1 即突发。<br><br>"
            "<b>CV (变异系数)</b>：标准差/均值。&gt; 1 即偏突发。"
        )
    ),
    "concentration": _help(
        "集中度指标", (
            "<b>Gini 系数</b>：来自经济学，0=完全平均，1=极度集中。"
            "Token 用量 Gini &gt; 0.7 比中国收入差距还大。<br><br>"
            "<b>top 5%</b>：把所有桶按 token 数排序，最高的 5% 占总量的百分比。≥ 25% 即重尾分布。<br><br>"
            "<b>top 10%</b>：同上，看前 10% 桶的占比。"
        )
    ),
    "acf": _help(
        "Autocorrelation Function (ACF)", (
            "测量当前桶与 N 桶之前的桶有多相似。范围 [-1, +1]。<br><br>"
            "怎么读：<br>"
            "• <b>短 lag 高（lag-30min）</b> = 强持续性，下个 30 分钟还会用<br>"
            "• <b>lag-12h 负相关</b> = 反相，典型白天 vs 黑夜<br>"
            "• <b>lag-24h 正相关</b> = 日周期<br>"
            "• <b>lag-7d ≈ 0</b> = 没有周周期"
        )
    ),
    "entropy": _help(
        "Shannon 熵 (二元)", (
            "信息论里的「不确定性」度量。"
            "把每个桶分成「活跃/闲」两类后算二元 Shannon 熵。<br><br>"
            "范围 [0, 1] bit：<br>"
            "• <b>0 bit</b>：完全可预测（要么全活跃要么全闲）<br>"
            "• <b>1 bit</b>：完全随机（50/50 分布）"
        )
    ),
    "changepoint": _help(
        "变化点检测", (
            "找时间序列中「前后均值显著不同」的拐点。<br><br>"
            "方法：<b>binary segmentation + BIC penalty</b>。在每个候选位置切一刀，"
            "比较「切了之后 SSE 减少多少」 vs 「BIC 惩罚阈值」。"
            "超过阈值才接受。<br><br>"
            "<code>penalty = α · σ² · log(N)</code>，α 默认 2.0。"
        )
    ),
    "markov_2": _help(
        "二态马尔可夫链", (
            "把每个 30 分钟桶标为 <b>I</b>=Idle (0 token) 或 <b>A</b>=Active (&gt;0)，"
            "数所有相邻桶之间的状态转移。<br><br>"
            "<b>稳态分布 π</b>：长期在每个状态的时间占比。<br>"
            "<b>平均停留</b>：进入某状态后期望连续待多久 = <code>1/(1−P[s][s])</code> × bucket。<br>"
            "<b>黏性指数</b>：P(A→A) − π(A)。+0.3 = 强黏性。"
        )
    ),
    "markov_3": _help(
        "三态马尔可夫链", (
            "比二态多分一个 token 强度档：<br>"
            "• <b>I</b> = Idle（0 token）<br>"
            "• <b>L</b> = Low（&lt; 阈值）<br>"
            "• <b>H</b> = High（≥ 阈值）<br><br>"
            "阈值默认是<b>活跃桶 token 数的中位数</b>。<br><br>"
            "矩阵 P[from][to]：行=当前状态，列=下个 30 分钟的状态。"
            "对角线（自留概率）越高越「沉浸」。"
        )
    ),
}


def render_html(patterns: dict) -> str:
    p = patterns
    params = p.get("params", {})
    feats = p.get("features", {})
    seasonal = p.get("seasonal", {})
    m2 = p.get("markov_2", {})
    m3 = p.get("markov_3", {})
    cp = p.get("changepoint", {})
    profile = p.get("profile", {})

    daily_totals = cp.get("daily_totals", []) or []
    total_tokens = sum(int(d.get("total", 0)) for d in daily_totals)
    n_buckets = feats.get("n_buckets", 0)
    active_pct = feats.get("active_pct", 0.0)
    n_active = int(round(n_buckets * active_pct / 100)) if n_buckets else 0

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="zh-CN">')
    parts.append("<head>")
    parts.append('<meta charset="UTF-8">')
    parts.append("<title>Token 用量时序分析报告</title>")
    parts.append("<style>")
    parts.append(_HTML_STYLE)
    parts.append("</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append('<div class="container">')

    parts.append("  <h1>Token 用量时序分析报告</h1>")
    parts.append('  <div class="meta">')
    parts.append(f'    <span>生成时间：{_esc(p.get("computed_at", ""))}</span>')
    parts.append(f'    <span>窗口：{_esc(params.get("days", "?"))} 天 '
                 f'{_esc(params.get("bucket_sec", 0)//60)}min + '
                 f'{_esc(params.get("cp_days", "?"))} 天日总量</span>')
    parts.append(f'    <span>时区：{_esc(params.get("tz", "local"))}</span>')
    parts.append("  </div>")

    # TL;DR / Profile
    parts.append('  <div class="tldr">')
    parts.append(f'    <strong>TL;DR — </strong>{_esc(profile.get("summary", ""))}{_TIPS["profile"]}')
    parts.append("    <ul>")
    for b in profile.get("bullets", []):
        parts.append(f"      <li>{_esc(b)}</li>")
    parts.append("    </ul>")
    parts.append("  </div>")

    # Section 1
    parts.append(f'  <h2>1. {_esc(params.get("days", "?"))} 天 '
                 f'{_esc(params.get("bucket_sec", 0)//60)} 分钟桶 — 基础时序特征</h2>')

    parts.append("  <h3>1.1 总体强度</h3>")
    parts.append("  <table>")
    parts.append("    <tr><th>指标</th><th>值</th></tr>")
    parts.append(f"    <tr><td>总 token</td>"
                 f'<td class="num"><strong>{_esc(_fmt_short(total_tokens))}</strong></td></tr>')
    parts.append(f"    <tr><td>桶数</td>"
                 f'<td class="num">{n_buckets}</td></tr>')
    parts.append(f"    <tr><td>活跃桶占比</td>"
                 f'<td class="num">{_fmt_pct(active_pct)} ({n_active}/{n_buckets})</td></tr>')
    parts.append(f"    <tr><td>静默桶占比</td>"
                 f'<td class="num">{_fmt_pct(100 - active_pct)} '
                 f'({n_buckets - n_active}/{n_buckets})</td></tr>')
    parts.append("  </table>")

    # 1.2 Stats
    stats = feats.get("stats", {})
    nz = feats.get("stats_active_only", {})
    parts.append("  <h3>1.2 描述性统计</h3>")
    parts.append("  <table>")
    parts.append("    <tr><th>维度</th><th>含 0 桶</th><th>仅活跃桶</th></tr>")
    parts.append(f"    <tr><td>mean</td>"
                 f'<td class="num">{_fmt_int(int(stats.get("mean", 0)))}</td>'
                 f'<td class="num">{_fmt_int(int(nz.get("mean", 0)))}</td></tr>')
    parts.append(f"    <tr><td>median</td>"
                 f'<td class="num">{_fmt_int(int(stats.get("median", 0)))}</td>'
                 f'<td class="num">{_fmt_int(int(nz.get("median", 0)))}</td></tr>')
    parts.append(f"    <tr><td>std</td>"
                 f'<td class="num">{_fmt_int(int(stats.get("std", 0)))}</td>'
                 f'<td class="num">{_fmt_int(int(nz.get("std", 0)))}</td></tr>')
    cv = feats.get("burstiness", {}).get("cv", 0)
    parts.append(f'    <tr><td><strong>CV</strong></td>'
                 f'<td class="num strong">{cv:.2f}</td><td class="num">—</td></tr>')
    parts.append(f"    <tr><td>max</td>"
                 f'<td class="num">{_fmt_int(stats.get("max", 0))}</td>'
                 f'<td class="num">—</td></tr>')
    parts.append("  </table>")
    parts.append(f"  <p>分位数（含 0 桶）：<code>"
                 f"p50={_fmt_short(stats.get('p50', 0))}, "
                 f"p75={_fmt_short(stats.get('p75', 0))}, "
                 f"p90={_fmt_short(stats.get('p90', 0))}, "
                 f"p95={_fmt_short(stats.get('p95', 0))}, "
                 f"p99={_fmt_short(stats.get('p99', 0))}</code></p>")

    # 1.3 Burstiness
    b = feats.get("burstiness", {})
    parts.append(f'  <h3>1.3 突发性指标{_TIPS["burstiness"]}</h3>')
    parts.append("  <table>")
    parts.append("    <tr><th>指标</th><th>值</th><th>含义</th></tr>")
    parts.append(f"    <tr><td>Goh-Barabási B</td>"
                 f'<td class="num strong">{_fmt_signed(b.get("goh_barabasi_B", 0))}</td>'
                 f"<td>0=泊松，+1=极端突发</td></tr>")
    parts.append(f"    <tr><td>Fano factor</td>"
                 f'<td class="num">{_fmt_short(b.get("fano_factor", 0))}</td>'
                 f"<td>=1 时为泊松</td></tr>")
    parts.append(f"    <tr><td>CV</td>"
                 f'<td class="num">{b.get("cv", 0):.3f}</td>'
                 f"<td>远高于 1.0 即突发</td></tr>")
    parts.append("  </table>")

    # 1.4 Concentration
    c = feats.get("concentration", {})
    parts.append(f'  <h3>1.4 集中度{_TIPS["concentration"]}</h3>')
    parts.append("  <table>")
    parts.append("    <tr><th>指标</th><th>值</th></tr>")
    parts.append(f'    <tr><td>Gini 系数</td>'
                 f'<td class="num strong">{c.get("gini", 0):.3f}</td></tr>')
    parts.append(f"    <tr><td>top 5%</td>"
                 f'<td class="num">{_fmt_pct(c.get("top5pct", 0))}</td></tr>')
    parts.append(f"    <tr><td>top 10%</td>"
                 f'<td class="num">{_fmt_pct(c.get("top10pct", 0))}</td></tr>')
    parts.append("  </table>")

    # 1.5 ACF
    parts.append(f'  <h3>1.5 自相关函数（ACF）{_TIPS["acf"]}</h3>')
    parts.append('  <div class="panel">')
    acf = feats.get("acf", []) or []
    if acf:
        max_abs = max((abs(a.get("value", 0.0)) for a in acf), default=1.0) or 1.0
        for a in acf:
            v = a.get("value", 0.0)
            label = a.get("lag_label", "?")
            width = _scaled_width(abs(v), max_abs, 400)
            if v >= 0:
                bar_cls = "bar"
                val_html = f'<span class="val">{_fmt_signed(v)}</span>'
            else:
                bar_cls = "bar dim"
                val_html = (f'<span class="val" style="color:var(--bad)">'
                            f'−{abs(v):.3f}</span>')
            parts.append(f'    <div class="bar-row">'
                         f'<span class="label">lag {_esc(label)}</span>'
                         f'{val_html}'
                         f'<div class="{bar_cls}" style="width: {width}px;"></div>'
                         f'</div>')
    parts.append("  </div>")

    # 1.6 Hour
    parts.append("  <h3>1.6 一天时段画像（local tz）</h3>")
    parts.append('  <div class="panel">')
    hours = seasonal.get("hour", []) or []
    if hours:
        max_mean = max((h.get("mean", 0) for h in hours), default=1.0) or 1.0
        for h in hours:
            mean = h.get("mean", 0.0)
            width = _scaled_width(mean, max_mean, 400)
            cls = "bar" if mean > 0 else "bar dim"
            parts.append(f'    <div class="bar-row">'
                         f'<span class="label">{h.get("hour", 0):02d}:00</span>'
                         f'<span class="val">{_esc(_fmt_short(mean))}</span>'
                         f'<div class="{cls}" style="width: {width}px;"></div>'
                         f'</div>')
    parts.append("  </div>")

    # 1.7 DOW
    parts.append("  <h3>1.7 一周分布</h3>")
    parts.append('  <div class="panel">')
    dows = seasonal.get("dow", []) or []
    if dows:
        max_mean = max((d.get("mean", 0) for d in dows), default=1.0) or 1.0
        for d in dows:
            mean = d.get("mean", 0.0)
            width = _scaled_width(mean, max_mean, 400)
            name = _DOW_CN.get(d.get("name", ""), d.get("name", "?"))
            cls = "bar" if mean > 0 else "bar dim"
            parts.append(f'    <div class="bar-row">'
                         f'<span class="label">{_esc(name)}</span>'
                         f'<span class="val">{_esc(_fmt_short(mean))}</span>'
                         f'<div class="{cls}" style="width: {width}px;"></div>'
                         f'</div>')
    parts.append("  </div>")

    # 1.8 Sessions
    runs = feats.get("runs", {})
    a_run = runs.get("active", {})
    i_run = runs.get("idle", {})
    parts.append("  <h3>1.8 Session 与静默期</h3>")
    parts.append("  <table>")
    parts.append("    <tr><th>类型</th><th>段数</th><th>中位</th><th>最长</th><th>平均</th></tr>")
    parts.append(f"    <tr><td>工作 session</td>"
                 f'<td class="num">{a_run.get("count", 0)}</td>'
                 f'<td class="num strong">{a_run.get("median_h", 0):.1f} h</td>'
                 f'<td class="num">{a_run.get("max_h", 0):.1f} h</td>'
                 f'<td class="num">{a_run.get("mean_h", 0):.1f} h</td></tr>')
    parts.append(f"    <tr><td>静默期</td>"
                 f'<td class="num">{i_run.get("count", 0)}</td>'
                 f'<td class="num">{i_run.get("median_h", 0):.1f} h</td>'
                 f'<td class="num">{i_run.get("max_h", 0):.1f} h</td>'
                 f'<td class="num">{i_run.get("mean_h", 0):.1f} h</td></tr>')
    parts.append("  </table>")

    parts.append(f'  <h3>1.9 信息论{_TIPS["entropy"]}</h3>')
    parts.append(f"  <p>二元活跃/闲熵 H = "
                 f"<strong>{feats.get('entropy_bits', 0):.4f} bits</strong></p>")

    # ---- Section 2: changepoint ----
    parts.append(f'  <h2>2. 变化点检测（最近 {_esc(params.get("cp_days", "?"))} 天）{_TIPS["changepoint"]}</h2>')
    parts.append("  <p><strong>方法</strong>：日总量上跑 binary segmentation + BIC penalty。</p>")
    parts.append("  <h3>2.1 检测结果</h3>")
    parts.append("  <table>")
    parts.append("    <tr><th>段</th><th>区间</th><th>天数</th><th>平均/天</th></tr>")
    segs = cp.get("segments", []) or []
    for i, s in enumerate(segs, 1):
        parts.append(f"    <tr><td>段 {i}</td>"
                     f"<td>{_esc(s.get('start_date', ''))} – "
                     f"{_esc(s.get('end_date', ''))}</td>"
                     f'<td class="num">{s.get("n_days", 0)}</td>'
                     f'<td class="num strong">{_esc(_fmt_short(s.get("mean", 0)))}</td></tr>')
    parts.append("  </table>")
    cps = cp.get("changepoints", []) or []
    if cps:
        parts.append(f"  <p>识别到 {len(cps)} 个变化点：</p>")
        parts.append("  <ul>")
        for c2 in cps:
            pct = c2.get("pct_change", 0.0)
            pct_str = "inf" if isinstance(pct, float) and math.isinf(pct) else f"{pct:+.1f}%"
            parts.append(f"    <li><strong>{_esc(c2.get('date', ''))}</strong>: "
                         f"前 {_esc(_fmt_short(c2.get('before_mean', 0)))}/天 → "
                         f"后 {_esc(_fmt_short(c2.get('after_mean', 0)))}/天 "
                         f"({_esc(pct_str)})</li>")
        parts.append("  </ul>")

    # 2.2 Daily totals chart
    parts.append("  <h3>2.2 日总量时序</h3>")
    parts.append('  <div class="panel">')
    if daily_totals:
        max_total = max((d.get("total", 0) for d in daily_totals), default=1) or 1
        cp_dates = {c2.get("date") for c2 in cps}
        for d in daily_totals:
            total = d.get("total", 0)
            width = _scaled_width(total, max_total, 1000)
            is_cp = d.get("date") in cp_dates
            if is_cp:
                bar_html = (f'<div class="bar" '
                            f'style="width: {width}px; background: var(--accent);"></div>')
                val_html = f'<span class="val strong">{_esc(_fmt_short(total))}</span>'
            else:
                cls = "bar" if total > 0 else "bar dim"
                bar_html = f'<div class="{cls}" style="width: {width}px;"></div>'
                val_html = f'<span class="val">{_esc(_fmt_short(total))}</span>'
            parts.append(f'    <div class="bar-row">'
                         f'<span class="label">{_esc(d.get("date", ""))}</span>'
                         f'{val_html}{bar_html}</div>')
    parts.append("  </div>")

    # ---- Section 3: Markov ----
    parts.append("  <h2>3. 马尔可夫状态转移矩阵</h2>")

    # 3.1 two-state
    parts.append(f'  <h3>3.1 二态 Markov（Idle / Active）{_TIPS["markov_2"]}</h3>')
    parts.append('  <table class="markov-table">')
    P = m2.get("P", {}) or {}
    states = m2.get("states", ["I", "A"])
    parts.append("    <tr><th></th>" +
                 "".join(f"<th>→ {_esc(s)}</th>" for s in states) + "</tr>")
    for s in states:
        row = P.get(s, {})
        cells = []
        for s2 in states:
            cls = "num self" if s == s2 else "num"
            cells.append(f'<td class="{cls}">{row.get(s2, 0):.2f}</td>')
        parts.append(f"    <tr><td><strong>{_esc(s)}</strong></td>"
                     + "".join(cells) + "</tr>")
    parts.append("  </table>")
    st = m2.get("stationary", {}) or {}
    dw = m2.get("dwell_h", {}) or {}
    parts.append("  <table>")
    parts.append("    <tr><th>指标</th><th>值</th></tr>")
    parts.append(f"    <tr><td>稳态 π(I)</td>"
                 f'<td class="num">{_fmt_pct(st.get("I", 0) * 100)}</td></tr>')
    parts.append(f"    <tr><td>稳态 π(A)</td>"
                 f'<td class="num">{_fmt_pct(st.get("A", 0) * 100)}</td></tr>')
    parts.append(f"    <tr><td>Idle 平均停留</td>"
                 f'<td class="num strong">{dw.get("I", 0):.2f} h</td></tr>')
    parts.append(f"    <tr><td>Active 平均停留</td>"
                 f'<td class="num strong">{dw.get("A", 0):.2f} h</td></tr>')
    parts.append(f"    <tr><td>黏性指数</td>"
                 f'<td class="num strong">{_fmt_signed(m2.get("stickiness", 0))}</td></tr>')
    parts.append("  </table>")

    # 3.2 three-state
    parts.append(f"  <h3>3.2 三态 Markov（Idle / Low&lt;"
                 f"{_esc(_fmt_short(m3.get('threshold', 0)))} / High≥）{_TIPS['markov_3']}</h3>")
    parts.append('  <table class="markov-table">')
    P = m3.get("P", {}) or {}
    states = m3.get("states", ["I", "L", "H"])
    nps = m3.get("n_per_state", {}) or {}
    parts.append("    <tr><th></th>"
                 + "".join(f"<th>→ {_esc(s)}</th>" for s in states)
                 + "<th>n</th></tr>")
    for s in states:
        row = P.get(s, {})
        cells = []
        for s2 in states:
            cls = "num self" if s == s2 else "num"
            cells.append(f'<td class="{cls}">{row.get(s2, 0):.2f}</td>')
        parts.append(f"    <tr><td><strong>{_esc(s)}</strong></td>"
                     + "".join(cells)
                     + f'<td class="num">{nps.get(s, 0)}</td></tr>')
    parts.append("  </table>")
    st = m3.get("stationary", {}) or {}
    dw = m3.get("dwell_h", {}) or {}
    parts.append("  <table>")
    parts.append("    <tr><th>状态</th><th>稳态 π</th><th>平均停留</th></tr>")
    for s in states:
        parts.append(f"    <tr><td>{_esc(s)}</td>"
                     f'<td class="num">{_fmt_pct(st.get(s, 0) * 100)}</td>'
                     f'<td class="num">{dw.get(s, 0):.2f} h</td></tr>')
    parts.append("  </table>")

    # Appendix
    parts.append("  <h2>附录 A — 用到的方法</h2>")
    parts.append("  <table>")
    parts.append("    <tr><th>方法</th><th>来源</th><th>用途</th></tr>")
    rows = [
        ("描述统计 + 分位数", "经典", "强度、分布形状"),
        ("Goh-Barabási burstiness B", "复杂网络", "比泊松突发多少"),
        ("Fano factor", "统计/物理", "方差/均值比"),
        ("Gini 系数", "经济学", "集中度（不平等）"),
        ("Top-X% 集中度", "Pareto 分析", "重尾程度"),
        ("ACF 自相关", "经典时序", "短期持续 + 周期性"),
        ("Hour-of-day / Day-of-week", "季节性分解", "昼夜 + 周节律"),
        ("Run-length 分析", "可靠性/生存分析", "session 长度 + 静默长度"),
        ("Shannon 熵", "信息论", "二元状态可预测性"),
        ("Binary segmentation + BIC", "变化点检测", "等级/趋势突变"),
        ("离散马尔可夫链 + 稳态分布", "概率论", "状态转移 + 长期占比"),
    ]
    for r in rows:
        parts.append(f"    <tr><td>{_esc(r[0])}</td>"
                     f"<td>{_esc(r[1])}</td>"
                     f"<td>{_esc(r[2])}</td></tr>")
    parts.append("  </table>")

    parts.append('  <div class="footer">')
    parts.append(f"    生成于 {_esc(p.get('computed_at', ''))} · "
                 f"{n_buckets} 桶 · {len(daily_totals)} 天日总量 · "
                 f"时区 {_esc(params.get('tz', 'local'))}")
    parts.append("  </div>")

    parts.append("</div>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
