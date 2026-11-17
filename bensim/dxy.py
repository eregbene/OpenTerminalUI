"""Synthetic US Dollar Index (DXY) from the broker's own bars -- ICMarkets MT5 has no DXY symbol, but all six basket
pairs (USDSEK downloaded via bensim.history). ICE formula (ICE FX Indexes methodology):

    DXY = 50.14348112 * EURUSD^-0.576 * USDJPY^0.136 * GBPUSD^-0.119 * USDCAD^0.091 * USDSEK^0.042 * USDCHF^0.036

    python -m bensim.dxy --store        # M5 DXY into candles (symbol DXY, source 'synthetic-ice')

OHLC per bar: open/close from the components' open/close; high/low from the components' extremes in the direction
each one moves DXY (an upper/lower bound -- the components' highs don't all happen at the same instant).
"""
from __future__ import annotations

import argparse
import io

import numpy as np

from . import db
from .backtest import load_m5

W = {"EURUSD": -0.576, "USDJPY": 0.136, "GBPUSD": -0.119, "USDCAD": 0.091, "USDSEK": 0.042, "USDCHF": 0.036}
K = 50.14348112


# without USDSEK (4.2%; the broker's history starts Jan 2025): the other weights scaled to 100% -- tracks the DXY closely
W_EX_SEK = {s: w / (1 - W["USDSEK"]) for s, w in W.items() if s != "USDSEK"}


def synth(start: str | None = None, end: str | None = None, weights: dict | None = None) -> dict[str, np.ndarray]:
    """M5 DXY bars on the timestamps where all its pairs have a bar (weights: W = the ICE formula, or W_EX_SEK)."""
    weights = weights or W
    bars = {s: load_m5(s, start, end) for s in weights}
    t = bars["EURUSD"]["t"]
    for b in bars.values():
        t = np.intersect1d(t, b["t"])
    out = {k: np.full(len(t), np.log(K)) for k in ("open", "high", "low", "close")}
    for s, w in weights.items():
        b = bars[s]
        idx = np.searchsorted(b["t"], t)
        up, dn = (b["high"], b["low"]) if w > 0 else (b["low"], b["high"])  # what pushes DXY up / down
        out["open"] += w * np.log(b["open"][idx])
        out["close"] += w * np.log(b["close"][idx])
        out["high"] += w * np.log(up[idx])
        out["low"] += w * np.log(dn[idx])
    return {"t": t, **{k: np.exp(v) for k, v in out.items()}}


def store(start: str | None = None) -> int:
    d = synth(start)
    buf = io.StringIO()
    for i in range(len(d["t"])):
        buf.write(f"DXY\tM5\t{int(d['t'][i])}\t{d['open'][i]:.5f}\t{d['high'][i]:.5f}\t{d['low'][i]:.5f}\t{d['close'][i]:.5f}\n")
    with db.connect() as c:
        c.execute("create temp table dx (symbol text, tf text, ts bigint, open float8, high float8, low float8, close float8)")
        with c.cursor().copy("copy dx from stdin") as cp:
            cp.write(buf.getvalue())
        c.execute("""insert into candles (symbol, tf, ts, open, high, low, close, source)
                     select symbol, tf, to_timestamp(ts), open, high, low, close, 'synthetic-ice' from dx
                     on conflict (symbol, tf, ts) do update set open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, source=excluded.source""")
        c.commit()
    return len(d["t"])


def _selfcheck() -> None:
    """At the 1973 base (all pairs = their weights' neutral point) the formula must give the constant back."""
    v = K * np.prod([1.0 ** w for w in W.values()])
    assert abs(v - K) < 1e-12
    assert abs(sum(abs(w) for w in W.values()) - 1.0) < 1e-12  # weights sum to 100%
    print("dxy selfcheck ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", action="store_true")
    ap.add_argument("--start", default=None)
    a = ap.parse_args()
    if a.store:
        print("stored", store(a.start), "M5 DXY bars")
    else:
        _selfcheck()
