"""FXCM M5/H1 data-quality assessment (approved 2026-11-03; read-only, no strategy backtests).

    python -m bensim.fxcm_quality        # -> reports/fxcm_quality_2026-11-03/{coverage.csv, ic_compare.csv, REPORT.md}

Per pair (data/bars/fxcm/{SYM}_M5.npz, _H1.npz): bars, range, duplicate / non-increasing timestamps, bad OHLC,
off-grid timestamps, weekday holes > 15 min (count and hours lost per year; weekend = Fri 21:00 -> Sun 21:00 UTC
excluded), spread coverage (share of bars with spr > 0, median / p90 spread in pips by year, by UTC hour).
Overlap with the IC Markets DB (EURUSD GBPUSD USDJPY USDCHF USDCAD AUDUSD): close-to-close difference in pips at
lag 0 and at +/-5 min / +/-1 h (the time-zone test: lag 0 must win), share of bars present in both.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import registry
from .backtest import load_m5

SRC = Path("data/bars/fxcm")
OUT = Path("reports/fxcm_quality_2026-11-03")
IC = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD"]


def pip(sym: str) -> float:
    return 0.01 if sym.endswith("JPY") else 0.0001


def load(sym: str, tf: str) -> pd.DataFrame:
    z = np.load(SRC / f"{sym}_{tf}.npz")
    return pd.DataFrame({k: z[k] for k in ("t", "o", "h", "l", "c", "spr")})


def weekend(ts: np.ndarray) -> np.ndarray:
    d = pd.to_datetime(ts, unit="s", utc=True)
    wd, h = d.weekday.to_numpy(), d.hour.to_numpy()
    return (wd == 5) | ((wd == 4) & (h >= 21)) | ((wd == 6) & (h < 21))


def quality(sym: str, tf: str = "M5") -> dict:
    df = load(sym, tf)
    t = df.t.to_numpy()
    step = 300 if tf == "M5" else 3600
    dt = np.diff(t)
    gap_start = t[:-1][dt > (900 if tf == "M5" else 2 * 3600)]
    gap_len = dt[dt > (900 if tf == "M5" else 2 * 3600)]
    wk = ~weekend(gap_start) & ~weekend(gap_start + gap_len - step)  # both ends inside trading time
    yrs = pd.to_datetime(gap_start[wk], unit="s", utc=True).year
    lost = pd.Series(gap_len[wk] / 3600, index=yrs).groupby(level=0).sum().round(1).to_dict()
    big = sorted(zip(gap_len[wk], gap_start[wk]), reverse=True)[:5]
    spr = df.spr.to_numpy() / pip(sym)
    y = pd.to_datetime(t, unit="s", utc=True).year
    hr = pd.to_datetime(t, unit="s", utc=True).hour
    by_year = pd.DataFrame({"y": y, "s": spr}).groupby("y").s.agg(lambda s: round(float(np.median(s[s > 0])), 2) if (s > 0).any() else None).to_dict()
    return {"symbol": sym, "tf": tf, "bars": int(len(t)),
            "first": str(pd.to_datetime(t[0], unit="s", utc=True)), "last": str(pd.to_datetime(t[-1], unit="s", utc=True)),
            "duplicates": int(len(t) - len(np.unique(t))), "non_increasing": int((dt <= 0).sum()), "off_grid": int((t % step != 0).sum()),
            "bad_ohlc": int(((df.h < df[["o", "c"]].max(1)) | (df.l > df[["o", "c"]].min(1)) | (df.l <= 0)).sum()),
            "weekday_holes": int(wk.sum()), "hours_lost_by_year": lost,
            "largest_holes": [(str(pd.to_datetime(int(s), unit="s", utc=True)), round(float(g) / 3600, 1)) for g, s in big],
            "spread_pos_share": round(float((spr > 0).mean()), 4), "spread_median_pips": round(float(np.median(spr[spr > 0])), 2) if (spr > 0).any() else None,
            "spread_p90_pips": round(float(np.quantile(spr[spr > 0], 0.9)), 2) if (spr > 0).any() else None,
            "spread_median_by_year": by_year,
            "spread_median_by_hour": pd.DataFrame({"h": hr, "s": spr}).query("s > 0").groupby("h").s.median().round(2).to_dict()}


def compare_ic(sym: str) -> dict:
    f = load(sym, "M5")
    ic = load_m5(sym, "2018-01-01", "2026-11-04")
    if not len(ic["t"]):
        return {"symbol": sym, "overlap_bars": 0}
    a = pd.Series(f.c.to_numpy(), index=f.t.to_numpy())
    b = pd.Series(ic["close"], index=ic["t"])
    out = {"symbol": sym, "ic_first": str(pd.to_datetime(int(ic["t"][0]), unit="s", utc=True))}
    lo, hi = max(a.index.min(), b.index.min()), min(a.index.max(), b.index.max())
    a, b = a[(a.index >= lo) & (a.index <= hi)], b[(b.index >= lo) & (b.index <= hi)]
    both = a.index.intersection(b.index)
    out |= {"overlap_from": str(pd.to_datetime(int(lo), unit="s", utc=True)), "overlap_to": str(pd.to_datetime(int(hi), unit="s", utc=True)),
            "fxcm_bars": int(len(a)), "ic_bars": int(len(b)), "in_both": int(len(both)),
            "share_ic_bars_in_fxcm": round(len(both) / max(1, len(b)), 4), "share_fxcm_bars_in_ic": round(len(both) / max(1, len(a)), 4)}
    for lag in (-3600, -300, 0, 300, 3600):  # FXCM close at t vs IC close at t + lag
        ix = both if lag == 0 else a.index.intersection(b.index - lag)
        d = (a.loc[ix].to_numpy() - b.loc[ix + lag].to_numpy()) / pip(sym)
        out[f"median_abs_diff_pips_lag_{lag // 60}m"] = round(float(np.median(np.abs(d))), 3) if len(d) else None
    d0 = (a.loc[both].to_numpy() - b.loc[both].to_numpy()) / pip(sym)
    out |= {"diff_p95_abs_pips": round(float(np.quantile(np.abs(d0), 0.95)), 2), "diff_mean_pips": round(float(d0.mean()), 3),
            "diff_over_5_pips_share": round(float((np.abs(d0) > 5).mean()), 5)}
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    syms = sorted({p.name.split("_")[0] for p in SRC.glob("*_M5.npz")})
    cov = [quality(s, "M5") for s in syms] + [quality(s, "H1") for s in syms if (SRC / f"{s}_H1.npz").exists()]
    cmp_ = [compare_ic(s) for s in IC if s in syms]
    pd.DataFrame(cov).to_csv(OUT / "coverage.csv", index=False)
    pd.DataFrame(cmp_).to_csv(OUT / "ic_compare.csv", index=False)
    (OUT / "detail.json").write_text(json.dumps({"coverage": cov, "ic_compare": cmp_}, indent=1, default=str))
    registry.append({"kind": "FXCM data-quality assessment (read-only)", "code": registry.code_version(), "command": "python -m bensim.fxcm_quality",
                     "outputs": {"dir": str(OUT)}, "symbols": syms})
    m5 = pd.DataFrame([c for c in cov if c["tf"] == "M5"])
    print(m5[["symbol", "bars", "first", "last", "duplicates", "non_increasing", "off_grid", "bad_ohlc", "weekday_holes", "spread_pos_share",
              "spread_median_pips", "spread_p90_pips"]].to_string(index=False))
    print(pd.DataFrame(cmp_).to_string(index=False))


if __name__ == "__main__":
    main()
