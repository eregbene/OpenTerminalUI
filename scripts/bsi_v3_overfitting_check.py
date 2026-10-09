"""How much of the best V3 enigma result is search luck? Deflated Sharpe, BH-FDR and CSCV PBO.

Trials = every (entry model, symbol) slice of data/research/bsi_v3_enigma_5y.json (real-fill models
only). Sharpe is per trade (mean R / std R), matched to that slice's trade count, as the vendored
module requires. PBO uses weekly summed R per trial (0 in weeks with no trade).

Run: python scripts/bsi_v3_overfitting_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import multipletesting as mt  # vendored from HKUDS/Vibe-Trading (MIT)

SRC = ROOT / "data" / "research" / "bsi_v3_enigma_5y.json"
OUT_DOC = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation" / "BSI_V3_ENIGMA_5Y_OVERFITTING.md"


def main() -> None:
    trades = json.loads(SRC.read_text(encoding="utf-8"))["trades"]
    rows = [(f"{m}:{sym}", pd.Timestamp(t), r) for m in ("market_confirm", "limit_mid") for sym, t, r in trades[m]]
    df = pd.DataFrame(rows, columns=["trial", "time", "r"])
    stats = []
    for trial, g in df.groupby("trial"):
        r = g["r"].to_numpy()
        sr = r.mean() / r.std(ddof=1)
        t = sr * np.sqrt(len(r))
        stats.append({"trial": trial, "n": len(r), "mean_r": r.mean(), "sr": sr, "t": t, "p": 1 - norm.cdf(t),
                      "skew": skew(r), "kurt": kurtosis(r, fisher=False)})
    st = pd.DataFrame(stats).sort_values("sr", ascending=False)
    best = st.iloc[0]
    dsr = mt.deflated_sharpe_ratio(best["sr"], n_trials=len(st), n_observations=int(best["n"]),
                                   trial_sharpe_std=st["sr"].std(ddof=1), skew=best["skew"], kurtosis=best["kurt"])
    psr = mt.probabilistic_sharpe_ratio(best["sr"], int(best["n"]), skew=best["skew"], kurtosis=best["kurt"])
    bh = mt.benjamini_hochberg(st["p"].to_numpy())
    weekly = df.assign(week=df["time"].dt.tz_localize(None).dt.to_period("W")).pivot_table(index="week", columns="trial", values="r", aggfunc="sum", fill_value=0.0)
    pbo = mt.probability_of_backtest_overfitting(weekly, n_splits=16)

    lines = [
        "# V3 enigma 5-year: overfitting check",
        "",
        f"Trials: {len(st)} (entry model x symbol). Weeks: {len(weekly)}.",
        "",
        f"- Best trial: **{best['trial']}**, {int(best['n'])} trades, {best['mean_r']:+.4f}R/trade, t={best['t']:.2f}",
        f"- PSR (best > 0, ignoring the search): {psr:.3f}",
        f"- Deflated Sharpe: {dsr.deflated_sharpe_ratio:.3f} (bar = expected best-of-{dsr.n_trials} by luck: SR {dsr.expected_maximum_sharpe:.4f} vs observed {dsr.observed_sharpe:.4f}; survives 95%: {dsr.survives})",
        f"- Benjamini-Hochberg @5% FDR: {bh.n_rejected} of {len(st)} trials significant",
        f"- PBO (CSCV, 16 splits): {pbo.pbo:.3f} over {pbo.n_splits} splits (>0.5 = picking the best backtest is worse than random)",
        "",
        "| trial | n | mean R | t | p |",
        "|---|---|---|---|---|",
        *[f"| {s.trial} | {s.n} | {s.mean_r:+.4f} | {s.t:.2f} | {s.p:.3f} |" for s in st.itertuples()],
    ]
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
