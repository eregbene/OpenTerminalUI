"""TradeGuard: daily/weekly loss + profit lock for a trader's MT5 account, run from OUR PC (out of her reach).

Rules (server time; day = 00:00, week = Monday 00:00):
  day P/L  = closed trades since day start  (+ open floating P/L in "floating" mode)
  week P/L = closed trades since week start (+ floating)
  day P/L <= -day_loss or >= day_profit  -> lock until next day
  week P/L <= -week_loss or >= week_profit -> lock until next Monday
  while locked: close every position + delete every order, whoever opened it (phone too). 0 = rule off.
  TRADING HOURS (optional, cfg "trading_hours"): trading is allowed only inside the listed LOCAL windows per weekday
  (Europe/Bucharest by default, DST-correct, independent of the broker server clock); a weekday with no windows
  (e.g. Saturday/Sunday) = no trading. Every position OPENED outside a window is closed; while outside a window all
  pending orders are deleted; positions opened inside a window are kept after it ends unless "close_at_window_end";
  positions opened before "effective_from_utc" (the switch-on time) are never closed by this rule.
  TradeGuard cannot stop an order reaching the broker: it closes it within ~1 s (the trade's spread + commission is
  lost). Absent "trading_hours" = no time restriction (old behaviour).
  TRADE PROFIT BY DAY (optional, rules "trade_profit_by_day" {"mon": 1100, ...}, local weekday): replaces the single
  "trade_profit" target for that day; a day missing from the map falls back to "trade_profit".

    python -m bensim.tradeguard replay        # 1-month replay of the live account's real trades, scaled to $25K
    python -m bensim.tradeguard live          # the guard (start via bensim/start_tradeguard.ps1)

Live settings: logs/tradeguard.json {"terminal", "portable", "login", "rules"} -- read only at the start of each
trading week, so changing it mid-week has no effect. Lock state: logs/tradeguard_state.json (survives restarts).
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

DAY, WEEK = 86400, 7 * 86400
LOGS = Path(__file__).resolve().parent.parent / "logs"
CFG = Path(os.environ.get("TRADEGUARD_CFG", LOGS / "tradeguard.json"))
STATE = CFG.with_name(CFG.stem + "_state.json")
DUMMY = {"day_loss": 250, "week_loss": 625, "day_profit": 300, "week_profit": 750}  # 1% / 2.5% / 1.2% / 3% of 25K


def day_start(t: int) -> int:
    return t - t % DAY


def week_start(t: int) -> int:
    d = day_start(t)
    return d - ((d // DAY + 3) % 7) * DAY  # 1970-01-01 was a Thursday


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TH_DEFAULT = {"tz": "Europe/Bucharest", "close_at_window_end": False, "delete_pending_outside": True}


def _local(utc: int, tz: str):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(utc, timezone.utc).astimezone(ZoneInfo(tz))


def in_trading_hours(utc: int, rule: dict | None) -> bool:
    """True if trading is allowed at utc. Windows are [start, end) in local time; no rule = always allowed."""
    if not rule:
        return True
    r = {**TH_DEFAULT, **rule}
    loc = _local(utc, r["tz"])
    hm = loc.hour * 60 + loc.minute
    for a, b in r.get("days", {}).get(DAYS[loc.weekday()], []):
        ah, am = map(int, a.split(":"))
        bh, bm = map(int, b.split(":"))
        if ah * 60 + am <= hm < bh * 60 + bm:
            return True
    return False


def trade_profit_for(utc: int, cfg: dict) -> float:
    """Per-trade profit target for the LOCAL weekday (trade_profit_by_day), else the flat trade_profit, else 0 (off)."""
    rules = cfg.get("rules", {})
    by_day = rules.get("trade_profit_by_day")
    if by_day:
        tz = cfg.get("trading_hours", {}).get("tz", TH_DEFAULT["tz"]) if cfg.get("trading_hours") else TH_DEFAULT["tz"]
        day = DAYS[_local(utc, tz).weekday()]
        if day in by_day:
            return float(by_day[day] or 0)
    return float(rules.get("trade_profit") or 0)


def trading_hours_step(mt5, rule: dict | None, utc: int, mine, to_utc, state: dict) -> list[str]:
    """One loop step of the trading-hours rule; returns the actions taken (log lines)."""
    if not rule:
        return []
    r = {**TH_DEFAULT, **rule}
    acts = []
    allowed = in_trading_hours(utc, r)
    if state.get("allowed") != allowed:
        state["allowed"] = allowed
        acts.append(f"TRADING HOURS: trading {'ALLOWED' if allowed else 'NOT allowed'} now ({r['tz']} {_local(utc, r['tz']):%a %H:%M})")
    since = int(r.get("effective_from_utc") or 0)  # positions opened before the rule was switched on are never closed by it
    for p in mine(mt5.positions_get()):
        if to_utc(int(p.time)) < since:
            continue
        opened_ok = in_trading_hours(to_utc(int(p.time)), r)
        if not opened_ok or (not allowed and r["close_at_window_end"]):
            close_pos(mt5, p, "TradeGuard trading hours")
            acts.append(f"closed #{p.ticket} {p.symbol} (opened {'outside' if not opened_ok else 'inside'} the trading hours)")
    if not allowed and r["delete_pending_outside"]:
        for o in mine(mt5.orders_get()):
            res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            acts.append(f"deleted order #{o.ticket} {o.symbol}: {getattr(res, 'retcode', None)}")
    return acts


def breach(day: float, week: float, r: dict, t: int) -> tuple[str, int] | None:
    """(reason, locked_until) for the first rule hit, else None."""
    for name, val, lim, sign, until in (("DAY LOSS", day, r["day_loss"], -1, day_start(t) + DAY),
                                        ("WEEK LOSS", week, r["week_loss"], -1, week_start(t) + WEEK),
                                        ("DAY PROFIT", day, r["day_profit"], 1, day_start(t) + DAY),
                                        ("WEEK PROFIT", week, r["week_profit"], 1, week_start(t) + WEEK)):
        if lim and val * sign >= lim:
            return name, until
    return None


def load_trades(days: int, scale_to: float) -> tuple[list[dict], float]:
    from . import bridge
    now = int(time.time()) + 3 * 3600  # server clock is ahead of UTC; overshoot is harmless
    ds = bridge.deals(now - days * DAY, now + DAY)
    pos: dict[int, dict] = {}
    for d in ds:
        if d["type"] not in (0, 1):
            continue
        p = pos.setdefault(d["position_id"], {"sym": d["symbol"], "net": 0.0, "exit_t": None})
        p["net"] += d["profit"] + d["commission"] + d["swap"] + d.get("fee", 0)
        if d["entry"] == 0:
            p.update(t0=d["time"], px0=d["price"], dir=1 if d["type"] == 0 else -1, gross=0.0, comm0=d["commission"])
        else:
            p.update(exit_t=d["time"], px1=d["price"])
            p["gross"] = p.get("gross", 0.0) + d["profit"]
    trades = [p for p in pos.values() if "t0" in p and p["exit_t"]]
    for p in trades:  # $ per 1.0 price move, from the trade's own result (no symbol tables needed)
        mv = (p["px1"] - p["px0"]) * p["dir"]
        p["k"] = p["gross"] / mv if mv else 0.0
    scale = scale_to / bridge.account()["balance"]
    return sorted(trades, key=lambda p: p["t0"]), scale


def replay(trades: list[dict], scale: float, rules: dict, floating: bool, closes: dict) -> dict:
    t0 = day_start(min(p["t0"] for p in trades))
    t1 = max(p["exit_t"] for p in trades) + 60
    pending, open_, closed = list(trades), [], []  # closed: (time, $)
    lock_until, locks, blocked = 0, [], 0
    for t in range(t0, t1, 60):
        while pending and pending[0]["t0"] < t + 60:
            p = pending.pop(0)
            if t < lock_until:
                blocked += 1
            else:
                open_.append(p)
        for p in [p for p in open_ if p["exit_t"] < t + 60]:
            open_.remove(p)
            closed.append((p["exit_t"], p["net"] * scale))
        px = {p["sym"]: closes[p["sym"]](t) for p in open_}
        fl = sum(p["k"] * (px[p["sym"]] - p["px0"]) * p["dir"] + p["comm0"] for p in open_) * scale
        ds, ws = day_start(t), week_start(t)
        day = sum(v for c, v in closed if c >= ds) + (fl if floating else 0)
        week = sum(v for c, v in closed if c >= ws) + (fl if floating else 0)
        if t >= lock_until and (b := breach(day, week, rules, t)):
            lock_until = b[1]
            locks.append((t, b[0], round(day), round(week), len(open_)))
            for p in open_:  # guard closes everything at this minute's price
                closed.append((t, (p["k"] * (px[p["sym"]] - p["px0"]) * p["dir"] + p["comm0"] * 2) * scale))
            open_ = []
    return {"pnl": sum(v for _, v in closed), "locks": locks, "blocked": blocked, "closed": closed}


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def close_pos(mt5, p, why: str) -> None:
    info, tick = mt5.symbol_info(p.symbol), mt5.symbol_info_tick(p.symbol)
    buy = p.type == mt5.POSITION_TYPE_BUY
    fill = mt5.ORDER_FILLING_FOK if info.filling_mode & 1 else mt5.ORDER_FILLING_IOC if info.filling_mode & 2 else mt5.ORDER_FILLING_RETURN
    r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "position": p.ticket, "symbol": p.symbol, "volume": p.volume,
                        "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
                        "price": tick.bid if buy else tick.ask, "deviation": 50, "type_filling": fill, "comment": why})
    log(f"{why}: close {p.symbol} #{p.ticket} {p.profit:+.2f}: {r.retcode if r else mt5.last_error()}")


def flatten(mt5, mine=lambda xs: list(xs or [])) -> None:
    """Close every position and delete every pending order on the account (any magic, manual/phone too)."""
    for p in mine(mt5.positions_get()):
        close_pos(mt5, p, "TradeGuard lock")
    for o in mine(mt5.orders_get()):
        r = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
        log(f"delete order {o.symbol} #{o.ticket}: {r.retcode if r else mt5.last_error()}")


def live() -> None:
    import MetaTrader5 as mt5

    from .mt5_time import server_offset_hours
    st = json.loads(STATE.read_text()) if STATE.exists() else {"lock_until": 0, "why": ""}
    th_state: dict = {}
    cfg, wk, down, last = None, None, 0, 0
    while True:
        utc = int(time.time())
        t = utc + server_offset_hours(utc) * 3600  # broker server time
        if week_start(t) != wk:  # settings can only change at the start of a new week
            cfg, wk = json.loads(CFG.read_text()), week_start(t)
            log(f"week {time.strftime('%Y-%m-%d', time.gmtime(wk))} rules {cfg['rules']} floating={cfg.get('floating', True)} trading_hours={cfg.get('trading_hours')}")
        acct = mt5.account_info() if mt5.terminal_info() else None
        if acct is None:
            mt5.initialize(path=cfg["terminal"], portable=cfg.get("portable", False))
            acct = mt5.account_info()
        if acct is None or (cfg.get("login") and acct.login != cfg["login"]):
            down += 1
            if down % 30 == 1:  # every ~5 min
                log(f"ALERT: guard cannot see the account ({mt5.last_error()}, login {acct and acct.login}) -- "
                    "terminal closed, logged out or password changed?")
            time.sleep(10)
            continue
        down = 0
        mg = cfg.get("magic")  # test only: watch just these trades
        mine = lambda xs: [x for x in xs or [] if mg is None or x.magic == mg]
        deals = [d for d in mine(mt5.history_deals_get(week_start(t) - DAY, t + DAY)) if d.type in (0, 1)]
        net = lambda since: sum(d.profit + d.commission + d.swap + d.fee for d in deals if d.time >= since)
        fl = sum(p.profit + p.swap for p in mine(mt5.positions_get())) if cfg.get("floating", True) else 0.0
        day, week = net(day_start(t)) + fl, net(week_start(t)) + fl
        if t >= st["lock_until"] and (b := breach(day, week, cfg["rules"], t)):
            st = {"lock_until": b[1], "why": b[0]}
            STATE.write_text(json.dumps(st))
            log(f"LOCK {b[0]}: day {day:+.2f} week {week:+.2f} -> no trading until "
                f"{time.strftime('%a %Y-%m-%d %H:%M', time.gmtime(b[1]))} server time")
        locked = t < st["lock_until"]
        if not locked and cfg.get("trading_hours"):
            for a in trading_hours_step(mt5, cfg["trading_hours"], utc, mine, lambda s_: s_ - server_offset_hours(s_ - 7200) * 3600, th_state):
                log(("ALERT: " if a.startswith("closed") else "") + a)
        if locked and (mine(mt5.positions_get()) or mine(mt5.orders_get())):
            flatten(mt5, mine)
        elif tp := trade_profit_for(utc, cfg):  # per-trade profit target (profit + swap, before commission); by local weekday if set
            for p in mine(mt5.positions_get()):
                if p.profit + p.swap >= tp:
                    close_pos(mt5, p, "TradeGuard trade target")
        if not mt5.terminal_info().trade_allowed and utc - last >= 300:
            log("ALERT: Algo Trading is OFF in the terminal -- the guard cannot close trades")
        if utc - last >= 300:
            last = utc
            log(f"day {day:+.2f} week {week:+.2f} equity {acct.equity:.2f} "
                + (f"LOCKED ({st['why']})" if locked else "ok"))
        time.sleep(1)


def status() -> None:
    """One line for the hourly check; restarts the guard watchdog if the guard is not running."""
    import subprocess
    import psutil
    procs = [(p.info["name"] or "", " ".join(p.info["cmdline"] or []), p.info["exe"] or "")
             for p in psutil.process_iter(["name", "cmdline", "exe"])]
    guard = sum(n == "python.exe" and "tradeguard live" in c for n, c, _ in procs)
    term = json.loads(CFG.read_text())["terminal"].lower()
    term = sum(e.lower() == term for _, _, e in procs)
    raw = (LOGS / "tradeguard.log").read_bytes()
    lines = [x for x in raw.decode("utf-16" if raw[:2] == b"\xff\xfe" else "utf-8", "replace").splitlines() if x[:2] == "20"]
    last = lines[-1] if lines else "no log"
    age = (time.time() - time.mktime(time.strptime(last[:19], "%Y-%m-%d %H:%M:%S"))) / 60 if lines else 1e9
    alerts = [x for x in lines[-15:] if "ALERT" in x or "LOCK" in x]
    if not guard:
        subprocess.run(["powershell", "-NoProfile", "-File", str(Path(__file__).with_name("start_tradeguard.ps1"))])
    ok = guard and term and age <= 7 and not any("ALERT" in x for x in alerts)
    print(f"tradeguard: {'OK' if ok else 'CHECK'} guard={guard} mt5={term} log {age:.0f}m old | {last[20:]}"
          + ("" if guard else " | guard was down -> restarted") + "".join(f" | {x}" for x in alerts[-2:]))


def main() -> None:
    if sys.argv[1:2] == ["status"]:
        return status()
    if sys.argv[1:2] == ["live"]:
        return live()
    if sys.argv[1:2] != ["replay"]:
        sys.exit(__doc__)
    from . import bridge
    trades, scale = load_trades(31, 25_000)
    closes = {}
    for sym in {p["sym"] for p in trades}:
        r = bridge.rates(sym, "M1", 0, 50_000)
        tt, cc = np.array([x["time"] for x in r]), np.array([x["close"] for x in r])
        closes[sym] = lambda t, tt=tt, cc=cc: cc[max(np.searchsorted(tt, t, "right") - 1, 0)]
    raw = sum(p["net"] for p in trades) * scale
    print(f"{len(trades)} real trades from the live account, last 31 days, scaled x{scale:.3f} to a $25K account")
    print(f"rules: {DUMMY}\nno guard: {raw:+,.0f} $")
    weeks = defaultdict(float)
    for p in trades:
        weeks[week_start(p['exit_t'])] += p["net"] * scale
    for mode in (True, False):
        res = replay(trades, scale, DUMMY, mode, closes)
        print(f"\n== {'FLOATING (open + closed)' if mode else 'CLOSED trades only'}: guarded {res['pnl']:+,.0f} $, "
              f"{len(res['locks'])} locks, {res['blocked']} trades blocked while locked")
        for t, why, d, w, n in res["locks"]:
            print(f"  {time.strftime('%a %m-%d %H:%M', time.gmtime(t))}  {why:11} day {d:+5}  week {w:+6}  closed {n} open")
        gw = defaultdict(float)
        for c, v in res["closed"]:
            gw[week_start(c)] += v
        for w in sorted(set(weeks) | set(gw)):
            print(f"  week {time.strftime('%m-%d', time.gmtime(w))}: no guard {weeks[w]:+7,.0f}  guarded {gw[w]:+7,.0f}")


if __name__ == "__main__":
    # self-check of the rule logic (no MT5 needed)
    mon = 1793577600  # 2026-11-02 Mon 00:00
    assert week_start(mon + 3 * DAY + 5) == mon and day_start(mon + 100) == mon
    assert breach(-250, -250, DUMMY, mon + 100) == ("DAY LOSS", mon + DAY)
    assert breach(-100, -625, DUMMY, mon + 100) == ("WEEK LOSS", mon + WEEK)
    assert breach(300, 300, DUMMY, mon) == ("DAY PROFIT", mon + DAY)
    assert breach(-249, 749, DUMMY, mon) is None
    main()
