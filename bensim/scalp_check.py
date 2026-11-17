"""Two scalping ideas (user ask 2026-10-31, from research + r/algotrading) -- 3-month test on MT5 data via the bridge.

    python -m bensim.scalp_check

Rules frozen 2026-10-31 BEFORE any run:
A. INTRADAY MOMENTUM (Gao, Han, Li & Zhou): USTEC (Nasdaq 100 CFD), M1. NY time (server = NY + 7h, mt5_time).
   signal = sign(close 10:00 NY - previous NY day's 16:00 close). At the 15:30 NY bar open trade in that direction
   (buy on the ask / sell on the bid), exit at the 16:00 close (15:59 bar). One trade a day. Spread from each bar.
   look-only: first half-hour measured 9:30->10:00 instead.
B. VWAP +/-2 SIGMA MEAN REVERSION (ours -- the Reddit bot gave no rules): USTEC, XAUUSD, EURUSD, M5, tick-volume VWAP
   anchored at the server day (00:00 = NY 17:00), sigma = volume-weighted std of typical price.
   * entry: an M5 close below VWAP-2sigma -> BUY next open (above +2sigma -> SELL), only from the 13th bar of the day,
     not in the last server hour (rollover), ADX(14, M5) < 25 (not a trend), one position at a time per symbol
   * target = the VWAP at the signal bar (fixed); stop = entry -/+ 1.5 x ATR(14, M5); time stop 24 bars (2h) or
     the day's last bar. A bar touching both = stop. Skip if the target is not beyond the entry.
   * costs: bar spread (longs fill/exit on bid+spread vs bid), FX/gold +$7/lot (0.007% of notional), USTEC no commission
   * R = initial stop distance. look-only: same without the ADX filter.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import bridge
from .gold_fvg import adx
from .mt5_time import server_to_utc
from .pattern_check import stat
from .strategy import atr

NY = ZoneInfo("America/New_York")


def rates(sym: str, tf: str, n: int) -> dict:
    r = bridge.rates(sym, tf, 0, n)
    b = {k: np.array([x[s] for x in r], dtype=float) for k, s in (("open", "open"), ("high", "high"), ("low", "low"),
                                                                  ("close", "close"), ("vol", "tick_volume"), ("spread", "spread"))}
    b["t"] = np.array([x["time"] for x in r], dtype=np.int64)  # server time
    b["spread"] *= bridge.tick(sym)["point"]
    return b


def momentum(b: dict, first_from_open: bool = False) -> pd.Series:
    ny = pd.to_datetime(server_to_utc(b["t"]), unit="s", utc=True).tz_convert(NY)
    hm = (ny.hour * 100 + ny.minute).to_numpy()
    day = ny.normalize()
    out, prev_close = {}, None
    for d in pd.unique(day):
        k = np.flatnonzero(day == d)
        at = {int(h): int(i) for h, i in zip(hm[k], k)}
        if all(x in at for x in (959, 1530, 1559)) and (prev_close is not None or first_from_open) and (930 in at or not first_from_open):
            ref = b["open"][at[930]] if first_from_open else prev_close
            s = np.sign(b["close"][at[959]] - ref)
            if s:
                e = b["open"][at[1530]] + (b["spread"][at[1530]] if s > 0 else 0)
                x = b["close"][at[1559]] + (b["spread"][at[1559]] if s < 0 else 0)
                out[d] = (x - e) * s  # index points = $ per 1 lot on IC Markets USTEC
        if 1559 in at:
            prev_close = b["close"][at[1559]]
    return pd.Series(out, dtype=float)


def vwap_trades(sym: str, b: dict, use_adx: bool = True) -> list[float]:
    tp = (b["high"] + b["low"] + b["close"]) / 3
    w = np.maximum(b["vol"], 1)
    day = b["t"] // 86400
    hour = (b["t"] % 86400) // 3600
    vw, sd, nbar = np.zeros(len(tp)), np.zeros(len(tp)), np.zeros(len(tp), dtype=int)
    for d in np.unique(day):
        k = np.flatnonzero(day == d)
        cw, cwp, cwp2 = np.cumsum(w[k]), np.cumsum(w[k] * tp[k]), np.cumsum(w[k] * tp[k] ** 2)
        vw[k] = cwp / cw
        sd[k] = np.sqrt(np.maximum(cwp2 / cw - vw[k] ** 2, 0))
        nbar[k] = np.arange(len(k))
    a, ax = atr(b), adx(b)
    comm = 0.0 if sym == "USTEC" else 0.00007
    out, i, n = [], 0, len(tp)
    while i < n - 2:
        c = b["close"][i]
        d = 1 if c < vw[i] - 2 * sd[i] else -1 if c > vw[i] + 2 * sd[i] else 0
        if not d or nbar[i] < 12 or hour[i] >= 23 or sd[i] <= 0 or (use_adx and ax[i] >= 25) or day[i + 1] != day[i]:
            i += 1
            continue
        j = i + 1
        e = b["open"][j] + (b["spread"][j] if d == 1 else 0)
        tgt, sl = vw[i], e - d * 1.5 * a[i]
        risk = abs(e - sl)
        if (tgt - e) * d <= 0 or risk <= 0:
            i += 1
            continue
        res = None
        for j in range(i + 1, min(i + 25, n)):
            sp = b["spread"][j]
            hi, lo, op = (b["high"][j], b["low"][j], b["open"][j]) if d == 1 else (b["high"][j] + sp, b["low"][j] + sp, b["open"][j] + sp)
            if (lo <= sl) if d == 1 else (hi >= sl):
                px = min(op, sl) if d == 1 else max(op, sl)
            elif (hi >= tgt) if d == 1 else (lo <= tgt):
                px = tgt
            elif j == min(i + 24, n - 1) or day[min(j + 1, n - 1)] != day[j]:
                px = b["close"][j] + (sp if d == -1 else 0)
            else:
                continue
            res = ((px - e) * d - comm * e) / risk
            break
        if res is not None:
            out.append(res)
        i = j + 1
    return out


def main() -> None:
    print("A. INTRADAY MOMENTUM -- USTEC, first half-hour sign -> last half-hour (15:30-16:00 NY), 1 lot = $1/point")
    m1 = rates("USTEC", "M1", 99000)
    for name, kw in (("RULE: prev close -> 10:00", {}), ("look: 9:30 -> 10:00", {"first_from_open": True})):
        s = momentum(m1, **kw)
        t = s.mean() / s.std() * np.sqrt(len(s)) if len(s) > 2 else 0
        print(f"  {name:26} days {len(s):3}  win {(s > 0).mean():4.0%}  avg {s.mean():+6.2f} pts  t {t:+.1f}  "
              f"total {s.sum():+8.1f} pts = ${s.sum():+,.0f}/lot   ({s.index.min():%Y-%m-%d} .. {s.index.max():%Y-%m-%d})")
    print("\nB. VWAP +/-2 sigma MEAN REVERSION, M5, target VWAP, stop 1.5 ATR, 2h time stop")
    for sym in ("USTEC", "XAUUSD", "EURUSD"):
        b = rates(sym, "M5", 20000)
        span = f"{pd.to_datetime(b['t'][0], unit='s'):%Y-%m-%d} .. {pd.to_datetime(b['t'][-1], unit='s'):%Y-%m-%d}"
        for name, kw in (("RULE (ADX<25)", {}), ("look: no ADX filter", {"use_adx": False})):
            r = pd.Series(vwap_trades(sym, b, **kw), dtype=float)
            print(f"  {sym:7} {name:20} {stat(r)}  PF {r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9):.2f}  {span}")


if __name__ == "__main__":
    main()
