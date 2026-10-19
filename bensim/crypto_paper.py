"""Crypto momentum PAPER book: $100,000 virtual, real Binance spot prices, rebalanced once a UTC day (DEMO stage).

    python -m bensim.crypto_paper --status         # holdings, equity, last rebalance
    python -m bensim.crypto_paper --run            # rebalance now if today's has not been done (the engine does this)

Runs inside the live engine's cycle (bensim.live, try/except: it can never stop FX trading): the first cycle after
00:05 UTC takes the 14 frozen coins' completed daily candles from the public REST API, computes the target weights
with crypto_momentum.weights() -- the exact backtested rule (28-day momentum, long/flat, 2% / 20-day vol, / N) --
and trades the paper portfolio at that close, paying 0.1% of every traded value (Binance spot fee).
Files on D: data/crypto_paper/state.json (cash, units, last day), ledger.csv (one row per day), trades.csv.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .crypto_momentum import LOOKBACK, VOL_N, weights

DIR = Path(__file__).resolve().parent.parent / "data/crypto_paper"
# frozen 2026-10-19 = the backtested universe (spot history <= 2020 among the top 30)
COINS = ["BTC", "ETH", "ZEC", "SOL", "NEAR", "XRP", "DOGE", "BNB", "UNI", "SAND", "FIL", "INJ", "ADA", "AVA"]
FEE, START_CASH = 0.001, 100_000.0
KLINES = "https://api.binance.com/api/v3/klines?symbol={s}USDT&interval=1d&limit={n}"


def closes(n: int = LOOKBACK + VOL_N + 20) -> pd.DataFrame:
    """Completed daily closes (UTC day of the candle's open) per coin."""
    out = {}
    for c in COINS:
        with urllib.request.urlopen(KLINES.format(s=c, n=n), timeout=30) as r:
            rows = [k for k in json.load(r) if k[6] < time.time() * 1000]  # completed candles only
        out[c] = pd.Series([float(k[4]) for k in rows], index=pd.to_datetime([k[0] for k in rows], unit="ms"))
    return pd.DataFrame(out)


def _state() -> dict:
    f = DIR / "state.json"
    return json.loads(f.read_text()) if f.exists() else {"cash": START_CASH, "units": {}, "last_day": None}


def step(log=print, force: bool = False) -> None:
    now = datetime.now(timezone.utc)
    if not force and now.hour == 0 and now.minute < 5:
        return  # give Binance a few minutes to publish the closed daily candle
    st = _state()
    if not force and st["last_day"] == str((pd.Timestamp(now).tz_localize(None) - pd.Timedelta(days=1)).date()):
        return  # yesterday's candle already traded: no API calls until the next UTC day
    px = closes()
    day = str(px.index[-1].date())  # the candle that just closed
    if st["last_day"] == day:
        return
    last = px.iloc[-1]
    w = weights(px).iloc[-1]
    equity = st["cash"] + sum(u * last[c] for c, u in st["units"].items())
    trades = []
    for c in COINS:
        have = st["units"].get(c, 0.0) * last[c]
        delta = w[c] * equity - have
        if abs(delta) < 1.0:
            continue
        st["units"][c] = st["units"].get(c, 0.0) + delta / last[c]
        st["cash"] -= delta + abs(delta) * FEE
        trades.append({"day": day, "coin": c, "side": "BUY" if delta > 0 else "SELL", "usd": round(abs(delta), 2),
                       "price": last[c], "fee": round(abs(delta) * FEE, 2)})
    st["units"] = {c: u for c, u in st["units"].items() if abs(u * last[c]) >= 1.0}
    st["last_day"] = day
    after = st["cash"] + sum(u * last[c] for c, u in st["units"].items())
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "state.json").write_text(json.dumps(st, indent=1))
    row = {"day": day, "equity": round(after, 2), "cash": round(st["cash"], 2), "invested_pct": round(w.sum() * 100, 1),
           **{f"w_{c}": round(w[c], 4) for c in COINS}}
    pd.DataFrame([row]).to_csv(DIR / "ledger.csv", mode="a", header=not (DIR / "ledger.csv").exists(), index=False)
    if trades:
        pd.DataFrame(trades).to_csv(DIR / "trades.csv", mode="a", header=not (DIR / "trades.csv").exists(), index=False)
    held = ", ".join(f"{c} {w[c] * 100:.1f}%" for c in COINS if w[c] > 0) or "nothing (all flat)"
    log("INFO", f"CRYPTO PAPER {day}: equity ${after:,.0f}, {len(trades)} trades, holding {held}")


def status() -> None:
    st = _state()
    if not st["last_day"]:
        return print("crypto paper book not started yet")
    led = pd.read_csv(DIR / "ledger.csv")
    print(f"last rebalance {st['last_day']}  equity ${led.equity.iloc[-1]:,.0f}  ({(led.equity.iloc[-1] / START_CASH - 1) * 100:+.2f}% "
          f"since {led.day.iloc[0]})  cash ${st['cash']:,.0f}  invested {led.invested_pct.iloc[-1]}%")
    for c, u in sorted(st["units"].items()):
        print(f"  {c:5} {u:,.6g} units")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.run:
        step(lambda lv, m: print(m), force=True)
    status()


if __name__ == "__main__":
    main()
