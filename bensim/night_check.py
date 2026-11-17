"""Night / Asian-session ideas (user ask 2026-11-03, "like Night Hunter Pro") -- 3-month test, MT5 bridge M5 bars.

    python -m bensim.night_check

Rules frozen 2026-11-03 BEFORE any run. Server time (NY+7). Night = 01:00-08:00 server (~22:00-05:00 UTC, the
rollover hour 00:00 skipped), everything flat by 09:00 server. Costs: each bar's real spread (bid bars; longs fill
and shorts exit on bid+spread) + commission 0.007% of notional round trip ($7/lot). R = initial stop distance.
A bar touching stop and target = stop. One trade per idea per symbol per night unless stated.
 A. NIGHT RANGE FADE (Night Hunter idea): range = high/low of the night bars so far (>= 12 bars = 1h formed).
    Range must be calm: width <= 1.0 x ATR(14) of H1 (M5 ATR x sqrt(12)) and >= 4 x spread. Limit SELL at the range
    high / BUY at the range low when price trades through it; target = range middle, stop = edge + 0.5 x width.
    Max one long + one short per night.
 B. ASIAN BREAKOUT at London: range = 01:00-08:00 high/low. From 09:00 to 13:00 server a buy-stop at the high /
    sell-stop at the low (first one only); stop = range middle, target = 1 x range width beyond the entry,
    exit 20:00 server.
 C. NIGHT BOLLINGER SCALP: M5 close outside BB(20, 2) with RSI(14) < 30 (> 70 for shorts) in the night window ->
    enter next open; target = the middle band at the signal, stop = 1.5 x ATR(14), time stop 12 bars / 09:00.
    One position at a time per symbol.
Symbols: 6 majors + quiet crosses the broker has (missing ones skipped).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .pattern_check import stat
from .pine90_check import rsi
from .scalp_check import rates
from .strategy import atr

SYMS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD",
        "EURGBP", "EURCHF", "AUDCAD", "AUDNZD", "NZDCAD", "GBPCAD", "EURCAD", "AUDCHF", "CADCHF"]
COMM = 0.00007


def walk(b: dict, j: int, d: int, e: float, sl: float, tp: float, end: int, fill_bar: bool = False) -> float:
    """Net R of a trade filled at price e (already incl. spread) on bar j; exits at sl/tp or the close of bar `end`."""
    risk = abs(e - sl)
    for k in range(j, end + 1):
        sp = b["spread"][k]
        hi, lo, op = b["high"][k], b["low"][k], b["open"][k]
        if d == -1:  # shorts exit on the ask
            hi, lo, op = hi + sp, lo + sp, op + sp
        stop = lo <= sl if d == 1 else hi >= sl
        if stop:
            px = sl if k == j and fill_bar else (min(op, sl) if d == 1 else max(op, sl))
        elif k > j or not fill_bar:
            if (hi >= tp) if d == 1 else (lo <= tp):
                px = tp
            elif k == end:
                px = b["close"][k] + (sp if d == -1 else 0)
            else:
                continue
        else:
            continue
        return ((px - e) * d - COMM * e) / risk
    return ((b["close"][end] + (b["spread"][end] if d == -1 else 0) - e) * d - COMM * e) / risk


def days(b: dict):
    day, hour = b["t"] // 86400, (b["t"] % 86400) // 3600
    for dd in np.unique(day):
        k = np.flatnonzero(day == dd)
        yield k, hour[k]


def fade(b: dict, h1atr: np.ndarray) -> list[float]:
    out = []
    for k, h in days(b):
        night = k[(h >= 1) & (h < 8)]
        flat = k[h < 9]
        if len(night) < 13 or not len(flat):
            continue
        end, done = flat[-1], set()
        for n, i in enumerate(night[12:], 12):
            hi, lo = b["high"][night[:n]].max(), b["low"][night[:n]].min()
            w, sp = hi - lo, b["spread"][i]
            if w > h1atr[i] or w < 4 * sp:
                continue
            mid = (hi + lo) / 2
            if -1 not in done and b["high"][i] >= hi:  # sell limit at the high (bid touches it)
                done.add(-1)
                out.append(walk(b, i, -1, hi, hi + 0.5 * w, mid, end, fill_bar=True))
            if 1 not in done and b["low"][i] + sp <= lo:  # buy limit at the low (ask touches it)
                done.add(1)
                out.append(walk(b, i, 1, lo, lo - 0.5 * w, mid, end, fill_bar=True))
            if len(done) == 2:
                break
    return out


def breakout(b: dict, last_hour: int = 13, stop_opp: bool = False, times: list | None = None) -> list[float]:
    """B; B2 (frozen 2026-11-03 from the 3-month look, before the 5y run) = last_hour=10, stop_opp=True."""
    out = []
    for k, h in days(b):
        night, win, day_ = k[(h >= 1) & (h < 8)], k[(h >= 9) & (h < last_hour)], k[h < 20]
        if len(night) < 60 or not len(win):
            continue
        hi, lo = b["high"][night].max(), b["low"][night].min()
        w, mid = hi - lo, (hi + lo) / 2
        for i in win:
            sp = b["spread"][i]
            if b["high"][i] + sp >= hi:  # buy stop at the high (ask)
                out.append(walk(b, i, 1, max(hi, b["open"][i] + sp), lo if stop_opp else mid, hi + w, day_[-1], fill_bar=True))
            elif b["low"][i] <= lo:
                out.append(walk(b, i, -1, min(lo, b["open"][i]), hi if stop_opp else mid, lo - w, day_[-1], fill_bar=True))
            else:
                continue
            if times is not None:
                times.append(int(b["t"][i]))
            break
    return out


def bollinger(b: dict) -> list[float]:
    c = pd.Series(b["close"])
    ma, sd = c.rolling(20).mean().to_numpy(), c.rolling(20).std().to_numpy()
    r, a = rsi(b["close"]), atr(b)
    hour = (b["t"] % 86400) // 3600
    day = b["t"] // 86400
    out, i, n = [], 20, len(c)
    while i < n - 2:
        d = 1 if b["close"][i] < ma[i] - 2 * sd[i] and r[i] < 30 else -1 if b["close"][i] > ma[i] + 2 * sd[i] and r[i] > 70 else 0
        if not d or not (1 <= hour[i] < 8) or day[i + 1] != day[i]:
            i += 1
            continue
        j = i + 1
        e = b["open"][j] + (b["spread"][j] if d == 1 else 0)
        tp, sl = ma[i], e - d * 1.5 * a[i]
        if (tp - e) * d <= 0:
            i += 1
            continue
        end = j + 11
        while end > j and (hour[end] >= 9 or day[end] != day[j]):
            end -= 1
        out.append(walk(b, j, d, e, sl, tp, min(end, n - 1)))
        i = end + 1
    return out


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # live DEMO beats research CPU
    except Exception:
        pass
    res = {"A fade": [], "B breakout": [], "C bollinger": []}
    rows = []
    for sym in SYMS:
        try:
            b = rates(sym, "M5", 20000)
        except Exception as ex:
            print(f"  {sym}: skipped ({ex.__class__.__name__})")
            continue
        h1atr = atr(b) * np.sqrt(12)
        span = f"{pd.to_datetime(b['t'][0], unit='s'):%Y-%m-%d}..{pd.to_datetime(b['t'][-1], unit='s'):%Y-%m-%d}"
        for name, r in (("A fade", fade(b, h1atr)), ("B breakout", breakout(b)), ("C bollinger", bollinger(b))):
            res[name] += r
            s = pd.Series(r, dtype=float)
            rows.append({"idea": name, "sym": sym, "n": len(s), "win": (s > 0).mean(), "avgR": s.mean(), "totR": s.sum()})
        print(f"  {sym:7} {span}  " + "  ".join(f"{r['idea'][0]} {r['n']:3} {r['totR']:+6.1f}R" for r in rows[-3:]), flush=True)
    pd.DataFrame(rows).to_csv("logs/night_check.csv", index=False)
    print()
    for name, r in res.items():
        g = pd.DataFrame([x for x in rows if x["idea"] == name])
        print(f"{name:12} {stat(pd.Series(r, dtype=float))}  symbols in profit {(g.totR > 0).sum()}/{len(g)}")


if __name__ == "__main__":
    main()
