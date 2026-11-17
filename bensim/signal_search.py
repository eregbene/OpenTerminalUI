"""Signal search: let a model look for an entry signal across many inputs at once, judged only on years it never saw.

    python -m bensim.signal_search build          # features + outcomes -> data/signal_search.pkl
    python -m bensim.signal_search run            # walk-forward 2023..2026 + opposite-trade + shuffled-label checks

Decision every H1 close, all symbols. Inputs are known at that close: returns, volatility, day / Asian / week levels
(liquidity), sweeps, Donchian position, H1/H4/D1 swing trend, session, weekday and a USD factor built from the other
pairs. Outcome per direction: market entry at the close, stop 1 ATR(H1), target 2 ATR, first touch on M5 bars (bid/ask,
a bar touching both = stop), 48h time exit, minus round-trip costs, in R. A model trained on years < Y picks trades in
year Y; one open trade per symbol. Believed only if every unseen year is positive, the opposite direction at the same
moments is worse, and the same model trained on shuffled outcomes finds nothing.
"""
from __future__ import annotations

import argparse
import os
import pickle

import numpy as np
import pandas as pd

from . import backtest as bt
from .strategy import Params, atr, pip, trend

SYMBOLS = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCHF", "USDCAD", "XAUUSD"]
USD_SIGN = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "XAUUSD": -1, "USDJPY": 1, "USDCHF": 1, "USDCAD": 1}  # +1: price up = USD up
START, END = "2021-10-04", "2026-10-14"
STOP_ATR, TARGET_ATR, HOLD_M5 = 1.0, 2.0, 48 * 12
PATH = "data/signal_search.pkl"  # our own cache file, written by build() (pickle is fine: never loaded from elsewhere)


def _outcomes(m5: dict, t_dec: np.ndarray, a: np.ndarray, sym: str) -> tuple[np.ndarray, ...]:
    """R (net of costs) and exit time of a long and a short opened at each decision time."""
    T, Hh, Ll, Cc = m5["t"], m5["high"], m5["low"], m5["close"]
    spr = Params().cost_pips.get(sym, 1.2) * pip(sym)  # round-trip cost incl. commission, as a price distance
    k0 = np.searchsorted(T, t_dec)  # first M5 bar opening at/after the decision
    n = len(T)
    pad = np.r_[np.arange(n), np.full(HOLD_M5, n - 1)]
    out = {}
    for d in (1, -1):
        r = np.full(len(t_dec), np.nan)
        tx = np.zeros(len(t_dec), np.int64)
        for i, k in enumerate(k0):
            if k >= n - 2 or not np.isfinite(a[i]) or a[i] <= 0:
                continue
            e = m5["open"][k]
            sl, tp = e - d * STOP_ATR * a[i], e + d * TARGET_ATR * a[i]
            w = pad[k:k + HOLD_M5]
            h, lo = Hh[w], Ll[w]
            hit_sl = (lo <= sl) if d == 1 else (h >= sl)
            hit_tp = (h >= tp) if d == 1 else (lo <= tp)
            js = int(np.argmax(hit_sl)) if hit_sl.any() else HOLD_M5
            jt = int(np.argmax(hit_tp)) if hit_tp.any() else HOLD_M5
            if js <= jt and js < HOLD_M5:
                gross, j = -STOP_ATR * a[i], js
            elif jt < HOLD_M5:
                gross, j = TARGET_ATR * a[i], jt
            else:
                j = HOLD_M5 - 1
                gross = (Cc[w[j]] - e) * d
            r[i] = (gross - spr) / (STOP_ATR * a[i])
            tx[i] = int(T[w[j]]) + 300
        out[d] = (r, tx)
    return out[1][0], out[-1][0], out[1][1], out[-1][1]


