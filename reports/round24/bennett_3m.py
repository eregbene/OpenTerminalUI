"""Round 24b (2026-11-09): Bennett, last 3 months (2026-08-07 .. 2026-11-06, IC M5), 7 symbols.
PRE-REGISTERED configs: LIVE (live DEMO, tight stop) | BASE (base video, his stop rule) | LATEST1 (base + newest-leg fib,
swing >= 1 ATR, no FVG needed) | LATEST2F (base + newest leg, swing >= 2 ATR, FVG required) | LIVE_LATEST.
Targets (user question 2026-11-09): every trade keeps ITS stop; target = his liquidity target (as traded) vs fixed
1:1, 1:2, 1:3 (target = entry +/- k x stop distance). All targets replayed by the same M5 walker: stop first when one bar
touches both, short side on ask (bid + spread), Friday 20:00 UTC close, 10-day max; R net of the round-trip cost.
$ = R x stop pips x pip value per lot (1/5/7 lots on the 100k) and R x $500.
    python reports/round24/bennett_3m.py
"""
import dataclasses
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim import bennett_strategy as bstrat  # noqa: E402
from bensim.backtest import exec_profile, load_m5, run_symbol  # noqa: E402
from bensim.strategy import pip  # noqa: E402

S, E = "2026-08-07T00:00:00+00:00", "2026-11-07T00:00:00+00:00"
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
B, R = bstrat.BennettParams, dataclasses.replace
CFG = {"LIVE": bstrat.LIVE, "BASE": B(),
       "LATEST1": B(leg_anchor="latest", leg_swing_atr=1.0, need_fvg=False),
       "LATEST2F": B(leg_anchor="latest", leg_swing_atr=2.0, need_fvg=True),
       "LIVE_LATEST": R(bstrat.LIVE, leg_anchor="latest", leg_swing_atr=1.0)}
COST = B().cost_pips
SPREAD = {"XAUUSD": 2.5}  # pips (strategy pip); FX ~0.8 on ICMarkets demo
M5 = {s: load_m5(s, "2026-08-01", "2026-11-20") for s in SYMS}


def walk(sym, d, t_in, entry, sl, tp):
    m = M5[sym]
    t, h, lo, c = m["t"], m["high"], m["low"], m["close"]
    p = pip(sym)
    spr = SPREAD.get(sym, 0.8) * p
    i0 = int(np.searchsorted(t, t_in))
    i1 = min(len(t), int(np.searchsorted(t, t_in + 10 * 86400)))
    risk = (entry - sl) * d
    for i in range(i0, i1):
        ts = pd.Timestamp(int(t[i]), unit="s")
        if ts.dayofweek == 4 and ts.hour >= 20:
            x = c[i] + (spr if d == -1 else 0)
            return (x - entry) * d / risk
        hh, ll = h[i] + (spr if d == -1 else 0), lo[i] + (spr if d == -1 else 0)
        if (ll <= sl) if d == 1 else (hh >= sl):
            return -1.0
        if (hh >= tp) if d == 1 else (ll <= tp):
            return (tp - entry) * d / risk
    x = c[i1 - 1] + (spr if d == -1 else 0)
    return (x - entry) * d / risk


def pipval(sym, price):
    return 1000 / price if sym == "USDJPY" else 10 / price if sym in ("USDCAD", "USDCHF") else 10.0


def main():
    rows = []
    for name, p in CFG.items():
        for s in SYMS:
            t = run_symbol((s, S, E, p, exec_profile("observed_account"), "bennett"))
            if not t.empty:
                rows.append(t.assign(cfg=name, sym=s))
        print("done", name, flush=True)
    df = pd.concat(rows, ignore_index=True)
    cost_r = df.sym.map(COST) * df.sym.map(pip) / (df.entry - df.sl).abs()
    risk = (df.entry - df.sl).abs()
    df["his_target"] = [walk(r.sym, r.dir, int(r.t_entry), r.entry, r.sl, r.tp) for r in df.itertuples()] - cost_r
    for k in (1, 2, 3):
        df[f"1:{k}"] = [walk(r.sym, r.dir, int(r.t_entry), r.entry, r.sl, r.entry + r.dir * k * rk) for r, rk in zip(df.itertuples(), risk)] - cost_r
    df["pv"] = (risk / df.sym.map(pip)) * [pipval(s, e) for s, e in zip(df.sym, df.entry)]
    df.to_csv("D:/Devops/trading/OpenTerminalUI/reports/round24/bennett_3m_trades.csv", index=False)
    print(f"\nwindow {S[:10]} .. {E[:10]}; R net of costs; engine R (as traded, incl. its own exits) shown first")
    for c, g in df.groupby("cfg"):
        print(f"\n== {c}: {len(g)} trades, engine {g.r.mean():+.3f}R/trade total {g.r.sum():+.1f}R | per sym {g.groupby('sym').r.sum().round(1).to_dict()}")
        for col in ("his_target", "1:1", "1:2", "1:3"):
            x = g[col]
            tt = x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 1 else 0
            u = (x * g.pv).sum()
            print(f"   {col:10} win {np.mean(x > 0):4.0%} {x.mean():+.3f}R/trade total {x.sum():+6.1f}R t {tt:+.2f} | "
                  f"${x.sum() * 500:+8,.0f} @$500 | 1/5/7 lots ${u:+8,.0f} / ${5 * u:+9,.0f} / ${7 * u:+9,.0f}")


if __name__ == "__main__":
    main()
