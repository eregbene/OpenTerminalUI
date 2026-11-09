"""News blackout for ALL live entries (user approved 2026-11-05): no new trade from 60 min before to 60 min after a
high-impact Forex Factory event of either currency (XAU -> USD). Open trades are not touched.
Evidence: 5y portfolio test 2021-11..2026-11 -- trades near high-impact news were negative in 5 of 6 years; blocking
them moved F03 portfolio from -0.006R to +0.012R (not significant; a safety rule, not an edge).

BENSIM_NEWS=off disables; BENSIM_NEWS_BEFORE / BENSIM_NEWS_AFTER (minutes) set the window.
Fail-closed: no calendar for the current week, or the DB unreachable -> entries blocked (reason says why).
The calendar (bensim.events, Forex Factory) is refreshed once per UTC day in a background process.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from . import db

ON = os.environ.get("BENSIM_NEWS", "on") != "off"
BEFORE, AFTER = int(os.environ.get("BENSIM_NEWS_BEFORE", "60")), int(os.environ.get("BENSIM_NEWS_AFTER", "60"))
_cache: dict = {"at": 0.0, "events": None, "week_ok": False}


def _load(now: float) -> None:
    t = datetime.fromtimestamp(now, timezone.utc)
    monday = (t - timedelta(days=t.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    with db.connect() as c:
        ev = c.execute("""select extract(epoch from ts)::bigint, currency, name from economic_events
                          where impact='high' and ts between %s and %s""",
                       (t - timedelta(minutes=AFTER + 30), t + timedelta(days=2))).fetchall()
        week = c.execute("select count(*) from economic_events where ts >= %s and ts < %s",
                         (monday, monday + timedelta(days=7))).fetchone()[0]
    _cache.update(at=now, events=ev, week_ok=week > 0)


def blocked(symbol: str, now: float | None = None) -> str | None:
    """None = entry allowed; else the reason (e.g. 'NEWS USD Non-Farm Employment Change 13:30Z')."""
    if not ON:
        return None
    now = time.time() if now is None else now
    try:
        if now - _cache["at"] > 600 or _cache["events"] is None:
            _load(now)
    except Exception as exc:  # fail closed
        return f"NEWS_CALENDAR_UNAVAILABLE {type(exc).__name__}"
    if not _cache["week_ok"]:
        return "NEWS_CALENDAR_MISSING (no events stored for this week)"
    ccys = {"USD" if c == "XAU" else c for c in (symbol[:3], symbol[3:6])}
    for ts, cur, name in _cache["events"]:
        if cur in ccys and now - AFTER * 60 <= ts <= now + BEFORE * 60:
            return f"NEWS {cur} {name} {datetime.fromtimestamp(ts, timezone.utc):%H:%M}Z"
    return None


def refresh_daily(state, log) -> None:
    """Once per UTC day (after 00:10): scrape this week + next week into economic_events, in a background process."""
    t = datetime.now(timezone.utc)
    if not ON or (t.hour == 0 and t.minute < 10) or state("news_calendar_day") == t.date().isoformat():
        return
    state("news_calendar_day", t.date().isoformat())
    monday = (t - timedelta(days=t.weekday())).date().isoformat()
    out = open(os.path.join(os.path.dirname(__file__), "..", "logs", "news_calendar.log"), "a")  # noqa: SIM115 (child keeps it)
    subprocess.Popen([sys.executable, "-m", "bensim.events", "--start", monday, "--weeks", "2"], stdout=out, stderr=subprocess.STDOUT,
                     cwd=os.path.join(os.path.dirname(__file__), ".."), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _cache["at"] = 0.0  # reload soon
    log("INFO", f"NEWS calendar refresh started (weeks from {monday})")


if __name__ == "__main__":  # self-check against the stored calendar: NFP Fri 2026-11-06 13:30Z
    nfp = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc).timestamp()
    assert blocked("EURUSD", nfp - 59 * 60) and blocked("XAUUSD", nfp + 59 * 60), "USD pairs blocked around NFP"
    _cache["at"] = 0.0
    assert blocked("EURGBP", nfp) is None, "no EUR/GBP high event at NFP"
    _cache["at"] = 0.0
    assert blocked("EURUSD", nfp + 61 * 60) is None, "window ends 60 min after"
    print("ok", blocked("USDCAD", nfp))
