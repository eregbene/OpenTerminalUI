"""Manager: Claude's discretionary hand on the DEMO, user-authorised 2026-10-21 ("close trades, take profits, place orders
if there is need"). Every action goes through here so it is capped, logged with its reason, and SCORED later.

    python -m bensim.manager review                          # chart read: H4/H1/M15 swing trend + each open trade's R
    python -m bensim.manager open EURUSD SELL 1.1250 1.1180 "reason"   # SL + TP required
    python -m bensim.manager close 2020560789 "reason"       # any bensim position (take profit / cut)
    python -m bensim.manager score                           # did the manager add or lose money vs doing nothing?

Caps: risk 0.25% of the initial balance per manager trade, max 2 manager trades open, the engine guard (daily loss,
one trade per pair, currency side), kill switch file respected. Manager trades carry MAGIC 770109.
Score: manager trades = their real P&L; manager closes = close price vs the trade's own SL/TP run on M5 after the close.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

from . import bridge, db, guard, live
from .backtest import resample
from .strategy import trend
from .trade_watch import verdict

MAGIC = 770109
RISK_PCT = 0.25
MAX_OPEN = 2
SCHEMA = """create table if not exists manager_actions (ts bigint, action text, ticket bigint, symbol text, dir int,
    price double precision, sl double precision, tp double precision, lots double precision, magic int, reason text,
    result jsonb)"""


def _log(action, ticket, sym, d, price, sl, tp, lots, magic, reason, result) -> None:
    with db.connect(autocommit=True) as c:
        c.execute(SCHEMA)
        c.execute("insert into manager_actions values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (int(time.time()), action, ticket, sym, d, price, sl, tp, lots, magic, reason, json.dumps(result, default=str)))


def _ours() -> list[dict]:
    return [p for p in bridge.positions() if 770100 < (p.get("magic") or 0) < 770200]


def tf_trends(sym: str) -> tuple[int, int, int]:
    h1 = live.h1_bars(sym)
    return int(trend(resample(h1, 14400))[-1]), int(trend(h1)[-1]), int(trend(resample(live.bars(sym), 900))[-1])


def review() -> None:
    name = {1: "up", -1: "dn", 0: "--"}
    pos = _ours()
    for sym in sorted({*live.LIVE_SYMBOLS, *live.BN_SYMBOLS, *(p["symbol"] for p in pos)}):
        h4, h1, m15 = tf_trends(sym)
        q = bridge.tick(sym)
        print(f"{sym:7} bid {q['bid']:<10} H4 {name[h4]}  H1 {name[h1]}  M15 {name[m15]}")
        for p in (p for p in pos if p["symbol"] == sym):
            d = 1 if p["type"] == 0 else -1
            stop = abs(p["price_open"] - p["sl"]) if p["sl"] else 0
            r = (p["price_current"] - p["price_open"]) * d / stop if stop else float("nan")
            print(f"   #{p['ticket']} magic {p['magic']} {'BUY' if d == 1 else 'SELL'} {p['volume']} @ {p['price_open']} "
                  f"sl {p['sl']} tp {p['tp']}  {r:+.2f}R  ${p['profit']:+.0f}  {verdict(d, h4, h1, m15)}  '{p.get('comment', '')}'")


def open_trade(sym: str, side: str, sl: float, tp: float, reason: str) -> dict:
    d = 1 if side == "BUY" else -1
    q = bridge.tick(sym)
    price = q["ask"] if d == 1 else q["bid"]
    assert (price - sl) * d > 0 and (tp - price) * d > 0, f"SL/TP on the wrong side of {price}"
    assert not live.STOP_FILE.exists(), "kill switch on"
    pos = _ours()
    assert sum(p["magic"] == MAGIC for p in pos) < MAX_OPEN, f"already {MAX_OPEN} manager trades open"
    initial, day_start = float(live.state("initial")), float(live.state("day_start")["balance"])
    lots = live.lots_for(q, price, sl, initial * RISK_PCT / 100)
    assert lots >= q["volume_min"], "lots below minimum"
    risk_usd = live.loss_per_lot(q, price, sl) * lots
    opens = [guard.Open(p["symbol"], 1 if p["type"] == 0 else -1, live.open_risk(p, bridge.tick(p["symbol"]))) for p in pos]
    ok, why = guard.check(initial, day_start, bridge.account()["equity"], opens, sym, d, risk_usd)
    assert ok, f"guard: {why}"
    res = bridge.order(sym, side, lots, sl, tp, MAGIC, "manager")
    _log("OPEN", res.get("order"), sym, d, res.get("price") or price, sl, tp, lots, MAGIC, reason, res)
    return res


def close_trade(ticket: int, reason: str) -> dict:
    p = next(p for p in _ours() if p["ticket"] == ticket)
    res = bridge.close(ticket, "manager close")
    _log("CLOSE", ticket, p["symbol"], 1 if p["type"] == 0 else -1, p["price_current"], p["sl"], p["tp"], p["volume"],
         p["magic"], reason, res)
    return res


def _counterfactual(sym: str, d: int, t0: int, sl: float, tp: float, price: float) -> float:
    """Price move (in the trade's direction) the trade would have made from t0 under its own SL/TP; open -> mark now."""
    m = live.bars(sym)
    for h, lo in zip(m["high"][m["t"] >= t0], m["low"][m["t"] >= t0]):
        if sl and ((lo <= sl) if d == 1 else (h >= sl)):
            return (sl - price) * d
        if tp and ((h >= tp) if d == 1 else (lo <= tp)):
            return (tp - price) * d
    return (m["close"][-1] - price) * d


def score() -> None:
    with db.connect() as c:
        c.execute(SCHEMA)
        rows = c.execute("select ts, action, ticket, symbol, dir, price, sl, tp, lots, magic, reason from manager_actions order by ts").fetchall()
    if not rows:
        print("no manager actions yet")
        return
    deals = bridge.deals(rows[0][0] - 86400, int(time.time()) + 86400 * 2)
    pnl = {}
    for x in deals:
        if x.get("entry") == 1:
            pnl[x["position_id"]] = pnl.get(x["position_id"], 0) + x["profit"] + x.get("commission", 0) + x.get("swap", 0)
    tot = 0.0
    for ts, act, tk, sym, d, price, sl, tp, lots, magic, reason in rows:
        if act == "OPEN":
            v = pnl.get(tk)
            note = f"P&L ${v:+.0f}" if v is not None else "still open"
            tot += v or 0
        else:  # closed early: + = the close beat letting the trade run to its own SL/TP
            q = bridge.tick(sym)
            usd = -_counterfactual(sym, d, ts, sl, tp, price) / q["trade_tick_size"] * q["trade_tick_value"] * lots
            note, tot = f"vs holding ${usd:+.0f}", tot + usd
        print(f"{time.strftime('%m-%d %H:%M', time.gmtime(ts))} {act:5} {sym} {'BUY' if d == 1 else 'SELL'} #{tk}: {note}  -- {reason}")
    print(f"manager value added so far: ${tot:+,.0f}")


if __name__ == "__main__":
    cmd, *a = sys.argv[1:] or ["review"]
    if cmd == "review":
        review()
    elif cmd == "open":
        print(open_trade(a[0], a[1].upper(), float(a[2]), float(a[3]), a[4]))
    elif cmd == "close":
        print(close_trade(int(a[0]), a[1]))
    elif cmd == "score":
        score()
    else:
        sys.exit(__doc__)
