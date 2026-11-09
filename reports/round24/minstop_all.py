"""Round 24e (2026-11-09): the user's stop rule on EVERY strategy updated today (3 months 2026-08-07 .. 2026-11-06):
keep the normal target, stop = max(own stop, target distance) (reward:risk at most 1:1), vs the strategy's own stop.
Trades: bennett_3m_trades.csv (Bennett) + mentors_3m_trades.csv (Faiz, JeaFx). Same M5 walker as bennett_3m.py
(stop first on ties, short on ask, Friday 20:00 UTC close, 10-day max); R net of the round-trip cost.
VERDICT rule (fixed before the run): adopt the 1:1 stop for a strategy only if it beats the own stop in total R AND in
$ at $500 risk AND its win rate clears the break-even win rate after costs.
    python reports/round24/minstop_all.py
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI/reports/round24")
sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim.strategy import pip  # noqa: E402

import bennett_3m as b3  # noqa: E402

D = "D:/Devops/trading/OpenTerminalUI/reports/round24/"
bn = pd.read_csv(D + "bennett_3m_trades.csv")[["cfg", "sym", "dir", "t_entry", "entry", "sl", "tp"]]
bn["cfg"] = "Bennett " + bn.cfg
mt = pd.read_csv(D + "mentors_3m_trades.csv")[["cfg", "sym", "dir", "t_entry", "entry", "sl", "tp"]]
T = pd.concat([bn, mt], ignore_index=True)
T = T[(T.tp - T.entry) * T.dir > 0].reset_index(drop=True)  # needs a real target ahead
from bensim.backtest import resample  # noqa: E402
from bensim.strategy import atr  # noqa: E402

A15 = {}
for s_ in T.sym.unique():
    b15 = resample(b3.M5[s_], 900)
    A15[s_] = (b15["t"], atr(b15))


def atr_at(sym, t):
    tt, a = A15[sym]
    k = max(0, int(np.searchsorted(tt + 900, t, side="right")) - 1)  # last closed M15 bar
    return float(a[k])


T["atr15"] = [atr_at(s_, int(t_)) for s_, t_ in zip(T.sym, T.t_entry)]
out = []
for rule in ("own stop", "stop >= target (1:1)", "stop >= 1 ATR(15M)"):
    own = (T.entry - T.sl).abs()
    rk = own if rule == "own stop" else np.maximum(own, (T.tp - T.entry).abs()) if "1:1" in rule else np.maximum(own, T.atr15)
    sl = T.entry - T.dir * rk
    cost = T.sym.map(b3.COST) * T.sym.map(pip) / rk
    r = np.array([b3.walk(x.sym, x.dir, int(x.t_entry), x.entry, s, x.tp) for x, s in zip(T.itertuples(), sl)]) - cost
    rr = (T.tp - T.entry).abs() / rk
    pv = (rk / T.sym.map(pip)) * [b3.pipval(s, e) for s, e in zip(T.sym, T.entry)]
    out.append(pd.DataFrame({"cfg": T.cfg, "rule": rule, "r": r, "usd1": r * pv, "stop_pips": rk / T.sym.map(pip),
                             "be": (1 + cost) / (rr + 1)}))  # break-even win rate incl. cost
R = pd.concat(out)
print("3 months 2026-08-07 .. 2026-11-06, normal targets; break-even win% = (1 + cost) / (1 + reward:risk)")
for c, g in R.groupby("cfg", sort=False):
    a = g[g.rule == "own stop"]
    alts = [g[g.rule == r_] for r_ in ("stop >= target (1:1)", "stop >= 1 ATR(15M)")]
    best = [x for x in alts if x.r.sum() > a.r.sum() and x.r.sum() * 500 > a.r.sum() * 500 and np.mean(x.r > 0) > x.be.mean()]
    verdict = ("ADOPT " + max(best, key=lambda x: x.r.sum()).rule.iloc[0]) if best else "keep own stop"
    print(f"\n== {c} ({len(a)} trades)  VERDICT: {verdict}")
    for v, x in [(a.rule.iloc[0], a)] + [(x.rule.iloc[0], x) for x in alts]:
        u = x.usd1.sum()
        print(f"   {v:22} median stop {x.stop_pips.median():6.1f}p win {np.mean(x.r > 0):4.0%} (need {x.be.mean():4.0%}) "
              f"{x.r.mean():+.3f}R total {x.r.sum():+6.1f}R | ${x.r.sum() * 500:+8,.0f} @$500 | 1/5/7 lots ${u:+8,.0f} / ${5 * u:+9,.0f} / ${7 * u:+9,.0f}")
