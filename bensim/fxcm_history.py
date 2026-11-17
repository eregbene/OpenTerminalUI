"""FXCM free candle history -> H1 bid bars + real spread, for pairs our broker DB does not hold (research only).

    python -m bensim.fxcm_history                      # 28 pairs of EUR GBP AUD NZD USD CAD CHF JPY, 2021-10 .. now
    python -m bensim.fxcm_history --symbols EURJPY,GBPJPY
    python -m bensim.fxcm_history --tf m1 --start 2012-01-01   # 1-minute files, stored as M5 -> {SYM}_M5.npz

Source: candledata.fxcorporate.com/H1/{SYM}/{YEAR}/{WEEK}.csv.gz -- one gzip CSV per ISO-ish week with
DateTime (MM/DD/YYYY HH:MM, UTC), BidOpen/High/Low/Close, AskOpen/High/Low/Close. Checked 2026-10-14 against our
ICMarkets EURUSD H1: same bars within 1-2 points. Some weeks are missing (404) -- counted and reported, not filled.
(Dukascopy was tried first: throttled to a few files per 15 minutes, unusable.)
Output: data/bars/fxcm/{SYM}_H1.npz with t (UTC open), o, h, l, c (bid) and spr (ask - bid at the close).
"""
from __future__ import annotations

import argparse
import gzip
import io
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

CCY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]  # market convention: earlier = base
PAIRS = [a + b for i, a in enumerate(CCY) for b in CCY[i + 1:]]
OUT = Path("data/bars/fxcm")
URL = "https://candledata.fxcorporate.com/{tf}/{s}/{y}/{w}.csv.gz"


def _to_m5(df: pd.DataFrame) -> pd.DataFrame:
    """1-minute rows -> 5-minute rows (bid OHLC, ask close), same column names."""
    t = pd.to_datetime(df.DateTime, format="%m/%d/%Y %H:%M:%S.%f")
    g = df.assign(k=t.dt.floor("5min")).groupby("k", sort=True)
    out = g.agg(BidOpen=("BidOpen", "first"), BidHigh=("BidHigh", "max"), BidLow=("BidLow", "min"), BidClose=("BidClose", "last"),
                AskClose=("AskClose", "last")).reset_index()
    return out.assign(DateTime=out.k.dt.strftime("%m/%d/%Y %H:%M:%S.000")).drop(columns="k")


def week(sym: str, y: int, w: int, tf: str = "H1") -> pd.DataFrame | None:
    req = urllib.request.Request(URL.format(tf=tf, s=sym, y=y, w=w), headers={"User-Agent": "Mozilla/5.0"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = gzip.decompress(r.read())
            text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "ignore")
            if not text.strip():  # the files are UTF-16; a few are empty
                return None
            df = pd.read_csv(io.StringIO(text))
            return _to_m5(df) if tf == "m1" else df
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except (urllib.error.URLError, TimeoutError, OSError, EOFError):  # EOFError: truncated download
            continue
    return None


def download(sym: str, start: str, tf: str = "H1") -> tuple[int, int]:
    s = pd.Timestamp(start, tz="UTC")
    jobs = [(y, w) for y in range(s.year, pd.Timestamp.now(tz="UTC").year + 1) for w in range(1, 54)]
    with ThreadPoolExecutor(4) as pool:
        frames = list(pool.map(lambda yw: week(sym, *yw, tf), jobs))
    got = [f for f in frames if f is not None and len(f)]
    if not got:  # FXCM does not carry every cross (e.g. EURCAD)
        return 0, len(frames)
    df = pd.concat(got, ignore_index=True)
    t = pd.to_datetime(df.DateTime, format="%m/%d/%Y %H:%M:%S.%f", utc=True)
    df = df.assign(t=t.astype("int64") // 10**9).drop_duplicates("t").sort_values("t")
    df = df[df.t >= s.timestamp()]
    np.savez_compressed(OUT / f"{sym}_{'M5' if tf == 'm1' else 'H1'}.npz", t=df.t.to_numpy(np.int64), o=df.BidOpen.to_numpy(), h=df.BidHigh.to_numpy(),
                        l=df.BidLow.to_numpy(), c=df.BidClose.to_numpy(), spr=(df.AskClose - df.BidClose).clip(lower=0).to_numpy())
    return len(df), sum(f is None for f in frames)


def load(sym: str, tf: str = "H1") -> dict[str, np.ndarray]:
    z = np.load(OUT / f"{sym}_{tf}.npz")
    return {"t": z["t"], "open": z["o"], "high": z["h"], "low": z["l"], "close": z["c"], "spr": z["spr"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(PAIRS))
    ap.add_argument("--start", default="2021-10-01")
    ap.add_argument("--tf", choices=["H1", "m1"], default="H1", help="m1 is stored as M5")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for s in a.symbols.split(","):
        if (OUT / f"{s}_{'M5' if a.tf == 'm1' else 'H1'}.npz").exists() and a.tf == "m1":  # resume: done pairs stay
            continue
        n, missing = download(s, a.start, a.tf)
        print(f"{s}: {n} {'M5' if a.tf == 'm1' else 'H1'} bars ({missing} week files absent, incl. future weeks)", flush=True)


if __name__ == "__main__":
    main()
