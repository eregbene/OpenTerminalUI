"""Round 24 (2026-11-09): Bennett 5 years (2021-10-12 .. 2026-10-31, IC M5 from the DB; USDCAD only from 2024-02).
PRE-REGISTERED configs (fixed before the run, after the manual frame check of his real trades):
  LIVE        the live DEMO config (external 4H leg, 30M agree, killzones)
  BASE        base video defaults (external 1H leg)
  LATEST1     base video + leg_anchor=latest (fib on the newest 1H break, swing pull-back >= 1 ATR), need_fvg off
  LATEST2F    base video + latest, swing >= 2 ATR, FVG in the OTE required (reproduced his 5 Jan entry 1.17217 exactly)
  LIVE_LATEST LIVE + latest (1 ATR)
Halves: 2021-10..2024-04 / 2024-04..2026-10. R net of costs (observed_account profile).
    python reports/round24/bennett_5y.py
"""
import dataclasses
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim import bennett_strategy as bstrat  # noqa: E402
from bensim.backtest import exec_profile, run_symbol  # noqa: E402

S, E, MID = "2021-10-12T00:00:00+00:00", "2026-10-31T00:00:00+00:00", pd.Timestamp("2024-04-20", tz="UTC")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
B, R = bstrat.BennettParams, dataclasses.replace
CFG = {"LIVE": bstrat.LIVE, "BASE": B(),
       "LATEST1": B(leg_anchor="latest", leg_swing_atr=1.0, need_fvg=False),
       "LATEST2F": B(leg_anchor="latest", leg_swing_atr=2.0, need_fvg=True),
       "LIVE_LATEST": R(bstrat.LIVE, leg_anchor="latest", leg_swing_atr=1.0)}
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round24/"


def job(a):
    name, sym = a
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    t = run_symbol((sym, S, E, CFG[name], exec_profile("observed_account"), "bennett"))
    return t.assign(cfg=name, sym=sym) if not t.empty else t


def pipval(sym, price):
    return 1000 / price if sym == "USDJPY" else 10 / price if sym in ("USDCAD", "USDCHF") else 10.0


if __name__ == "__main__":
    jobs = [(c, s) for c in CFG for s in SYMS]
    with ProcessPoolExecutor(3) as pool:
        fr = []
        for (c, s), t in zip(jobs, pool.map(job, jobs)):
            print("done", c, s, len(t), flush=True)
            if not t.empty:
                fr.append(t)
    df = pd.concat(fr, ignore_index=True)
    df.to_csv(OUT + "bennett_5y_trades.csv", index=False)
    df["t"] = pd.to_datetime(df.t_entry, unit="s", utc=True) if df.t_entry.dtype != object else pd.to_datetime(df.t_entry, utc=True)
    df["usd1"] = df.r * df.risk_pips * [pipval(s, e) for s, e in zip(df.sym, df.entry)]
    for c, g in df.groupby("cfg"):
        tt = g.r.mean() / g.r.std() * np.sqrt(len(g))
        h1, h2 = g[g.t < MID], g[g.t >= MID]
        u = g.usd1.sum()
        print(f"\n{c:12} {len(g):4} tr win {np.mean(g.r > 0):4.0%} {g.r.mean():+.3f}R t {tt:+.2f} total {g.r.sum():+7.1f}R | "
              f"h1 {len(h1)} {h1.r.mean():+.3f} | h2 {len(h2)} {h2.r.mean():+.3f} | $500: ${g.r.sum() * 500:+,.0f} | "
              f"1/5/7 lots ${u:+,.0f} / ${5 * u:+,.0f} / ${7 * u:+,.0f}")
        print("   per year:", g.groupby(g.t.dt.year).r.sum().round(1).to_dict())
        print("   per sym :", g.groupby("sym").r.sum().round(1).to_dict())
