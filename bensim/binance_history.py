"""Binance history (USD-M perpetual futures + spot, USDT pairs) -> compressed files on D: (C: is full: no DB rows).

    python -m bensim.binance_history --top 30                    # top 30 COINS by futures volume, futures + spot, M5
    python -m bensim.binance_history --symbols BTCUSDT.SPOT      # one name from symbols.json
    python -m bensim.binance_history --update                    # refresh everything already downloaded

Files: data/bars/binance/<SYMBOL>_M5.npz (futures) and <SYMBOL>.SPOT_M5.npz (spot), arrays t (UTC s) o h l c n
(n = trade count). Universe + tick sizes: data/bars/binance/symbols.json (read by strategy.CRYPTO_TICK).
Coins only: futures underlyingType COIN (Binance also lists tokenized stocks, gold, oil -- excluded), perpetual, USDT.
History: the public archive data.binance.vision (monthly zips; spot from 2017-08, futures from 2019-12), the
current month + archive gaps from the public REST API (no key). Months before a coin's listing are 404 = skipped.
"""
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

OUT = Path("data/bars/binance")
ARCHIVE = {"um": "https://data.binance.vision/data/futures/um/monthly/klines/{s}/{i}/{s}-{i}-{y}-{m:02d}.zip",
           "spot": "https://data.binance.vision/data/spot/monthly/klines/{s}/{i}/{s}-{i}-{y}-{m:02d}.zip"}
REST = {"um": "https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval={i}&startTime={a}&limit=1500",
        "spot": "https://api.binance.com/api/v3/klines?symbol={s}&interval={i}&startTime={a}&limit=1000"}
INFO = {"um": "https://fapi.binance.com/fapi/v1/exchangeInfo", "spot": "https://api.binance.com/api/v3/exchangeInfo"}
INTERVAL, STEP = {"M5": "5m", "M15": "15m", "H1": "1h"}, {"M5": 300, "M15": 900, "H1": 3600}
FIRST = (2017, 8)  # Binance spot archive starts August 2017


def _get(url: str) -> bytes | None:
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None  # month before the listing / unknown symbol
            time.sleep(2 * (attempt + 1))
        except OSError:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"download failed: {url}")


def universe(top: int) -> dict[str, dict]:
    """Top COIN perpetuals by 24h quote volume + their spot pair when Binance spot lists it. {name: {tick, market}}."""
    fut = json.loads(_get(INFO["um"]))["symbols"]
    coins = {s["symbol"]: s for s in fut if s.get("underlyingType") == "COIN" and s["contractType"] == "PERPETUAL"
             and s["quoteAsset"] == "USDT" and s["status"] == "TRADING"}
    vol = {x["symbol"]: float(x["quoteVolume"]) for x in json.loads(_get("https://fapi.binance.com/fapi/v1/ticker/24hr"))}
    pick = sorted(coins, key=lambda s: -vol.get(s, 0))[:top]
    spot = {s["symbol"]: s for s in json.loads(_get(INFO["spot"]))["symbols"] if s["status"] == "TRADING"}
    tick = lambda s: float(next(f["tickSize"] for f in s["filters"] if f["filterType"] == "PRICE_FILTER"))  # noqa: E731
    out = {}
    for s in pick:
        out[s] = {"tick": tick(coins[s]), "market": "um"}
        if s in spot:
            out[s + ".SPOT"] = {"tick": tick(spot[s]), "market": "spot"}
    return out


def _rows(text: str) -> list[list[str]]:
    rows = [r for r in (ln.split(",") for ln in text.splitlines()) if r and r[0].isdigit()]  # newer files have a header
    for r in rows:
        if len(r[0]) > 13:  # the spot archive switched to microsecond timestamps in 2025 -> ms
            r[0] = r[0][:13]
    return rows


def _to_arrays(rows) -> dict[str, np.ndarray]:
    if not rows:
        return {}
    a = np.array([[int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(float(r[8]))] for r in rows])
    return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4], "n": a[:, 5].astype(np.int64)}


def _dedupe(d: dict) -> dict:
    _, keep = np.unique(d["t"], return_index=True)
    return {k: v[keep] for k, v in d.items()}


