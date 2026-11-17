"""Round 5: ADAPTIVE regime switching per strategy (written before running; nothing tuned on the result).

Question: does each strategy work only in some market regimes, and can a switch that LEARNS those regimes from the
strategy's own past trades improve the next year out of sample?

Trades: reports/round4/r4_hist.load() = the nine strategies' historical trades, net of costs (R; flow ML / AM in bp).
Regime of a trade = market_intel.regime.series on the pair's H1 bars (FXCM mid; XAUUSD from IC M5), the last bar CLOSED
before entry:  range / transitioning / trend-WITH the trade / trend-AGAINST the trade  x  volatility low / normal / high
(ATR14 percentile over the previous 6,200 H1 bars: <= 20 / 20-80 / >= 80)  = 12 cells.

Walk-forward, test years 2014..2026; each year's decision uses ONLY trades that EXITED before 1 January of that year:
  BASE   trade everything (the strategy as it is)
  RA     regime switch: a cell is OFF for the year if its trailing-3-year trades (n >= 20) have mean < 0
  RB     regime switch, stricter: OFF only if that mean < 0 with t <= -1.0 (n >= 20)
  MOM    strategy momentum: trade only while the strategy's last 50 CLOSED trades have mean > 0 (checked at each entry)
Cells with too little history stay ON (no change without evidence).
Pass (per strategy x policy): the trades the policy REMOVES average < 0 in BOTH halves (2014-19, 2020-26), the policy's
total beats BASE in both halves, and Welch t (removed vs kept) <= -2; Benjamini-Hochberg over all 27 (9 x 3) tests.
    python reports/round5/r5_regime_switch.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path[:0] = ["D:/Devops/trading/bensim_phase20_dev", "D:/Devops/trading/OpenTerminalUI", "D:/Devops/trading/OpenTerminalUI/reports/round4"]
import r4_hist  # noqa: E402
from bensim.backtest import load_m5, resample  # noqa: E402
from market_intel import regime as mreg  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round5/"
FX = "D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/"
YEARS = range(2014, 2027)


def regimes(sym: str) -> pd.DataFrame | None:
    if sym == "XAUUSD":
        m5 = load_m5("XAUUSD", "2020-06-01", "2026-11-01")
        if not len(m5["t"]):
            return None
        b = resample(m5, 3600)
    else:
        try:
            z = np.load(FX + f"{sym}_H1.npz")
        except FileNotFoundError:
            return None
        half = z["spr"] / 2
        b = {"t": z["t"].astype(np.int64), "open": z["o"] + half, "high": z["h"] + half, "low": z["l"] + half, "close": z["c"] + half}
    s = mreg.series(b)
    atr = pd.Series(s["atr14"])
    pct = atr.rolling(6200, min_periods=1000).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)  # causal percentile
    return pd.DataFrame({"t_close": pd.to_datetime(b["t"] + 3600, unit="s"), "label": s["label"], "direction": s["direction"], "atr_pct": pct.to_numpy()})


def cell(label: int, direction: int, d: int, pct: float) -> str | None:
    if np.isnan(pct):
        return None
    reg = {0: "range", 1: "transition"}.get(int(label)) or ("trend_with" if int(direction) == d else "trend_against")
    vol = "lowvol" if pct <= 0.2 else ("highvol" if pct >= 0.8 else "normvol")
    return f"{reg}|{vol}"


def main() -> None:
    S = r4_hist.load()
    syms = sorted({s for T in S.values() for s in T.symbol.unique()})
    R = {s: regimes(s) for s in syms}
    res, tests, keep_cols = {}, [], {}
    for name, T in S.items():
        T = T.sort_values("t").reset_index(drop=True)
        T["cell"] = None
        for sym, g in T.groupby("symbol"):
            rg = R.get(sym)
            if rg is None:
                continue
            j = np.searchsorted(rg.t_close.to_numpy(), g.t.to_numpy(), side="right") - 1
            ok = j >= 0
            jj = np.where(ok, j, 0)
            T.loc[g.index, "cell"] = [cell(rg.label.iat[k], rg.direction.iat[k], d, rg.atr_pct.iat[k]) if o else None
                                      for k, d, o in zip(jj, g.dir.to_numpy(), ok)]
        T = T[T.cell.notna()].copy()  # earlier years stay: they are the training history
        T["year"] = T.t.dt.year
        pol = {"RA": np.ones(len(T), bool), "RB": np.ones(len(T), bool), "MOM": np.ones(len(T), bool)}
        offcells = {}
        for Y in YEARS:
            cut = pd.Timestamp(f"{Y}-01-01")
            hist = T[(T.t_exit < cut) & (T.t >= pd.Timestamp(f"{Y - 3}-01-01"))]
            yr = (T.year == Y).to_numpy()
            off_a, off_b = set(), set()
            for c, h in hist.groupby("cell"):
                if len(h) >= 20 and h.r.mean() < 0:
                    off_a.add(c)
                    tt = h.r.mean() / (h.r.std(ddof=1) / np.sqrt(len(h))) if h.r.std() > 0 else 0
                    if tt <= -1.0:
                        off_b.add(c)
            offcells[Y] = {"RA": sorted(off_a), "RB": sorted(off_b)}
            pol["RA"][yr] = ~T.cell[yr].isin(off_a).to_numpy()
            pol["RB"][yr] = ~T.cell[yr].isin(off_b).to_numpy()
        # strategy momentum: mean of the last 50 trades CLOSED before each entry
        ex = T.sort_values("t_exit")
        ex_t, ex_r = ex.t_exit.to_numpy(), ex.r.to_numpy()
        csum = np.r_[0, np.cumsum(ex_r)]
        k = np.searchsorted(ex_t, T.t.to_numpy(), side="left")  # number of trades closed before entry
        last50 = np.where(k >= 50, (csum[k] - csum[np.maximum(k - 50, 0)]) / 50, np.nan)
        pol["MOM"] = np.where(np.isnan(last50), True, last50 > 0)
        test = T.year.between(min(YEARS), max(YEARS)).to_numpy()
        out = {"unit": T.unit.iloc[0], "n_test": int(test.sum()), "base_total": round(float(T.r[test].sum()), 1), "base_mean": round(float(T.r[test].mean()), 4)}
        cells = T[test].groupby("cell").r.agg(["count", "mean"]).round(3)
        out["cells_all_years"] = cells.to_dict(orient="index")
        for p, m in pol.items():
            kept, rem = test & m, test & ~m
            row = {"kept": int(kept.sum()), "removed": int(rem.sum()), "total": round(float(T.r[kept].sum()), 1),
                   "mean_kept": round(float(T.r[kept].mean()), 4) if kept.any() else None, "mean_removed": round(float(T.r[rem].mean()), 4) if rem.any() else None}
            halves = {}
            for hname, (a, b) in (("2014-19", (2014, 2019)), ("2020-26", (2020, 2026))):
                h = T.year.between(a, b).to_numpy()
                halves[hname] = {"base": round(float(T.r[test & h].sum()), 1), "policy": round(float(T.r[kept & h].sum()), 1),
                                 "removed_mean": round(float(T.r[rem & h].mean()), 4) if (rem & h).any() else None, "removed_n": int((rem & h).sum())}
            row["halves"] = halves
            tt = stats.ttest_ind(T.r[rem], T.r[kept], equal_var=False) if rem.sum() > 5 and kept.sum() > 5 else None
            row["t_removed_vs_kept"] = round(float(tt.statistic), 2) if tt is not None else None
            row["p_one_sided"] = float(tt.pvalue / 2) if (tt is not None and tt.statistic < 0) else 1.0
            row["by_year_policy_minus_base"] = {int(Y): round(float(T.r[kept & (T.year == Y).to_numpy()].sum() - T.r[test & (T.year == Y).to_numpy()].sum()), 1) for Y in YEARS if ((T.year == Y) & test).any()}
            out[p] = row
            tests.append({"strategy": name, "policy": p, "p": row["p_one_sided"], "t": row["t_removed_vs_kept"],
                          "both_halves_removed_negative": all((v["removed_mean"] is not None and v["removed_mean"] < 0) for v in halves.values()),
                          "both_halves_better": all(v["policy"] > v["base"] for v in halves.values()), "removed": row["removed"],
                          "gain": round(row["total"] - out["base_total"], 1)})
        out["off_cells_by_year_RB"] = {y: v["RB"] for y, v in offcells.items()}
        res[name] = out
        print(name, {p: (out[p]["total"], out[p]["removed"], out[p]["t_removed_vs_kept"]) for p in pol}, "base", out["base_total"], flush=True)
    Tt = pd.DataFrame(tests)
    p = Tt.p.to_numpy()
    o, m = np.argsort(p), len(p)
    ok = p[o] <= 0.05 * np.arange(1, m + 1) / m
    bh = np.zeros(m, bool)
    if ok.any():
        bh[o[: np.max(np.flatnonzero(ok)) + 1]] = True
    Tt["bh"] = bh
    Tt["PASS"] = Tt.bh & Tt.both_halves_removed_negative & Tt.both_halves_better & (Tt.t <= -2)
    Tt.to_csv(OUT + "r5_tests.csv", index=False)
    res["_tests"] = Tt.to_dict(orient="records")
    json.dump(res, open(OUT + "r5_results.json", "w"), indent=1, default=str)
    print(Tt.to_string())


if __name__ == "__main__":
    main()
