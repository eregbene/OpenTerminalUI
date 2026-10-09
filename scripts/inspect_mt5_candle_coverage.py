from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD"]
TIMEFRAMES = ["MN1", "W1", "D1", "H4", "H1", "M15", "M5", "M1"]


def main() -> None:
    con = sqlite3.connect("data/openterminalui.db")
    cur = con.cursor()
    print("schema:")
    for row in cur.execute("pragma table_info(mt5_canonical_candles)"):
        print(row)
    print("coverage:")
    start = datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat()
    end = datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat()
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            row = cur.execute(
                """
                select count(*), min(timestamp), max(timestamp)
                from mt5_canonical_candles
                where broker_symbol=? and timeframe=? and timestamp>=? and timestamp<?
                """,
                (symbol, tf, start, end),
            ).fetchone()
            if row and row[0]:
                print(symbol, tf, row)


if __name__ == "__main__":
    main()
