"""Walk-forward "improver" for fib618: learn the best filter on the past, trade it on the unseen next period, roll on.

    python -m bensim.walk_forward [--trades data/fib618_base_5y.csv] [--train 12] [--test 1]

Candidates = every single condition and every AND-pair of conditions: the engine's own checks (trend, BOS/CHoCH, FVG,
OB, discount, sweep, killzone, liquidity ahead), the indicators fib traders add (H1 200 EMA side, RSI 14, ADX 14,
MACD histogram, rejection candle -- all from H1 bars CLOSED before the entry), session and pair. Each step: score every
candidate on trades that EXITED inside the last `train` months (min 25 trades), take the best avg R; if even the best is
<= 0, stand aside. Apply it to the next `test` months. The stitched unseen months are the honest result. Placebo: the
same loop picking a random candidate (with >= 25 training trades), 200 times.
"""
from __future__ import annotations

import argparse
import itertools

import numpy as np
import pandas as pd

from .backtest import load_m5, resample

MIN_N = 25


def _h1_indicators(sym: str) -> pd.DataFrame:
    h = resample(load_m5(sym, "2021-06-01", None), 3600)
    c, hi, lo, o = (pd.Series(h[k]) for k in ("close", "high", "low", "open"))
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / 14).mean(), (-d.clip(upper=0)).ewm(alpha=1 / 14).mean()
    tr = pd.concat([hi - lo, (hi - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14).mean()
    pdm = (hi.diff()).where((hi.diff() > -lo.diff()) & (hi.diff() > 0), 0.0)
    ndm = (-lo.diff()).where((-lo.diff() > hi.diff()) & (-lo.diff() > 0), 0.0)
    pdi, ndi = 100 * pdm.ewm(alpha=1 / 14).mean() / atr, 100 * ndm.ewm(alpha=1 / 14).mean() / atr
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    rng = (hi - lo).replace(0, np.nan)
    return pd.DataFrame({"t_close": h["t"] + 3600, "ema200": c.ewm(span=200).mean(), "close": c,
                         "rsi": 100 - 100 / (1 + up / dn), "adx": (100 * (pdi - ndi).abs() / (pdi + ndi)).ewm(alpha=1 / 14).mean(),
                         "macd_h": macd - macd.ewm(span=9).mean(),
                         "bull_rej": (np.minimum(o, c) - lo) / rng, "bear_rej": (hi - np.maximum(o, c)) / rng})


def conditions(t: pd.DataFrame) -> dict[str, np.ndarray]:
    parts = []
    for s, g in t.groupby("symbol"):
        ind = _h1_indicators(s)
        i = np.searchsorted(ind.t_close.to_numpy(), g.t_entry.to_numpy(), side="right") - 1  # last H1 closed at entry
        parts.append(pd.DataFrame(ind.iloc[i].to_numpy(), index=g.index, columns=ind.columns, dtype=float))
    x = pd.concat(parts).loc[t.index]
    d = t.dir.to_numpy()
    hr = (t.t_entry % 86400) // 3600
    rsi_dir = np.where(d > 0, x.rsi, 100 - x.rsi)  # low = pulled back (oversold for a long)
    cond = {
        "trend H1 swing": t.chk_swing_trend.astype(bool),
        "BOS (not CHoCH)": (t.chk_internal_break == "BOS"),
        "FVG": t.chk_fvg.astype(bool), "OB": t.chk_ob.astype(bool),
        "discount/premium": t.chk_discount.astype(bool), "any sweep": (t.chk_sweep != "none"),
        "killzone": t.chk_killzone.astype(bool),
        "liquidity >=1R ahead": (t.chk_liq_ahead_r.fillna(99) >= 1),
        "EMA200 side agrees": (np.sign(x.close - x.ema200) == d),
        "RSI pulled back (<45 dir)": (rsi_dir < 45), "RSI not stretched (<70 dir)": (rsi_dir < 70),
        "ADX > 25 (trending)": (x.adx > 25), "ADX < 20 (ranging)": (x.adx < 20),
        "MACD hist agrees": (np.sign(x.macd_h) == d),
        "rejection candle (wick>=40%)": (np.where(d > 0, x.bull_rej, x.bear_rej) >= 0.4),
        "London 07-12": ((hr >= 7) & (hr < 12)), "NY 12-17": ((hr >= 12) & (hr < 17)),
        "not Asia (07-21)": ((hr >= 7) & (hr < 21)),
    }
    for s in sorted(t.symbol.unique()):
        cond[f"not {s}"] = (t.symbol != s).to_numpy()
    return {k: np.asarray(v, bool) for k, v in cond.items()}


def candidates(cond: dict[str, np.ndarray], n: int) -> list[tuple[str, np.ndarray]]:
    out = [("ALL trades", np.ones(n, bool))] + list(cond.items())
    out += [(f"{a} + {b}", cond[a] & cond[b]) for a, b in itertools.combinations(cond, 2)]
    return out


def walk(t: pd.DataFrame, cands: list, train: int, test: int, rng: np.random.Generator | None = None) -> pd.DataFrame:
    months = pd.date_range(pd.Timestamp(t.t_entry.min(), unit="s").normalize().replace(day=1), pd.Timestamp.now(), freq="MS")
    te, tx, r = t.t_entry.to_numpy(), t.t_exit.to_numpy(), t.r.to_numpy()
    rows = []
    for k in range(train, len(months), test):
        a, b = months[k - train].timestamp(), months[k].timestamp()
        c = months[min(k + test, len(months) - 1)].timestamp() if k + test < len(months) else 1e12
        tr = (te >= a) & (tx < b)
        ts = (te >= b) & (te < c)
        best, name, score = None, "", -np.inf
        if rng is not None:
            ok = [(nm, m) for nm, m in cands if (m & tr).sum() >= MIN_N]
            name, best = ok[rng.integers(len(ok))]
        else:
            for nm, m in cands:
                sel = m & tr
                if sel.sum() >= MIN_N and (s := r[sel].mean()) > score:
                    best, name, score = m, nm, s
            if score <= 0:
                best, name = None, "STAND ASIDE"
        pick = (best & ts) if best is not None else np.zeros(len(t), bool)
        rows.append({"month": months[k].strftime("%Y-%m"), "rule": name, "train_avg": score, "n": int(pick.sum()),
                     "R": float(r[pick].sum()), "all_n": int(ts.sum()), "all_R": float(r[ts].sum())})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="data/fib618_base_5y.csv")
    ap.add_argument("--train", type=int, default=12, help="months of history to learn from")
    ap.add_argument("--test", type=int, default=1, help="months traded before re-learning")
    a = ap.parse_args()
    t = pd.read_csv(a.trades).sort_values("t_entry").reset_index(drop=True)
    cands = candidates(conditions(t), len(t))
    print(f"{a.trades}: {len(t)} trades, {len(cands)} candidate rules, learn {a.train} months -> trade next {a.test}")
    w = walk(t, cands, a.train, a.test)
    w["year"] = w.month.str[:4]
    pd.set_option("display.width", 200)
    print(w[["month", "rule", "train_avg", "n", "R", "all_R"]].to_string(index=False, float_format=lambda v: f"{v:+.2f}"))
    y = w.groupby("year").agg(n=("n", "sum"), R=("R", "sum"), all_n=("all_n", "sum"), all_R=("all_R", "sum"))
    print("\nBy year (unseen months only):\n", y.to_string(float_format=lambda v: f"{v:+.1f}"))
    eq = w.R.cumsum()
    print(f"\nIMPROVER: {w.n.sum()} trades {w.R.sum():+.1f}R (avg {w.R.sum() / max(w.n.sum(), 1):+.3f}R), max DD {(eq.cummax() - eq).max():.1f}R,"
          f" months +{(w.R > 0).sum()} / -{(w.R < 0).sum()} / flat {(w.R == 0).sum()}")
    eq2 = w.all_R.cumsum()
    print(f"NO FILTER same months: {w.all_n.sum()} trades {w.all_R.sum():+.1f}R, max DD {(eq2.cummax() - eq2).max():.1f}R")
    rng = np.random.default_rng(0)
    plac = np.array([walk(t, cands, a.train, a.test, rng).R.sum() for _ in range(200)])
    print(f"PLACEBO random rule each step (200 runs): median {np.median(plac):+.1f}R, 5-95% [{np.percentile(plac, 5):+.1f}, "
          f"{np.percentile(plac, 95):+.1f}]R; improver beats {np.mean(plac < w.R.sum()) * 100:.0f}% of random runs")


if __name__ == "__main__":
    main()
