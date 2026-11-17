"""Round 6f-EXACT (2026-11-06 night): same as below but with EXACT quotes -- each month opens and closes at the LAST
H1 bar CLOSE before the month starts (bid close; ask = bid close + that close's spread), not the first bar's open (which
was often the Sunday rollover bar, inflated by the close-spread construction). Nothing else changed.
Round 6f: carry re-test with the BROKER's swap and volatility-scaled sizing (written before running).
Pairs with 2y yields on both sides: EURUSD, USDJPY, USDCAD, EURJPY, CADJPY (EURCAD: no FXCM bars). Monthly, from the
first H1 bar of each month: hold the higher-yield side (yields lagged one day). Carry earned = (|yield diff| - MARKUP)
per year, pro rata for the days held; MARKUP = what the broker keeps, measured on this DEMO account's own swaps
(2026-10/11: USDJPY long 1.9 %/yr vs 2.6 % gap, CADJPY long 0.7 vs 1.3, USDCAD long 0.9 vs 1.2 -> 0.35-1.0 %, mid 0.7).
If |diff| <= MARKUP the pair is not held that month (no positive carry on either side). Costs: real spread round trip
+ 0.3 bp per rebalance.
Sizing: VOL = weight min(2, 8 % / realised vol of the pair's daily returns over the previous 20 trading days,
annualised); FLAT = weight 1. Basket = mean over the pairs held that month.
Outputs per variant: monthly bp by split (DISC <= 2018 / VAL 2019-22 / LOCK 2023-26, grade as round 6), % per year,
monthly Sharpe (annualised), max drawdown, worst months, per pair. MARKUP sensitivity 0.5 / 0.7 / 1.0 / 1.5 %.
    python reports/round6/r6f_carry.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path[:0] = ["D:/Devops/trading/OpenTerminalUI", "D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import bars, by_split, grade  # noqa: E402

D_ = "D:/Devops/trading/OpenTerminalUI/data/"
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round6/"
PAIRS = {"EURUSD": ("EUR", "USD"), "USDJPY": ("USD", "JPY"), "USDCAD": ("USD", "CAD"), "EURJPY": ("EUR", "JPY"), "CADJPY": ("CAD", "JPY")}


def monthly(markup: float, sizing: str) -> pd.DataFrame:
    y = pd.read_csv(D_ + "macro/yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    rows = []
    for sym, (b_, q) in PAIRS.items():
        z = np.load(f"D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/{sym}_H1.npz")
        F = pd.DataFrame({"t": pd.to_datetime(z["t"] + 3600, unit="s"), "bid": z["c"], "ask": z["c"] + z["spr"]}).set_index("t")
        F["c"] = (F.bid + F.ask) / 2
        daily = F.c.resample("D").last().dropna()
        vol = (np.log(daily).diff().rolling(20, min_periods=15).std() * np.sqrt(252)).shift(1)
        M = F.groupby(F.index.to_period("M")).last()  # last close of each month = the price going into the next month
        M.index = M.index.to_timestamp() + pd.offsets.MonthBegin(1)
        for a, z in zip(M.index[:-1], M.index[1:]):
            yd = y.loc[:a]
            if not len(yd):
                continue
            yd = yd.iloc[-1]
            if pd.isna(yd[b_]) or pd.isna(yd[q]):
                continue
            diff = float(yd[b_] - yd[q])
            if abs(diff) <= markup:
                continue
            d = 1 if diff > 0 else -1
            v = vol.loc[:a]
            w = 1.0 if sizing == "FLAT" else (min(2.0, 0.08 / float(v.iloc[-1])) if len(v) and np.isfinite(v.iloc[-1]) and v.iloc[-1] > 0 else np.nan)
            if not np.isfinite(w):
                continue
            ent, ex_ = (M.ask.loc[a], M.bid.loc[z]) if d > 0 else (M.bid.loc[a], M.ask.loc[z])
            px = d * (ex_ - ent) / ent * 1e4  # spread already paid through bid / ask
            carry = (abs(diff) - markup) * 100 * (z - a).days / 365
            cost = 0.3
            rows.append({"t": a, "sym": sym, "w": w, "price_bp": px * w, "carry_bp": carry * w, "cost_bp": cost * w, "r": (px + carry - cost) * w})
    return pd.DataFrame(rows)


def summary(R: pd.DataFrame) -> dict:
    B = R.groupby("t").agg(r=("r", "mean"), price=("price_bp", "mean"), carry=("carry_bp", "mean"), n=("sym", "size")).reset_index()
    eq = B.r.cumsum()
    worst = B.nsmallest(4, "r")[["t", "r"]]
    out = by_split(B)
    out.update({"months": int(len(B)), "pct_per_year": round(float(B.r.mean() * 12 / 100), 2), "sharpe_ann": round(float(B.r.mean() / B.r.std() * np.sqrt(12)), 2),
                "max_dd_bp": round(float((eq - eq.cummax()).min()), 0), "worst_months": {str(t.date()): round(float(r), 0) for t, r in zip(worst.t, worst.r)},
                "mean_split": {"price_bp": round(float(B.price.mean()), 1), "carry_bp": round(float(B.carry.mean()), 1)},
                "per_pair_mean_bp": R.groupby("sym").r.mean().round(1).to_dict(), "pairs_held_avg": round(float(B.n.mean()), 2),
                "by_year_pct": (B.groupby(B.t.dt.year).r.sum() / 100).round(2).to_dict()})
    out["grade"] = grade(out)
    return out


def main() -> None:
    res = {}
    for markup in (0.5, 0.7, 1.0, 1.5):
        for sizing in ("VOL", "FLAT"):
            res[f"markup{markup}_{sizing}"] = summary(monthly(markup, sizing))
            v = res[f"markup{markup}_{sizing}"]
            print(f"markup {markup}% {sizing:4}  " + "  ".join(f"{s} {v[s].get('mean', 0):+6.1f}bp (t {v[s].get('t', 0):+.2f})" for s in ("DISC", "VAL", "LOCK"))
                  + f"  {v['pct_per_year']:+.2f}%/yr  Sharpe {v['sharpe_ann']}  maxDD {v['max_dd_bp']:.0f}bp  yrs+ {v['years_pos']}  -> {v['grade']}", flush=True)
    json.dump(res, open(OUT + "r6f_carry_EXACT_results.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
