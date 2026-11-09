"""Research books LIVE on the DEMO account (2026-10-20, user request) -- five strategies that FAILED their backtests,
traded anyway to collect real results. Run from live.cycle() after fib618/Bennett; a failure here never stops them.

    python -m bensim.books check        # what every book sees right now per symbol (no orders, no DB writes)
    python -m bensim.books selfcheck    # the fill / cancel / pick-one-per-pair rules on hand-made prices
    BENSIM_BOOKS=imbalance,enigma,...   # which books trade (default all five; "off" = none)

Books (rules exactly as backtested; magic per book; risk fixed $ RISK_USD each via live.size_trade since 2026-11-09; BENSIM_BOOKS_RISK_SCALE unused):
  imbalance  770104  strategy.plan default Params (donchian break -> imbalance zone), market at the trigger, fixed SL/TP
  enigma     770105  fvg_strategy.plan, H4 FVG -> M15 shift, market at the trigger, fixed SL/TP
  sflow      770106  JeaFx structure, entry A: H1 close-only BOS -> limit at the zone's near edge (killed by an M5 close
                     beyond the far side, a newer H1 BOS or 5 days); stop = protected swing -/+ 0.1 ATR; target =
                     impulse extreme at the fill (>= 1R); 10-day hold
  parallax   770107  ParallaxFX "author": D1 (broker day = New York 17:00) BB + Stoch + indecision/engulfing, limits at
                     23.6 / 38.2 of the setup candle, stop 78.6, target -100 extension, cancelled once the target
                     prints, 5-day expiry, 30-day hold
  cta        770108  CTA momentum v2: D1 with-trend bar after a 2-bar pullback -> stop order 1 pip beyond it (10 days,
                     cancelled if the midpoint trades first), stop = midpoint (beyond the bar if narrow); TRAIL under
                     each completed pullback (engine-managed: closed when the bid crosses it), 120-day hold
ONE TRADE PER PAIR (user rule): a pair already held by ANY bensim book (fib618, Bennett, flow, these) gets no new
trade; when several books fill on the same pair in the same cycle, the highest CONFIRMATION wins -- confirmation =
timeframes agreeing with the trade (H4, H1, M15 swing trend, strategy.trend) + other books signalling the same side
there now; ties go to the order above. The losers are recorded SKIP_LOWER_CONF.
Pending limits / stops are checked on each just-closed M5 bar (spread = backtest model) and filled at MARKET.
Every signal is one row in live_books (unique per book, symbol, signal time, side, leg): never sent twice.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback

import numpy as np
import pandas as pd

from . import bridge, db, fvg_strategy, guard, news_block, sflow
from . import cta_momentum as cta
from . import parallax as px
from .backtest import Exec, _spread, resample
from .config import env
from .mt5_time import server_to_utc
from .strategy import Params, atr, order_for, pip, plan, trend

MAGICS = {"imbalance": 770104, "enigma": 770105, "sflow": 770106, "parallax": 770107, "cta": 770108, "channel": 770110}
# 2026-11-06 user GO (Stage 4 item 4): parallax + cta SHADOW only (signals recorded with status SHADOW, never sent)
# 2026-11-09 user GO: channel (Bennett range mode, BUYS ONLY -- the only filter that passed the untouched 2021-24 check;
# 5y +258R t 2.38) LIVE at $1,000 risk (live.BUDGETS["channel"]); stopped for good after CHANNEL_KILL_USD realised loss.
_on = env("BENSIM_BOOKS", "imbalance,enigma,sflow,channel").lower()
CHANNEL_SIDES = (1,) if env("BENSIM_CHANNEL_SIDES", "buy").lower() == "buy" else (1, -1)
CHANNEL_KILL_USD = float(env("BENSIM_CHANNEL_KILL_USD", "30000"))  # realised loss since start -> no new channel trades
ON = [] if _on == "off" else [b for b in _on.split(",") if b in MAGICS]
SHADOW = [b for b in env("BENSIM_BOOKS_SHADOW", "parallax,cta").lower().split(",") if b in MAGICS and b not in ON]
SYMBOLS = [x for x in env("BENSIM_BOOKS_SYMBOLS", "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD,XAUUSD").split(",") if x]
RISK_SCALE = float(env("BENSIM_BOOKS_RISK_SCALE", "0.5"))
IMB, ENIGMA = Params(), fvg_strategy.FvgParams(htf="H4")
EX = Exec()
DAY = 86400
BIG = 1e18  # 'no pullback yet' (finite: jsonb has no Infinity)

SCHEMA = """create table if not exists live_books (book text, symbol text, signal_time bigint, dir int, leg int,
    kind text, entry double precision, stop double precision, target double precision, cancel double precision,
    expire bigint, hold bigint, status text, ticket bigint, conf int, extra jsonb, created timestamptz default now(),
    updated timestamptz default now(), primary key (book, symbol, signal_time, dir, leg))"""


# ---------------------------------------------------------------- pure rules (selfcheck covers them)
def touched(kind: str, d: int, entry: float, cancel: float, h: float, lo: float, c: float, spr: float) -> str | None:
    """One closed M5 bar (bid) against a pending order -> 'fill', 'cancel' or None, in each backtest's own order:
    limit (parallax): cancel (target printed) checked first; zone (sflow): fill first, killed by a CLOSE beyond the far
    side; stop (cta, bid prices as its daily test): trigger first, cancelled if the midpoint trades."""
    ask_l = lo + spr
    if kind == "limit":
        if (h >= cancel) if d == 1 else (ask_l <= cancel):
            return "cancel"
        return "fill" if ((ask_l <= entry) if d == 1 else (h >= entry)) else None
    if kind == "zone":
        if (ask_l <= entry) if d == 1 else (h >= entry):
            return "fill"
        return "cancel" if ((c < cancel) if d == 1 else (c > cancel)) else None
    if kind == "stop":
        if (h >= entry) if d == 1 else (lo <= entry):
            return "fill"
        return "cancel" if ((lo <= cancel) if d == 1 else (h >= cancel)) else None
    raise ValueError(kind)


def pick(cands: list[dict], held: set[str]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """One trade per pair: skip pairs already held, else the highest conf wins (ties: book order in MAGICS)."""
    order = list(MAGICS)
    win, lose = [], []
    for sym in dict.fromkeys(c["symbol"] for c in cands):
        group = sorted((c for c in cands if c["symbol"] == sym), key=lambda c: (-c["conf"], order.index(c["book"])))
        if sym in held:
            lose += [(c, "SKIP_PAIR_OPEN") for c in group]
            continue
        win.append(group[0])
        lose += [(c, "SKIP_LOWER_CONF") for c in group[1:]]
    return win, lose


def trail_update(st: dict, d: int, h: float, lo: float, c: float) -> dict:
    """cta_momentum.trade()'s trail on one completed D1 bar after the fill day: after a pullback of >= 2 bars, a
    CLOSE beyond the extreme made before it moves the stop to that pullback's low (high for shorts), never back."""
    st = dict(st)
    if (h > st["ext"]) if d == 1 else (lo < st["ext"]):
        if st["pb_len"] >= 2 and ((c > st["ext"]) if d == 1 else (c < st["ext"])):
            st["stop"] = max(st["stop"], st["pb"]) if d == 1 else min(st["stop"], st["pb"])
        st.update(ext=h if d == 1 else lo, pb=BIG if d == 1 else -BIG, pb_len=0)
    else:
        st["pb_len"] += 1
        st["pb"] = min(st["pb"], lo) if d == 1 else max(st["pb"], h)
    return st


