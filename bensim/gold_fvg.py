"""Gold breakout-FVG (user ask 2026-10-30, from a Reddit r/Daytrading gold summary) -- 3-month test.

    python -m bensim.gold_fvg [start] [end]       # default 2026-07-26 .. 2026-10-26 (DB end)

Rules (frozen 2026-10-30 before any result was seen):
  * XAUUSD M5 (DB, UTC, bid) -> M15 signal bars; H4 = swing trend (strategy.trend) of completed H4 bars
  * levels: previous UTC day high/low, Asia range high/low (00:00-07:00 UTC, today), round $50 numbers
  * breakout: an M15 candle that OPENS below a level and CLOSES above it (long; mirror for short), in the H4 trend
    direction, ADX(14, M15) >= 20 at its close, candle closing 12:00-16:00 UTC (London/NY overlap)
  * FVG: the candles around the breakout leave a gap -- long: high[i-1] < low[i+1] (seen at the close of i+1)
  * entry: limit at the near edge of the gap (long low[i+1], short high[i+1]) -- the first pullback into it;
    unfilled by 17:00 UTC -> cancelled
  * stop: far edge of the gap -/+ 0.5 x ATR(14, M15) (wider stop, smaller size); target 2R; anything open at
    20:00 UTC is closed at the bid/ask. One trade per day (first signal).
  * costs as the backtest: spread model (long fills/exits on bid+spread vs bid), $7/lot commission, 0.1 pip slip
    on stops; a bar touching both stop and target = stop
Look-only (not the rule): the same with each filter removed (no ADX / no H4 / any hour).
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from .backtest import Exec, _spread, load_m5, resample
from .pattern_check import stat
from .strategy import atr, pip, trend

SYM = "XAUUSD"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
START = ARGS[0] if ARGS else "2026-07-26"
END = ARGS[1] if len(ARGS) > 1 else "2026-10-26"
COMM_PER_OZ = 7.0 / 100  # $7 per 100 oz lot, round trip


def adx(b: dict, n: int = 14) -> np.ndarray:
    """Wilder ADX."""
    h, l, c = b["high"], b["low"], b["close"]
    up, dn = np.diff(h, prepend=h[0]), -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))

    def w(x):
        out, s = np.zeros_like(x), 0.0
        for i, v in enumerate(x):
            s = v if i == 0 else s + (v - s) / n
            out[i] = s
        return out
    a = w(tr)
    pdi, mdi = 100 * w(pdm) / a, 100 * w(mdm) / a
    dx = 100 * abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-12)
    return w(dx)


def signals(m5: dict, t0: int, use_adx=True, use_h4=True, use_hours=True) -> list[dict]:
    m15, h4 = resample(m5, 900), resample(m5, 14400)
    a14, ax, h4tr = atr(m15), adx(m15), trend(h4)
    h4_done = h4["t"] + 14400
    day = m15["t"] // 86400
    hour = (m15["t"] % 86400) // 3600
    out, used = [], set()
    for i in range(30, len(m15["t"]) - 1):
        j = i + 1  # gap is known at the close of j
        t_close = int(m15["t"][i]) + 900
        if m15["t"][j] < t0 or m15["t"][j] != m15["t"][i] + 900 or day[i] in used:
            continue
        if use_hours and not (12 <= (t_close % 86400) / 3600 <= 16):
            continue
        if use_adx and ax[i] < 20:
            continue
        k = int(np.searchsorted(h4_done, t_close, side="right")) - 1
        h4d = int(h4tr[k]) if k >= 0 else 0
        prev = day == day[:i][day[:i] < day[i]].max()  # previous trading day (skips weekends/holidays)
        asia = (day == day[i]) & (hour < 7)
        lv = [m15["high"][prev].max() if prev.any() else np.nan, m15["low"][prev].min() if prev.any() else np.nan,
              m15["high"][asia].max() if asia.any() else np.nan, m15["low"][asia].min() if asia.any() else np.nan]
        o, c = m15["open"][i], m15["close"][i]
        lv += [r * 50 for r in range(int(min(o, c) // 50), int(max(o, c) // 50) + 2)]
        for d in (1, -1):
            if use_h4 and h4d != d:
                continue
            if not any((o < L <= c) if d == 1 else (o > L >= c) for L in lv if L == L):
                continue
            gap = (m15["low"][j] - m15["high"][i - 1]) if d == 1 else (m15["low"][i - 1] - m15["high"][j])
            if gap <= 0:
                continue
            entry = m15["low"][j] if d == 1 else m15["high"][j]
            far = m15["high"][i - 1] if d == 1 else m15["low"][i - 1]
            sl = far - d * 0.5 * a14[j]
            out.append({"t": int(m15["t"][j]) + 900, "dir": d, "entry": float(entry), "sl": float(sl),
                        "tp": float(entry + 2 * (entry - sl)), "day": int(day[i])})
            used.add(day[i])
            break
    return out


def simulate(m5: dict, s: dict, ex: Exec) -> float | None:
    d, e, sl, tp = s["dir"], s["entry"], s["sl"], s["tp"]
    risk, slip = abs(e - sl), ex.slippage_pips * pip(SYM)
    day0 = s["t"] - s["t"] % 86400
    k = int(np.searchsorted(m5["t"], s["t"]))
    filled = False
    for i in range(k, len(m5["t"])):
        t = int(m5["t"][i])
        sp = _spread(SYM, t, ex)
        hi, lo = m5["high"][i], m5["low"][i]
        if not filled:
            if t >= day0 + 17 * 3600:
                return None
            if (lo + sp <= e) if d == 1 else (hi >= e):  # buy limit fills on the ask, sell limit on the bid
                filled = True
            else:
                continue
        exit_hi, exit_lo = (hi, lo) if d == 1 else (hi + sp, lo + sp)  # long exits on bid, short on ask
        if (exit_lo <= sl) if d == 1 else (exit_hi >= sl):
            return -1 - (slip + COMM_PER_OZ) / risk
        if (exit_hi >= tp) if d == 1 else (exit_lo <= tp):
            return 2 - COMM_PER_OZ / risk
        if t + 300 >= day0 + 20 * 3600:
            px = m5["close"][i] + (sp if d == -1 else 0)
            return ((px - e) * d - COMM_PER_OZ) / risk
    return None


def main() -> None:
    lo = (pd.Timestamp(START, tz="UTC") - pd.Timedelta(days=40)).isoformat()
    m5, ex, t0 = load_m5(SYM, lo, END), Exec(), int(pd.Timestamp(START, tz="UTC").timestamp())
    print(f"{SYM} breakout-FVG {START} .. {END} (2R target, 0.25% risk = ${ex.start_balance * ex.risk_pct / 100:.0f}/R)")
    for name, kw in (("RULE (all filters)", {}), ("look: no ADX", {"use_adx": False}),
                     ("look: no H4 trend", {"use_h4": False}), ("look: any hour", {"use_hours": False})):
        sig = signals(m5, t0, **kw)
        rows = [(s, simulate(m5, s, ex)) for s in sig]
        r = pd.Series([x for _, x in rows if x is not None], dtype=float)
        side = pd.Series([s["dir"] for s, x in rows if x is not None])
        print(f"  {name:20} signals {len(sig):3}  filled: {stat(r)}  = ${r.sum() * 250:+,.0f}"
              f"   long {r[side.values == 1].mean() if (side == 1).any() else float('nan'):+.2f}R"
              f" short {r[side.values == -1].mean() if (side == -1).any() else float('nan'):+.2f}R")


if __name__ == "__main__":
    main()
