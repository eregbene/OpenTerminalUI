"""Time machine (idea from github.com/Jakeschincariol/day-trader-skill, rebuilt for MT5 forex): replay the rest of a
trade 10,000 times and report target first / stop first / still open at the Friday close.

    python -m bensim.timemachine run EURUSD SELL 1.1120 1.1150 1.1060            # entry sl tp, from now
    python -m bensim.timemachine run EURUSD SELL 1.1120 1.1150 1.1060 --at "2026-10-29 10:00"
    python -m bensim.timemachine open                                            # every open bensim position

Rules (frozen 2026-10-29, before any result was seen):
  * pool: M5 candles (DB, UTC, bid) of the last --weeks weeks before the start, cut into 1-hour blocks of 12 bars keyed
    by hour-of-week; each bar stored as open/high/low/close over the previous close (Monday blocks keep the weekend gap);
    each hour-of-week's average drift per bar is removed (fix 2026-10-29: else replays carry the trend + hour habits)
  * one replay: from the start, every following hour is a random past block of the SAME hour-of-week, scaled to the
    current price; ends at Friday 20:00 UTC (bensim's Friday close)
  * exits as the backtest: long on bid, short on ask (bid + backtest spread model); a bar touching both = stop;
    a gap through the stop counts as -1R (real fills can be worse)
  * R = (exit - entry) * dir / |entry - sl|; open at the end -> the replay's price then
It is a NULL model: past hours are shuffled, so direction/memory is gone. It cannot show an edge -- it shows how often
plain noise of this hour/week reaches the stop or the target, and how long the trade is likely to stay open.
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from .backtest import Exec, _spread, load_m5

FRIDAY_CLOSE_UTC = 20
MAGICS = range(770101, 770110)


def blocks(b: dict[str, np.ndarray]) -> dict[int, np.ndarray]:
    """hour-of-week -> (n_blocks, 12, 4) ratios of o/h/l/c to the previous close."""
    t, prev = b["t"], np.r_[np.nan, b["close"][:-1]]
    r = np.stack([b[k] / prev for k in ("open", "high", "low", "close")], axis=1)
    hour = t // 3600
    out: dict[int, list] = {}
    for h in np.unique(hour):
        i = np.flatnonzero(hour == h)
        if len(i) != 12 or i[0] == 0 or np.any(np.isnan(r[i])):
            continue
        out.setdefault(int((h // 24 + 3) % 7 * 24 + h % 24), []).append(r[i])  # 1970-01-01 = Thursday -> Monday = 0
    # remove each hour-of-week's average drift per bar (arithmetic, so a replayed price has no expected move);
    # else the replays keep the last N weeks' trend and hour-of-day habits -- direction the null model must not have
    return {k: v / np.mean(v[:, :, 3]) for k, v in ((k, np.array(v)) for k, v in out.items())}


def bars_by_slot(b: dict[str, np.ndarray], window: int = 2) -> dict[int, np.ndarray]:
    """REPO MODE (their method as published): 5-minute-of-day slot -> (n, 1, 4) single-bar ratios from that slot +/-
    `window` bars, drift KEPT (the pool's recent direction stays in the replays)."""
    prev = np.r_[np.nan, b["close"][:-1]]
    r = np.stack([b[k] / prev for k in ("open", "high", "low", "close")], axis=1)[:, None, :]
    slot = (b["t"] % 86400) // 300
    ok = ~np.isnan(r).any(axis=(1, 2))
    return {s: r[ok & np.isin(slot, [(s + w) % 288 for w in range(-window, window + 1)])] for s in range(288)}


def friday_close(t0: int) -> int:
    d = pd.Timestamp(t0, unit="s", tz="UTC")
    fri = (d + pd.Timedelta(days=(4 - d.weekday()) % 7)).normalize() + pd.Timedelta(hours=FRIDAY_CLOSE_UTC)
    return int(fri.timestamp()) if fri.timestamp() > t0 else int((fri + pd.Timedelta(days=7)).timestamp())


def replay(pool: dict[int, np.ndarray], sym: str, d: int, entry: float, sl: float, tp: float, price: float, t0: int,
           until: int, n: int = 10_000, seed: int = 19, unit: int = 3600, ex: Exec | None = None) -> dict:
    """Vectorised replays from `price` at time t0 (first bar = the M5 bar starting at t0, rounded down).
    unit 3600 = hour-of-week blocks (default pool), 300 = 5-minute-of-day single bars (repo mode pool)."""
    rng, ex = np.random.default_rng(seed), ex or Exec()  # ex: the spread model (R2 passes the trades' own profile)
    key = (lambda t: int(((t // 3600) // 24 + 3) % 7 * 24 + (t // 3600) % 24)) if unit == 3600 else \
        (lambda t: int((t % 86400) // 300))
    risk = abs(entry - sl)
    p = np.full(n, price)
    res = np.full(n, np.nan)  # R once closed
    hit = np.zeros(n, np.int8)  # 1 target, -1 stop, 0 open
    first_hour_stop = np.zeros(n, bool)
    t = t0 - t0 % 300
    while t < until and (hit == 0).any():
        blk = pool.get(key(t))
        if blk is None or not len(blk):  # slot never seen in the pool (holiday-thin): price stands still
            t += unit - t % unit
            continue
        draw = blk[rng.integers(0, len(blk), n)]  # (n, bars per unit, 4)
        for k in range((t % unit) // 300, unit // 300):
            if t >= until:
                break
            o, h, l, c = (draw[:, k, j] * p for j in range(4))
            sp = _spread(sym, t, ex) if d == -1 else 0.0
            live = hit == 0
            stop = live & ((l <= sl) if d == 1 else (h + sp >= sl))
            targ = live & ~stop & ((h >= tp) if d == 1 else (l + sp <= tp))
            hit[stop], res[stop] = -1, -1.0
            hit[targ], res[targ] = 1, (tp - entry) * d / risk
            first_hour_stop |= stop & (t < t0 + 3600)
            p = np.where(hit == 0, c, p)
            t += 300
    still = hit == 0
    res[still] = ((p[still] + (_spread(sym, until, ex) if d == -1 else 0.0)) - entry) * d / risk
    return {"n": n, "seed": seed, "target": float((hit == 1).mean()), "stop": float((hit == -1).mean()),
            "open": float(still.mean()), "stop_1h": float(first_hour_stop.mean()), "avg_r": float(res.mean()),
            "target_r": (tp - entry) * d / risk}


def verdict(r: dict) -> str:
    """Their wording: among replays that hit a side, target share vs the break-even share 1/(1+R)."""
    sides = r["target"] + r["stop"]
    if sides == 0:
        return "No replay reached the stop or the target before the Friday close."
    need, got = 1 / (1 + r["target_r"]), r["target"] / sides
    gap = (got - need) * 100
    side = "target" if gap > 0 else "stop"
    if abs(gap) < 5:
        word = "Close to a coin flip" if abs(r["target_r"] - 1) < 0.05 else "Close to break-even"
    else:
        word = f"{'Leans' if abs(gap) < 15 else 'Strongly'} toward the {side}"
    return f"{word}: target first {got:.1%} of the replays that hit a side, break-even needs {need:.1%}."


def run(sym: str, d: int, entry: float, sl: float, tp: float, t0: int, price: float | None = None, weeks: int = 26,
        n: int = 10_000, seed: int = 19, repo: bool = False) -> dict:
    """repo=True: their method -- last 60 days, single 5-minute bars by time of day +/- 2, direction kept."""
    assert (entry - sl) * d > 0 and (tp - entry) * d > 0, "SL/TP on the wrong side of the entry"
    span = 60 * 86400 if repo else weeks * 7 * 86400
    lo = pd.Timestamp(t0 - span, unit="s", tz="UTC").isoformat()
    hi = pd.Timestamp(t0, unit="s", tz="UTC").isoformat()
    m5 = load_m5(sym, lo, hi)
    pool = bars_by_slot(m5) if repo else blocks(m5)
    assert len(m5["t"]) >= 5000 and pool, f"{sym}: too few M5 bars before the start"
    return replay(pool, sym, d, entry, sl, tp, entry if price is None else price, t0, friday_close(t0), n, seed,
                  unit=300 if repo else 3600) | {"mode": "repo (60 days, direction kept)" if repo else
                                                  "bensim (26 weeks, direction removed)"}


def show(title: str, r: dict) -> None:
    print(f"{title}\n  [{r['mode']}] {r['n']:,} replays to the Friday close, seed {r['seed']}, "
          f"target = {r['target_r']:+.2f}R")
    print(f"  target first {r['target']:6.1%}   stop first {r['stop']:6.1%}   still open {r['open']:6.1%}")
    print(f"  stop inside the first hour {r['stop_1h']:.1%}   average R {r['avg_r']:+.3f}")
    print(f"  {verdict(r)}")
    if r["open"] > 0.4:
        print(f"  {r['open']:.0%} still open at the Friday close: the price then decides those (average R above).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run", "open"])
    ap.add_argument("args", nargs="*", help="run: SYMBOL BUY|SELL ENTRY SL TP")
    ap.add_argument("--at", help="UTC start, default now")
    ap.add_argument("--weeks", type=int, default=26)
    ap.add_argument("--replays", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--repo", action="store_true", help="their method: 60 days, 5-min bars by time of day, direction kept")
    a = ap.parse_args()
    t0 = int(pd.Timestamp(a.at, tz="UTC").timestamp()) if a.at else int(time.time())
    if a.mode == "run":
        sym, side, entry, sl, tp = a.args[0].upper(), a.args[1].upper(), *map(float, a.args[2:5])
        show(f"{sym} {side} {entry} sl {sl} tp {tp}", run(sym, 1 if side == "BUY" else -1, entry, sl, tp, t0,
                                                            weeks=a.weeks, n=a.replays, seed=a.seed, repo=a.repo))
        return
    from . import bridge
    for p in bridge.positions():
        if p["magic"] not in MAGICS or not p["sl"] or not p["tp"]:
            continue
        d = 1 if p["type"] == 0 else -1
        try:
            r = run(p["symbol"], d, p["price_open"], p["sl"], p["tp"], t0, price=p["price_current"], weeks=a.weeks, repo=a.repo,
                    n=a.replays, seed=a.seed)
        except AssertionError as e:
            print(f"#{p['ticket']} {p['symbol']}: {e}")
            continue
        show(f"#{p['ticket']} {p['symbol']} {'BUY' if d == 1 else 'SELL'} magic {p['magic']} @ {p['price_open']} "
             f"now {p['price_current']} sl {p['sl']} tp {p['tp']}", r)


if __name__ == "__main__":
    main()
