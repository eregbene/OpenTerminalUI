"""Clean Dukascopy history (via ForexSB's static archive) saved as files on D:, not in the database.

ForexSB repackages Dukascopy's UTC feed as one file per symbol/timeframe, capped at 200,000 bars:
M5 ~ 2.7 years, M15 ~ 8 years, M30 ~ 16 years. Each bar carries Dukascopy's spread (points).
Reuses backend/historical_intelligence/providers/forexsb_provider.py (the same source as the
existing FOREXSB rows in mt5_canonical_candles), so no new parsing code.

    python -m bensim_research.clean_data            # download/refresh all 7 instruments x M5/M15/M30
    from bensim_research.clean_data import load; b = load("EURUSD", "M5")   # data.Bars (UTC)
    spread_px = load_spread("EURUSD", "M5")                                  # per-bar spread in price units
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

import numpy as np

from backend.historical_intelligence.providers.forexsb_provider import ForexSBHistoricalProvider

from . import data as D

DEST = D.ROOT / "data" / "bars" / "forexsb"
TFS = ("M5", "M15", "M30")


async def _fetch(sym: str, tf: str) -> np.ndarray:
    bars = await ForexSBHistoricalProvider().fetch_bars(
        canonical_symbol=sym, broker_symbol=sym, timeframe=tf,
        start=datetime(2000, 1, 1, tzinfo=timezone.utc), end=datetime(2100, 1, 1, tzinfo=timezone.utc))
    return np.array([(int(b.time.timestamp()), b.open, b.high, b.low, b.close, b.tick_volume, b.spread) for b in bars], dtype=float)


def download(symbols: list[str], tfs: tuple[str, ...] = TFS) -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        for tf in tfs:
            raw = asyncio.run(_fetch(sym, tf))
            if not len(raw):
                print(f"{sym} {tf}: no data")
                continue
            bars, rep = D.clean(raw[:, :6], tf)
            keep = np.isin(raw[:, 0].astype(np.int64), bars.t)
            spread_pts = raw[keep, 6]
            np.savez(DEST / f"{sym}_{tf}.npz", t=bars.t, o=bars.o, h=bars.h, l=bars.l, c=bars.c, v=bars.v, spread_pts=spread_pts)
            hl = D.holes(bars)
            print(f"{sym} {tf}: {len(bars):,} bars {rep['start'][:10]} -> {rep['end'][:10]} | dropped dup/bad/spike/weekend "
                  f"{rep['duplicates']}/{rep['bad_ohlc']}/{rep['spikes']}/{rep['weekend_bars']} | holes>2h {len(hl)} "
                  f"({sum(b - a for a, b in hl) / 86400:.1f} days) | median spread {np.median(spread_pts):.0f} pts", flush=True)


def load(sym: str, tf: str = "M5") -> D.Bars:
    z = np.load(DEST / f"{sym}_{tf}.npz")
    return D.Bars(tf, z["t"], z["o"], z["h"], z["l"], z["c"], z["v"])


def load_spread(sym: str, tf: str = "M5") -> np.ndarray:
    """Per-bar spread in price units. ForexSB stores points = 1/priceScale: 0.00001 on FX, 0.001 on JPY pairs and gold
    (checked: EURUSD median 0.3 pip, USDJPY 0.6 pip, XAUUSD $0.56)."""
    z = np.load(DEST / f"{sym}_{tf}.npz")
    return z["spread_pts"] * (0.001 if sym == "XAUUSD" or sym.endswith("JPY") else 0.00001)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(D.SYMBOLS))
    ap.add_argument("--tfs", default=",".join(TFS))
    a = ap.parse_args()
    download(a.symbols.split(","), tuple(a.tfs.split(",")))


if __name__ == "__main__":
    main()
