"""Round 24c (2026-11-09, user idea): keep the NORMAL target, widen the STOP to at least target-distance / k
(k = 1, 2, 3 -> reward:risk capped at 1:1, 1:2, 1:3); his stop is kept when it is already wider. Same trades as
bennett_3m.py (reads bennett_3m_trades.csv), same M5 walker (stop first on ties, short on ask, Friday 20:00 close,
10-day max), R net of the round-trip cost.
Money: (a) fixed $ risk per trade ($500: lots shrink when the stop widens) and (b) fixed lots 1/5/7 (a wider stop = a
bigger loss per losing trade).
    python reports/round24/bennett_3m_minstop.py
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI/reports/round24")
sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim.strategy import pip  # noqa: E402

import bennett_3m as b3  # noqa: E402  (re-uses its M5 data + walker; importing it does NOT rerun the backtest -- see guard)

df = pd.read_csv("D:/Devops/trading/OpenTerminalUI/reports/round24/bennett_3m_trades.csv")
tp_dist = (df.tp - df.entry).abs()
rows = []
for k in (None, 1, 2, 3):
    rk = (df.entry - df.sl).abs() if k is None else np.maximum((df.entry - df.sl).abs(), tp_dist / k)
    sl = df.entry - df.dir * rk
    cost = df.sym.map(b3.COST) * df.sym.map(pip) / rk
    r = np.array([b3.walk(x.sym, x.dir, int(x.t_entry), x.entry, s, x.tp) for x, s in zip(df.itertuples(), sl)]) - cost
    pv = (rk / df.sym.map(pip)) * [b3.pipval(s, e) for s, e in zip(df.sym, df.entry)]
    rows.append(pd.DataFrame({"cfg": df.cfg, "variant": "his stop" if k is None else f"stop >= TP/{k} (max 1:{k})",
                              "r": r, "usd1": r * pv, "stop_pips": rk / df.sym.map(pip)}))
R = pd.concat(rows)
for c, g in R.groupby("cfg", sort=False):
    print(f"\n== {c} ({len(g) // 4} trades, normal target)")
    for v, x in g.groupby("variant", sort=False):
        u = x.usd1.sum()
        t = x.r.mean() / x.r.std() * np.sqrt(len(x)) if len(x) > 1 else 0
        print(f"   {v:22} median stop {x.stop_pips.median():5.1f}p win {np.mean(x.r > 0):4.0%} {x.r.mean():+.3f}R t {t:+.2f} "
              f"total {x.r.sum():+6.1f}R | ${x.r.sum() * 500:+8,.0f} @$500 risk | fixed 1/5/7 lots ${u:+8,.0f} / ${5 * u:+9,.0f} / ${7 * u:+9,.0f}")
