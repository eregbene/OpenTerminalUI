"""Live engine (DEMO): the backtest's exact decision path, run after every M5 close, with an FTMO guard.

    python -m bensim.live --dry-run          # decide + log, never send orders
    python -m bensim.live                    # trade the demo account (risk BENSIM_RISK_PCT, default 0.5%)
    python -m bensim.live --once --dry-run   # one cycle, for checks

Each cycle (8 s after every M5 close, UTC):
  1. account + our positions (magic MAGIC) from the bridge; record today's 00:00 Europe/Prague balance
  2. kill switch: file bensim.STOP in the repo root (or an engine halt) -> no new orders; flatten rule closes all
  3. time exit: close positions open >= max_hold_bars M15 bars (as the backtest does)
  4. per symbol: last 3500 completed M5 bars from MT5 -> UTC -> the same windows the backtest gives plan();
     stale data (last bar not the bar that just closed) -> skip that symbol (fail closed)
  5. TRIGGERED plan -> recorded once per (symbol, signal_time, dir) (restart-safe, never doubles) -> quote ->
     order_for() -> lots from the broker's tick value -> guard.check() -> market order with SL/TP
  6. Bennett (second strategy, magic MAGIC_BN, BENSIM_BENNETT=off disables): bennett_strategy.LIVE on its own pairs,
     the backtest's windows (the long 1H context from native MT5 H1 bars), its own risk scale (default half), its
     own signal table live_signals_bennett; the guard and the -4% brake are shared by both strategies
Everything (signals, decisions, orders, errors, heartbeat) goes to tables live_* in the bensim DB.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from . import bennett_strategy as bstrat
from . import bridge, db, guard, indicators
from .backtest import _win, resample
from .config import ROOT, env
from .mt5_time import server_to_utc
from .strategy import Params, order_for, plan

MAGIC = 770101
PARAMS = Params(zone_mode="fib", confirm_any=("killzone", "poi", "sweep"), confirm_min=2)  # fib 61.8% + any 2 of (killzone, POI, sweep), fixed SL/TP
RISK_PCT = float(env("BENSIM_RISK_PCT", "0.5"))
# 2026-10-13: AUDUSD and USDJPY dropped -- negative in 2025, 2026 and over 5 years (and under Bennett)
LIVE_SYMBOLS = [x for x in env("BENSIM_LIVE_SYMBOLS", "EURUSD,GBPUSD,USDCHF,USDCAD,XAUUSD").split(",") if x]
WEAK_SETUP_RISK = float(env("BENSIM_WEAK_SETUP_RISK", "0.5"))  # risk multiplier when fewer than all confirmations agree
LIVE_FROM = int(datetime.fromisoformat(env("BENSIM_LIVE_FROM", "2026-10-12T06:00:00+00:00")).timestamp())  # dry-run before this (UTC)
STOP_FILE = ROOT / "bensim.STOP"
MAGIC_BN = 770102
BN_ON = env("BENSIM_BENNETT", "on").lower() != "off"
BN_SYMBOLS = [x for x in env("BENSIM_BENNETT_SYMBOLS", ",".join(bstrat.LIVE_SYMBOLS)).split(",") if x]
BN_RISK_SCALE = float(env("BENSIM_BENNETT_RISK_SCALE", "0.5"))  # x RISK_PCT: half risk while it proves itself live
PRAGUE = ZoneInfo("Europe/Prague")

SCHEMA = """
create table if not exists live_state (k text primary key, v jsonb not null, updated timestamptz default now());
create table if not exists live_signals (symbol text, signal_time bigint, dir int, created timestamptz default now(),
    entry double precision, stop double precision, target double precision, checks jsonb, decision text, detail jsonb,
    primary key (symbol, signal_time, dir));
create table if not exists live_events (ts timestamptz default now(), level text, msg text);
create table if not exists live_signals_bennett (symbol text, signal_time bigint, dir int, created timestamptz default now(),
    entry double precision, stop double precision, target double precision, checks jsonb, decision text, detail jsonb,
    primary key (symbol, signal_time, dir));
