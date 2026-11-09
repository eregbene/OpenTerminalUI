"""Round 8i (user request 2026-11-07): the LIVE Flow ML model (data/flow_models/flow_2026.pkl, trained on data <= 2024)
replayed on the broker's own H1 bars for the last ~2 months, ALL 25 pairs, with vs without the ATR-expansion filter.
Read-only (bridge rates). Live rules: every H1 close, best side's prediction > the live threshold, no entries at
20-00 UTC, one open trade per pair, exit after 6 h. Features exactly as live (bensim.flow_live / edge_miner functions,
computed over each pair's full bar history so every row only uses bars before it). Result = mid-to-mid bp minus the
model's own spread estimate for that pair-hour at both ends + 0.3 bp. ATR filter: ATR(6)/ATR(48) of the H1 range > 1.2.
    cd bensim_phase20_dev && python ../OpenTerminalUI/reports/round8/r8i_last2m.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/bensim_phase20_dev")
from intel import flow_shadow as FS  # noqa: E402  (sets the live repo paths, read-only)

F, em = FS.F, FS.em
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round8/"
START = pd.Timestamp("2026-09-07")
LIVE5 = ["EURUSD", "USDJPY", "USDCAD", "EURJPY", "CADJPY"]
AUD = ["AUDJPY", "AUDUSD", "EURAUD", "AUDNZD", "AUDCAD", "AUDCHF"]
LATE = {20, 21, 22, 23, 0}


def main() -> None:
    mdl = F.model()
    frames, atr, mids = [], {}, {}
    for s in F.UNIVERSE:
        m = F.h1(s, 2000)
        if not len(m["t"]):
            continue
        hrs = ((m["t"] + 3600) // 3600 % 24).astype(int)
        m["spr"] = np.array([mdl["spread_bp"].get((s, h), np.nan) for h in hrs]) / 1e4 * m["close"]
        f = em.features(s, m)
        frames.append(f)
        rng = pd.Series(m["high"] - m["low"])
        atr[s] = pd.Series((rng.rolling(6).mean() / rng.rolling(48).mean()).to_numpy(), index=pd.to_datetime(m["t"] + 3600, unit="s"))
        mids[s] = pd.Series(f.mid.to_numpy(), index=f.t)
    f = em.add_strength(pd.concat(frames, ignore_index=True))
    f = f[f.t >= START].copy()
    f = F.add_gaps(f)
    pl = np.mean([ml.predict(f[mdl["feats"]]) for ml, _ in mdl["models"]], axis=0)
    ps = np.mean([ms.predict(f[mdl["feats"]]) for _, ms in mdl["models"]], axis=0)
    f = f.assign(pl=pl, ps=ps).sort_values(["symbol", "t"])
    rows = []
    for s, g in f.groupby("symbol"):
        busy = pd.Timestamp(0)
        for r in g.itertuples():
            if r.t.hour in LATE or r.t < busy or max(r.pl, r.ps) <= mdl["thr"]:
                continue
            exit_t = r.t + pd.Timedelta(hours=6)
            if exit_t not in mids[s].index:
                continue
            d = 1 if r.pl >= r.ps else -1
            gross = d * (mids[s].at[exit_t] - r.mid) / r.mid * 1e4
            cost = mdl["spread_bp"].get((s, r.t.hour), np.nan) / 2 + mdl["spread_bp"].get((s, exit_t.hour), np.nan) / 2 + 0.3
            rows.append({"symbol": s, "t": r.t, "side": d, "pred": max(r.pl, r.ps), "net_bp": gross - cost, "atr648": float(atr[s].asof(r.t))})
            busy = exit_t
    T = pd.DataFrame(rows)
    T["atr_up"] = T.atr648 > 1.2
    T["group"] = np.where(T.symbol.isin(LIVE5), "LIVE5", np.where(T.symbol.isin(AUD), "AUD", "OTHER14"))
    T.to_csv(OUT + "r8i_last2m_trades.csv", index=False)

    def s_(g):
        return {"n": int(len(g)), "mean_bp": round(float(g.net_bp.mean()), 2) if len(g) else None, "total_bp": round(float(g.net_bp.sum()), 1),
                "win": round(float((g.net_bp > 0).mean()), 3) if len(g) else None}
    res = {"window": f"{T.t.min()} .. {T.t.max()}", "threshold": mdl["thr"], "model_year": mdl["year"]}
    for grp in ("LIVE5", "AUD", "OTHER14", "ALL"):
        g = T if grp == "ALL" else T[T.group == grp]
        res[grp] = {"all_signals": s_(g), "atr_expanding_only": s_(g[g.atr_up]), "not_expanding": s_(g[~g.atr_up])}
    res["by_pair"] = {s: {"all": s_(g), "atr_only": s_(g[g.atr_up])} for s, g in T.groupby("symbol")}
    json.dump(res, open(OUT + "r8i_last2m_results.json", "w"), indent=1, default=str)
    print("window", res["window"], "| model", res["model_year"], "thr", round(res["threshold"], 2))
    for grp in ("LIVE5", "AUD", "OTHER14", "ALL"):
        v = res[grp]
        print(f"{grp:8} all {v['all_signals']} | ATR expanding {v['atr_expanding_only']} | not expanding {v['not_expanding']}")
    print("by pair (all / ATR only, mean bp, n):")
    for s, v in sorted(res["by_pair"].items(), key=lambda kv: -(kv[1]["all"]["total_bp"])):
        print(f"  {s}: all {v['all']['mean_bp']} (n {v['all']['n']}) | ATR {v['atr_only']['mean_bp']} (n {v['atr_only']['n']})")


if __name__ == "__main__":
    main()
