"""Round 6b: confirm FLIPPED SFLOW on M5 paths (written before running).
Every historical sflow entry-A trade (data/sflow_fxcm_ours_2012_2021.csv + data/sflow_5y.csv) is simulated twice from
its fill time on M5 bars (FXCM M5 bid for FX, IC M5 for XAUUSD): ORIGINAL (its own stop / target) and FLIPPED
(opposite side; stop and target mirrored around the entry). Exit: stop first on a shared bar, target, else the close
after 10 days (2,880 M5 bars), as in the original research. Shorts trigger on the ask (bid + the bar's spread) where
the spread is known. Net of the trade's own recorded cost_r.
Validity gate (before reading the flip): the re-simulated ORIGINAL must match the recorded results (correlation >= 0.9,
mean within 0.05 R in each split). If it doesn't, the flip result is not reported as evidence.
Grade as round 3: PROVEN = DISC mean > 0 p < 0.01 and VAL, LOCK p < 0.05; PROMISING = positive in all three and the
better of VAL / LOCK p < 0.10. Week-clustered t.
    python reports/round6/r6b_flip_m5.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path[:0] = ["D:/Devops/trading/OpenTerminalUI", "D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import by_split, grade  # noqa: E402
from bensim.backtest import load_m5  # noqa: E402

D_ = "D:/Devops/trading/OpenTerminalUI/data/"
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round6/"
HOLD = 2880


def m5(sym: str) -> dict | None:
    if sym == "XAUUSD":
        b = load_m5("XAUUSD", "2021-01-01", "2026-11-01")
        if not len(b["t"]):
            return None
        return {"t": b["t"].astype(np.int64), "h": b["high"], "l": b["low"], "c": b["close"], "spr": np.zeros(len(b["t"]))}
    z = np.load(D_ + f"bars/fxcm/{sym}_M5.npz")
    return {"t": z["t"].astype(np.int64), "h": z["h"], "l": z["l"], "c": z["c"], "spr": z["spr"]}


def sim(b: dict, i0: int, d: int, entry: float, stop: float, tgt: float) -> tuple[float, str, int]:
    risk = abs(entry - stop)
    end = min(i0 + HOLD, len(b["t"]) - 1)
    h, l, c, s = b["h"], b["l"], b["c"], b["spr"]
    for k in range(i0, end + 1):
        hi, lo = (h[k], l[k]) if d > 0 else (h[k] + s[k], l[k] + s[k])  # longs exit on bid, shorts on ask
        if (d > 0 and lo <= stop) or (d < 0 and hi >= stop):
            return -1.0, "stop", k
        if (d > 0 and hi >= tgt) or (d < 0 and lo <= tgt):
            return abs(tgt - entry) / risk, "target", k
    px = c[end] if d > 0 else c[end] + s[end]
    return d * (px - entry) / risk, "time", end


def main() -> None:
    if "--from-csv" in sys.argv:  # reuse the saved simulation (report step only)
        F = pd.read_csv(OUT + "r6b_flip_trades.csv", parse_dates=["t"])
        return report(F)
    sf = pd.concat([pd.read_csv(D_ + "sflow_fxcm_ours_2012_2021.csv"), pd.read_csv(D_ + "sflow_5y.csv")])
    sf = sf[sf.kind == "A"].dropna(subset=["entry", "stop", "target"])
    cache, rows = {}, []
    for r in sf.itertuples():
        if r.symbol not in cache:
            cache[r.symbol] = m5(r.symbol)
        b = cache[r.symbol]
        if b is None or abs(r.entry - r.stop) <= 0:
            continue
        i0 = int(np.searchsorted(b["t"], int(r.fill_t), side="left"))  # the first M5 bar at / after the fill
        if i0 >= len(b["t"]) - 2:
            continue
        cr = r.cost_r if np.isfinite(r.cost_r) else 0.0
        o_r, o_how, _ = sim(b, i0, int(r.dir), r.entry, r.stop, r.target)
        f_r, f_how, _ = sim(b, i0, -int(r.dir), r.entry, 2 * r.entry - r.stop, 2 * r.entry - r.target)
        rows.append({"t": pd.Timestamp(int(r.fill_t), unit="s"), "symbol": r.symbol, "recorded": r.r, "recorded_how": r.how,
                     "orig": o_r - cr, "orig_how": o_how, "flip": f_r - cr, "flip_how": f_how})
    F = pd.DataFrame(rows)
    F.to_csv(OUT + "r6b_flip_trades.csv", index=False)
    report(F)


def report(F: pd.DataFrame) -> None:
    sp = np.where(F.t.dt.year <= 2018, "DISC", np.where(F.t.dt.year <= 2022, "VAL", "LOCK"))
    check = {"corr": round(float(F[["orig", "recorded"]].corr().iloc[0, 1]), 3), "same_exit_type": round(float((F.orig_how == F.recorded_how).mean()), 3)}
    for s in ("DISC", "VAL", "LOCK"):
        check[f"{s}_mean_orig_vs_recorded"] = [round(float(F.orig[sp == s].mean()), 3), round(float(F.recorded[sp == s].mean()), 3)]
    valid = check["corr"] >= 0.9 and all(abs(v[0] - v[1]) <= 0.05 for k, v in check.items() if k.endswith("recorded"))
    res = {"n": int(len(F)), "simulator_check": check, "simulator_valid": bool(valid),
           "original_resim": by_split(F, "orig"), "recorded_original": by_split(F, "recorded"), "flipped": by_split(F, "flip"),
           "flip_minus_orig_by_split": {s: round(float((F.flip - F.orig)[sp == s].mean()), 3) for s in ("DISC", "VAL", "LOCK")},
           "flipped_by_year": F.groupby(F.t.dt.year).flip.mean().round(3).to_dict(),
           "flipped_by_pair": F.groupby("symbol").flip.agg(["count", "mean"]).round(3).to_dict(orient="index"),
           "flipped_exit_mix": F.flip_how.value_counts(normalize=True).round(3).to_dict()}
    res["grade_flipped"] = grade(res["flipped"]) if valid else "NOT GRADED (simulator failed the validity gate)"
    json.dump(res, open(OUT + "r6b_results.json", "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
