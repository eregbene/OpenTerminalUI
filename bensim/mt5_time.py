"""ICMarkets (and most "New York close" MT5 brokers) server time = UTC+2, or UTC+3 while the US is on
daylight saving (2nd Sunday of March -> 1st Sunday of November). Verified 2026-10-09 against Dukascopy UTC
candles across the 2025/2026 DST switch weeks (median close difference 0.1-0.3 pip at this offset).
NOT Europe/Athens: Athens switches on EU dates, which is 1 hour off for ~3 weeks a year."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

_NY = ZoneInfo("America/New_York")


def server_offset_hours(utc_ts: int) -> int:
    return 3 if datetime.fromtimestamp(utc_ts, timezone.utc).astimezone(_NY).dst() else 2


def server_to_utc(server_ts: np.ndarray) -> np.ndarray:
    """Vectorised: decide the offset from (server time - 2h), which is the true UTC or 1h after it --
    both on the same side of a DST switch, because brokers switch on a weekend with the market closed."""
    approx = server_ts - 7200
    days = np.unique(approx // 86400)
    off = {int(d): server_offset_hours(int(d) * 86400 + 43200) for d in days}
    return server_ts - np.array([off[int(d)] for d in approx // 86400], dtype=np.int64) * 3600


def utc_to_server(utc_ts: int) -> int:
    return int(utc_ts) + server_offset_hours(int(utc_ts)) * 3600


def rollover_nights(t_in: int, t_out: int, triple_day: int = 2) -> int:
    """Swap nights between two SERVER-time epochs (rollover = server midnight): Mon/Tue/Thu/Fri nights count 1,
    the Wednesday night (MT5 swap_rollover3days, 0=Mon) counts 3, Saturday/Sunday nights 0 -> 7 per full week."""
    n = 0
    for day in range(int(t_in) // 86400, int(t_out) // 86400):
        wd = (day + 3) % 7  # 1970-01-01 was a Thursday -> Monday = 0
        n += 0 if wd >= 5 else (3 if wd == triple_day else 1)
    return n