"""


def log(level: str, msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {level} {msg}", flush=True)
    try:
        with db.connect(autocommit=True) as c:
            c.execute("insert into live_events (level, msg) values (%s, %s)", (level, msg[:4000]))
    except Exception:  # the log must never kill the engine
        pass


def state(key: str, value: object = None) -> object:
    with db.connect(autocommit=True) as c:
        if value is not None:
            c.execute("insert into live_state (k, v) values (%s, %s) on conflict (k) do update set v = excluded.v, updated = now()", (key, json.dumps(value)))
            return value
        row = c.execute("select v from live_state where k=%s", (key,)).fetchone()
        return row[0] if row else None


def bars(symbol: str, n: int = 3500) -> dict[str, np.ndarray]:
    rows = sorted(bridge.rates(symbol, "M5", 1, n), key=lambda r: r["time"])  # position 1 = newest completed bar
    t = server_to_utc(np.array([r["time"] for r in rows], dtype=np.int64))
    return {"t": t, **{k: np.array([r[k] for r in rows], dtype=float) for k in ("open", "high", "low", "close")}}


def windows(m5: dict[str, np.ndarray]) -> tuple[dict, dict, dict]:
    """The exact windows backtest.run_symbol hands plan() after the newest M5 close."""
    m15, h1 = resample(m5, 900), resample(m5, 3600)
    now = int(m5["t"][-1]) + 300
    i15 = int(np.searchsorted(m15["t"] + 900, now, side="right"))
    i1 = int(np.searchsorted(h1["t"] + 3600, now, side="right"))
    return _win(m15, i15, 300), _win(h1, i1, 200), _win(m5, len(m5["t"]), 100)


def h1_bars(symbol: str, n: int = 1400) -> dict[str, np.ndarray]:
    """Completed native H1 bars (UTC) -- identical to H1 resampled from M5, and ~54 days of it in one call."""
    rows = sorted(bridge.rates(symbol, "H1", 1, n), key=lambda r: r["time"])
    t = server_to_utc(np.array([r["time"] for r in rows], dtype=np.int64))
    return {"t": t, **{k: np.array([r[k] for r in rows], dtype=float) for k in ("open", "high", "low", "close")}}


def bennett_windows(m5: dict[str, np.ndarray], h1: dict[str, np.ndarray], p: bstrat.BennettParams = bstrat.LIVE) -> tuple:
    """The exact windows backtest.run_symbol hands the Bennett plan() after the newest M5 close:
    (M15 300, H1 200, M5 m5_bars, htf = H1 htf_bars). h1 = native H1 (live) or H1 resampled from M5 (tests)."""
    m15 = resample(m5, 900)
    now = int(m5["t"][-1]) + 300
    i15 = int(np.searchsorted(m15["t"] + 900, now, side="right"))
    i1 = int(np.searchsorted(h1["t"] + 3600, now, side="right"))
    return _win(m15, i15, 300), _win(h1, i1, 200), _win(m5, len(m5["t"]), p.m5_bars), _win(h1, i1, p.htf_bars)


def loss_per_lot(q: dict, entry: float, stop: float) -> float:
    return abs(entry - stop) / q["trade_tick_size"] * q["trade_tick_value"]


def lots_for(q: dict, entry: float, stop: float, risk_usd: float) -> float:
    step = q["volume_step"]
    raw = risk_usd / loss_per_lot(q, entry, stop)
    return round(math.floor(raw / step + 1e-9) * step, 2)


def open_risk(p: dict, q: dict) -> float:
    d = 1 if p["type"] == 0 else -1
    if not p["sl"] or (p["sl"] - p["price_open"]) * d >= 0:
        return 0.0
    return max(0.0, (p["price_current"] - p["sl"]) * d) / q["trade_tick_size"] * q["trade_tick_value"] * p["volume"]


def day_start_balance(acct: dict) -> float:
    today = datetime.now(PRAGUE).date().isoformat()
    s = state("day_start") or {}
    if s.get("day") != today:  # first cycle of the Prague day: balance now = balance at 00:00 (no trading at midnight)
        s = state("day_start", {"day": today, "balance": acct["balance"]})
        log("INFO", f"new day {today}: day-start balance {acct['balance']:.2f}")
    return float(s["balance"])


def cycle(dry: bool) -> None:
    acct = bridge.account()
    initial = float(state("initial") or state("initial", acct["balance"]))
    day_start = day_start_balance(acct)
    equity = float(acct["equity"])
    ours = [p for p in bridge.positions() if p.get("magic") in (MAGIC, MAGIC_BN)]
    quotes = {p["symbol"]: bridge.tick(p["symbol"]) for p in ours}
    state("heartbeat", {"utc": int(time.time()), "equity": equity, "open": len(ours), "dry": dry})

    if guard.flatten_now(initial, day_start, equity):
        for p in ours:
            log("WARN", f"DAILY FLATTEN close {p['symbol']} #{p['ticket']}: {bridge.close(p['ticket'], 'bensim flatten') if not dry else 'dry'}")
        return
    for p in ours:  # time exit, as in the backtest (Bennett: the plan's own hold, kept in the order comment "... h<bars>")
        hold = PARAMS.max_hold_bars * 900
        if p.get("magic") == MAGIC_BN and " h" in str(p.get("comment", "")):
            hold = int(str(p["comment"]).rsplit(" h", 1)[1]) * 900
        opened = int(server_to_utc(np.array([p["time"]], dtype=np.int64))[0])
        if time.time() - opened >= hold:
            log("INFO", f"TIME EXIT {p['symbol']} #{p['ticket']}: {bridge.close(p['ticket'], 'bensim time') if not dry else 'dry'}")
    halted = STOP_FILE.exists()
    opens = [guard.Open(p["symbol"], 1 if p["type"] == 0 else -1, open_risk(p, quotes[p["symbol"]])) for p in ours]
    expected = (int(time.time()) // 300) * 300 - 300  # start time of the M5 bar that just closed
    for sym in LIVE_SYMBOLS:
        try:
            m5 = bars(sym)
            if not len(m5["t"]) or int(m5["t"][-1]) < expected:
                continue  # market closed or feed behind: never trade on stale bars
            pl = plan(*windows(m5), sym, PARAMS)
            if not pl or pl.get("status") != "TRIGGERED":
                continue
            with db.connect(autocommit=True) as c:
                new = c.execute("""insert into live_signals (symbol, signal_time, dir, entry, stop, target, checks) values (%s,%s,%s,%s,%s,%s,%s)
                                   on conflict do nothing returning 1""",
                                (sym, pl["signal_time"], pl["dir"], pl["entry"], pl["stop"], pl["target"], json.dumps(pl["checks"], default=str))).fetchone()
            if not new:
                continue  # already handled (restart / repeat) -- never send twice
            decision, detail = decide(sym, pl, initial, day_start, equity, opens, halted, dry)
            if decision == "SENT":
                opens.append(guard.Open(sym, pl["dir"], detail["risk_usd"]))
            with db.connect(autocommit=True) as c:
                c.execute("update live_signals set decision=%s, detail=%s where symbol=%s and signal_time=%s and dir=%s",
                          (decision, json.dumps(detail, default=str), sym, pl["signal_time"], pl["dir"]))
            log("TRADE" if decision == "SENT" else "INFO", f"{sym} {'BUY' if pl['dir'] == 1 else 'SELL'} signal {pl['signal_time']}: {decision} {detail}")
        except Exception as exc:  # one symbol failing must not stop the others
            log("ERROR", f"{sym}: {exc!r}\n{traceback.format_exc(limit=3)}")
    for sym in (BN_SYMBOLS if BN_ON else []):
        try:
            m5 = bars(sym)
            if not len(m5["t"]) or int(m5["t"][-1]) < expected:
                continue
            m15, h1w, m5w, htf = bennett_windows(m5, h1_bars(sym))
            pl = bstrat.plan(m15, h1w, m5w, sym, bstrat.LIVE, htf=htf)
            if not pl or pl.get("status") != "TRIGGERED":
                continue
            with db.connect(autocommit=True) as c:
                new = c.execute("""insert into live_signals_bennett (symbol, signal_time, dir, entry, stop, target, checks) values (%s,%s,%s,%s,%s,%s,%s)
                                   on conflict do nothing returning 1""",
                                (sym, pl["signal_time"], pl["dir"], pl["entry"], pl["stop"], pl["target"], json.dumps(pl["checks"], default=str))).fetchone()
            if not new:
                continue
            decision, detail = decide_bn(sym, pl, initial, day_start, equity, opens, halted, dry)
            if decision == "SENT":
                opens.append(guard.Open(sym, pl["dir"], detail["risk_usd"]))
            with db.connect(autocommit=True) as c:
                c.execute("update live_signals_bennett set decision=%s, detail=%s where symbol=%s and signal_time=%s and dir=%s",
                          (decision, json.dumps(detail, default=str), sym, pl["signal_time"], pl["dir"]))
            log("TRADE" if decision == "SENT" else "INFO", f"BENNETT {sym} {'BUY' if pl['dir'] == 1 else 'SELL'} signal {pl['signal_time']}: {decision} {detail}")
        except Exception as exc:
            log("ERROR", f"BENNETT {sym}: {exc!r}\n{traceback.format_exc(limit=3)}")


def decide_bn(sym: str, pl: dict, initial: float, day_start: float, equity: float, opens: list[guard.Open], halted: bool, dry: bool) -> tuple[str, dict]:
    """Bennett order: market at the trigger close (as the backtest), fixed SL/TP, BN_RISK_SCALE x the normal risk."""
    if halted:
        return "SKIP_KILL_SWITCH", {}
    q = bridge.tick(sym)
    spec, why = bstrat.order_for(pl, q["bid"], q["ask"], bstrat.LIVE)
    if spec is None:
        return f"SKIP_{why}", {"bid": q["bid"], "ask": q["ask"]}
    scale = guard.risk_scale(initial, equity) * BN_RISK_SCALE
    lots = lots_for(q, spec["price"], spec["sl"], initial * RISK_PCT / 100 * scale)
    if lots < q["volume_min"]:
        return "SKIP_LOTS_BELOW_MIN", {"lots": lots}
    risk_usd = loss_per_lot(q, spec["price"], spec["sl"]) * lots
    ok, why = guard.check(initial, day_start, equity, opens, sym, pl["dir"], risk_usd)
    detail = {"price": spec["price"], "sl": spec["sl"], "tp": spec["tp"], "rr": round(spec["rr"], 2), "lots": lots,
              "risk_usd": round(risk_usd, 2), "scale": scale, "hold_bars": pl["hold_bars"]}
    if not ok:
        return f"SKIP_GUARD_{why}", detail
    if dry:
        return "DRY_RUN", detail
    res = bridge.order(sym, spec["side"], lots, spec["sl"], spec["tp"], MAGIC_BN, f"bennett {pl['signal_time'] % 10**8} h{pl['hold_bars']}")
    detail["result"] = {k: res.get(k) for k in ("ok", "retcode", "comment", "order", "deal", "price", "volume", "detail")}
    return ("SENT" if res.get("ok") else "ORDER_REJECTED"), detail


def decide(sym: str, pl: dict, initial: float, day_start: float, equity: float, opens: list[guard.Open], halted: bool, dry: bool) -> tuple[str, dict]:
    if halted:
        return "SKIP_KILL_SWITCH", {}
    q = bridge.tick(sym)
    spec, why = order_for(pl, q["bid"], q["ask"], PARAMS)
    if spec is None:
        return f"SKIP_{why}", {"bid": q["bid"], "ask": q["ask"]}
    n_conf = sum(indicators.passes(pl["checks"], (k,)) for k in PARAMS.confirm_any)
    confidence = 1.0 if n_conf >= len(PARAMS.confirm_any) else WEAK_SETUP_RISK  # 3 of 3: full risk, 2 of 3: half
    scale = guard.risk_scale(initial, equity) * confidence
    risk_target = initial * RISK_PCT / 100 * scale
    lots = lots_for(q, spec["price"], spec["sl"], risk_target)
    if lots < q["volume_min"]:
        return "SKIP_LOTS_BELOW_MIN", {"lots": lots}
    risk_usd = loss_per_lot(q, spec["price"], spec["sl"]) * lots
    ok, why = guard.check(initial, day_start, equity, opens, sym, pl["dir"], risk_usd)
    detail = {"price": spec["price"], "sl": spec["sl"], "tp": spec["tp"], "rr": round(spec["rr"], 2), "lots": lots, "risk_usd": round(risk_usd, 2), "scale": scale,
              "confirmations": n_conf}
    if not ok:
        return f"SKIP_GUARD_{why}", detail
    if dry:
        return "DRY_RUN", detail
    res = bridge.order(sym, spec["side"], lots, spec["sl"], spec["tp"], MAGIC, f"fib618 {pl['signal_time']}")
    detail["result"] = {k: res.get(k) for k in ("ok", "retcode", "comment", "order", "deal", "price", "volume", "detail")}
    return ("SENT" if res.get("ok") else "ORDER_REJECTED"), detail


def setup_lines(sym: str) -> list[str]:
    """fib618 for one symbol from the newest MT5 bars: H1 trend, any setup waiting for its fill (entry/SL), and the
    exact M15 close that would trigger the next BUY / SELL signal (plus the other conditions)."""
    from .strategy import atr, trend

    m15, h1, m5 = windows(bars(sym))
    waiting: list = []
    pl = plan(m15, h1, m5, sym, PARAMS, waiting=waiting)
    a = atr(m15)
    exp = a[-1] / np.mean(a[-50:])
    hi, lo = m15["high"][-PARAMS.channel:].max(), m15["low"][-PARAMS.channel:].min()
    h1t = {1: "UP", -1: "DOWN", 0: "-"}[int(trend(h1)[-1])]
    last = datetime.fromtimestamp(int(m15["t"][-1]) + 900, timezone.utc)
    out = [f"{sym:7} H1 {h1t:4} | ATR exp {exp:.2f} (need >= {PARAMS.min_atr_expansion}) | data to {last:%a %H:%M}Z | state {(pl or {}).get('status', 'none')}"]
    for d, e, sl in waiting:
        out.append(f"        WAITING {'BUY' if d == 1 else 'SELL'}: entry {e:.5g}  SL {sl:.5g}  (market entry on the first M5 close touching it)")
    if h1t == "UP":
        out.append(f"        next BUY  signal: M15 close >= {hi + PARAMS.min_break_atr * a[-1]:.5g} + 2 of (killzone, POI at fib 0.618, sweep)")
    if h1t == "DOWN":
        out.append(f"        next SELL signal: M15 close <= {lo - PARAMS.min_break_atr * a[-1]:.5g} + 2 of (killzone, POI at fib 0.618, sweep)")
    return out


def setups() -> None:
    for sym in LIVE_SYMBOLS:
        try:
            print("\n".join(setup_lines(sym)))
        except Exception as exc:
            print(f"{sym:7} error: {exc!r}")


def status() -> None:
    hb = state("heartbeat") or {}
    age = int(time.time()) - int(hb.get("utc", 0))
    print(f"heartbeat {age}s ago ({'OK' if age < 400 else 'STALE -- engine down?'}): {hb}")
    print(f"day start: {state('day_start')}  initial: {state('initial')}  kill switch: {STOP_FILE.exists()}")
    with db.connect() as c:
        for r in c.execute("select to_char(created,'MM-DD HH24:MI'), symbol, dir, decision, detail->>'lots', detail->>'risk_usd' from live_signals order by created desc limit 10"):
            print("signal", *r)
        if c.execute("select to_regclass('live_signals_bennett')").fetchone()[0]:
            for r in c.execute("select to_char(created,'MM-DD HH24:MI'), symbol, dir, decision, detail->>'lots', detail->>'risk_usd' from live_signals_bennett order by created desc limit 10"):
                print("bennett", *r)
        for r in c.execute("select to_char(ts,'MM-DD HH24:MI:SS'), level, left(msg, 160) from live_events where level <> 'INFO' order by ts desc limit 10"):
            print("event ", *r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="never send orders (also automatic before BENSIM_LIVE_FROM)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--setups", action="store_true", help="print waiting setups and next trigger prices per symbol")
    a = ap.parse_args()
    if a.status:
        return status()
    if a.setups:
        return setups()
    with db.connect(autocommit=True) as c:
        c.execute(SCHEMA)
    log("INFO", f"live engine start: dry={a.dry_run} live_from={datetime.fromtimestamp(LIVE_FROM, timezone.utc):%Y-%m-%d %H:%M}Z risk={RISK_PCT}% "
                f"params={PARAMS.zone_mode}+{PARAMS.confirm_min}of{','.join(PARAMS.confirm_any)} symbols={','.join(LIVE_SYMBOLS)} | "
                f"bennett={'on' if BN_ON else 'off'} {','.join(BN_SYMBOLS)} risk x{BN_RISK_SCALE}")
    failures = 0
    while True:
        try:
            cycle(a.dry_run or time.time() < LIVE_FROM)
            failures = 0
        except Exception as exc:  # bridge down, DB down...: log, wait for the next bar, try again
            failures += 1
            log("ERROR", f"cycle ({failures} in a row): {exc!r}")
            if failures >= 3:
                raise SystemExit("3 failed cycles -- exiting so the watchdog restarts the bridge and the engine") from exc
        if a.once:
            return
        now = time.time()
        time.sleep((math.floor(now / 300) + 1) * 300 + 8 - now)


if __name__ == "__main__":
    main()
