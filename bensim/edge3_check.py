"""3 'edge' ideas (user ask 2026-11-03) -- 3-month test on MT5 bridge data, rules frozen before any run.

    python -m bensim.edge3_check

Server time (NY+7: London 08:00 = 10:00 server, NY 09:30 = 16:30 server). Costs = real bar spread + 0.007% commission
(USTEC no commission). R = initial stop. Same fill/exit conventions as bensim.night_check.walk.
 D. 20-DAY DONCHIAN (Turtle): D1 close... entry when price trades through the prior 20-day high/low (stop order),
    stop 2 x ATR(20), exit when price trades through the 10-day opposite channel. Entries 2026-08-01..; open trades
    marked at the last close. One position per symbol.
 O. OPENING RANGE BREAKOUT: FX+gold: range = 10:00-10:30 server (London open), USTEC: 16:30-17:00 (NY open).
    Stop-entry at the break within the next 3 hours (first break only), stop = other side of the range, target 2R,
    exit 19:00 server (USTEC 22:55).
 S. LIQUIDITY SWEEP (ICT): Asian range 01:00-08:00 server. 10:00-13:00: a bar's wick takes the Asian high (low) and
    the M5 bar closes back inside -> SELL (BUY) next open; stop = sweep extreme + spread; target = the other side of
    the Asian range; skip if target < 1.5R. One per day. Exit 19:00 server.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .night_check import days, walk
from .pattern_check import stat
from .scalp_check import rates
from .strategy import atr

FX = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "EURGBP", "EURCHF", "AUDCAD", "AUDNZD",
      "NZDCAD", "GBPCAD", "EURCAD", "AUDCHF", "CADCHF"]
SYMS = FX + ["XAUUSD", "USTEC"]


def donchian(b: dict, t0: int) -> list[float]:
    a = pd.Series(np.maximum(b["high"] - b["low"], np.abs(b["high"] - np.r_[b["close"][0], b["close"][:-1]]))).rolling(20).mean().to_numpy()
    out, i, n = [], 21, len(b["close"])
    while i < n:
        hi20, lo20 = b["high"][i - 20:i].max(), b["low"][i - 20:i].min()
        sp = b["spread"][i]
        d = 1 if b["high"][i] + sp >= hi20 else -1 if b["low"][i] <= lo20 else 0
        if not d or b["t"][i] < t0:
            i += 1
            continue
        e = max(hi20, b["open"][i] + sp) if d == 1 else min(lo20, b["open"][i])
        sl, risk = e - d * 2 * a[i - 1], 2 * a[i - 1]
        res = None
        for k in range(i, n):
            if k > i:
                ex10 = b["low"][k - 10:k].min() if d == 1 else b["high"][k - 10:k].max() + b["spread"][k]
                sl = max(sl, ex10) if d == 1 else min(sl, ex10)  # 10-day exit channel acts as a trailing stop
            lo_, hi_ = b["low"][k], b["high"][k] + b["spread"][k]
            if (lo_ <= sl) if d == 1 else (hi_ >= sl):
                px = min(b["open"][k], sl) if d == 1 else max(b["open"][k] + b["spread"][k], sl)
                res = ((px - e) * d - 0.00007 * e) / risk
                break
        if res is None:  # still open: mark to the last close
            k = n - 1
            res = ((b["close"][k] + (b["spread"][k] if d == -1 else 0) - e) * d - 0.00007 * e) / risk
        out.append(res)
        i = k + 1
    return out


def orb(b: dict, sym: str) -> list[float]:
    r0, r1, last, flat = (16.5, 17, 20, 22.9) if sym == "USTEC" else (10, 10.5, 13.5, 19)
    out = []
    for k, _ in days(b):
        h = (b["t"][k] % 86400) / 3600
        rng, win = k[(h >= r0) & (h < r1)], k[(h >= r1) & (h < last)]
        end = k[h < flat]
        if len(rng) < 6 or not len(win):
            continue
        hi, lo = b["high"][rng].max(), b["low"][rng].min()
        for i in win:
            sp = b["spread"][i]
            if b["high"][i] + sp >= hi:
                e = max(hi, b["open"][i] + sp)
                out.append(walk(b, i, 1, e, lo, e + 2 * (e - lo), end[-1], fill_bar=True))
            elif b["low"][i] <= lo:
                e = min(lo, b["open"][i])
                out.append(walk(b, i, -1, e, hi + sp, e - 2 * (hi + sp - e), end[-1], fill_bar=True))
            else:
                continue
            break
    return out


def sweep(b: dict) -> list[float]:
    out = []
    for k, hh in days(b):
        night, win, end = k[(hh >= 1) & (hh < 8)], k[(hh >= 10) & (hh < 13)], k[hh < 19]
        if len(night) < 60 or len(win) < 2:
            continue
        hi, lo = b["high"][night].max(), b["low"][night].min()
        for i in win[:-1]:
            d = -1 if b["high"][i] > hi and b["close"][i] < hi else 1 if b["low"][i] < lo and b["close"][i] > lo else 0
            if not d:
                continue
            j, sp = i + 1, b["spread"][i + 1]
            e = b["open"][j] + (sp if d == 1 else 0)
            sl = b["high"][i] + sp if d == -1 else b["low"][i]
            tp = lo + sp if d == -1 else hi
            if (tp - e) * d < 1.5 * abs(e - sl) or abs(e - sl) <= 0:
                break
            out.append(walk(b, j, d, e, sl, tp, end[-1]))
            break
    return out


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    res, rows = {"D donchian20": [], "O ORB": [], "S sweep": []}, []
    t0 = int(pd.Timestamp("2026-08-01").timestamp())
    for sym in SYMS:
        try:
            m5, d1 = rates(sym, "M5", 20000), rates(sym, "D1", 300)
        except Exception as ex:
            print(f"  {sym}: skipped ({ex.__class__.__name__})")
            continue
        if sym == "USTEC":
            import bensim.night_check as nc
            nc.COMM = 0.0
        for name, r in (("D donchian20", donchian(d1, t0)), ("O ORB", orb(m5, sym)), ("S sweep", sweep(m5))):
            res[name] += r
            s = pd.Series(r, dtype=float)
            rows.append({"idea": name, "sym": sym, "n": len(s), "totR": s.sum()})
        print(f"  {sym:7} " + "  ".join(f"{x['idea'][0]} {x['n']:3} {x['totR']:+6.1f}R" for x in rows[-3:]), flush=True)
    pd.DataFrame(rows).to_csv("logs/edge3_check.csv", index=False)
    print()
    for name, r in res.items():
        g = pd.DataFrame([x for x in rows if x["idea"] == name])
        print(f"{name:13} {stat(pd.Series(r, dtype=float))}  symbols in profit {(g.totR > 0).sum()}/{len(g)}")


if __name__ == "__main__":
    main()
