"""Macro FX: do 2-year government yield gaps drive the pairs (the post-2021 rate regime)?

    python -m bensim.macro_yields fetch     # central-bank / treasury downloads -> data/macro/yields_2y.csv
    python -m bensim.macro_yields rule      # rate-gap momentum rule on USD/EUR/JPY/CAD pairs, 2012-now

Sources (all free, daily, fetched 2026-10-14): USD US Treasury daily par yield curve (2 Yr); EUR ECB euro-area AAA
spot curve 2Y (YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y); JPY Ministry of Finance JGB 2Y; CAD Bank of Canada benchmark 2-year
(BD.CDN.2YR.DQ.YLD). GBP/CHF/AUD/NZD daily series were not downloadable from a script (blocked / monthly only).
Yields are LAGGED ONE BUSINESS DAY before use (ECB publishes the next day), so nothing is used before it is public.

Rule M1 (fixed before running): every NY-close day, gap = y2(base) - y2(quote); if the gap rose over the last N days
(N = 5, 20, 60, all reported) -> long the pair, else short; position held day to day; P&L = next day's mid return in bp
minus the pair's median real spread whenever the position flips. Pairs: EURUSD USDJPY USDCAD EURJPY CADJPY (EURCAD not on FXCM).
"""
from __future__ import annotations

import argparse
import io
import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from . import parallax as px
from .fxcm_history import load

OUT = Path("data/macro")
PAIRS = ["EURUSD", "USDJPY", "USDCAD", "EURJPY", "CADJPY"]  # EURCAD: not on FXCM
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def fetch() -> pd.DataFrame:
    cols = {}
    us = []
    for y in range(2011, pd.Timestamp.now().year + 1):
        u = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
             f"{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv")
        d = pd.read_csv(io.BytesIO(_get(u)))
        us.append(pd.Series(d["2 Yr"].to_numpy(), index=pd.to_datetime(d.Date, format="%m/%d/%Y")))
    cols["USD"] = pd.concat(us)
    e = pd.read_csv(io.BytesIO(_get("https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y"
                                    "?format=csvdata&startPeriod=2011-01-01")))
    cols["EUR"] = pd.Series(e.OBS_VALUE.to_numpy(), index=pd.to_datetime(e.TIME_PERIOD))
    # MoF: the history file stops at the end of last month; the current month is a separate file -- read both
    j = pd.concat([pd.read_csv(io.BytesIO(_get(f"https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/{name}")),
                               skiprows=1, na_values="-", encoding="latin-1")
                   for name in ("historical/jgbcme_all.csv", "jgbcme.csv")])
    j = j[pd.to_datetime(j.Date, format="%Y/%m/%d", errors="coerce").notna()].drop_duplicates("Date", keep="last")
    cols["JPY"] = pd.Series(pd.to_numeric(j["2Y"], errors="coerce").to_numpy(), index=pd.to_datetime(j.Date, format="%Y/%m/%d"))
    c = json.loads(_get("https://www.bankofcanada.ca/valet/observations/BD.CDN.2YR.DQ.YLD/json?start_date=2011-01-01"))
    cols["CAD"] = pd.Series({pd.Timestamp(o["d"]): float(o["BD.CDN.2YR.DQ.YLD"]["v"]) for o in c["observations"]
                             if o.get("BD.CDN.2YR.DQ.YLD", {}).get("v") not in (None, "")})
    y = pd.DataFrame({k: v[~v.index.duplicated()] for k, v in cols.items()}).sort_index()
    y = y[y.index >= "2011-01-01"]
    OUT.mkdir(parents=True, exist_ok=True)
    y.to_csv(OUT / "yields_2y.csv")
    print(y.describe().round(2).to_string())
    print(y.tail(3).to_string())
    return y


def daily_mid(sym: str) -> tuple[pd.Series, float]:
    m = load(sym, "H1")
    b = px.ny_bars({"t": m["t"], "open": m["open"], "high": m["high"], "low": m["low"], "close": m["close"] + m["spr"] / 2}, 24)
    b = b[pd.to_datetime(b.t_close, unit="s").dt.dayofweek < 5]
    s = pd.Series(b.close.to_numpy(), index=pd.to_datetime(b.t_close, unit="s").dt.normalize())
    return s[~s.index.duplicated(keep="last")], float(np.median(m["spr"] / m["close"]) * 1e4)


