"""Round 25b (2026-11-09): Bennett CHANNEL mode (bensim/bennett_channel.py, rules frozen before any test) on the
UNTOUCHED period 2021-10-12 .. 2024-11-06 (IC M5 in the DB; USDCAD only from 2024-02). Nothing was tuned on it.
PASS bar FIXED BEFORE THE RUN: total R > 0, t >= 1.5, >= 2 of the 3 years positive, $ > 0 at $500 risk.
Stops: own (3.5 pips beyond the low) and own with a 1 x ATR(15M) floor. Same M5 walker as two_year.py.
$ at $500 risk and at fixed 1 / 5 / 7 lots on the 100k.
    python reports/round25/channel_untouched.py
"""
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI/reports/round25")
S, E = "2021-10-12", "2024-11-07"
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]


def job(sym):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    from bensim import bennett_channel as bc
    return [dict(x, sym=sym) for x in bc.run(sym, S, E)]


if __name__ == "__main__":
    import two_year as ty
    from bensim.backtest import load_m5, resample
    from bensim.strategy import atr, pip
    with ProcessPoolExecutor(3) as pool:
        rows = sum(pool.map(job, SYMS), [])
    T = pd.DataFrame(rows)
    T = T[(T.tp - T.entry) * T.dir > 0].reset_index(drop=True)
    print(f"{len(T)} trades", T.sym.value_counts().to_dict(), flush=True)
    M5, A15 = {}, {}
    for s in T.sym.unique():
        M5[s] = load_m5(s, "2021-10-01", "2024-11-20")
        b = resample(M5[s], 900)
        A15[s] = (b["t"], atr(b))
    T["atr15"] = [float(A15[s][1][max(0, int(np.searchsorted(A15[s][0] + 900, t, side="right")) - 1)]) for s, t in zip(T.sym, T.t_entry)]
    P = T.sym.map(pip)
    T["year"] = pd.to_datetime(T.t_entry, unit="s").dt.year
    print(f"\nCHANNEL mode, UNTOUCHED {S} .. {E}; PASS = total > 0, t >= 1.5, >= 2 of 3 years > 0, $ > 0 @$500")
    for rule in ("own stop", "stop >= 1 ATR(15M)"):
        own = (T.entry - T.sl).abs()
        rk = own if rule == "own stop" else np.maximum(own, T.atr15)
        sl = T.entry - T.dir * rk
        r = np.array([ty.walk(M5[x.sym], x.dir, int(x.t_entry), x.entry, s_, x.tp, pp, x.sym) for x, s_, pp in zip(T.itertuples(), sl, P)])
        r = r - T.sym.map(ty.COST) * P / rk
        g = pd.DataFrame({"sym": T.sym, "year": T.year, "r": r,
                          "usd1": r * (rk / P) * [ty.pipval(s, e) for s, e in zip(T.sym, T.entry)]}).dropna()
        t = g.r.mean() / g.r.std() * np.sqrt(len(g))
        yrs = g.groupby("year").r.sum()
        y3 = yrs[[y for y in yrs.index if y in (2022, 2023, 2024)]]
        ok = g.r.sum() > 0 and t >= 1.5 and (y3 > 0).sum() >= 2
        u = g.usd1.sum()
        print(f"\n== {rule}: {len(g)} trades win {np.mean(g.r > 0):.0%} {g.r.mean():+.3f}R/trade t {t:+.2f} total {g.r.sum():+.1f}R  "
              f"{'PASS' if ok else 'FAIL'}")
        print(f"   $ at $500 risk: ${g.r.sum() * 500:+,.0f} | fixed 1 lot ${u:+,.0f} | 5 lots ${5 * u:+,.0f} | 7 LOTS ${7 * u:+,.0f}")
        print("   per year R:", yrs.round(1).to_dict(), "| 7-lot $ per year:", (g.groupby('year').usd1.sum() * 7).round(0).to_dict())
        print("   per pair R:", g.groupby("sym").r.sum().round(1).to_dict())
        print("   worst losing streak (trades):", int(max((len(list(x)) for k, x in __import__('itertools').groupby(g.r < 0) if k), default=0)),
              "| top-3 winners R:", g.r.nlargest(3).round(1).tolist())
