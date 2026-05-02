#!/usr/bin/env python3
"""macOS menubar widget for Claude Code token usage.

Run:   python3 ~/.claude/skills/token-usage/menubar/app.py
Deps:  pip3 install rumps
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone

try:
    import rumps
    from PyObjCTools import AppHelper
except ImportError:
    sys.stderr.write("rumps is not installed. Install with:\n  pip3 install rumps\n")
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))
import token_stats as ts  # noqa: E402

DASHBOARD_SCRIPT = os.path.join(SKILL_ROOT, "dashboard", "server.py")
DASHBOARD_PORT = 8787
DASHBOARD_URL = f"http://127.0.0.1:{DASHBOARD_PORT}/"

INTERVALS = [("10s", 10), ("30s", 30), ("1m", 60), ("5m", 300)]
DEFAULT_INTERVAL_SEC = 30

_EPOCH       = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FAR_FUTURE  = datetime(9999, 1, 1, tzinfo=timezone.utc)


def fmt_short(n: float) -> str:
    n = int(n)
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:     return f"{n/1_000_000:.2f}M"
    if n >= 1_000:         return f"{n/1_000:.1f}K"
    return str(n)


# ---- corpus cache: same mtime-signature pattern as dashboard ----
_cache_lock = threading.Lock()
_cache = {"sig": None, "records": []}


def _signature():
    out = []
    for f in ts.list_transcript_files():
        try:
            st = os.stat(f)
            out.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    out.sort()
    return tuple(out)


def get_records():
    sig = _signature()
    with _cache_lock:
        if _cache["sig"] == sig:
            return _cache["records"]
    records = ts.scan_records(_EPOCH, _FAR_FUTURE)
    with _cache_lock:
        _cache["records"] = records
        _cache["sig"] = sig
    return records


def filter_named(records, name: str):
    s, e, _ = ts.resolve_named_range(name)
    s_utc = s.astimezone(timezone.utc)
    e_utc = e.astimezone(timezone.utc)
    return [r for r in records if s_utc <= r.t_utc < e_utc]


def dashboard_running() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", DASHBOARD_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


class TokenUsageApp(rumps.App):
    def __init__(self):
        # Plain text only — emojis in NSStatusItem can render as an icon
        # on top of the text, making it look like the title is wrapping.
        super().__init__("Claude …", quit_button="Quit")

        self.today_item     = rumps.MenuItem("Today:      —")
        self.yesterday_item = rumps.MenuItem("Yesterday:  —")
        self.week_item      = rumps.MenuItem("This week:  —")
        self.month_item     = rumps.MenuItem("This month: —")
        self.rate_item      = rumps.MenuItem("Last 1h:    —")
        self.usd_item       = rumps.MenuItem("Est $ today: —")
        self.updated_item   = rumps.MenuItem("Updated: —")

        self.interval_menu = rumps.MenuItem("Refresh interval")
        for label, sec in INTERVALS:
            mi = rumps.MenuItem(label, callback=self._make_interval_cb(sec))
            if sec == DEFAULT_INTERVAL_SEC:
                mi.state = 1
            self.interval_menu.add(mi)

        self.menu = [
            self.today_item, self.yesterday_item, self.week_item, self.month_item,
            None,
            self.rate_item, self.usd_item,
            None,
            self.updated_item,
            self.interval_menu,
            rumps.MenuItem("Refresh now",     callback=self.refresh_now),
            rumps.MenuItem("Open dashboard",  callback=self.open_dashboard),
        ]

        self.timer = rumps.Timer(self._on_tick, DEFAULT_INTERVAL_SEC)
        self.timer.start()
        # Warm the cache + first paint on a background thread so the
        # menubar appears immediately rather than blocking ~1-2s on first scan.
        threading.Thread(target=self._refresh_safe, daemon=True).start()

    def _make_interval_cb(self, sec: int):
        def cb(sender):
            for item in self.interval_menu.values():
                item.state = 0
            sender.state = 1
            self.timer.stop()
            self.timer.interval = sec
            self.timer.start()
            self._on_tick(None)  # refresh immediately on interval change
        return cb

    # Timer fires on the main thread. We do the I/O on a background thread
    # and the quick UI update back on the main thread (rumps polls main loop).
    def _on_tick(self, _):
        threading.Thread(target=self._refresh_safe, daemon=True).start()

    def _refresh_safe(self):
        try:
            state = self._compute_state()
        except Exception as e:
            AppHelper.callAfter(self._apply_error, str(e))
            return
        AppHelper.callAfter(self._apply_state, state)

    def _compute_state(self) -> dict:
        """I/O and aggregation only. Safe to run on any thread."""
        records = get_records()
        today_records = filter_named(records, "today")
        today_total   = sum(r.total for r in today_records)
        today_usd     = ts.usd_estimate(today_records)

        y_total = sum(r.total for r in filter_named(records, "yesterday"))
        w_total = sum(r.total for r in filter_named(records, "this-week"))
        m_total = sum(r.total for r in filter_named(records, "this-month"))

        now = datetime.now(ts.local_tz())
        h_s = (now - timedelta(hours=1)).astimezone(timezone.utc)
        h_e = now.astimezone(timezone.utc)
        rate_tokens = sum(r.total for r in records if h_s <= r.t_utc < h_e)
        rate_per_min = int(rate_tokens / 60) if rate_tokens else 0

        return {
            "today": today_total, "yesterday": y_total,
            "week": w_total, "month": m_total,
            "rate_per_min": rate_per_min,
            "usd_today": today_usd,
            "now": now,
        }

    def _apply_state(self, s: dict):
        """UI writes — must run on the main thread."""
        self.title = fmt_short(s["today"])
        self.today_item.title     = f"Today:      {fmt_short(s['today'])}"
        self.yesterday_item.title = f"Yesterday:  {fmt_short(s['yesterday'])}"
        self.week_item.title      = f"This week:  {fmt_short(s['week'])}"
        self.month_item.title     = f"This month: {fmt_short(s['month'])}"
        self.rate_item.title      = f"Last 1h:    {fmt_short(s['rate_per_min'])} tok/min"
        self.usd_item.title       = f"Est $ today: ${s['usd_today']:.2f}"
        self.updated_item.title   = f"Updated: {s['now'].strftime('%H:%M:%S')}"

    def _apply_error(self, msg: str):
        self.title = "Claude ×"
        self.updated_item.title = ("Error: " + msg)[:60]

    def refresh_now(self, _):
        self._on_tick(None)

    def open_dashboard(self, _):
        if not dashboard_running():
            subprocess.Popen(
                [sys.executable, DASHBOARD_SCRIPT, "--no-open"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(15):
                if dashboard_running():
                    break
                threading.Event().wait(0.2)
        webbrowser.open(DASHBOARD_URL)


if __name__ == "__main__":
    TokenUsageApp().run()