def rule() -> None:
    y = pd.read_csv(OUT / "yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5)
    y = y.shift(1)  # one business day publication lag
    res = []
    for sym in PAIRS:
        px_, cost = daily_mid(sym)
        gap = (y[sym[:3]] - y[sym[3:]]).reindex(px_.index, method="ffill")
        ret = np.log(px_).diff().shift(-1) * 1e4  # next day's mid return, bp
        for n in (5, 20, 60):
            pos = np.sign(gap - gap.shift(n)).replace(0, np.nan).ffill()
            pnl = pos * ret - pos.diff().abs().fillna(0) / 2 * cost  # a flip long<->short = close + open = one full spread
            df = pd.DataFrame({"pnl": pnl, "year": pnl.index.year}).dropna()
            df = df[df.year >= 2012]
            res.append(df.assign(pair=sym, n=n))
    r = pd.concat(res)
    pd.set_option("display.width", 200)
    for n, g in r.groupby("n"):
        port = g.groupby(level=0).pnl.mean()  # equal-weight the 6 pairs each day
        ys = port.groupby(port.index.year).sum()
        def per(a, b):
            p = port[(port.index.year >= a) & (port.index.year <= b)]
            return f"{p.mean() * 252:+.0f} bp/yr Sharpe {p.mean() / p.std() * np.sqrt(252):+.2f}"
        print(f"\nM1 N={n:2}d  2012-17: {per(2012, 2017)} | 2018-19: {per(2018, 2019)} | 2020-now: {per(2020, 2030)} | "
              f"years+ {(ys > 0).sum()}/{len(ys)}")
        print("   per year (bp, 6-pair average): " + " ".join(f"{k}:{v:+.0f}" for k, v in ys.items()))
        pp = g[g.year >= 2020].groupby("pair").pnl.mean() * 252
        print("   2020-now by pair (bp/yr): " + ", ".join(f"{k} {v:+.0f}" for k, v in pp.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "rule", "ml", "checks", "ensemble"])
    a = ap.parse_args()
    {"fetch": fetch, "rule": rule, "ml": ml, "checks": ml_checks, "ensemble": ml_ensemble}[a.cmd]()



def ml(h: int = 6) -> None:
    """The edge-miner model + yield features (gap level, 5/20/60-day gap change, lagged 1 day; NaN for pairs without
    yields), yearly walk-forward exactly like bensim.edge_wf (train all prior years, threshold from the previous year),
    traded on the 5 yield pairs. Run once with and once without the yield features -- the difference is the answer."""
    import pickle

    from . import edge_wf as wf
    from .edge_miner import FEATURES, PATH
    with open(PATH, "rb") as fh:  # our own cache from edge_miner build
        f = pickle.load(fh)
    y = pd.read_csv(OUT / "yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    day = f.t.dt.normalize()
    for c in ("level", "d5", "d20", "d60"):
        f[f"gap_{c}"] = np.nan
    for sym in f.symbol.unique():
        b, q = sym[:3], sym[3:]
        if b not in y or q not in y:
            continue
        g = y[b] - y[q]
        k = f.symbol == sym
        f.loc[k, "gap_level"] = g.reindex(day[k], method="ffill").to_numpy()
        for n in (5, 20, 60):
            f.loc[k, f"gap_d{n}"] = (g - g.shift(n)).reindex(day[k], method="ffill").to_numpy()
    macro = FEATURES + ["gap_level", "gap_d5", "gap_d20", "gap_d60"]
    wf.H = h
    for label, feats in (("WITHOUT yields", FEATURES), ("WITH yields", macro)):
        wf.FEATURES = feats
        rows = []
        for yr in range(2016, int(f.year.max()) + 1):
            tr = f[f.year < yr - 1]
            ok = np.isfinite(tr[f"long_{h}"]) & np.isfinite(tr[f"short_{h}"])
            tr = tr[ok]
            fit = lambda yy: __import__("sklearn.ensemble", fromlist=["x"]).HistGradientBoostingRegressor(  # noqa: E731
                max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=2000, l2_regularization=1.0,
                random_state=0).fit(tr[feats], yy)
            ml_, ms_ = fit(np.clip(tr[f"long_{h}"], -300, 300)), fit(np.clip(tr[f"short_{h}"], -300, 300))
            prev = f[(f.year == yr - 1) & f.symbol.isin(PAIRS)]
            thr = float(np.quantile(np.maximum(ml_.predict(prev[feats]), ms_.predict(prev[feats])), 0.95))
            cur = f[(f.year == yr) & f.symbol.isin(PAIRS)]
            rows.append(wf.trades(cur, ml_.predict(cur[feats]), ms_.predict(cur[feats]), thr))
        t = pd.concat(rows, ignore_index=True)
        ys = t.groupby("year").bp.mean()
        post = t[t.year >= 2021].bp
        print(f"{label:15} {h}h: {len(t)} trades avg {t.bp.mean():+.2f} bp t {t.bp.mean() / t.bp.std() * np.sqrt(len(t)):+.2f} "
              f"years+ {(ys > 0).sum()}/{len(ys)} | 2021-now avg {post.mean():+.2f} bp ({len(post)} tr)")
        print("     per year: " + " ".join(f"{k}:{v:+.2f}" for k, v in ys.items()), flush=True)



def ml_checks(h: int = 6) -> None:
    """Stress checks for the WITH-yields walk-forward: placebo (shuffled training outcomes), model settings
    (min_samples_leaf 1000 / 4000, another seed), and +0.5 bp extra cost per trade."""
    import pickle

    from . import edge_wf as wf
    from .edge_miner import FEATURES, PATH
    from sklearn.ensemble import HistGradientBoostingRegressor
    with open(PATH, "rb") as fh:  # our own cache from edge_miner build
        f = pickle.load(fh)
    y = pd.read_csv(OUT / "yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    day = f.t.dt.normalize()
    for c in ("level", "d5", "d20", "d60"):
        f[f"gap_{c}"] = np.nan
    for sym in f.symbol.unique():
        b, q = sym[:3], sym[3:]
        if b in y and q in y:
            g, k = y[b] - y[q], f.symbol == sym
            f.loc[k, "gap_level"] = g.reindex(day[k], method="ffill").to_numpy()
            for n in (5, 20, 60):
                f.loc[k, f"gap_d{n}"] = (g - g.shift(n)).reindex(day[k], method="ffill").to_numpy()
    feats = FEATURES + ["gap_level", "gap_d5", "gap_d20", "gap_d60"]
    wf.H = h
    rng = np.random.default_rng(1)
    variants = {"base (leaf 2000, seed 0)": dict(leaf=2000, seed=0, shuffle=False), "leaf 1000": dict(leaf=1000, seed=0, shuffle=False),
                "leaf 4000": dict(leaf=4000, seed=0, shuffle=False), "seed 7": dict(leaf=2000, seed=7, shuffle=False),
                "PLACEBO shuffled outcomes": dict(leaf=2000, seed=0, shuffle=True)}
    for label, v in variants.items():
        rows = []
        for yr in range(2016, int(f.year.max()) + 1):
            tr = f[f.year < yr - 1]
            tr = tr[np.isfinite(tr[f"long_{h}"]) & np.isfinite(tr[f"short_{h}"])]
            yl, ys_ = np.clip(tr[f"long_{h}"].to_numpy(), -300, 300), np.clip(tr[f"short_{h}"].to_numpy(), -300, 300)
            if v["shuffle"]:
                yl, ys_ = rng.permutation(yl), rng.permutation(ys_)
            mk = lambda yy: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,  # noqa: E731
                                                          min_samples_leaf=v["leaf"], l2_regularization=1.0, random_state=v["seed"]).fit(tr[feats], yy)
            ml_, ms_ = mk(yl), mk(ys_)
            prev = f[(f.year == yr - 1) & f.symbol.isin(PAIRS)]
            thr = float(np.quantile(np.maximum(ml_.predict(prev[feats]), ms_.predict(prev[feats])), 0.95))
            cur = f[(f.year == yr) & f.symbol.isin(PAIRS)]
            rows.append(wf.trades(cur, ml_.predict(cur[feats]), ms_.predict(cur[feats]), thr))
        t = pd.concat(rows, ignore_index=True)
        ys = t.groupby("year").bp.mean()
        post = t[t.year >= 2021].bp
        print(f"{label:28} {len(t):5} tr avg {t.bp.mean():+.2f} bp (+0.5bp cost: {t.bp.mean() - 0.5:+.2f}) t {t.bp.mean() / t.bp.std() * np.sqrt(len(t)):+.2f} "
              f"years+ {(ys > 0).sum()}/{len(ys)} | 2021-now {post.mean():+.2f} bp years+ {(ys.loc[2021:] > 0).sum()}/{len(ys.loc[2021:])}", flush=True)



def ml_ensemble(h: int = 6, seeds: tuple[int, ...] = (0, 7, 13, 21, 42)) -> pd.DataFrame:
    """WITH-yields walk-forward where every prediction is the AVERAGE of models trained with 5 seeds (the seed only
    changes the internal early-stopping split; averaging removes that luck). Same rules otherwise. Saves the trades."""
    import pickle

    from . import edge_wf as wf
    from .edge_miner import FEATURES, PATH
    from sklearn.ensemble import HistGradientBoostingRegressor
    with open(PATH, "rb") as fh:  # our own cache from edge_miner build
        f = pickle.load(fh)
    y = pd.read_csv(OUT / "yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    day = f.t.dt.normalize()
    for c in ("level", "d5", "d20", "d60"):
        f[f"gap_{c}"] = np.nan
    for sym in f.symbol.unique():
        b, q = sym[:3], sym[3:]
        if b in y and q in y:
            g, k = y[b] - y[q], f.symbol == sym
            f.loc[k, "gap_level"] = g.reindex(day[k], method="ffill").to_numpy()
            for n in (5, 20, 60):
                f.loc[k, f"gap_d{n}"] = (g - g.shift(n)).reindex(day[k], method="ffill").to_numpy()
    feats = FEATURES + ["gap_level", "gap_d5", "gap_d20", "gap_d60"]
    wf.H = h
    rows = []
    for yr in range(2016, int(f.year.max()) + 1):
        tr = f[f.year < yr - 1]
        tr = tr[np.isfinite(tr[f"long_{h}"]) & np.isfinite(tr[f"short_{h}"])]
        prev = f[(f.year == yr - 1) & f.symbol.isin(PAIRS)]
        cur = f[(f.year == yr) & f.symbol.isin(PAIRS)]
        pl_prev, ps_prev, pl, ps = 0.0, 0.0, 0.0, 0.0
        for s in seeds:
            mk = lambda yy: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,  # noqa: E731
                                                          min_samples_leaf=2000, l2_regularization=1.0, random_state=s).fit(tr[feats], yy)
            ml_, ms_ = mk(np.clip(tr[f"long_{h}"], -300, 300)), mk(np.clip(tr[f"short_{h}"], -300, 300))
            pl_prev, ps_prev = pl_prev + ml_.predict(prev[feats]) / len(seeds), ps_prev + ms_.predict(prev[feats]) / len(seeds)
            pl, ps = pl + ml_.predict(cur[feats]) / len(seeds), ps + ms_.predict(cur[feats]) / len(seeds)
        thr = float(np.quantile(np.maximum(pl_prev, ps_prev), 0.95))
        t = wf.trades(cur, pl, ps, thr)
        rows.append(t)
        print(f"  {yr}: {len(t)} trades avg {t.bp.mean():+.2f} bp", flush=True)
    t = pd.concat(rows, ignore_index=True)
    ys = t.groupby("year").bp.mean()
    post = t[t.year >= 2021].bp
    print(f"ENSEMBLE 5 seeds: {len(t)} tr avg {t.bp.mean():+.2f} bp (+0.5bp cost {t.bp.mean() - 0.5:+.2f}) t {t.bp.mean() / t.bp.std() * np.sqrt(len(t)):+.2f} "
          f"years+ {(ys > 0).sum()}/{len(ys)} | 2021-now {post.mean():+.2f} bp t {post.mean() / post.std() * np.sqrt(len(post)):+.2f} years+ {(ys.loc[2021:] > 0).sum()}/{len(ys.loc[2021:])}")
    print("  by pair: " + ", ".join(f"{k} {v:+.2f}" for k, v in t.groupby("symbol").bp.mean().items()))
    t.to_csv(OUT / "ensemble_trades.csv", index=False)
    return t


if __name__ == "__main__":
    main()
