"""Combined strategies on the clean 2-year data (ForexSB/Dukascopy, 2024-02-12 -> 2026-10-09).

Imbalance trades come from clean_retest (saved CSV). Donchian combination B (breakout -> entry at its own
imbalance zone) is run here on clean M15. Grid of combinations is fixed below; a learn/test check picks the
best combination on 2024 only and reports it on 2025-2026 unchanged.

python -m bensim_research.clean_combos
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

from . import clean_data as C
from . import data as D
from . import donchian as DC
from .clean_retest import START
from .imb_wf import DEST
from .run import OUT


def combo_b() -> pd.DataFrame:
    rows = []
    for sym in D.SYMBOLS:
        m15 = D.resample(C.load(sym, "M5"), "M15")
        t = DC.imb_entry_trades(sym, m15, DC.signals(m15), START, int(m15.t[-1]))
        if not t.empty:
            t["data_hole"] = D.overlaps_hole(D.holes(m15), t.t_entry.to_numpy(), t.t_exit.to_numpy())
            rows.append(t)
    return pd.concat(rows, ignore_index=True)


def _b_history(sym: str) -> pd.DataFrame:
    from . import imb_zone as I
    from .run import _low_priority
    _low_priority()
    m15 = D.resample(I.load_base(sym), "M15")  # older FOREXSB M15 (+M30 patch for EURUSD), never used to define combo B
    t = DC.imb_entry_trades(sym, m15, DC.signals(m15), int(pd.Timestamp("2018-09-01", tz="UTC").timestamp()), START)
    if not t.empty:
        t["data_hole"] = D.overlaps_hole(D.holes(m15), t.t_entry.to_numpy(), t.t_exit.to_numpy())
    print(sym, len(t), flush=True)
    return t


def history_b() -> None:
    """Combo B on 2018-09 -> 2024-02 (out of sample for the clean-window result)."""
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(3) as ex:
        h = pd.concat(ex.map(_b_history, D.SYMBOLS), ignore_index=True)
    h = h[~h.data_hole.astype(bool)]
    h.to_csv(DEST / "combo_B_history_2018_2024.csv.gz", index=False)
    print("combo B 2018-09 -> 2024-02:", stats(h))
    print("by symbol:", {k: (len(g), round(float(g.r.mean()), 3)) for k, g in h.groupby("symbol")})
    print("by direction:", {("buy" if k == 1 else "sell"): (len(g), round(float(g.r.mean()), 3)) for k, g in h.groupby("dir")})


def stats(g: pd.DataFrame) -> dict:
    if len(g) < 20:
        return {"n": len(g)}
    r = g.r.to_numpy()
    eq = np.cumsum(r[np.argsort(g.t_entry.to_numpy())])
    yr = g.groupby(pd.to_datetime(g.t_entry, unit="s").dt.year).r.mean()
    mon = g.groupby(pd.to_datetime(g.t_entry, unit="s").dt.strftime("%Y-%m")).r.sum()
    return {"n": len(r), "win%": round(100 * float((g.exit_reason == "target").mean()), 1), "avg_R": round(float(r.mean()), 3),
            "t": round(float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))), 2), "total_R": round(float(r.sum()), 1),
            "max_dd_R": round(float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()), 1),
            "pos_months%": round(100 * float((mon > 0).mean()), 0), **{f"y{y}": round(float(v), 3) for y, v in yr.items()}}


def main() -> None:
    import sys
    if "--history-b" in sys.argv:
        return history_b()
    t = pd.read_csv(DEST / "clean_retest_trades.csv.gz")
    imb = t[t.variant == "imbalance"].copy()
    hour = (imb.t_entry % 86400) // 3600
    filters = {
        "tf": {"H1+H4": imb.tf.isin(["H1", "H4"]), "H1": imb.tf == "H1", "H4": imb.tf == "H4"},
        "stop": {"any": np.ones(len(imb), bool), ">8 spreads": imb.minmult_val > 8},
        "gbp": {"keep": np.ones(len(imb), bool), "no GBPUSD": imb.symbol != "GBPUSD"},
        "session": {"any": np.ones(len(imb), bool), "no 12-20 UTC": ~hour.between(12, 19)},
        "htf": {"any": np.ones(len(imb), bool), "skip HTF-aligned": imb.htf == 0},
        "donchian": {"any": np.ones(len(imb), bool), "donchian agrees": imb.donchian_agrees.astype(bool)},
    }
    combos = []
    for choice in itertools.product(*[f.items() for f in filters.values()]):
        mask = np.logical_and.reduce([np.asarray(m) for _, m in choice])
        name = " | ".join(k for k, _ in choice)
        g = imb[mask]
        combos.append({"combo": name, **stats(g), "_mask": mask})
    df = pd.DataFrame(combos)
    # learn/test: best on 2024 only (>= 100 trades in 2024), then its 2025-2026 result unchanged
    y = pd.to_datetime(imb.t_entry, unit="s").dt.year.to_numpy()
    learn = []
    for c in combos:
        g24, gT = imb[c["_mask"] & (y == 2024)], imb[c["_mask"] & (y >= 2025)]
        if len(g24) >= 100:
            learn.append({"combo": c["combo"], "2024_avg": round(float(g24.r.mean()), 3), "2024_n": len(g24),
                          "2025_26_avg": round(float(gT.r.mean()), 3) if len(gT) else None, "2025_26_n": len(gT)})
    L = pd.DataFrame(learn).sort_values("2024_avg", ascending=False)
    b = combo_b()
    b = b[~b.data_hole.astype(bool)]
    out = {"combinations": df.drop(columns="_mask").sort_values("avg_R", ascending=False).to_dict("records"),
           "learn_2024_top10": L.head(10).to_dict("records"),
           "learn_2024_best_on_2025_26": L.head(1).to_dict("records"),
           "learn_top10_avg_on_2025_26": round(float(L.head(10)["2025_26_avg"].mean()), 3),
           "all_combos_median_on_2025_26": round(float(L["2025_26_avg"].median()), 3),
           "combo_B_donchian_entry_at_imbalance_zone": stats(b),
           "donchian_alone": stats(t[t.variant == "donchian alone"])}
    (OUT / "clean_combos_report.json").write_text(json.dumps(out, indent=1, default=str))
    show = df.drop(columns="_mask").sort_values("avg_R", ascending=False)
    cols = ["combo", "n", "win%", "avg_R", "t", "total_R", "max_dd_R", "pos_months%", "y2024", "y2025", "y2026"]
    print(f"{len(df)} combinations. Top 12 by avg R (whole period, in-sample):")
    print(show[cols].head(12).to_string(index=False))
    print("\nBottom 3:"); print(show[cols].tail(3).to_string(index=False))
    print("\nLEARN on 2024 only -> TEST 2025-26 (top 8 picked on 2024):")
    print(L.head(8).to_string(index=False))
    print(f"\n  top-10 (picked on 2024) average on 2025-26: {out['learn_top10_avg_on_2025_26']:+.3f}R | median of all combos on 2025-26: {out['all_combos_median_on_2025_26']:+.3f}R")
    print("\ncombo B (donchian breakout -> limit at its imbalance zone):", out["combo_B_donchian_entry_at_imbalance_zone"])
    print("donchian alone:", out["donchian_alone"])


if __name__ == "__main__":
    main()
