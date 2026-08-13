"""Yahoo Finance historical data provider.

Used for the initial historical backfill where MT5's own broker history is shallow, unreliable,
or (for older bars) suspected synthetic -- see mt5_provider.py's docstring. Never the live/entry
data path; this provider is backfill-only.

IMPORTANT (per explicit requirement): Yahoo has no spot XAUUSD ticker. "GC=F" is COMEX GOLD
FUTURES -- a different, though correlated, instrument from broker XAUUSD spot execution pricing.
`is_proxy_for("XAUUSD")` returns True so every bar ingested for XAUUSD from this provider is
persisted with `proxy=True` and a `lineage.symbol_mapping` note -- it must never be silently
treated as identical to real XAUUSD execution data. No transformation/basis-adjustment is
applied here; if a future use case needs futures-to-spot conversion, that must be an explicit,
validated step, not implicit in ingestion.

Yahoo's own intraday-history API limits (confirmed empirically this session, 2026-08-12, and
consistent with Yahoo's documented policy): ~60 trading days for 5m/15m, ~2-3 years for 1h, and
long (10-25+ year) history for daily bars. This provider does not fabricate data beyond what
Yahoo actually returns -- a request for a wider range than Yahoo supports for that interval
simply returns whatever Yahoo gives back (which may not cover the full requested window). Yahoo
has no native 4-hour interval; this provider does not attempt to synthesize H4 by resampling H1
-- see historical_intelligence/ingestion.py for the explicit, separately-validated higher-
timeframe construction utility (Part 5) that operates on already-canonical single-provider data.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from backend.historical_intelligence.providers.base import HistoricalBar, HistoricalDataProvider

logger = logging.getLogger(__name__)

_YAHOO_TICKERS = {
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X", "USDCHF": "USDCHF=X", "NZDUSD": "NZDUSD=X", "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    # XAUUSD has no Yahoo spot ticker -- GC=F (COMEX futures) is the nearest available series
    # and is NOT the same instrument. See module docstring / is_proxy_for below.
    "XAUUSD": "GC=F",
}

_PROXY_SYMBOLS = {"XAUUSD"}

_INTERVALS = {"M5": "5m", "M15": "15m", "H1": "1h", "D1": "1d"}
# H4 deliberately absent -- Yahoo has no native 4h interval (see module docstring).


class YahooHistoricalProvider(HistoricalDataProvider):
    name = "YAHOO"

    def provider_symbol(self, canonical_symbol: str) -> str:
        return _YAHOO_TICKERS.get(canonical_symbol.upper(), f"{canonical_symbol.upper()}=X")

    def is_proxy_for(self, canonical_symbol: str) -> bool:
        return canonical_symbol.upper() in _PROXY_SYMBOLS

    async def fetch_bars(
        self,
        *,
        canonical_symbol: str,
        broker_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[HistoricalBar]:
        interval = _INTERVALS.get(timeframe.upper())
        if interval is None:
            logger.debug("Yahoo provider: no native interval for timeframe=%s (symbol=%s) -- returning empty, not synthesizing", timeframe, canonical_symbol)
            return []
        yf_symbol = self.provider_symbol(canonical_symbol)
        rows = await asyncio.to_thread(self._fetch_sync, yf_symbol, interval, start, end)
        return [row for row in rows if start <= row.time < end]

    @staticmethod
    def _fetch_sync(yf_symbol: str, interval: str, start: datetime, end: datetime) -> list[HistoricalBar]:
        import warnings

        import yfinance as yf

        warnings.filterwarnings("ignore")
        try:
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(start=start, end=end, interval=interval, auto_adjust=False)
        except Exception as exc:
            logger.warning("Yahoo historical fetch failed for %s interval=%s: %s", yf_symbol, interval, exc.__class__.__name__)
            return []
        if df is None or df.empty:
            return []
        bars: list[HistoricalBar] = []
        for ts, row in df.iterrows():
            ts_utc = ts.to_pydatetime()
            if ts_utc.tzinfo is None:
                ts_utc = ts_utc.replace(tzinfo=timezone.utc)
            else:
                ts_utc = ts_utc.astimezone(timezone.utc)
            try:
                bars.append(
                    HistoricalBar(
                        time=ts_utc,
                        open=float(row["Open"]), high=float(row["High"]), low=float(row["Low"]), close=float(row["Close"]),
                        tick_volume=int(row.get("Volume") or 0), spread=0, real_volume=int(row.get("Volume") or 0),
                        complete=True,
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return bars
