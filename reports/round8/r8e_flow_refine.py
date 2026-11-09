"""Round 8e: refine the two REAL edges (written before running).
(a) FLOW AM TIMING: EURUSD short, entry at the H1 close of hour E in {08, 09, 10} UTC (i.e. at E:00... the bar ending
    at E+1? no: entry at the close of the bar that ENDS at E:00), exit at the close of the bar ending at X in {11, 12, 13}
    UTC, weekdays, exact quotes (sell the bid, buy back the ask) - 0.3 bp. 9 variants. Pick the best on DISC (2012-18)
    ONLY; report it on VAL / LOCK next to the live 09 -> 12. No BH needed for the pick, but the pick must beat the live
    variant on VAL and LOCK to be called better.
(b) VOLATILITY-MANAGED SIZING (Moreira & Muir 2017): weight = (median EURUSD / pair realised vol over the previous year)
    / (realised vol over the previous 20 trading days), capped 0.25 .. 2.0, applied to (1) the flow ML walk-forward
    trades (data/macro/ensemble_trades.csv, live late-hour skip applied) and (2) the flow AM trades (live 09 -> 12).
    Compared on the SAME trades: mean bp, Sharpe of the daily P&L (annualised), max drawdown, by split. Sizing only
    (no trade added or removed). Better = higher Sharpe in both 2016-20 and 2021-26 (flow ML) / DISC and LOCK (AM).
    python reports/round8/r8e_flow_refine.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path[:0] = ["D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import by_split  # noqa: E402

FX = "D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/"
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round8/"


def h1(sym: str) -> pd.DataFrame:
    z = np.load(FX + f"{sym}_H1.npz")
    F = pd.DataFrame({"t": pd.to_datetime(z["t"] + 3600, unit="s"), "bid": z["c"], "ask": z["c"] + z["spr"]}).set_index("t")
    F["mid"] = (F.bid + F.ask) / 2
    return F


def am(F: pd.DataFrame, e: int, x: int) -> pd.DataFrame:
    rows = []
    for day in pd.date_range("2012-01-02", "2026-10-09", freq="B"):
        a, b = day + pd.Timedelta(hours=e), day + pd.Timedelta(hours=x)
        if a in F.index and b in F.index:
            rows.append({"t": day, "r": (F.bid.at[a] - F.ask.at[b]) / F.bid.at[a] * 1e4 - 0.3})
    return pd.DataFrame(rows)


def vol_weights(F: pd.DataFrame) -> pd.Series:
    d = F.mid.groupby(F.index.normalize()).last()
    lr = np.log(d).diff()
    rv20 = lr.rolling(20, min_periods=15).std().shift(1)
    target = rv20.rolling(250, min_periods=120).median()
    return (target / rv20).clip(0.25, 2.0)


def sharpe_dd(df: pd.DataFrame, col: str) -> dict:
    day = df.groupby(df.t.dt.normalize())[col].sum()
    eq = day.cumsum()
    return {"sharpe_ann": round(float(day.mean() / day.std() * np.sqrt(252)), 2) if day.std() > 0 else None,
            "max_dd_bp": round(float((eq - eq.cummax()).min()), 0), "mean_bp": round(float(df[col].mean()), 3), "n": int(len(df))}


def main() -> None:
    res = {}
    E = h1("EURUSD")
    grid = {(e, x): am(E, e, x) for e in (8, 9, 10) for x in (11, 12, 13)}
    tab = {f"{e:02d}->{x:02d}": by_split(R) for (e, x), R in grid.items()}
    best = max(tab, key=lambda k: tab[k]["DISC"].get("t", -9))
    res["a_am_grid"] = tab
    res["a_pick_on_DISC"] = {"pick": best, "pick_stats": tab[best], "live_09->12": tab["09->12"],
                             "pick_beats_live_on_VAL_and_LOCK": bool(tab[best]["VAL"].get("mean", -9) > tab["09->12"]["VAL"].get("mean", 9)
                                                                    and tab[best]["LOCK"].get("mean", -9) > tab["09->12"]["LOCK"].get("mean", 9))}
    # (b) vol-managed sizing
    ens = pd.read_csv("D:/Devops/trading/OpenTerminalUI/data/macro/ensemble_trades.csv", parse_dates=["t"])
    ens = ens[~ens.hour.isin([20, 21, 22, 23, 0])]
    W = {s: vol_weights(h1(s)) for s in ens.symbol.unique()}
    ens["w"] = [W[s].asof(t.normalize()) if len(W[s].dropna()) else np.nan for s, t in zip(ens.symbol, ens.t)]
    ens = ens.dropna(subset=["w"])
    ens["managed"] = ens.bp * ens.w
    ens["half"] = np.where(ens.t.dt.year <= 2020, "2016-20", "2021-26")
    res["b_flowml"] = {h: {"flat": sharpe_dd(g, "bp"), "vol_managed": sharpe_dd(g, "managed"), "avg_weight": round(float(g.w.mean()), 3)}
                       for h, g in ens.groupby("half")}
    A = grid[(9, 12)].copy()
    wE = vol_weights(E)
    A["w"] = [wE.asof(t) for t in A.t]
    A = A.dropna(subset=["w"])
    A["managed"] = A.r * A.w
    A["split"] = np.where(A.t.dt.year <= 2018, "DISC", np.where(A.t.dt.year <= 2022, "VAL", "LOCK"))
    res["b_flowam"] = {s: {"flat": sharpe_dd(g, "r"), "vol_managed": sharpe_dd(g, "managed"), "avg_weight": round(float(g.w.mean()), 3)}
                       for s, g in A.groupby("split")}
    json.dump(res, open(OUT + "r8e_results.json", "w"), indent=1, default=str)
    print("AM grid (DISC/VAL/LOCK mean bp, t):")
    for k, v in tab.items():
        print(" ", k, [(round(v[s].get("mean", 0), 2), round(v[s].get("t", 0), 2)) for s in ("DISC", "VAL", "LOCK")], v["years_pos"])
    print("pick", res["a_pick_on_DISC"]["pick"], "beats live on VAL & LOCK:", res["a_pick_on_DISC"]["pick_beats_live_on_VAL_and_LOCK"])
    print("flow ML vol-managed:", json.dumps(res["b_flowml"]))
    print("flow AM vol-managed:", json.dumps(res["b_flowam"]))


if __name__ == "__main__":
    main()
