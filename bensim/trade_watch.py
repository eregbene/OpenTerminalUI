"""In-trade watch -- OBSERVE ONLY, it never closes or changes a trade (2026-10-19 user choice: observe + learn).

Every engine cycle (5 min) each open position (fib618, Bennett, flow) gets the swing trend (strategy.trend) on H4, H1
and M15, its R so far, and a verdict: HOLD (no timeframe against it), WARN (one or two against), EXIT? (H4 and M15
both against). Rows go to live_trade_watch; a verdict change is logged ("WATCH ...").

Learning = scoring, not tuning: `python -m bensim.trade_watch report` takes every CLOSED watched trade and, per rule,
compares "exit at the first time the rule fired" with what the trade really made. A rule earns a switch-on only with
>= 30 trades, a positive average and t >= 2 -- the backtest (13-14 Oct) said trend-flip exits HURT, so it must prove it.
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict

import numpy as np

from . import bridge, db
from .backtest import resample
from .strategy import trend

SCHEMA = """create table if not exists live_trade_watch (ticket bigint, ts bigint, symbol text, magic int, dir int,
    entry double precision, sl double precision, r_now double precision, h4 int, h1 int, m15 int, verdict text,
    primary key (ticket, ts))"""
RULES = {  # fixed before any data -- do not tune on the report
    "H4+M15 against": lambda d, h4, h1, m15: h4 == -d and m15 == -d,
    "all 3 against": lambda d, h4, h1, m15: h4 == h1 == m15 == -d,
    "H4 against": lambda d, h4, h1, m15: h4 == -d,
    "H1 against": lambda d, h4, h1, m15: h1 == -d,
    "M15 against": lambda d, h4, h1, m15: m15 == -d,
}
_last: dict[int, str] = {}  # ticket -> last verdict (log only changes)


def verdict(d: int, h4: int, h1: int, m15: int) -> str:
    if h4 == -d and m15 == -d:
        return "EXIT?"
    return "WARN" if -d in (h4, h1, m15) else "HOLD"


def watch(positions: list[dict], bars, h1_bars, log) -> None:
    """bars / h1_bars: live.bars / live.h1_bars (completed M5 / H1 bars in UTC)."""
    now = int(time.time())
    with db.connect(autocommit=True) as c:
        c.execute(SCHEMA)
        for p in positions:
            d, sym = (1 if p["type"] == 0 else -1), p["symbol"]
            h1 = h1_bars(sym)
            h4t, h1t, m15t = (int(trend(resample(h1, 14400))[-1]), int(trend(h1)[-1]), int(trend(resample(bars(sym), 900))[-1]))
            stop = abs(p["price_open"] - p["sl"]) if p["sl"] else 0.0
            r = (p["price_current"] - p["price_open"]) * d / stop if stop else float("nan")
            v = verdict(d, h4t, h1t, m15t)
            c.execute("insert into live_trade_watch values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing",
                      (p["ticket"], now, sym, p.get("magic"), d, p["price_open"], p["sl"], r, h4t, h1t, m15t, v))
            if _last.get(p["ticket"]) != v:
                name = {1: "up", -1: "down", 0: "-"}
                log("INFO", f"WATCH {sym} #{p['ticket']} {'BUY' if d == 1 else 'SELL'} {_last.get(p['ticket'], 'new')} -> {v} "
                            f"(H4 {name[h4t]}, H1 {name[h1t]}, M15 {name[m15t]}) at {r:+.2f}R -- observe only")
                _last[p["ticket"]] = v


def score(rows: list[tuple], final_r: dict[int, float]) -> dict[str, list[float]]:
    """rows (ticket, ts, dir, r_now, h4, h1, m15) sorted by ts -> per rule: (R at the first firing) - (final R), per trade."""
    out: dict[str, list[float]] = defaultdict(list)
    by: dict[int, list[tuple]] = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    for tk, rs in by.items():
        if tk not in final_r:
            continue  # still open
        for name, rule in RULES.items():
            hit = next((x for x in rs if rule(x[2], x[4], x[5], x[6]) and np.isfinite(x[3])), None)
            if hit:
                out[name].append(hit[3] - final_r[tk])
    return out


def report() -> None:
    with db.connect() as c:
        c.execute(SCHEMA)
        rows = c.execute("select ticket, ts, dir, r_now, h4, h1, m15 from live_trade_watch order by ts").fetchall()
        meta = {r[0]: r[1:] for r in c.execute("select distinct on (ticket) ticket, ts, dir, entry, sl from live_trade_watch order by ticket, ts").fetchall()}
    if not rows:
        print("no watched trades yet")
        return
    open_now = {p["ticket"] for p in bridge.positions()}
    t0 = min(m[0] for m in meta.values()) - 3 * 86400  # deal times are broker server time: pad generously
    exits: dict[int, float] = {}
    for x in sorted(bridge.deals(t0, int(time.time()) + 86400 * 2), key=lambda x: x["time"]):
        if x.get("entry") == 1 and x.get("position_id") in meta:  # entry 1 = closing deal
            exits[x["position_id"]] = x["price"]
    final_r = {tk: (exits[tk] - m[2]) * m[1] / abs(m[2] - m[3]) for tk, m in meta.items()
               if tk in exits and tk not in open_now and m[3]}
    print(f"watched trades: {len(meta)}  closed + scored: {len(final_r)}  final avg {np.mean(list(final_r.values())) if final_r else 0:+.2f}R")
    print("rule = exit at its first firing instead of holding; benefit = R then - final R (+ = the rule would have helped)")
    for name, b in score(rows, final_r).items():
        a = np.array(b)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 2 and a.std() > 0 else 0.0
        ok = "SWITCH-ON CANDIDATE" if len(a) >= 30 and a.mean() > 0 and t >= 2 else ("too few" if len(a) < 30 else "no")
        print(f"  {name:16} fired on {len(a):3} trades  avg benefit {a.mean():+.2f}R  helped {np.mean(a > 0):.0%}  t {t:+.1f}  -> {ok}")


def _selfcheck() -> None:
    assert verdict(1, -1, 1, -1) == "EXIT?" and verdict(1, 1, -1, 1) == "WARN" and verdict(-1, -1, -1, 0) == "HOLD"
    rows = [(1, 0, 1, 0.5, 1, 1, 1), (1, 1, 1, 0.8, -1, 1, -1), (1, 2, 1, -0.2, -1, -1, -1), (2, 0, -1, 0.1, -1, -1, -1)]
    s = score(rows, {1: -1.0, 2: 2.0})
    assert np.allclose([s["H4+M15 against"], s["all 3 against"], s["M15 against"]], [[1.8], [0.8], [1.8]])  # trade 2 never fired
    print("trade_watch selfcheck ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report", "selfcheck"])
    a = ap.parse_args()
    report() if a.cmd == "report" else _selfcheck()
