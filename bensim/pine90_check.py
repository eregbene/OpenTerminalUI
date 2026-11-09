"""'90% win rate scalping strategy' (user paste 2026-10-31, Pine v5) -- 3-month test on all FX + gold and all Binance coins.

    python -m bensim.pine90_check

Rules exactly as the Pine script (frozen before any run):
  * EMA9 crosses EMA21 (up = long, down = short) AND RSI(14) >/< 50 AND MACD(12,26) line >/< signal(9), at a bar close
  * entry at the NEXT bar's open (Pine default); an opposite signal reverses the position (strategy.entry behaviour)
  * TP +2% / SL -1% of the entry price; a bar touching both = SL (conservative). R = 1% move; break-even win rate 33.3%
  * timeframes 5m / 15m / 1H (its "best timeframes")
Costs: FX/gold = backtest spread model + $7/lot commission (0.007% of notional); crypto = 0.05% per side (futures taker
fee -- shorts need futures), no spread. Windows: FX 2026-07-26..2026-10-26, crypto 2026-07-18..2026-10-18 (each DB end).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .backtest import Exec, _spread, load_m5, resample
from .binance_history import OUT, load
from .config import SYMBOLS

TFS = {"5m": 300, "15m": 900, "1H": 3600}
FX_WIN, CR_WIN = ("2026-07-26", "2026-10-26"), ("2026-07-18", "2026-10-18")


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.maximum(d, 0)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.maximum(-d, 0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).to_numpy()


def trades(b: dict, t0: int, cost_pct) -> list[float]:
    """Net R per trade (R = 1% of entry). cost_pct(i) = round-trip cost in % of price for a trade entered at bar i."""
    c = b["close"]
    f, s = ema(c, 9), ema(c, 21)
    r = rsi(c)
    macd = ema(c, 12) - ema(c, 26)
    sig = ema(macd, 9)
    up = (f > s) & (np.r_[False, f[:-1] <= s[:-1]]) & (r > 50) & (macd > sig)
    dn = (f < s) & (np.r_[False, f[:-1] >= s[:-1]]) & (r < 50) & (macd < sig)
    out, pos = [], None  # pos = (dir, entry, entry_bar)
    for i in range(30, len(c) - 1):
        if pos:
            d, e, k = pos
            tp, sl = e * (1 + .02 * d), e * (1 - .01 * d)
            hit_sl = b["low"][i] <= sl if d == 1 else b["high"][i] >= sl
            hit_tp = b["high"][i] >= tp if d == 1 else b["low"][i] <= tp
            if hit_sl or hit_tp:
                out.append((-1.0 if hit_sl else 2.0) - cost_pct(k))
                pos = None
        want = 1 if up[i] else -1 if dn[i] else 0
        if want and b["t"][i] >= t0 and (not pos or pos[0] != want):
            if pos:  # reverse at the next open
                d, e, k = pos
                out.append((b["open"][i + 1] - e) / e * 100 * d - cost_pct(k))
            pos = (want, b["open"][i + 1], i + 1)
    return out


def summary(name: str, r: list[float]) -> dict:
    r = np.array(r)
    if not len(r):
        return {"name": name, "n": 0}
    pf = r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)
    return {"name": name, "n": len(r), "win": (r > 0).mean() * 100, "avgR": r.mean(), "totR": r.sum(), "pf": pf}


def main() -> None:
    rows = []
    ex = Exec()
    for sym in SYMBOLS:
        m5 = load_m5(sym, "2026-06-01", FX_WIN[1])
        t0 = int(pd.Timestamp(FX_WIN[0], tz="UTC").timestamp())
        for tf, sec in TFS.items():
            b = resample(m5, sec)
            cost = lambda k, b=b: (_spread(sym, int(b["t"][k]), ex) / b["open"][k] + 0.00007) * 100
            rows.append({"class": "FX/gold", "tf": tf, **summary(sym, trades(b, t0, cost))})
    meta = json.loads((OUT / "symbols.json").read_text())
    lo, t0 = pd.Timestamp("2026-06-01").timestamp(), int(pd.Timestamp(CR_WIN[0], tz="UTC").timestamp())
    for name in [n for n in meta if n.endswith(".SPOT")]:
        m = load(name)
        k = (m["t"] >= lo) & (m["t"] < pd.Timestamp(CR_WIN[1], tz="UTC").timestamp())
        m5 = {x: np.asarray(m[x])[k] for x in ("t", "open", "high", "low", "close")}
        for tf, sec in TFS.items():
            rows.append({"class": "crypto", "tf": tf, **summary(name.replace("USDT.SPOT", ""), trades(resample(m5, sec), t0, lambda k: 0.10))})
    df = pd.DataFrame(rows)
    df.to_csv("logs/pine90_check.csv", index=False)
    pd.set_option("display.width", 200)
    print("CLAIM: 90% win rate, PF 2.35.  Break-even win rate at 1:2 = 33.3%.  R = 1% price move.\n")
    for (cl, tf), g in df.groupby(["class", "tf"], sort=False):
        g = g[g.n > 0]
        n = g.n.sum()
        win = (g.win * g.n).sum() / n
        print(f"{cl:8} {tf:4} symbols {len(g):2}  trades {n:5}  win {win:5.1f}%  avg {g.totR.sum() / n:+.3f}R  "
              f"total {g.totR.sum():+7.1f}R  symbols in profit {(g.totR > 0).sum()}/{len(g)}  best win% {g.win.max():.0f}")
    best = df[df.n >= 10].sort_values("win", ascending=False).head(5)
    print("\nhighest win rates (n>=10):\n" + best[["class", "tf", "name", "n", "win", "avgR", "totR"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
