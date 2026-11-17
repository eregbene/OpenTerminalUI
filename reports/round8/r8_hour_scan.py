"""Round 8a: systematic hour-of-day scan (written before running; Flow AM -- EURUSD short 09:00 -> 12:00 UTC -- is one
member of this family and is reported as a check, not selected).
Every FXCM pair (25), every UTC entry hour (0-23), both directions, holds 3 h and 6 h: enter at the open of the entry
hour's H1 bar, exit at the open of the bar `hold` hours later, weekdays only, one trade per day per test. Net = real
spread half in + half out + 0.3 bp slippage. 2,400 tests.
Splits DISC 2012-2018 / VAL 2019-2022 / LOCK 2023-2026; daily trades -> t on the daily series (one trade per day).
Selection: Benjamini-Hochberg q = 0.05 on the DISC one-sided p over ALL 2,400 tests, mean > 0.
PROVEN: DISC BH + VAL p < 0.05 + LOCK p < 0.05 (all means > 0). PROMISING: DISC BH + VAL p < 0.05 + LOCK mean > 0.
A London-local-hour variant (DST-aware) is run as a second, separate family with its own BH.
    python reports/round8/r8_hour_scan.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path[:0] = ["D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import PAIRS  # noqa: E402

FX = "D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/"


def raw(sym: str) -> dict | None:
    """EXACT quotes only (fix 2026-11-06): bid close and ask close (= bid close + the spread at that same close).
    The earlier version built bar OPEN mids from the CLOSE spread, which inflates opens of rollover-hour bars and
    faked rollover 'edges'."""
    try:
        z = np.load(FX + f"{sym}_H1.npz")
    except FileNotFoundError:
        return None
    return {"t": z["t"].astype(np.int64), "bid": z["c"], "ask": z["c"] + z["spr"]}

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round8/"


def tstat(x: np.ndarray) -> tuple[float, float, float]:
    if len(x) < 50:
        return np.nan, np.nan, 1.0
    m, s = x.mean(), x.std(ddof=1)
    t = m / (s / np.sqrt(len(x))) if s > 0 else 0.0
    return float(m), float(t), float(stats.t.sf(t, len(x) - 1))


def scan(clock: str) -> pd.DataFrame:
    rows = []
    for sym in PAIRS:
        b = raw(sym)
        if b is None:
            continue
        t = pd.to_datetime(b["t"] + 3600, unit="s")  # decision time = this bar's CLOSE = the next hour's start
        hour = t.hour if clock == "utc" else t.tz_localize("UTC").tz_convert("Europe/London").hour
        bid, ask = b["bid"], b["ask"]
        yr, wd = t.year.to_numpy(), t.weekday.to_numpy()
        idx_by_t = {int(x): i for i, x in enumerate(b["t"])}
        for hold in (3, 6):
            j = np.array([idx_by_t.get(int(x) + hold * 3600, -1) for x in b["t"]])
            ok = (j >= 0) & (wd < 5)
            long_ = np.full(len(bid), np.nan)
            short_ = np.full(len(bid), np.nan)
            long_[ok] = (bid[j[ok]] - ask[ok]) / ask[ok] * 1e4 - 0.3      # buy the ask now, sell the bid later
            short_[ok] = (bid[ok] - ask[j[ok]]) / bid[ok] * 1e4 - 0.3     # sell the bid now, buy the ask later
            for h in range(24):
                m = ok & (np.asarray(hour) == h)
                for d in (1, -1):
                    net = (long_ if d > 0 else short_)[m]
                    y = yr[m]
                    row = {"clock": clock, "symbol": sym, "hour": h, "dir": d, "hold": hold}
                    for sp_, (a, z) in (("DISC", (2012, 2018)), ("VAL", (2019, 2022)), ("LOCK", (2023, 2026))):
                        x = net[(y >= a) & (y <= z)]
                        x = x[np.isfinite(x)]
                        mm, tt, pp = tstat(x)
                        row.update({f"{sp_}_n": len(x), f"{sp_}_bp": mm, f"{sp_}_t": tt, f"{sp_}_p": pp})
                    xs = pd.Series(net).groupby(y).mean()
                    row["years_pos"] = int((xs > 0).sum())
                    rows.append(row)
        print(clock, sym, flush=True)
    T = pd.DataFrame(rows)
    p = T.DISC_p.where(T.DISC_bp > 0, 1.0).fillna(1.0).to_numpy()
    o_, m_ = np.argsort(p), len(p)
    ok_ = p[o_] <= 0.05 * np.arange(1, m_ + 1) / m_
    T["DISC_bh"] = False
    if ok_.any():
        T.loc[T.index[o_[: np.max(np.flatnonzero(ok_)) + 1]], "DISC_bh"] = True
    T["grade"] = np.where(T.DISC_bh & (T.VAL_bp > 0) & (T.VAL_p < 0.05) & (T.LOCK_bp > 0) & (T.LOCK_p < 0.05), "PROVEN",
                          np.where(T.DISC_bh & (T.VAL_bp > 0) & (T.VAL_p < 0.05) & (T.LOCK_bp > 0), "PROMISING", "FAILED"))
    return T


def main() -> None:
    res = {}
    for clock in ("utc", "london"):
        T = scan(clock)
        T.to_csv(OUT + f"r8_hour_scan_exact_{clock}.csv", index=False)
        keep = T[T.grade != "FAILED"].sort_values("DISC_t", ascending=False)
        res[clock] = {"tests": int(len(T)), "disc_bh": int(T.DISC_bh.sum()), "grades": T.grade.value_counts().to_dict(),
                      "passing": keep.round(3).to_dict(orient="records"),
                      "flow_am_check": T[(T.symbol == "EURUSD") & (T.hour == 9) & (T.dir == -1) & (T.hold == 3)].round(3).to_dict(orient="records"),
                      "top10_disc": T.sort_values("DISC_t", ascending=False).head(10).round(3).to_dict(orient="records")}
        print(clock, res[clock]["grades"], "BH survivors", res[clock]["disc_bh"], flush=True)
    json.dump(res, open(OUT + "r8_hour_scan_exact_results.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
