"""Insert-only candle backfill from the MT5 bridge: adds bars AFTER what the DB has, never rewrites a stored bar.

    python -m bensim.backfill                       # config.SYMBOLS, M5
    python -m bensim.backfill --symbols EURUSD,XAUUSD

Rules (audit P0, 2026-11-03): real broker bars only; `on conflict do nothing` keeps every existing row as it was;
only completed bars (bridge position >= 1); bad OHLC rows are dropped and counted; weekday holes > 15 min after
the backfill are reported (never filled). Writes data/audit/backfill_<date>.json.
"""
from __future__ import annotations

import argparse
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import bridge, db
from .config import SYMBOLS
from .mt5_time import server_to_utc

OUT = Path("data/audit")


def last_ts(c, sym: str, tf: str) -> int | None:
    r = c.execute("select extract(epoch from max(ts))::bigint from candles where symbol=%s and tf=%s", (sym, tf)).fetchone()
    return r[0]


def weekday_holes(t: np.ndarray, step: int = 300, min_gap: int = 900) -> list[tuple[str, str, int]]:
    """Gaps > min_gap between consecutive bars that start Mon-Thu, or Fri before 20:00 UTC (weekend close is normal)."""
    out = []
    for a, b in zip(t[:-1], t[1:]):
        if b - a > min_gap:
            d = datetime.fromtimestamp(int(a), timezone.utc)
            if d.weekday() < 4 or (d.weekday() == 4 and d.hour < 20):
                out.append((d.isoformat(), datetime.fromtimestamp(int(b), timezone.utc).isoformat(), int((b - a) // 60)))
    return out


def backfill(sym: str, tf: str = "M5") -> dict:
    with db.connect() as c:
        have = last_ts(c, sym, tf)
    if have is None:
        return {"symbol": sym, "error": "no stored bars: use bensim.history for a first download"}
    need = int((datetime.now(timezone.utc).timestamp() - have) // 300) + 600  # bars since the last one, + overlap
    rows = bridge.rates(sym, tf, 1, need)
    t = server_to_utc(np.array([r["time"] for r in rows], dtype=np.int64)) if rows else np.zeros(0, np.int64)
    buf, new, bad = io.StringIO(), 0, 0
    for r, ts in zip(rows, t):
        if ts <= have:
            continue
        if not (r["high"] >= max(r["open"], r["close"]) and r["low"] <= min(r["open"], r["close"]) and r["low"] > 0):
            bad += 1
            continue
        new += 1
        buf.write(f"{sym}\t{tf}\t{datetime.fromtimestamp(int(ts), timezone.utc).isoformat()}\t{r['open']}\t{r['high']}\t{r['low']}"
                  f"\t{r['close']}\t{int(r.get('tick_volume', 0))}\tICMARKETS_MT5\n")
    inserted = 0
    if new:
        with db.connect() as c, c.cursor() as cur:
            cur.execute("create temp table _c (like candles) on commit drop")
            with cur.copy("copy _c from stdin") as cp:
                cp.write(buf.getvalue())
            cur.execute("insert into candles select * from _c on conflict (symbol, tf, ts) do nothing")
            inserted = cur.rowcount
            c.commit()
    with db.connect() as c:
        tt = np.array([r[0] for r in c.execute(
            "select extract(epoch from ts)::bigint from candles where symbol=%s and tf=%s and ts > to_timestamp(%s) order by ts",
            (sym, tf, have - 86400)).fetchall()], dtype=np.int64)
    return {"symbol": sym, "had_until": datetime.fromtimestamp(have, timezone.utc).isoformat(),
            "now_until": datetime.fromtimestamp(int(tt[-1]), timezone.utc).isoformat() if len(tt) else None,
            "fetched": len(rows), "new_rows": new, "inserted": inserted, "bad_ohlc_dropped": bad,
            "weekday_holes_over_15min": weekday_holes(tt)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    a = ap.parse_args()
    res = [backfill(s) for s in a.symbols.split(",") if s]
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"backfill_{datetime.now(timezone.utc):%Y-%m-%d}.json"
    f.write_text(json.dumps({"run_at": datetime.now(timezone.utc).isoformat(), "results": res}, indent=1))
    for r in res:
        print(r["symbol"], r.get("had_until"), "->", r.get("now_until"), "inserted", r.get("inserted"),
              "holes", len(r.get("weekday_holes_over_15min", [])), r.get("error", ""))
    print("wrote", f)


if __name__ == "__main__":
    main()
