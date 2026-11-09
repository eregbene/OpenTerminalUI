"""Round 3, areas A (conditional currency strength), B (structure / liquidity / FVG), C (volatility), D (session),
I (new patterns): every rule below was written BEFORE this script was run (PREREG.md). Each rule gives a direction
d in {+1, -1, 0} per pair-hour. Tested in BOTH directions (d and -d = the reversal reading) on 5 outcomes:
net 6 / 12 / 24 / 48h bp and the 1-ATR-stop / 2-ATR-target bracket (R), one open trade per pair per rule.
Liquidity events count the FIRST time per day (per week for the weekly levels).
    source reports/round3/r3env.sh && python reports/round3/r3_tests.py
writes r3_registry.csv (every test, all splits) and r3_tests_summary.txt
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

R3 = "D:/Devops/trading/OpenTerminalUI/reports/round3/"
SPLITS = {"DISC": (2012, 2018), "VAL": (2019, 2022), "LOCK": (2023, 2026)}
EXTRA = {6: 0.3 + 0.125, 12: 0.3 + 0.25, 24: 0.3 + 0.5, 48: 0.3 + 1.0, "b12": 0.3 + 0.5}  # bp: slippage + swap
HOLD = {6: 6, 12: 12, 24: 24, 48: 48, "b12": 24}


def load() -> pd.DataFrame:
    d = pd.read_pickle(R3 + "r3_panel.pkl")  # our own file (r3_build.py)
    d = d.sort_values(["symbol", "t"]).reset_index(drop=True)
    g = d.groupby("symbol", sort=False)
    day, wk = d.t.dt.floor("D"), d.t.dt.to_period("W")
    for c, per in (("sw_pd", day), ("sw_asia", day), ("sw_lon", day), ("fb_pd", day), ("brk_pd", day), ("brk_asia", day),
                   ("sw_pw", wk), ("brk_pw", wk)):
        nz = d[c] != 0
        first = nz & (nz.groupby([d.symbol, per, d[c]]).cumsum() == 1)
        d[c] = np.where(first, d[c], 0).astype(np.int8)
    sw = np.sign(d.sw_pd + d.sw_asia + d.sw_lon + d.sw_eq + d.sw_pw).astype(float)
    for k in (3, 6, 12):  # a sweep in the last k bars (incl. this one), direction of the latest
        d[f"sw{k}"] = sw.where(sw != 0).groupby(d.symbol).transform(lambda s: s.ffill(limit=k - 1)).fillna(0).astype(np.int8)
    d["z24"] = d.ret_24  # already vol-normalised (fix 2026-11-06: was divided by vol again -> C04 never fired)
    d["compress_prev"] = g.compress6.shift(1)
    d["nr7_prev"] = g.nr7.shift(1)
    d["brk_pd_prev"] = g.brk_pd.shift(1)
    d["year"] = d.t.dt.year
    d["wk"] = d.t.astype("int64") // 10**9 // (7 * 86400)
    return d


def rules(d: pd.DataFrame) -> dict[str, np.ndarray]:
    sgn = np.sign
    sg5 = np.where(d.rk_gap_24.abs() >= 5, sgn(d.rk_gap_24), 0)
    sg5l = np.where(d.rk_gap_120.abs() >= 5, sgn(d.rk_gap_120), 0)
    body_dir = sgn(d.body_0)
    usd = d.symbol.str.contains("USD")
    gap80 = d.wk_gap[(d.year <= 2018) & (d.week_open == 1)].abs().quantile(0.8)  # DISC only
    R = {
        # A conditional currency strength (24h ranks; +7 gap = strongest vs weakest)
        "A00 strongest-vs-weakest 24h": d.tb_24.to_numpy(),
        "A00b strongest-vs-weakest 120h": d.tb_120.to_numpy(),
        "A01 strong-vs-weak (gap>=5) 24h": sg5,
        "A02 strongest-vs-weakest at 00/12 UTC": np.where(d.t.dt.hour.isin([0, 12]), d.tb_24, 0),
        "A1 +momentum (ret_120 agrees)": np.where(sgn(d.ret_120) == sg5, sg5, 0),
        "A2 +vol expansion (vol_ratio>1.2)": np.where(d.vol_ratio > 1.2, sg5, 0),
        "A3 +structure (internal trend agrees)": np.where(d.st_tr10 == sg5, sg5, 0),
        "A4 +trend persistence (er_120>0.3)": np.where(d.er_120 > 0.3, sg5, 0),
        "A5 +liquidity sweep same side (3 bars)": np.where(d.sw3 == sg5, sg5, 0),
        "A6 +FVG first retest same side": np.where(d.fvg_rt == sg5, sg5, 0),
        "A7a +London open hour": np.where(d.lon_open == 1, sg5, 0),
        "A7b +New York open hour": np.where(d.ny_open == 1, sg5, 0),
        "A7c +Tokyo only": np.where((d.s_tokyo == 1) & (d.s_london == 0), sg5, 0),
        "A8 +room to S/R (not at range extreme)": np.where(((sg5 == 1) & (d.rpos_120 < 0.8)) | ((sg5 == -1) & (d.rpos_120 > 0.2)), sg5, 0),
        "A9 +ATR expansion (ATR6/48>1.2)": np.where(d.atr6_48 > 1.2, sg5, 0),
        "A10 +H4 and D1 trend agree": np.where((d.mtf_h4 == sg5) & (d.mtf_d1 == sg5), sg5, 0),
        "A11 120h strength + 6h pullback": np.where(sgn(d.ret_6) == -sg5l, sg5l, 0),
        "A12 exhaustion: range extreme vs strength": np.where((d.rpos_120 > 0.95) & (d.rk_gap_24 <= -3), -1, np.where((d.rpos_120 < 0.05) & (d.rk_gap_24 >= 3), 1, 0)),
        # B structure / liquidity / imbalance (direction = the event's own reading)
        "B01 BOS internal (10)": d.st_bos10.to_numpy(),
        "B02 CHoCH internal (10)": d.st_choch10.to_numpy(),
        "B03 BOS swing (50)": d.st_bos50.to_numpy(),
        "B04 CHoCH swing (50)": d.st_choch50.to_numpy(),
        "B05 previous-day H/L sweep": d.sw_pd.to_numpy(),
        "B06 previous-week H/L sweep": d.sw_pw.to_numpy(),
        "B07 Asian H/L sweep in London": np.where(d.s_london == 1, d.sw_asia, 0),
        "B08 London H/L sweep in New York": np.where(d.s_ny == 1, d.sw_lon, 0),
        "B09 equal highs/lows sweep": d.sw_eq.to_numpy(),
        "B10 sweep + displacement": np.where((d.sw3 != 0) & (body_dir == d.sw3) & (d.body_0.abs() > 0.6) & (d.size_0 > 1.2), d.sw3, 0),
        "B11 sweep + CHoCH": np.where((d.sw6 != 0) & (d.st_choch10 == d.sw6), d.sw6, 0),
        "B12 failed previous-day breakout": d.fb_pd.to_numpy(),
        "B13 previous-day breakout (first close)": np.where(d.brk_pd_prev == 0, d.brk_pd, 0),
        "B14 FVG first retest": d.fvg_rt.to_numpy(),
        "B15 FVG first retest + H4 trend": np.where(d.fvg_rt == d.mtf_h4, d.fvg_rt, 0),
        "B16 FVG with displacement (size>0.3, disp>1.5)": np.where((d.fvg_size > 0.3) & (d.fvg_disp > 1.5), d.fvg_new, 0),
        "B17 sweep (12 bars) + FVG retest": np.where((d.fvg_rt != 0) & (d.sw12 == d.fvg_rt), d.fvg_rt, 0),
        "B18 swing trend + internal BOS": np.where(d.st_bos10 == d.st_tr50, d.st_bos10, 0),
        "B19 compression then BOS": np.where(d.compress_prev < 0.3, d.st_bos10, 0),
        # C volatility
        "C01 compression breakout bar": np.where((d.compress_prev < 0.3) & (d.size_0 > 1.5), body_dir, 0),
        "C02 close outside Bollinger band": d.bb_out.to_numpy(),
        "C03 momentum acceleration": np.where((sgn(d.ret_6) == sgn(d.ret_24)) & (d.ret_6.abs() > d.ret_24.abs() / 2) & (d.vol_ratio > 1.2), sgn(d.ret_6), 0),
        "C04 high-vol 24h overextension": np.where((d.vol_pct > 0.8) & (d.z24.abs() > 2.5), sgn(d.z24), 0),
        "C05 ATR expansion + strong body": np.where((d.atr6_48 > 1.5) & (d.body_0.abs() > 0.7), body_dir, 0),
        # D session / time
        "D01 Asian range breakout at London open": np.where(d.lon_open == 1, d.brk_asia, 0),
        "D02 London move at NY open": np.where(d.ny_open == 1, sgn(d.ret_6), 0),
        "D03 week-open gap": np.where((d.week_open == 1) & (d.wk_gap.abs() > gap80), sgn(d.wk_gap), 0),
        "D04 Friday 15 UTC 3-day move": np.where((d.dow == 4) & (d.t.dt.hour == 15), sgn(d.ret_72), 0),
        "D05 month-end 15 UTC USD 120h move": np.where((d.month_end == 1) & (d.t.dt.hour == 15) & usd, sgn(d.ret_120), 0),
        "D06 Asian drift at London open": np.where(d.lon_open == 1, sgn(d.ret_6), 0),
        "D07 quarter-end 15 UTC 120h move": np.where((d.quarter_end == 1) & (d.t.dt.hour == 15), sgn(d.ret_120), 0),
        # I new patterns
        "I01 5+ bar streak": np.where(d.streak.abs() >= 5, sgn(d.streak), 0),
        "I02 NR7 then range bar": np.where((d.nr7_prev == 1) & (d.body_0.abs() > 0.5) & (d.size_0 > 1.0), body_dir, 0),
        "I03 wick rejection at 120h extreme": np.where((d.uwick > 0.6) & (d.rpos_120 > 0.9), -1, np.where((d.lwick > 0.6) & (d.rpos_120 < 0.1), 1, 0)),
        "I04 pair lags its currency strength (6h)": np.where(sgn(d.ret_6) == -sg5, sg5, 0),
        "I05 2-ATR single bar": np.where(d.size_0 > 2.0, body_dir, 0),
        "I06 trend acceleration (er_24>0.6)": np.where(d.er_24 > 0.6, sgn(d.ret_24), 0),
    }
    return {k: np.nan_to_num(np.asarray(v, float)).astype(np.int8) for k, v in R.items()}


def take(sym_start: np.ndarray, idx: np.ndarray, hold: int) -> np.ndarray:
    """One open trade per pair: keep a signal only if >= hold bars after the last kept one (same pair)."""
    keep, last, cur = [], -10**9, -1
    for i in idx:
        s = sym_start[i]
        if s != cur:
            cur, last = s, -10**9
        if i - last >= hold:
            keep.append(i)
            last = i
    return np.asarray(keep, dtype=np.int64)


def stat(r: np.ndarray, wk: np.ndarray, yr: np.ndarray, sym: np.ndarray) -> dict:
    if len(r) < 30:
        return {"n": len(r)}
    s = pd.Series(r).groupby(wk).sum()
    se = s.std(ddof=1) / np.sqrt(len(s))
    t = s.mean() / se if se > 0 else 0.0
    m = r.mean()
    k = len(s) / len(r)  # per-trade CI from the clustered SE
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    return {"n": len(r), "mean": m, "ci_lo": m - 1.96 * se * k, "ci_hi": m + 1.96 * se * k, "t": t, "p": stats.t.sf(t, len(s) - 1),
            "win": (r > 0).mean(), "pf": pos / neg if neg > 0 else np.inf,
            "yrs_pos": (pd.Series(r).groupby(yr).mean() > 0).mean(), "pairs_pos": (pd.Series(r).groupby(sym).mean() > 0).mean()}


def main() -> None:
    d = load()
    R = rules(d)
    sym = d.symbol.to_numpy()
    sym_start = np.flatnonzero(np.r_[True, sym[1:] != sym[:-1]])[np.cumsum(np.r_[True, sym[1:] != sym[:-1]]) - 1]
    yr, wk = d.year.to_numpy(), d.wk.to_numpy()
    out = {}
    for h in (6, 12, 24, 48, "b12"):
        if h == "b12":
            L, S = d.b12_l.to_numpy() - EXTRA[h] / d.atr_bp.to_numpy(), d.b12_s.to_numpy() - EXTRA[h] / d.atr_bp.to_numpy()
        else:
            L, S = d[f"long_{h}"].to_numpy() - EXTRA[h], d[f"short_{h}"].to_numpy() - EXTRA[h]
        out[h] = (L, S)
    rows = []
    for name, sig in R.items():
        for flip, lab in ((1, "as-defined"), (-1, "reversed")):
            dd = sig * flip
            for h, (L, S) in out.items():
                idx = np.flatnonzero((dd != 0) & np.isfinite(L) & np.isfinite(S))
                idx = take(sym_start, idx, HOLD[h])
                r = np.where(dd[idx] > 0, L[idx], S[idx])
                row = {"rule": name, "area": name[0], "dir": lab, "outcome": f"{h}h" if h != "b12" else "bracket_R"}
                for sp, (a, b) in SPLITS.items():
                    k = (yr[idx] >= a) & (yr[idx] <= b)
                    for kk, v in stat(r[k], wk[idx][k], yr[idx][k], sym[idx][k]).items():
                        row[f"{sp}_{kk}"] = v
                row["ALL_yrs_pos"] = (pd.Series(r).groupby(yr[idx]).mean() > 0).mean() if len(r) else np.nan
                row["ALL_pairs_pos"] = (pd.Series(r).groupby(sym[idx]).mean() > 0).mean() if len(r) else np.nan
                rows.append(row)
        print(name, flush=True)
    T = pd.DataFrame(rows)
    # Benjamini-Hochberg over every DISC test (one-sided p; missing p = 1)
    p = T.DISC_p.fillna(1.0).to_numpy()
    o = np.argsort(p)
    m = len(p)
    thr = 0.05 * np.arange(1, m + 1) / m
    passed = p[o] <= thr
    kmax = np.max(np.flatnonzero(passed)) if passed.any() else -1
    bh = np.zeros(m, bool)
    if kmax >= 0:
        bh[o[: kmax + 1]] = True
    T["DISC_bh"] = bh & (T.DISC_mean > 0)

    def grade(x):
        val = x.VAL_p < 0.05 and x.VAL_mean > 0
        lock = x.LOCK_p < 0.05 and x.LOCK_mean > 0
        stable = x.ALL_yrs_pos >= 0.6 and x.ALL_pairs_pos >= 0.6
        if x.DISC_bh and val and lock and stable:
            return "PROVEN"
        if (x.DISC_bh and val and x.LOCK_mean > 0) or (x.DISC_p < 0.01 and x.DISC_mean > 0 and x.VAL_p < 0.10 and x.VAL_mean > 0 and x.LOCK_p < 0.10 and x.LOCK_mean > 0):
            return "PROMISING"
        return "FAILED"
    T["grade"] = T.apply(grade, axis=1)
    T.to_csv(R3 + "r3_registry.csv", index=False)
    cols = ["rule", "dir", "outcome", "DISC_n", "DISC_mean", "DISC_t", "VAL_mean", "VAL_t", "LOCK_mean", "LOCK_t", "ALL_yrs_pos", "ALL_pairs_pos", "grade"]
    with open(R3 + "r3_tests_summary.txt", "w") as fh:
        fh.write(f"tests {m}, DISC BH survivors {int(T.DISC_bh.sum())}\n{T.grade.value_counts().to_dict()}\n\n")
        fh.write("NOT FAILED:\n" + T[T.grade != "FAILED"][cols].round(3).to_string() + "\n\n")
        fh.write("DISC BH survivors:\n" + T[T.DISC_bh][cols].round(3).to_string() + "\n\n")
        fh.write("Top 25 by DISC t:\n" + T.sort_values("DISC_t", ascending=False).head(25)[cols].round(3).to_string() + "\n")
    print(open(R3 + "r3_tests_summary.txt").read())


if __name__ == "__main__":
    main()