def _rest(sym: str, market: str, tf: str, a_s: int, b_s: int) -> list:
    rows, a = [], a_s * 1000
    while a < b_s * 1000:
        part = json.loads(_get(REST[market].format(s=sym, i=INTERVAL[tf], a=a)) or b"[]")
        part = [r for r in part if r[6] < time.time() * 1000 and r[0] < b_s * 1000]  # completed bars only
        if not part:
            break
        rows += [[str(x) for x in r] for r in part]
        a = int(part[-1][0]) + STEP[tf] * 1000
    return rows


def download(name: str, market: str, tf: str = "M5") -> int:
    sym = name.split(".")[0]
    rows, now = [], datetime.now(timezone.utc)
    y, m = FIRST if market == "spot" else (2019, 12)
    while (y, m) < (now.year, now.month):
        z = _get(ARCHIVE[market].format(s=sym, i=INTERVAL[tf], y=y, m=m))
        if z:
            with zipfile.ZipFile(io.BytesIO(z)) as f:
                rows += _rows(f.read(f.namelist()[0]).decode())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    month0 = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())
    rows += _rest(sym, market, tf, month0, int(time.time()))
    if not rows:
        return 0
    d = _dedupe(_to_arrays(rows))
    gaps = np.flatnonzero(np.diff(d["t"]) > 2 * 3600)  # the archive misses some days (SOL/XRP Feb+Apr 2022): REST refill
    extra = [r for g in gaps for r in _rest(sym, market, tf, int(d["t"][g]) + STEP[tf], int(d["t"][g + 1]))]
    if extra:
        e = _to_arrays(extra)
        d = _dedupe({k: np.concatenate([d[k], e[k]]) for k in d})
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / f"{name}_{tf}.npz", **d)
    return len(d["t"])


def load(name: str, tf: str = "M5") -> dict[str, np.ndarray]:
    z = np.load(OUT / f"{name}_{tf}.npz")
    return {"t": z["t"], "open": z["o"], "high": z["h"], "low": z["l"], "close": z["c"], "n": z["n"]}


FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate?symbol={s}&startTime={a}&limit=1000"


def download_funding(sym: str) -> int:
    """All funding payments of a USD-M perpetual -> <SYM>_funding.npz (t = UTC s, rate = fraction per payment;
    longs PAY a positive rate, shorts receive it). Every 8h (some coins 4h/1h at times)."""
    rows, a = [], 1_500_000_000_000
    while True:
        part = json.loads(_get(FUNDING.format(s=sym, a=a)) or b"[]")
        if not part:
            break
        rows += part
        a = int(part[-1]["fundingTime"]) + 1
        if len(part) < 1000:
            break
    if not rows:
        return 0
    t = np.array([int(r["fundingTime"]) // 1000 for r in rows], dtype=np.int64)
    rate = np.array([float(r["fundingRate"]) for r in rows])
    np.savez_compressed(OUT / f"{sym}_funding.npz", t=t, rate=rate)
    return len(t)


def load_funding(sym: str) -> tuple[np.ndarray, np.ndarray]:
    z = np.load(OUT / f"{sym}_funding.npz")
    return z["t"], z["rate"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--symbols", default="", help="names from symbols.json (BTCUSDT or BTCUSDT.SPOT); default = --top universe")
    ap.add_argument("--update", action="store_true", help="re-download every name in symbols.json")
    ap.add_argument("--tf", default="M5")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--funding", action="store_true", help="download funding history for every futures name, then stop")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    meta_f = OUT / "symbols.json"
    meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
    if not a.update and not a.symbols:
        meta |= universe(a.top)
        meta_f.write_text(json.dumps(meta, indent=1))
    names = a.symbols.split(",") if a.symbols else list(meta)
    if a.funding:
        for nm in [n for n in names if meta.get(n, {}).get("market") == "um"]:
            print(f"{nm:16} funding payments: {download_funding(nm):,}", flush=True)
        return

    def job(nm: str) -> str:
        t0 = time.time()
        n = download(nm, meta[nm]["market"], a.tf)
        if not n:
            return f"{nm:16} no data"
        z = load(nm, a.tf)
        span = f"{datetime.fromtimestamp(int(z['t'][0]), timezone.utc):%Y-%m-%d}..{datetime.fromtimestamp(int(z['t'][-1]), timezone.utc):%Y-%m-%d}"
        return f"{nm:16} {n:>9,} bars {span}  gaps>2h {int(np.sum(np.diff(z['t']) > 7200))}  ({time.time() - t0:.0f}s)"

    with ThreadPoolExecutor(a.workers) as pool:
        for line in pool.map(job, names):
            print(line, flush=True)


if __name__ == "__main__":
    main()
