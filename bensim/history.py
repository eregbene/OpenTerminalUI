"""Download broker-quality history (ICMarkets via the MT5 bridge) into the `candles` table, in UTC.

    python -m bensim.history --years 5            # all symbols, M5
    python -m bensim.history --audit              # holes / coverage report into data_audit

Resumable and idempotent (upsert on symbol/tf/ts). Only completed bars are stored (position >= 1).
"""
from __future__ import annotations

import argparse
import io
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from . import bridge, db
from .config import SYMBOLS
from .mt5_time import server_to_utc

CHUNK = 50_000


def download(symbol: str, tf: str = "M5", years: float = 5.0) -> int:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=365.25 * years + 7)).timestamp())
    rows, start = [], 1
    while True:
        part = bridge.rates(symbol, tf, start, CHUNK)
        for _ in range(8):  # the terminal may still be pulling older history from the broker: ask again
            if part and part[0]["time"] - 3 * 3600 < cutoff or len(part) == CHUNK:
                break
            time.sleep(15)
            part = bridge.rates(symbol, tf, start, CHUNK)
        if not part:
            break
        rows.extend(part)
        start += len(part)
        if part[0]["time"] - 3 * 3600 < cutoff or len(part) < CHUNK:  # part is oldest-first
            break
    # positions count back from "now": a bar forming mid-download shifts them, so chunks can overlap
    rows = list({r["time"]: r for r in rows}.values())
    rows.sort(key=lambda r: r["time"])
    if not rows:
        return 0
    t_srv = np.array([r["time"] for r in rows], dtype=np.int64)
    t = server_to_utc(t_srv)
    keep = t >= cutoff
    buf = io.StringIO()
    for r, ts, k in zip(rows, t, keep):
        if k and r["high"] >= max(r["open"], r["close"]) and r["low"] <= min(r["open"], r["close"]) and r["low"] > 0:
            buf.write(f"{symbol}\t{tf}\t{datetime.fromtimestamp(int(ts), timezone.utc).isoformat()}\t{r['open']}\t{r['high']}\t{r['low']}\t{r['close']}\t{int(r.get('tick_volume', 0))}\tICMARKETS_MT5\n")
    buf.seek(0)
    with db.connect() as c, c.cursor() as cur:
        cur.execute("create temp table _c (like candles) on commit drop")
        with cur.copy("copy _c from stdin") as cp:
            cp.write(buf.read())
        cur.execute("""insert into candles select * from _c on conflict (symbol, tf, ts) do update set
                       open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                       tick_volume=excluded.tick_volume, source=excluded.source""")
        n = cur.rowcount
        c.commit()
    return n


TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def holes(t: np.ndarray, step: int, max_gap: int = 2 * 3600) -> list[tuple[int, int]]:
    """Missing-data spans (start, end) in UTC seconds: gaps between consecutive bars longer than `max_gap`
    that are not the normal FX weekend (Friday after 20:00 UTC -> Sunday, <= 54h)."""
    g = np.diff(t)
    dow = ((t[:-1] // 86400) + 3) % 7
    weekend = (dow == 4) & ((t[:-1] % 86400) >= 20 * 3600) & (g <= 54 * 3600)
    idx = np.flatnonzero((g > max_gap) & ~weekend)
    return [(int(t[i] + step), int(t[i + 1])) for i in idx]


def audit(symbol: str, tf: str = "M5") -> dict:
    with db.connect() as c:
        rows = c.execute("select extract(epoch from ts)::bigint from candles where symbol=%s and tf=%s order by ts", (symbol, tf)).fetchall()
    t = np.array([r[0] for r in rows], dtype=np.int64)
    spans = holes(t, TF_SECONDS[tf]) if len(t) > 1 else []
    out = {"bars": len(t), "first": datetime.fromtimestamp(int(t[0]), timezone.utc) if len(t) else None,
           "last": datetime.fromtimestamp(int(t[-1]), timezone.utc) if len(t) else None,
           "holes": len(spans), "missing_days": round(sum(b - a for a, b in spans) / 86400, 1)}
    with db.connect() as c:
        c.execute("""insert into data_audit (symbol, tf, source, first_ts, last_ts, bars, holes_over_2h, missing_days, notes)
                     values (%s,%s,'ICMARKETS_MT5',%s,%s,%s,%s,%s,%s) on conflict (symbol, tf, source) do update set
                     first_ts=excluded.first_ts, last_ts=excluded.last_ts, bars=excluded.bars, holes_over_2h=excluded.holes_over_2h,
                     missing_days=excluded.missing_days, notes=excluded.notes, checked_at=now()""",
                  (symbol, tf, out["first"], out["last"], out["bars"], out["holes"], out["missing_days"],
                   "; ".join(f"{datetime.fromtimestamp(a, timezone.utc):%Y-%m-%d %H:%M}+{(b - a) / 3600:.0f}h" for a, b in spans[:40])))
        c.commit()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--tf", default="M5")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--audit", action="store_true")
    a = ap.parse_args()
    db.init()
    for sym in a.symbols.split(","):
        t0 = time.time()
        if not a.audit:
            n = download(sym, a.tf, a.years)
            print(f"{sym} {a.tf}: {n:,} bars stored ({time.time() - t0:.0f}s)", flush=True)
        print(f"{sym} {a.tf} audit: {audit(sym, a.tf)}", flush=True)


if __name__ == "__main__":
    main()
