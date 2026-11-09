"""Gold NY "fake move" (Reddit r/Daytrading 1mw7p8z, u/Otherwise_Amoeba_767, user ask 2026-10-30) -- 3-month test.

    python -m bensim.gold_fakemove [start] [end]     # default 2026-07-26 .. 2026-10-26 (DB end)

His rules (from his replies): London+NY only; trend from 15 min or higher; "enter on the fake move during 9:30am to
10:15am NY", 1-min entry; no trades until 15 min after news; add on and trail (not modelled).
Our reading (frozen 2026-10-30 before any result was seen) -- "fake" is undefined in the post, so this is OURS:
  * XAUUSD M5 (DB, bid; no M1 in the DB). NY time via America/New_York (DST handled)
  * trend = M15 swing trend (strategy.trend) at 9:30 NY; 0 -> no trade
  * fake move (uptrend): after 9:30 price trades at least 0.5 x ATR(14, M15) BELOW the 9:30 open (against the
    trend); entry when an M5 bar then CLOSES back above the 9:30 open, before 10:15 NY. Mirror for downtrend
  * entry at that close (buy on the ask, sell on the bid); stop = the fake move's extreme -/+ spread; target 2R;
    open at 16:00 NY -> closed. One trade per day
  * news: no entry if a high-impact USD event is within the 15 minutes before the entry bar's close
  * costs as the backtest: spread model, $7/lot commission, 0.1 pip slip on stops; a bar touching both = stop
Look-only (not the rule): no trend filter; any dip counts (0 x ATR).
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from .backtest import Exec, _news_times, _spread, load_m5, resample
from .pattern_check import stat
from .strategy import atr, pip, trend

SYM = "XAUUSD"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
START = ARGS[0] if ARGS else "2026-07-26"
END = ARGS[1] if len(ARGS) > 1 else "2026-10-26"
COMM_PER_OZ = 7.0 / 100


def trades(m5: dict, t0: int, ex: Exec, use_trend=True, depth=0.5) -> list[tuple[int, float]]:
    m15 = resample(m5, 900)
    tr15, a15 = trend(m15), atr(m15)
    news = _news_times(SYM)
    ny = pd.to_datetime(m5["t"], unit="s", utc=True).tz_convert("America/New_York")
    mins = (ny.hour * 60 + ny.minute).to_numpy()
    nyday = ny.normalize().to_numpy()
    out = []
    for k in np.flatnonzero((mins == 9 * 60 + 30) & (m5["t"] >= t0)):
        i15 = int(np.searchsorted(m15["t"] + 900, m5["t"][k], side="right")) - 1  # last M15 closed by 9:30
        d = int(tr15[i15]) if use_trend else 0
        o930 = m5["open"][k]
        ext = None
        for i in range(k, len(m5["t"])):
            if nyday[i] != nyday[k] or mins[i] >= 10 * 60 + 15:
                break
            for dd in ((d,) if use_trend else (1, -1)):
                if dd == 0:
                    continue
                far = m5["low"][k:i + 1].min() if dd == 1 else m5["high"][k:i + 1].max()
                if (o930 - far) * dd < depth * a15[i15]:
                    continue
                c = m5["close"][i]
                if (c - o930) * dd <= 0:
                    continue
                now = int(m5["t"][i]) + 300
                if np.any((news <= now) & (news > now - 900)):
                    continue
                ext = (i, dd, far)
                break
            if ext:
                break
        if not ext:
            continue
        i, dd, far = ext
        now = int(m5["t"][i]) + 300
        sp = _spread(SYM, now, ex)
        entry = m5["close"][i] + (sp if dd == 1 else 0)
        sl = far - dd * sp
        risk = (entry - sl) * dd
        if risk <= 0:
            continue
        out.append((now, simulate(m5, i + 1, dd, entry, sl, risk, nyday, mins, ex)))
    return out


def simulate(m5, j, d, entry, sl, risk, nyday, mins, ex) -> float:
    tp, slip = entry + d * 2 * risk, ex.slippage_pips * pip(SYM)
    for i in range(j, len(m5["t"])):
        sp = _spread(SYM, int(m5["t"][i]), ex)
        hi, lo = (m5["high"][i], m5["low"][i]) if d == 1 else (m5["high"][i] + sp, m5["low"][i] + sp)
        if (lo <= sl) if d == 1 else (hi >= sl):
            return -1 - (slip + COMM_PER_OZ) / risk
        if (hi >= tp) if d == 1 else (lo <= tp):
            return 2 - COMM_PER_OZ / risk
        if nyday[i] != nyday[j - 1] or mins[i] >= 16 * 60 - 5:
            px = m5["close"][i] + (sp if d == -1 else 0)
            return ((px - entry) * d - COMM_PER_OZ) / risk
    return ((m5["close"][-1] - entry) * d - COMM_PER_OZ) / risk


def main() -> None:
    lo = (pd.Timestamp(START, tz="UTC") - pd.Timedelta(days=20)).isoformat()
    m5, ex, t0 = load_m5(SYM, lo, END), Exec(), int(pd.Timestamp(START, tz="UTC").timestamp())
    print(f"{SYM} NY fake move {START} .. {END} (2R target, $250/R)")
    for name, kw in (("RULE", {}), ("look: no trend filter", {"use_trend": False}), ("look: any dip (0 ATR)", {"depth": 0.0})):
        r = pd.Series([x for _, x in trades(m5, t0, ex, **kw)], dtype=float)
        print(f"  {name:22} {stat(r)}  = ${r.sum() * 250:+,.0f}")


if __name__ == "__main__":
    main()