def _features(sym: str, m5: dict) -> pd.DataFrame:
    h1 = bt.resample(m5, 3600)
    t = h1["t"] + 3600  # decision time = H1 close
    o, h, lo, c = h1["open"], h1["high"], h1["low"], h1["close"]
    a = atr(h1)
    f = pd.DataFrame({"t": t, "symbol": sym, "atr": a, "close": c})
    for n in (1, 4, 24, 120):
        f[f"ret_{n}"] = (c - np.r_[np.full(n, c[0]), c[:-n]]) / a
    f["atr_ratio"] = a / pd.Series(a).rolling(100, min_periods=20).mean().to_numpy()
    f["range_atr"] = (h - lo) / a
    f["body"] = (c - o) / np.maximum(h - lo, 1e-12)
    for n in (24, 120):
        hi, ll = pd.Series(h).rolling(n).max().to_numpy(), pd.Series(lo).rolling(n).min().to_numpy()
        f[f"pos_{n}"] = (c - ll) / np.maximum(hi - ll, 1e-12)
        f[f"to_hi_{n}"], f[f"to_lo_{n}"] = (hi - c) / a, (c - ll) / a
    don_hi = pd.Series(h).rolling(20).max().shift(1).to_numpy()
    don_lo = pd.Series(lo).rolling(20).min().shift(1).to_numpy()
    f["don_break"] = np.where(c > don_hi, (c - don_hi) / a, np.where(c < don_lo, (c - don_lo) / a, 0.0))
    # day / previous day / Asian session / previous week levels (liquidity) -- only completed parts
    df = pd.DataFrame({"day": (h1["t"] // 86400), "h": h, "l": lo, "hr": (h1["t"] % 86400) // 3600, "wk": (h1["t"] // 86400 + 3) // 7})
    dh, dl = df.groupby("day").h.max(), df.groupby("day").l.min()
    pdh, pdl = df.day.map(dh.shift(1)).to_numpy(), df.day.map(dl.shift(1)).to_numpy()
    f["to_pdh"], f["to_pdl"] = (pdh - c) / a, (c - pdl) / a
    cum_hi, cum_lo = df.groupby("day").h.cummax().to_numpy(), df.groupby("day").l.cummin().to_numpy()
    f["day_pos"] = (c - cum_lo) / np.maximum(cum_hi - cum_lo, 1e-12)
    asia = df[df.hr < 7]
    ah, al = df.day.map(asia.groupby("day").h.max()).to_numpy(), df.day.map(asia.groupby("day").l.min()).to_numpy()
    after = df.hr.to_numpy() >= 7
    f["to_asia_hi"] = np.where(after, (ah - c) / a, np.nan)
    f["to_asia_lo"] = np.where(after, (c - al) / a, np.nan)
    wh, wl = df.groupby("wk").h.max(), df.groupby("wk").l.min()
    f["to_pwh"], f["to_pwl"] = (df.wk.map(wh.shift(1)).to_numpy() - c) / a, (c - df.wk.map(wl.shift(1)).to_numpy()) / a
    # sweep in the last 3 bars: wick beyond the previous 24h extreme, close back inside (+1 = lows swept, bullish)
    p24h = pd.Series(h).rolling(24).max().shift(1).to_numpy()
    p24l = pd.Series(lo).rolling(24).min().shift(1).to_numpy()
    sw = np.where((lo < p24l) & (c > p24l), 1, np.where((h > p24h) & (c < p24h), -1, 0))
    f["sweep3"] = pd.Series(sw).rolling(3, min_periods=1).sum().to_numpy()
    # swing trend on H1 / H4 / D1, value known at this close
    f["tr_h1"] = trend(h1)
    for sec, name in ((14400, "tr_h4"), (86400, "tr_d1")):
        b = bt.resample(m5, sec)
        tr = trend(b)
        idx = np.searchsorted(b["t"] + sec, t, side="right") - 1
        f[name] = np.where(idx >= 0, tr[np.clip(idx, 0, None)], 0)
    # variance ratio (momentum > 1 > mean reversion), 5 days of H1 returns, q = 6
    r1 = pd.Series(np.diff(np.log(c), prepend=np.log(c[0])))
    r6 = pd.Series(np.log(c) - np.log(np.r_[np.full(6, c[0]), c[:-6]]))
    f["vr"] = (r6.rolling(120).var() / (6 * r1.rolling(120).var())).to_numpy()
    f["hour"], f["weekday"] = (t % 86400) // 3600, ((t // 86400) + 3) % 7
    f["ret1_raw"] = r1.to_numpy()
    f["r_long"], f["r_short"], f["x_long"], f["x_short"] = _outcomes(m5, t, a, sym)
    return f


def build() -> pd.DataFrame:
    frames = []
    for s in SYMBOLS:
        m5 = bt.load_m5(s, START, END)
        if len(m5["t"]):
            frames.append(_features(s, m5))
            print(s, len(frames[-1]), flush=True)
    f = pd.concat(frames, ignore_index=True)
    # USD factor: average USD move of the OTHER pairs over 4h / 24h (no lookahead: aligned on the same decision time)
    for n in (4, 24):
        piv = f.pivot_table(index="t", columns="symbol", values=f"ret_{n}") * pd.Series(USD_SIGN)
        tot, cnt = piv.sum(axis=1), piv.notna().sum(axis=1)
        own = f.set_index(["t", "symbol"])[f"ret_{n}"] * f.symbol.map(USD_SIGN).to_numpy()
        f[f"usd_{n}"] = ((tot.reindex(f.t).to_numpy() - own.to_numpy()) / np.maximum(cnt.reindex(f.t).to_numpy() - 1, 1))
        f[f"usd_{n}_pair"] = f[f"usd_{n}"] * f.symbol.map(USD_SIGN)  # >0: the dollar move pushes this pair up
    f["year"] = pd.to_datetime(f.t, unit="s").dt.year
    with open(PATH, "wb") as fh:
        pickle.dump(f, fh)
    return f


FEATURES = ["ret_1", "ret_4", "ret_24", "ret_120", "atr_ratio", "range_atr", "body", "pos_24", "to_hi_24", "to_lo_24", "pos_120",
            "to_hi_120", "to_lo_120", "don_break", "to_pdh", "to_pdl", "day_pos", "to_asia_hi", "to_asia_lo", "to_pwh", "to_pwl",
            "sweep3", "tr_h1", "tr_h4", "tr_d1", "vr", "hour", "weekday", "usd_4", "usd_24", "usd_4_pair", "usd_24_pair", "sym_id"]


def _trades(test: pd.DataFrame, pl: np.ndarray, ps: np.ndarray, thr: float) -> pd.DataFrame:
    """Take the better side when its predicted R > thr; one open trade per symbol."""
    rows, busy = [], {}
    for i, x in enumerate(test.itertuples()):
        if busy.get(x.symbol, 0) > x.t:
            continue
        d = 1 if pl[i] >= ps[i] else -1
        if max(pl[i], ps[i]) <= thr:
            continue
        r, opp, tx = (x.r_long, x.r_short, x.x_long) if d == 1 else (x.r_short, x.r_long, x.x_short)
        if not np.isfinite(r):
            continue
        busy[x.symbol] = tx
        rows.append((x.t, x.symbol, d, r, opp, x.year))
    return pd.DataFrame(rows, columns=["t", "symbol", "dir", "r", "opp", "year"])


def run(top: float = 0.03, shuffle: bool = False, seed: int = 0, window: int = 0, diff: bool = False) -> pd.DataFrame:
    """window: train on only the last N years (0 = all earlier years). diff: one model on r_long - r_short (direction)."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    with open(PATH, "rb") as fh:
        f = pickle.load(fh)
    f = f.sort_values(["t", "symbol"]).reset_index(drop=True)
    f["sym_id"] = f.symbol.map({s: i for i, s in enumerate(SYMBOLS)})
    f = f[np.isfinite(f.r_long) & np.isfinite(f.r_short)]
    out = []
    for y in (2023, 2024, 2025, 2026):
        tr = f[(f.year < y) & (f.x_long < pd.Timestamp(f"{y}-01-01", tz="UTC").timestamp())]  # outcome known before the test year
        if window:
            tr = tr[tr.year >= y - window]
        te = f[f.year == y]
        preds = {}
        targets = {"d": (tr.r_long - tr.r_short).to_numpy().clip(-4, 4)} if diff else             {s: tr[s].to_numpy().clip(-1.5, TARGET_ATR / STOP_ATR + 0.5) for s in ("r_long", "r_short")}
        for side, yv in targets.items():
            if shuffle:
                yv = np.random.default_rng(seed).permutation(yv)
            m = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=400,
                                              l2_regularization=1.0, random_state=seed)
            m.fit(tr[FEATURES], yv)
            preds[side] = (m.predict(tr[FEATURES]), m.predict(te[FEATURES]))
        if diff:  # predicted edge of long over short; the side is its sign, the size |edge|
            preds = {"r_long": tuple(p / 2 for p in preds["d"]), "r_short": tuple(-p / 2 for p in preds["d"])}
        thr = float(np.quantile(np.maximum(preds["r_long"][0], preds["r_short"][0]), 1 - top))  # set on TRAINING years only
        tt = _trades(te, preds["r_long"][1], preds["r_short"][1], thr)
        out.append(tt)
    return pd.concat(out, ignore_index=True)


def report(tt: pd.DataFrame, label: str) -> None:
    def st(r):
        return f"{len(r):5} tr  win {np.mean(r > 0) * 100:4.1f}%  avg {r.mean():+.3f}R  total {r.sum():+7.1f}R  t {r.mean() / r.std() * np.sqrt(len(r)):+.2f}"
    print(f"\n== {label}")
    for y, g in tt.groupby("year"):
        print(f"  {y}  model: {st(g.r.to_numpy())} | opposite: {g.opp.mean():+.3f}R")
    print(f"  ALL   model: {st(tt.r.to_numpy())} | opposite: {tt.opp.mean():+.3f}R")
    print("  by symbol:", tt.groupby("symbol").r.agg(["count", "mean"]).round(3).to_dict("index"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "run"])
    ap.add_argument("--top", type=float, default=0.03)
    ap.add_argument("--stop", type=float, default=1.0)
    ap.add_argument("--target", type=float, default=2.0)
    ap.add_argument("--hold", type=int, default=48, help="hours")
    ap.add_argument("--window", type=int, default=0, help="train on the last N years only (0 = all)")
    ap.add_argument("--diff", action="store_true", help="one direction model (r_long - r_short)")
    a = ap.parse_args()
    STOP_ATR, TARGET_ATR, HOLD_M5 = a.stop, a.target, a.hold * 12
    if (a.stop, a.target, a.hold) != (1.0, 2.0, 48):
        PATH = f"data/signal_search_s{a.stop:g}_t{a.target:g}_h{a.hold}.pkl"
    os.environ.setdefault("OMP_NUM_THREADS", "4")  # leave CPU for the live engine
    if a.cmd == "build":
        build()
    else:
        with open(PATH, "rb") as fh:
            base = pickle.load(fh)
        print("baseline (every hour, every symbol): long", round(np.nanmean(base.r_long), 3), "R  short", round(np.nanmean(base.r_short), 3), "R")
        tag = f"{'direction model' if a.diff else 'long+short models'}, train window {a.window or 'all'}y"
        for top in (0.01, 0.03, 0.10):
            report(run(top, window=a.window, diff=a.diff), f"{tag}, top {top:.0%} predictions (threshold from training years)")
        report(run(a.top, shuffle=True, window=a.window, diff=a.diff), f"PLACEBO {tag}: SHUFFLED outcomes, top {a.top:.0%}")
