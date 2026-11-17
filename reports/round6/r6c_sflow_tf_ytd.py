"""Round 6c (user request 2026-11-06): sflow ORIGINAL (entry A) vs FLIPPED (A_placebo = the same fill replayed in the
opposite direction, same distances: sflow.py's own definition) on six structure timeframes, all pairs, 2026 year to date.
Reuses bensim.sflow.run() unchanged; only the (structure TF, lower TF) pair is added:
  M5/M5, M15/M5, M30/M5, H1/M5 (live), H4/M15 (in sflow), H12/H1. Fills and exits always on M5 bid + spread.
Data: FXCM M5 for the 25 FX pairs (FXCM's own spread, no commission), IC M5 for XAUUSD. Window 2026-01-01 .. data end.
CAVEAT (stated before running): 6 timeframes x 26 pairs x 2 directions on ~10 months = many comparisons on one short
window. This is exploration; anything that looks good must be confirmed on 2012-2025 before any use.
    python reports/round6/r6c_sflow_tf_ytd.py
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim import sflow  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round6/"
TFS = {"M5/M5": (300, 300), "M15/M5": (900, 300), "M30/M5": (1800, 300), "H1/M5": (3600, 300), "H4/M15": (14400, 900), "H12/H1": (43200, 3600)}
FX = ["AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD", "CADCHF", "CADJPY", "EURAUD", "EURCHF", "EURGBP", "EURJPY", "EURNZD", "EURUSD",
      "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD", "NZDCAD", "NZDCHF", "NZDJPY", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]


def _init() -> None:
    import os
    import ctypes
    sflow.PAIRS.update(TFS)
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL


def job(a):
    sym, tf, src = a
    try:
        rows = sflow.run((sym, tf, "2026-01-01", "2026-11-01", src))
    except Exception as exc:  # noqa: BLE001
        return [{"symbol": sym, "pair": tf, "kind": "ERROR", "r": np.nan, "err": repr(exc)[:200]}]
    return [dict(r, pair=tf) for r in rows if r["kind"] in ("A", "A_placebo")]


def stat(g: pd.DataFrame) -> dict:
    r = g.r.to_numpy()
    if len(r) < 5:
        return {"n": int(len(r))}
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if r.std() > 0 else 0.0
    return {"n": int(len(r)), "mean_R": round(float(r.mean()), 3), "total_R": round(float(r.sum()), 1), "t": round(float(t), 2), "win": round(float((r > 0).mean()), 3)}


def main() -> None:
    jobs = [(s, tf, "fxcm") for tf in TFS for s in FX] + [("XAUUSD", tf, "icm") for tf in TFS]
    if "--from-csv" in sys.argv:
        T = pd.read_csv(OUT + "r6c_trades.csv")
    else:
        with ProcessPoolExecutor(4, initializer=_init) as pool:
            T = pd.DataFrame([r for rs in pool.map(job, jobs) for r in rs])
        T.to_csv(OUT + "r6c_trades.csv", index=False)
    if "err" not in T:
        T["err"] = None
    err = T[T.kind == "ERROR"]
    T = T[T.kind != "ERROR"]
    T["side"] = np.where(T.kind == "A", "original", "flipped")
    res = {"window": "2026-01-01 .. data end (FXCM M5 to 2026-10-09)", "errors": err[["symbol", "pair", "err"]].to_dict(orient="records"), "by_tf": {}}
    for tf in TFS:
        g = T[T.pair == tf]
        o, f = g[g.side == "original"], g[g.side == "flipped"]
        byp = f.groupby("symbol").r.agg(["count", "mean", "sum"]).round(3)
        res["by_tf"][tf] = {"original": stat(o), "flipped": stat(f),
                            "flipped_pairs_positive": f"{int((byp['mean'] > 0).sum())}/{len(byp)}",
                            "flipped_best_pairs": byp.sort_values("sum").tail(5).to_dict(orient="index"),
                            "flipped_worst_pairs": byp.sort_values("sum").head(3).to_dict(orient="index"),
                            "flipped_by_month": f.groupby(pd.to_datetime(f.fill_t, unit="s").dt.month).r.sum().round(1).to_dict()}
    json.dump(res, open(OUT + "r6c_results.json", "w"), indent=1, default=str)
    for tf, v in res["by_tf"].items():
        print(f"{tf:7} original {v['original']}  |  FLIPPED {v['flipped']}  pairs+ {v['flipped_pairs_positive']}")
    print("errors:", len(err))


if __name__ == "__main__":
    main()
