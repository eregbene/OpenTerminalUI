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
