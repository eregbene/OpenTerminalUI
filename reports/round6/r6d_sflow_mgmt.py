"""Round 6d: sflow TRADE MANAGEMENT (written before running; nothing tuned on the result).
Every historical sflow entry-A trade (2012-2026, FXCM M5 for FX, IC M5 for XAUUSD) is re-simulated from its fill on M5
bars under each fixed variant. Stop first on a shared bar; shorts trigger on the ask (bid + bar spread); breakeven and
partial levels take effect from the NEXT bar after they are reached (no same-bar optimism); 10-day hold; net of the
trade's recorded cost_r.
  BASE    original rules (stop, impulse-extreme target ~3.2R)
  MS10    skip trades with a stop < 10 pips (pip = 0.0001 / JPY 0.01 / XAU 0.1)
  T15     fixed target 1.5R          T2   fixed target 2R
  BE1     stop to entry once +1R was reached
  P1      half closed at +1R, the rest keeps the original stop and target
  P1BE    half at +1R, the rest's stop moved to entry
  SES     only fills in Asia (00-06 UTC) or late (17-23 UTC)
  combos  MS10+T2, MS10+P1BE, MS10+SES, MS10+T2+SES, MS10+P1BE+SES
Splits DISC <= 2018, VAL 2019-22, LOCK 2023-26. PROVEN: DISC mean > 0 with BH-adjusted p < 0.05 (over the 14 variants),
VAL p < 0.05, LOCK p < 0.05 (week-clustered t). PROMISING: positive in all three, the better of VAL / LOCK p < 0.10.
    python reports/round6/r6d_sflow_mgmt.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path[:0] = ["D:/Devops/trading/OpenTerminalUI", "D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import by_split  # noqa: E402
from r6b_flip_m5 import m5  # noqa: E402

D_ = "D:/Devops/trading/OpenTerminalUI/data/"
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round6/"
HOLD = 2880
VARIANTS = {"BASE": {}, "MS10": {"ms": 10}, "T15": {"t": 1.5}, "T2": {"t": 2.0}, "BE1": {"be": 1.0}, "P1": {"p": 1.0}, "P1BE": {"p": 1.0, "pbe": True},
            "SES": {"ses": True}, "MS10+T2": {"ms": 10, "t": 2.0}, "MS10+P1BE": {"ms": 10, "p": 1.0, "pbe": True}, "MS10+SES": {"ms": 10, "ses": True},
            "MS10+T2+SES": {"ms": 10, "t": 2.0, "ses": True}, "MS10+P1BE+SES": {"ms": 10, "p": 1.0, "pbe": True, "ses": True}, "BE1+T2": {"be": 1.0, "t": 2.0}}


def pip(sym: str) -> float:
    return 0.1 if sym == "XAUUSD" else (0.01 if sym.endswith("JPY") else 0.0001)


def sim(b, i0, d, entry, stop, tgt, be=None, part=None, part_be=False) -> float:
    """R of one trade (gross). part = R level where half is closed; be = R level that moves the stop to entry."""
    risk = abs(entry - stop)
    h, l, c, s = b["h"], b["l"], b["c"], b["spr"]
    end = min(i0 + HOLD, len(b["t"]) - 1)
    cur_stop, done_half, booked, size, arm_be, arm_rest_be = stop, False, 0.0, 1.0, False, False
    for k in range(i0, end + 1):
        hi, lo = (h[k], l[k]) if d > 0 else (h[k] + s[k], l[k] + s[k])
        if arm_be or arm_rest_be:
            cur_stop = entry
        if (d > 0 and lo <= cur_stop) or (d < 0 and hi >= cur_stop):
            return booked + size * (d * (cur_stop - entry) / risk)
        if (d > 0 and hi >= tgt) or (d < 0 and lo <= tgt):
            return booked + size * abs(tgt - entry) / risk
        fav = ((hi - entry) if d > 0 else (entry - lo)) / risk
        if part is not None and not done_half and fav >= part:
            booked, size, done_half = 0.5 * part, 0.5, True
            arm_rest_be = part_be
        if be is not None and fav >= be:
            arm_be = True
    px = c[end] if d > 0 else c[end] + s[end]
    return booked + size * d * (px - entry) / risk


def main() -> None:
    sf = pd.concat([pd.read_csv(D_ + "sflow_fxcm_ours_2012_2021.csv"), pd.read_csv(D_ + "sflow_5y.csv")])
    sf = sf[sf.kind == "A"].dropna(subset=["entry", "stop", "target"])
    cache, rows = {}, []
    for r in sf.itertuples():
        if r.symbol not in cache:
            cache[r.symbol] = m5(r.symbol)
        b = cache[r.symbol]
        risk = abs(r.entry - r.stop)
        if b is None or risk <= 0:
            continue
        i0 = int(np.searchsorted(b["t"], int(r.fill_t), side="left"))
        if i0 >= len(b["t"]) - 2:
            continue
        d, cr = int(r.dir), (r.cost_r if np.isfinite(r.cost_r) else 0.0)
        hr = pd.Timestamp(int(r.fill_t), unit="s").hour
        row = {"t": pd.Timestamp(int(r.fill_t), unit="s"), "symbol": r.symbol, "stop_pips": risk / pip(r.symbol), "asia_late": hr <= 6 or hr >= 17}
        for name, v in VARIANTS.items():
            if (v.get("ms") and row["stop_pips"] < v["ms"]) or (v.get("ses") and not row["asia_late"]):
                row[name] = np.nan  # not traded under this variant
                continue
            tgt = r.entry + d * v["t"] * risk if v.get("t") else r.target
            row[name] = sim(b, i0, d, r.entry, r.stop, tgt, v.get("be"), v.get("p"), v.get("pbe", False)) - cr
        rows.append(row)
    F = pd.DataFrame(rows)
    F.to_csv(OUT + "r6d_trades.csv", index=False)
    res, pvals = {}, {}
    for name in VARIANTS:
        g = F[["t", name]].dropna().rename(columns={name: "r"})
        res[name] = by_split(g)
        res[name]["all"] = {"n": int(len(g)), "mean": round(float(g.r.mean()), 4), "total": round(float(g.r.sum()), 1)}
        pvals[name] = res[name]["DISC"].get("p", 1.0) if res[name]["DISC"].get("mean", -1) > 0 else 1.0
    names = list(pvals)
    p = np.array([pvals[n] for n in names])
    o, m = np.argsort(p), len(p)
    ok = p[o] <= 0.05 * np.arange(1, m + 1) / m
    bh = set(np.array(names)[o[: np.max(np.flatnonzero(ok)) + 1]]) if ok.any() else set()
    for name in names:
        d_, v_, l_ = (res[name][s] for s in ("DISC", "VAL", "LOCK"))
        if name in bh and v_.get("mean", -1) > 0 and v_.get("p", 1) < 0.05 and l_.get("mean", -1) > 0 and l_.get("p", 1) < 0.05:
            res[name]["grade"] = "PROVEN"
        elif all(x.get("mean", -1) > 0 for x in (d_, v_, l_)) and min(v_.get("p", 1), l_.get("p", 1)) < 0.10:
            res[name]["grade"] = "PROMISING"
        else:
            res[name]["grade"] = "FAILED"
        res[name]["disc_bh"] = name in bh
    json.dump(res, open(OUT + "r6d_results.json", "w"), indent=1, default=str)
    for name, v in res.items():
        print(f"{name:15} " + "  ".join(f"{s} {v[s].get('mean', 0):+.3f} (t {v[s].get('t', 0):+.2f}, n {v[s].get('n')})" for s in ("DISC", "VAL", "LOCK"))
              + f"  yrs+ {v['years_pos']}  -> {v['grade']}")


if __name__ == "__main__":
    main()