# ---------------------------------------------------------------- data
_cache: dict = {}


def d1_bars(sym: str, n: int = 320) -> pd.DataFrame:
    """Completed broker D1 bars (broker midnight = New York 17:00), UTC open times."""
    key = (sym, int(time.time()) // 3600)
    if key not in _cache:
        rows = sorted(bridge.rates(sym, "D1", 1, n), key=lambda r: r["time"])
        t = server_to_utc(np.array([r["time"] for r in rows], dtype=np.int64))
        _cache[key] = pd.DataFrame({"t_open": t, **{k: [float(r[k]) for r in rows] for k in ("open", "high", "low", "close")}})
    return _cache[key]


def conf(sym: str, d: int, m5: dict, h1: dict) -> int:
    return sum(int(x) == d for x in (trend(resample(h1, 14400))[-1], trend(h1)[-1], trend(resample(m5, 900))[-1]))


# ---------------------------------------------------------------- signals (each returns new rows for the bar just closed)
def sig_plan(book: str, sym: str, m5: dict, h1: dict, windows) -> list[dict]:
    m15w, h1w, m5w = windows(m5)
    if book == "imbalance":
        pl, hold = plan(m15w, h1w, m5w, sym, IMB), IMB.max_hold_bars * IMB.entry_sec
    else:
        pl, hold = fvg_strategy.plan(m15w, h1w, m5w, sym, ENIGMA), ENIGMA.max_hold_bars * 900
    if not pl or pl.get("status") != "TRIGGERED":
        return []
    return [{"kind": "market", "signal_time": int(pl["signal_time"]), "dir": int(pl["dir"]), "leg": 0, "entry": pl["entry"],
             "stop": pl["stop"], "target": pl["target"], "cancel": 0.0, "expire": int(time.time()) + 600, "hold": hold,
             "extra": {"plan": {k: pl[k] for k in ("entry", "stop", "target")}}}]


def sig_sflow(sym: str, h1: dict) -> list[dict]:
    ev = sflow.structure(h1)
    if not ev or ev[-1]["i"] != len(h1["t"]) - 1 or not ev[-1]["zone"]:
        return []
    e = ev[-1]
    d, (top, bot) = e["d"], e["zone"]
    near, far = (top, bot) if d == 1 else (bot, top)
    t_bos = int(h1["t"][e["i"]]) + 3600
    return [{"kind": "zone", "signal_time": int(h1["t"][e["i"]]), "dir": d, "leg": 0, "entry": near,
             "stop": far - d * 0.1 * atr(h1)[e["i"]], "target": 0.0, "cancel": far, "expire": t_bos + sflow.WAIT_DAYS * DAY,
             "hold": sflow.HOLD_DAYS * DAY, "extra": {"t_origin": int(h1["t"][e["origin"]])}}]


def sig_channel(sym: str, m5: dict, h1: dict) -> list[dict]:
    """Bennett channel mode (bensim.bennett_channel.setup, the backtested function): only on the M5 close that completes
    a 15M bar. Pending 'zone' limit at the 62% OTE, cancelled on a close below the leg low, 24 h, 10-day hold."""
    from . import bennett_channel as bc  # noqa: PLC0415
    from .bennett_strategy import agg  # noqa: PLC0415
    t_close = int(m5["t"][-1]) + 300
    if t_close % 900:
        return []
    m15 = resample(m5, 900)
    i = int(np.searchsorted(m15["t"] + 900, t_close, side="right")) - 1
    if i < 0 or int(m15["t"][i]) + 900 != t_close:
        return []
    h4 = bc.h4_closed(agg(h1, 14400), t_close)
    out = []
    for d in CHANNEL_SIDES:
        s = bc.setup(h4, m15, i, d, pip(sym))
        if s:
            out.append({"kind": "zone", "signal_time": int(m15["t"][i]), "dir": d, "leg": 0, "entry": s["entry"], "stop": s["stop"],
                        "target": s["target"], "cancel": s["ext"], "expire": t_close + DAY, "hold": 10 * DAY, "extra": {}})
    return out


def channel_dead(c, log) -> bool:
    """Kill switch: realised P&L (profit + commission + swap) of magic 770110 since the book's start <= -CHANNEL_KILL_USD."""
    row = c.execute("select v from live_state where k='channel_start'").fetchone()
    start = int(row[0]) if row else None
    if start is None:
        start = int(time.time())
        c.execute("insert into live_state (k, v) values ('channel_start', %s) on conflict (k) do nothing", (json.dumps(start),))
    key = ("channel_pnl", int(time.time()) // 900)
    if key not in _cache:
        ds = [d for d in bridge.deals(start - 3600, int(time.time()) + 3600) if d.get("magic") == MAGICS["channel"]]
        _cache[key] = sum(d.get("profit", 0.0) + d.get("commission", 0.0) + d.get("swap", 0.0) + d.get("fee", 0.0) for d in ds)
    if _cache[key] <= -CHANNEL_KILL_USD:
        log("WARN", f"BOOK channel KILL SWITCH: realised {_cache[key]:+,.0f} USD <= -{CHANNEL_KILL_USD:,.0f} -- no new channel trades")
        return True
    return False


def sig_parallax(b: pd.DataFrame) -> list[dict]:
    s = px.setups(px.indicators(b))
    if s.empty or s.index[-1] != b.index[-1]:
        return []
    r = s.iloc[-1]
    d = int(r.dir)
    target = (r.high if d == 1 else r.low) + d * px.TARGETS["-100"] * (r.high - r.low)
    return [{"kind": "limit", "signal_time": int(r.t_open), "dir": d, "leg": k, "entry": e, "stop": st, "target": target,
             "cancel": target, "expire": int(r.t_open) + DAY + px.EXPIRE * DAY, "hold": px.MAX_HOLD * DAY, "extra": {}}
            for k, (e, st, _) in enumerate(px.legs_for(r, "author"))]


def sig_cta(sym: str, b: pd.DataFrame) -> list[dict]:
    s = cta.signals(b, "v2")
    if s.empty or s.index[-1] != b.index[-1]:
        return []
    r, d, buf = s.iloc[-1], int(s.iloc[-1].dir), pip(sym)
    tr = pd.concat([b.high - b.low, (b.high - b.close.shift()).abs(), (b.low - b.close.shift()).abs()], axis=1).max(axis=1)
    a = tr.rolling(20).mean().shift(1).iloc[-1]
    stop = (r.high + r.low) / 2
    if r.high - r.low < 0.5 * a:  # narrow bar: stop beyond the whole bar
        stop = r.low - buf if d == 1 else r.high + buf
    return [{"kind": "stop", "signal_time": int(r.t_open), "dir": d, "leg": 0, "entry": (r.high + buf) if d == 1 else (r.low - buf),
             "stop": stop, "target": 0.0, "cancel": (r.high + r.low) / 2, "expire": int(r.t_open) + DAY + 14 * DAY,
             "hold": 120 * DAY, "extra": {}}]


# ---------------------------------------------------------------- engine step
def _held(magics: set[int]) -> tuple[list[dict], set[str]]:
    pos = bridge.positions()
    return pos, {p["symbol"] for p in pos if p.get("magic") in magics}


def _exits(pos: list[dict], c, m5_of, log, dry: bool) -> None:
    """Time exits (all books) and the CTA trail (closed by the engine when the bid crosses it)."""
    mine = {p["ticket"]: p for p in pos if p.get("magic") in MAGICS.values()}
    if not mine:
        return
    rows = c.execute("select book, symbol, signal_time, dir, leg, hold, extra, ticket from live_books where status='SENT' and ticket = any(%s)",
                     (list(mine),)).fetchall()
    for book, sym, st_, d, leg, hold, extra, ticket in rows:
        p = mine[ticket]
        if time.time() - extra["fill_t"] >= hold:
            log("INFO", f"BOOK {book} TIME EXIT {sym} #{p['ticket']}: {bridge.close(p['ticket'], f'{book} time') if not dry else 'dry'}")
            continue
        if book != "cta":
            continue
        b = d1_bars(sym)
        tr = extra["trail"]
        for _, x in b[b.t_open > tr["last_t"]].iterrows():  # completed D1 bars not yet applied; the first = the fill day
            tr = trail_update(tr, d, x.high, x.low, x.close) if tr["fill_seen"] else dict(tr, ext=x.high if d == 1 else x.low, fill_seen=True)
            tr["last_t"] = int(x.t_open)
        if tr != extra["trail"]:
            extra["trail"] = tr
            c.execute("update live_books set extra=%s, updated=now() where book=%s and symbol=%s and signal_time=%s and dir=%s and leg=%s",
                      (json.dumps(extra), book, sym, st_, d, leg))
        m5 = m5_of(sym)
        if (m5["low"][-1] <= tr["stop"]) if d == 1 else (m5["high"][-1] + _spread(sym, int(m5["t"][-1]), EX) >= tr["stop"]):
            log("INFO", f"BOOK cta TRAIL EXIT {sym} #{p['ticket']} at trail {tr['stop']:.5g}: "
                        f"{bridge.close(p['ticket'], 'cta trail') if not dry else 'dry'}")


def _send(cd: dict, initial: float, day_start: float, equity: float, opens: list, dry: bool) -> tuple[str, dict]:
    sym, d = cd["symbol"], cd["dir"]
    q = bridge.tick(sym)
    price = q["ask"] if d == 1 else q["bid"]
    target = cd["target"]
    if cd["book"] == "sflow":  # target = the impulse extreme printed before the fill
        target = cd["impulse_ext"]
    if (price - cd["stop"]) * d <= 0 or (target and (target - price) * d <= 0):
        return "SKIP_PRICE_OUTSIDE_PLAN", {"price": price}
    if cd["book"] == "sflow" and (target - price) * d < 1.0 * (price - cd["stop"]) * d:
        return "SKIP_RR_BELOW_1", {"price": price, "target": target}
    from .live import BUDGETS, size_trade  # noqa: PLC0415  (live imports this module)
    sz = size_trade(q, price, cd["stop"], BUDGETS[cd["book"]] * guard.risk_scale(initial, equity))  # 2026-11-09: fixed $
    if not sz["ok"]:
        return "SKIP_LOTS_BELOW_MIN", sz
    lots, risk_usd = sz["lots"], sz["est_loss"]
    ok, why = guard.check(initial, day_start, equity, opens, sym, d, risk_usd)
    news = news_block.blocked(sym) if ok else None
    ok, why = (False, news) if news else (ok, why)
    detail = {"price": price, "sl": cd["stop"], "tp": target or 0.0, "lots": lots, "risk_usd": round(risk_usd, 2), "conf": cd["conf"]}
    if not ok:
        return f"SKIP_GUARD_{why}", detail
    if dry:
        return "DRY_RUN", detail
    res = bridge.order(sym, "BUY" if d == 1 else "SELL", lots, cd["stop"], target or 0.0, MAGICS[cd["book"]],
                       f"{cd['book']} {cd['signal_time'] % 10**8}")
    detail["result"] = {k: res.get(k) for k in ("ok", "retcode", "comment", "order", "deal", "price", "volume")}
    return ("SENT" if res.get("ok") else "ORDER_REJECTED"), detail


def step(initial: float, day_start: float, equity: float, halted: bool, dry: bool, log, bars, h1_bars, windows,
         open_risk, all_magics: set[int]) -> None:
    if not ON:
        return
    now = int(time.time())
    expected = (now // 300) * 300 - 300
    m5c: dict = {}

    def m5_of(sym: str) -> dict:
        if sym not in m5c:
            m5c[sym] = bars(sym)
        return m5c[sym]

    with db.connect(autocommit=True) as c:
        c.execute(SCHEMA)
        pos, held = _held(all_magics | set(MAGICS.values()))
        _exits(pos, c, m5_of, log, dry)
        ch_dead = "channel" in ON and channel_dead(c, log)
        cands: list[dict] = []
        for sym in SYMBOLS:
            try:
                m5 = m5_of(sym)
                if not len(m5["t"]) or int(m5["t"][-1]) < expected:
                    continue  # market closed / stale feed
                h1, rows = h1_bars(sym), []
                for book in ON + SHADOW:
                    if book in ("imbalance", "enigma"):
                        new = sig_plan(book, sym, m5, h1, windows)
                    elif book == "sflow":
                        new = sig_sflow(sym, h1)
                    elif book == "channel":
                        new = [] if ch_dead else sig_channel(sym, m5, h1)
                    else:
                        b = d1_bars(sym)
                        new = sig_parallax(b) if book == "parallax" else sig_cta(sym, b)
                    for r in new:  # record once; markets go straight to the pick, pendings wait for a later M5 bar
                        ins = c.execute("""insert into live_books (book, symbol, signal_time, dir, leg, kind, entry, stop, target, cancel,
                                           expire, hold, status, extra) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                           on conflict do nothing returning 1""",
                                        (book, sym, r["signal_time"], r["dir"], r["leg"], r["kind"], r["entry"], r["stop"], r["target"],
                                         r["cancel"], r["expire"], r["hold"],
                                         "SHADOW" if book in SHADOW else ("PENDING" if r["kind"] != "market" else "NEW"),
                                         json.dumps(r["extra"]))).fetchone()
                        if ins:
                            log("INFO", f"BOOK {book} {sym} {'BUY' if r['dir'] == 1 else 'SELL'} {r['kind']} signal: entry {r['entry']:.5g} "
                                        f"stop {r['stop']:.5g} target {r['target']:.5g}{' (SHADOW, not sent)' if book in SHADOW else ''}")
                            if r["kind"] == "market" and book not in SHADOW:
                                rows.append(dict(r, book=book, symbol=sym))
                spr = _spread(sym, int(m5["t"][-1]), EX)
                for book, sig_t, d, leg, kind, entry, stop, target, cancel, expire, hold, extra in c.execute(
                        """select book, signal_time, dir, leg, kind, entry, stop, target, cancel, expire, hold, extra from live_books
                           where status='PENDING' and symbol=%s and book = any(%s)""", (sym, ON)).fetchall():
                    key = (book, sym, sig_t, d, leg)
                    newer = book == "sflow" and any(int(h1["t"][e["i"]]) > sig_t for e in sflow.structure(h1)[-3:])
                    if now >= expire or newer:
                        c.execute("update live_books set status='EXPIRED', updated=now() where book=%s and symbol=%s and signal_time=%s and dir=%s and leg=%s", key)
                        continue
                    if int(m5["t"][-1]) < sig_t + {"sflow": 3600, "channel": 900}.get(book, DAY):
                        continue  # the signal bar itself is still the newest data
                    if book == "channel" and ch_dead:
                        continue
                    hit = touched(kind, d, entry, cancel, m5["high"][-1], m5["low"][-1], m5["close"][-1], spr)
                    if hit == "cancel":
                        c.execute("update live_books set status='CANCELLED', updated=now() where book=%s and symbol=%s and signal_time=%s and dir=%s and leg=%s", key)
                    elif hit == "fill":
                        r = {"book": book, "symbol": sym, "signal_time": sig_t, "dir": d, "leg": leg, "kind": kind, "stop": stop,
                             "target": target, "hold": hold, "extra": extra}
                        if book == "sflow":
                            seg_h1 = h1["t"] >= extra["t_origin"]
                            hi = max(h1["high"][seg_h1].max(), m5["high"][m5["t"] >= h1["t"][-1] + 3600].max(initial=-np.inf))
                            lo = min(h1["low"][seg_h1].min(), m5["low"][m5["t"] >= h1["t"][-1] + 3600].min(initial=np.inf))
                            r["impulse_ext"] = float(hi if d == 1 else lo)
                        rows.append(r)
                for r in rows:
                    r["conf"] = conf(sym, r["dir"], m5, h1)
                cands += rows
            except Exception as exc:
                log("ERROR", f"BOOKS {sym}: {exc!r}\n{traceback.format_exc(limit=3)}")
        for r in cands:  # + other books on the same side of the same pair now
            r["conf"] += sum(1 for x in cands if x is not r and x["symbol"] == r["symbol"] and x["dir"] == r["dir"] and x["book"] != r["book"])
        win, lose = pick(cands, held)
        quotes = {p["symbol"]: bridge.tick(p["symbol"]) for p in pos if p.get("magic") in all_magics | set(MAGICS.values())}
        opens = [guard.Open(p["symbol"], 1 if p["type"] == 0 else -1, open_risk(p, quotes[p["symbol"]]))
                 for p in pos if p.get("magic") in all_magics | set(MAGICS.values())]
        decided = [(r, *(_send(r, initial, day_start, equity, opens, dry) if not halted else ("SKIP_KILL_SWITCH", {}))) for r in win]
        decided += [(r, why, {"conf": r["conf"]}) for r, why in lose]
        for r, decision, detail in decided:
            extra = dict(r["extra"], decision_detail=detail, fill_t=now)
            if decision == "SENT":
                res = detail.get("result") or {}
                extra["trail"] = {"stop": r["stop"], "ext": detail["price"], "pb": BIG if r["dir"] == 1 else -BIG, "pb_len": 0,
                                  "fill_seen": False, "last_t": int(d1_bars(r["symbol"]).t_open.iloc[-1])}  # next D1 bar = fill day
                ticket = res.get("order")
                opens.append(guard.Open(r["symbol"], r["dir"], detail["risk_usd"]))
            else:
                ticket = None
            # a pending order that lost the pick stays PENDING: it may still fill on a later bar
            c.execute("""update live_books set status=%s, ticket=%s, conf=%s, extra=%s, updated=now()
                         where book=%s and symbol=%s and signal_time=%s and dir=%s and leg=%s and status in ('NEW','PENDING')""",
                      ("PENDING" if decision.startswith("SKIP_LOWER") and r["kind"] != "market" else decision, ticket, r["conf"],
                       json.dumps(extra, default=float), r["book"], r["symbol"], r["signal_time"], r["dir"], r["leg"]))
            log("TRADE" if decision == "SENT" else "INFO",
                f"BOOK {r['book']} {r['symbol']} {'BUY' if r['dir'] == 1 else 'SELL'} conf {r['conf']}: {decision} {detail}")


# ---------------------------------------------------------------- checks
def selfcheck() -> None:
    # parallax limit: the target printing first cancels; ask touching the entry fills (buy)
    assert touched("limit", 1, 1.1000, 1.1100, 1.1100, 1.0990, 1.1050, 0.0001) == "cancel"
    assert touched("limit", 1, 1.1000, 1.1100, 1.1050, 1.0999, 1.1020, 0.0001) == "fill"
    assert touched("limit", -1, 1.1000, 1.0900, 1.0995, 1.0950, 1.0960, 0.0001) is None
    # sflow zone: touch fills even on a bar that closes beyond the far side; a close beyond without touch cancels (sell)
    assert touched("zone", -1, 1.2000, 1.2050, 1.2060, 1.1990, 1.2055, 0.0001) == "fill"
    assert touched("zone", 1, 1.2000, 1.1950, 1.2100, 1.2001, 1.2050, 0.0) is None
    assert touched("zone", 1, 1.2000, 1.1950, 1.2100, 1.1995, 1.1940, 0.0002) == "fill"
    # cta stop: trigger first; the midpoint trading without a trigger cancels
    assert touched("stop", 1, 1.3000, 1.2900, 1.3001, 1.2950, 1.2990, 0.0001) == "fill"
    assert touched("stop", 1, 1.3000, 1.2900, 1.2990, 1.2899, 1.2950, 0.0001) == "cancel"
    # one per pair: held pair skipped, highest conf wins, tie -> book order
    c = [{"book": "cta", "symbol": "EURUSD", "conf": 2}, {"book": "sflow", "symbol": "EURUSD", "conf": 3},
         {"book": "parallax", "symbol": "GBPUSD", "conf": 1}, {"book": "imbalance", "symbol": "GBPUSD", "conf": 1},
         {"book": "enigma", "symbol": "USDJPY", "conf": 3}]
    win, lose = pick(c, held={"USDJPY"})
    assert [(w["book"], w["symbol"]) for w in win] == [("sflow", "EURUSD"), ("imbalance", "GBPUSD")]
    assert sorted(why for _, why in lose) == ["SKIP_LOWER_CONF", "SKIP_LOWER_CONF", "SKIP_PAIR_OPEN"]
    # cta trail: 2-bar pullback then a close above the old extreme -> stop up to the pullback low
    st = {"stop": 1.00, "ext": 1.10, "pb": BIG, "pb_len": 0}
    for h, lo, cl in ((1.09, 1.05, 1.06), (1.08, 1.04, 1.05), (1.12, 1.07, 1.11)):
        st = trail_update(st, 1, h, lo, cl)
    assert st["stop"] == 1.04 and st["ext"] == 1.12 and st["pb_len"] == 0
    print("books selfcheck OK")


def check() -> None:
    """Print what each book sees on the newest bars (no DB writes, no orders)."""
    from .live import bars, h1_bars, windows  # noqa: PLC0415
    for sym in SYMBOLS:
        m5, h1, b = bars(sym), h1_bars(sym), d1_bars(sym)
        ev = sflow.structure(h1)
        last = ev[-1] if ev else None
        ps, cs = px.setups(px.indicators(b)), cta.signals(b, "v2")
        print(f"{sym:7} imbalance {(plan(*windows(m5), sym, IMB) or {}).get('status', '-'):9} "
              f"enigma {(fvg_strategy.plan(*windows(m5), sym, ENIGMA) or {}).get('status', '-'):9} "
              f"sflow last BOS {pd.to_datetime(h1['t'][last['i']], unit='s'):%m-%d %H:%M} {'up' if last['d'] == 1 else 'down'}"
              f"{' zone' if last['zone'] else ''} | parallax last setup {pd.to_datetime(ps.t_open.iloc[-1], unit='s'):%m-%d} "
              f"| cta last signal {pd.to_datetime(cs.t_open.iloc[-1], unit='s'):%m-%d} | conf up/down "
              f"{conf(sym, 1, m5, h1)}/{conf(sym, -1, m5, h1)}" if last is not None and len(ps) and len(cs) else f"{sym}: not enough history")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["check", "selfcheck"])
    a = ap.parse_args()
    selfcheck() if a.what == "selfcheck" else check()


if __name__ == "__main__":
    main()
