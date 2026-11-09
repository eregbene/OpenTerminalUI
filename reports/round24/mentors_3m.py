"""Round 24d (2026-11-09): 3-month tests (2026-08-07 .. 2026-11-06, IC M5, 7 symbols) of the MENTOR-MATCHED versions vs
the old code, fixed before the run:
  Faiz OLD      orderflow flow mode as in October (internal structure, M15, 2R minimum, 12 h setup life)
  Faiz MATCHED  swing structure, M5, no stated minimum (1R), 24 h setup life -- reproduces his F1 + F4 trades exactly
  JeaFx OLD     sflow entry A, zone dies at the next BOS, structure H1 (live book) and M15
  JeaFx MATCHED sflow entry A, zone lives until touched / closed through (reproduces his NZDJPY +5.42% long)
R net of costs; $ = R x $500 and fixed 1/5/7 lots on the 100k.
    python reports/round24/mentors_3m.py
"""
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
S, E = "2026-08-07", "2026-11-07"
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]


def pipval(sym, price):
    return 1000 / price if sym == "USDJPY" else 10 / price if sym in ("USDCAD", "USDCHF") else 10.0


def faiz(a):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    from bensim import orderflow_strategy as of
    from bensim.backtest import exec_profile, run_symbol
    name, sym = a
    P = of.OrderFlowParams
    p = P(mode="flow", breaks="any", flow_min_rr=2.0) if name == "Faiz OLD" else \
        P(mode="flow", breaks="any", structure="swing", entry_sec=300, plan_bars=288, flow_min_rr=1.0)
    t = run_symbol((sym, S + "T00:00:00+00:00", E + "T00:00:00+00:00", p, exec_profile("observed_account"), "orderflow"))
    if t.empty:
        return pd.DataFrame()
    return pd.DataFrame({"cfg": name, "sym": sym, "r": t.r, "stop_pips": t.risk_pips, "entry": t.entry, "dir": t.dir,
                         "t_entry": t.t_entry, "sl": t.sl, "tp": t.tp})


def jeafx(a):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    from bensim import sflow
    from bensim.strategy import pip
    name, pair, sym = a
    sflow.PAIRS["M15/M5"] = (900, 300)
    sflow.ZONE_LIFE = "touch" if "MATCHED" in name else "next_bos"
    rows = [r for r in sflow.run((sym, pair, S, E, "ic")) if r["kind"] == "A"]
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    return pd.DataFrame({"cfg": f"{name} {pair}", "sym": sym, "r": d.r, "stop_pips": (d.entry - d.stop).abs() / pip(sym), "entry": d.entry,
                         "dir": d.dir, "t_entry": d.fill_t, "sl": d.stop, "tp": d.target})


if __name__ == "__main__":
    with ProcessPoolExecutor(3) as pool:
        fr = list(pool.map(faiz, [(n, s) for n in ("Faiz OLD", "Faiz MATCHED") for s in SYMS]))
        fr += list(pool.map(jeafx, [(n, pr, s) for n in ("JeaFx OLD", "JeaFx MATCHED") for pr in ("H1/M5", "M15/M5") for s in SYMS]))
    df = pd.concat([f for f in fr if not f.empty], ignore_index=True)
    df["usd1"] = df.r * df.stop_pips * [pipval(s, e) for s, e in zip(df.sym, df.entry)]
    df.to_csv("D:/Devops/trading/OpenTerminalUI/reports/round24/mentors_3m_trades.csv", index=False)
    print(f"window {S} .. {E}, 7 symbols, R net of costs")
    for c, g in df.groupby("cfg", sort=False):
        u, t = g.usd1.sum(), (g.r.mean() / g.r.std() * np.sqrt(len(g)) if len(g) > 1 else 0)
        print(f"{c:24} {len(g):4} tr win {np.mean(g.r > 0):4.0%} {g.r.mean():+.3f}R t {t:+.2f} total {g.r.sum():+6.1f}R | "
              f"${g.r.sum() * 500:+8,.0f} @$500 | 1/5/7 lots ${u:+8,.0f} / ${5 * u:+9,.0f} / ${7 * u:+9,.0f} | "
              f"per sym {g.groupby('sym').r.sum().round(1).to_dict()}")
