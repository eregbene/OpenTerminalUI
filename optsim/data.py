"""Options engine data (all on D:, data/options/). Separate from the FX (bensim) and crypto engines.

    python -m optsim.data            # stocks M5 (MT5) + daily IV (DoltHub) + weekly ATM near-expiry chain sample (DoltHub)

stocks/<T>.csv.gz   t (UTC s), open, high, low, close   -- MT5 CFD feed, regular + extended session as the broker has it
iv/<T>.csv.gz       date, iv (30-day ATM implied vol), hv   -- DoltHub post-no-preference/options volatility_history
chain/<T>.csv.gz    date, expiration, strike, call_put, bid, ask, vol, delta -- one Wednesday a week, strikes +-4%,
                     expirations <= 21 days (DoltHub keeps only >= ~2-week expiries; no weeklies / 0DTE): real quoted spreads + short-dated IV to calibrate the option model.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1] / "data" / "options"
TICKERS = {"TSLA": "TSLA.NAS", "NVDA": "NVDA.NAS", "AAPL": "AAPL.NAS", "AMZN": "AMZN.NAS", "MSFT": "MSFT.NAS",
           "AMD": "AMD.NAS", "NFLX": "NFLX.NAS", "QQQ": "QQQ.NAS", "SPY": "SPY.NYSE"}  # META/GOOGL: not on the broker
DOLT = "https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master"


def dolt(sql: str, tries: int = 8) -> list[dict]:
    for i in range(tries):
        try:
            with urllib.request.urlopen(DOLT + "?" + urllib.parse.urlencode({"q": sql}), timeout=120) as r:
                d = json.load(r)
            if d["query_execution_status"] == "Success":
                return d["rows"]
            err = d["query_execution_message"]
        except Exception as exc:  # network hiccup / gateway timeout
            err = repr(exc)
        time.sleep(10 * (i + 1))
    raise RuntimeError(f"dolt failed: {err} -- {sql}")


def stocks() -> None:
    from bensim import bridge
    from bensim.mt5_time import server_to_utc
    (ROOT / "stocks").mkdir(parents=True, exist_ok=True)
    for t, s in TICKERS.items():
        rows = sorted(bridge.rates(s, "M5", 1, 99000), key=lambda r: r["time"])
        df = pd.DataFrame(rows)
        df["t"] = server_to_utc(df["time"].to_numpy(np.int64))
        df[["t", "open", "high", "low", "close"]].to_csv(ROOT / "stocks" / f"{t}.csv.gz", index=False)
        print(f"stocks {t}: {len(df)} bars from {pd.to_datetime(df.t.min(), unit='s'):%Y-%m-%d}", flush=True)


def stocks_m1() -> None:
    """M1 for the 1-minute entry candles (his entries are on the 1-min chart): page back 99,000 bars at a time."""
    from bensim import bridge
    from bensim.mt5_time import server_to_utc
    (ROOT / "m1").mkdir(parents=True, exist_ok=True)
    for t, s in TICKERS.items():
        out = ROOT / "m1" / f"{t}.csv.gz"
        if out.exists():
            return
        rows, start = [], 1
        while True:
            part = bridge.rates(s, "M1", start, 99000)
            rows += part
            if len(part) < 99000 or min(r["time"] for r in part) < 1627776000:  # 2021-08-01
                break
            start += 99000
            time.sleep(2)  # be gentle: the live engine shares this bridge
        df = pd.DataFrame(rows).drop_duplicates("time").sort_values("time")
        df["t"] = server_to_utc(df["time"].to_numpy(np.int64))
        df[["t", "open", "high", "low", "close", "tick_volume"]].to_csv(out, index=False)
        print(f"m1 {t}: {len(df)} bars from {pd.to_datetime(df.t.min(), unit='s'):%Y-%m-%d}", flush=True)


def iv() -> None:
    (ROOT / "iv").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(4) as pool:  # DoltHub answers ~1 month/query: tickers in parallel
        list(pool.map(_iv_one, TICKERS))


def _iv_one(t: str) -> None:
    if True:
        if (ROOT / "iv" / f"{t}.csv.gz").exists():
            return
        rows = []
        for m in pd.date_range("2021-07-01", pd.Timestamp.now(), freq="MS"):  # monthly: the API times out on bigger ranges
            rows += dolt(f"SELECT date, iv_current AS iv, hv_current AS hv FROM volatility_history "
                         f"WHERE act_symbol='{t}' AND date BETWEEN '{m:%Y-%m-%d}' AND '{m + pd.offsets.MonthEnd(0):%Y-%m-%d}'")
        if not rows:
            print(f"iv {t}: no DoltHub data (ETF) -- realized vol fallback", flush=True)
            return
        df = pd.DataFrame(rows).astype({"iv": float, "hv": float})
        df.to_csv(ROOT / "iv" / f"{t}.csv.gz", index=False)
        print(f"iv {t}: {len(df)} days", flush=True)


def chain() -> None:
    (ROOT / "chain").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(_chain_one, TICKERS))


def _chain_one(t: str) -> None:
    if True:
        out = ROOT / "chain" / f"{t}.csv.gz"
        if out.exists():
            return
        st = pd.read_csv(ROOT / "stocks" / f"{t}.csv.gz")
        day = st.assign(d=pd.to_datetime(st.t, unit="s").dt.date).groupby("d").close.last()
        weds = [d for d in day.index if d.weekday() == 2 and d.day <= 7]  # first Wednesday of each month (~35 s/query)
        rows = []
        for d in weds:
            px = day[d]
            rows += dolt(f"SELECT date, expiration, strike, call_put, bid, ask, vol, delta FROM option_chain "
                         f"WHERE act_symbol='{t}' AND date='{d}' AND expiration <= '{d + pd.Timedelta(days=21)}' "
                         f"AND strike BETWEEN {px * 0.96:.2f} AND {px * 1.04:.2f}")
        df = pd.DataFrame(rows)
        if len(df):
            df = df.astype({"strike": float, "bid": float, "ask": float, "vol": float, "delta": float})
            df.to_csv(out, index=False)
        print(f"chain {t}: {len(weds)} Wednesdays, {len(df)} quotes", flush=True)


if __name__ == "__main__" and sys.argv[1:] == ["m1"]:
    stocks_m1()
elif __name__ == "__main__":
    if not (ROOT / "stocks" / "SPY.csv.gz").exists():
        stocks()
    iv()
    chain()
