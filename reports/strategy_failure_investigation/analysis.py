"""Read-only loss anatomy of the existing P2 v1 trades (observed_account costs, Friday 20:00 UTC close = live rule).
python reports/strategy_failure_investigation/analysis.py  -> anatomy.csv, trades_with_excursions.csv
MFE/MAE are computed from the DB M5 bid bars between entry and exit (shorts on bid + modelled spread, as the backtest)."""
import numpy as np, pandas as pd
from bensim.backtest import load_m5, exec_profile, _spread
from bensim.strategy import pip
OUT = "reports/strategy_failure_investigation"
t = pd.read_csv("reports/experiments/p2_2026-11-03/trades_observed_account.csv")
ex = exec_profile("observed_account")
rows = []
for sym, g in t.groupby("symbol"):
    m = load_m5(sym, "2026-07-01", "2026-11-04"); T = m["t"]
    for i, x in g.iterrows():
        a = np.searchsorted(T, x.t_entry); b = np.searchsorted(T, x.t_exit)
        seg = slice(a, max(a + 1, b))
        risk = abs(x.entry - x.sl); d = x.dir
        if d == 1:
            mfe = (m["high"][seg].max() - x.entry) / risk; mae = (x.entry - m["low"][seg].min()) / risk
        else:
            sp = np.array([_spread(sym, int(tt), ex) for tt in T[seg]])
            mfe = (x.entry - (m["low"][seg] + sp).min()) / risk; mae = ((m["high"][seg] + sp).max() - x.entry) / risk
        rows.append((i, mfe, mae, risk / pip(sym), abs(x.tp - x.entry) / pip(sym), (x.t_exit - x.t_entry) / 3600,
                     x.spread_pips_entry / (risk / pip(sym))))
e = pd.DataFrame(rows, columns=["i", "mfe_r", "mae_r", "stop_pips", "target_pips", "hold_h", "spread_r"]).set_index("i")
t = t.join(e)
t.to_csv(f"{OUT}/trades_with_excursions.csv", index=False)
def streak(r):
    m = c = 0
    for v in r:
        c = c + 1 if v < 0 else 0; m = max(m, c)
    return m
out = []
for s, g in t.groupby("strategy"):
    g = g.sort_values("t_exit"); r = g.r; eq = r.cumsum(); dd = (eq.cummax().clip(lower=0) - eq).max()
    L = g[g.r < 0]; W = g[g.r > 0]
    out.append({"strategy": s, "n": len(g), "win_rate": round((r > 0).mean(), 3), "avg_win_r": round(W.r.mean(), 2) if len(W) else None,
        "avg_loss_r": round(L.r.mean(), 2) if len(L) else None, "expectancy_r": round(r.mean(), 3), "pf": round(W.r.sum() / -L.r.sum(), 2) if len(L) else None,
        "max_dd_r": round(dd, 1), "max_consec_losses": streak(r.values), "avg_hold_h": round(g.hold_h.mean(), 1),
        "median_stop_pips": round(g.stop_pips.median(), 1), "median_target_pips": round(g.target_pips.median(), 1), "median_rr_planned": round(g.rr_planned.median(), 2),
        "target_hit": round((g.exit_reason == "target").mean(), 3), "stop_hit": round((g.exit_reason == "stop").mean(), 3),
        "time_or_friday": round(g.exit_reason.isin(["time", "friday_close"]).mean(), 3),
        "breakeven_win_rate_needed": round(1 / (1 + g.rr_planned.median()), 3),
        "median_mfe_r": round(g.mfe_r.median(), 2), "median_mae_r": round(g.mae_r.median(), 2),
        "losers_reaching_1R_first": round((L.mfe_r >= 1).mean(), 3) if len(L) else None,
        "losers_never_0p3R": round((L.mfe_r < 0.3).mean(), 3) if len(L) else None,
        "winners_mae_over_0p5R": round((W.mae_r >= 0.5).mean(), 3) if len(W) else None,
        "median_spread_r": round(g.spread_r.median(), 3), "cost_r_total": round((g.r_gross - g.r).mean() + g.spread_r.mean(), 3)})
a = pd.DataFrame(out); a.to_csv(f"{OUT}/anatomy.csv", index=False)
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 40); print(a.T.to_string())
