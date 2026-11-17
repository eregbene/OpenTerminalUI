"""Trade journal + weekly review (OBSERVE ONLY -- it never changes a trade or a rule).

Every closed DEMO trade (all bensim magics) gets: strategy, result in $ and R, best/worst point in R, exit reason,
context at entry (H4/H1/M15 swing trend, session, news before/during) and loss tags. Rows go to live_journal
(cumulative); the week's review is written to reports/weekly/trade_review_<monday>.md:

    python -m bensim.journal                  # last finished week (Mon-Sun UTC)
    python -m bensim.journal --week 2026-11-02

The engine starts it every Saturday from 06:00 UTC (schedule()). "Ideas to check" are only CANDIDATES: a rule goes
live only after the 5-10y backtest + untouched-period check and a shadow run, then the user's GO (user 2026-11-10).
"""
from __future__ import annotations

import argparse
import json

import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import bridge, db
from .backtest import resample
from .books import MAGICS
from .mt5_time import server_to_utc
from .strategy import pip, trend

NAMES = {770101: "fib618", 770102: "bennett", 770103: "flow", 770109: "manager", 770111: "faiz", **{v: k for k, v in MAGICS.items()}}
REASON = {0: "closed by you (terminal)", 1: "closed by you (mobile)", 2: "closed by you (web)", 3: "engine (time / Friday / kill)",
          4: "stop loss", 5: "take profit", 6: "stop-out"}
REF = {"USD": ("EURUSD", -1), "EUR": ("EURUSD", 1), "GBP": ("GBPUSD", 1), "JPY": ("USDJPY", -1), "AUD": ("AUDUSD", 1),
       "CAD": ("USDCAD", -1), "CHF": ("USDCHF", -1), "NZD": ("NZDUSD", 1)}  # currency -> pair + sign (+ = currency up)
TAGS = {
    "against_h4": "against the H4 trend at entry",
    "all_against": "H4, H1 and M15 all against at entry",
    "news_during": "high-impact news of its currencies during the trade",
    "news_before": "high-impact news 1-3 h before entry",
    "fast_stop": "stopped within 1 hour",
    "stop_then_ran": "stopped, then price went +2R our way within 48 h (stop too tight / too early)",
    "never_positive": "never got to +0.5R",
    "gave_back": "was +1R or more, ended a loss",
    "gold": "gold (XAUUSD)",
    "asia": "entered in the Asian session (before 07:00 UTC)",
}
# ideas only from facts known AT ENTRY (fast_stop / stop_then_ran / gave_back describe how a loss happened -- winners can't have them)
ENTRY_TAGS = {"against_h4", "all_against", "news_before", "gold", "asia"}
SCHEMA = """create table if not exists live_journal (position_id bigint primary key, week date, strategy text, symbol text,
    dir int, t_open bigint, t_close bigint, pnl double precision, r double precision, reason text, tags jsonb, ctx jsonb,
    created timestamptz default now())"""
OUT = Path(__file__).resolve().parent.parent / "reports" / "weekly"


def _bars(sym: str, t_from: int) -> dict:
    n = min(30000, int((time.time() - t_from) / 300) + 3000)  # + warm-up for the H4 swing trend
    rows = sorted(bridge.rates(sym, "M5", 1, n), key=lambda r: r["time"])
    t = server_to_utc(np.array([r["time"] for r in rows], dtype=np.int64))
    return {"t": t, **{k: np.array([r[k] for r in rows], dtype=float) for k in ("open", "high", "low", "close")}}


def _trend_at(b: dict, sec: int, t: int) -> int:
    x = resample(b, sec)
    j = int(np.searchsorted(x["t"] + sec, t, side="right")) - 1  # last bar closed by t
    return int(trend(x)[j]) if j >= 0 else 0


def _news(c, t0: int, t1: int) -> list[tuple]:
    return c.execute("""select extract(epoch from ts)::bigint, currency, name, actual, forecast, previous from economic_events
                        where impact='high' and ts >= to_timestamp(%s) and ts < to_timestamp(%s) order by ts""", (t0, t1)).fetchall()


def trades(t0: int, t1: int) -> list[dict]:
    """Closed positions whose LAST exit is in [t0, t1) (UTC), all bensim magics."""
    ds = [d for d in bridge.deals(t0 - 30 * 86400 + 7200, t1 + 7200 + 86400) if 770100 < (d.get("magic") or 0) < 770200]
    for d in ds:
        d["utc"] = int(server_to_utc(np.array([d["time"]], dtype=np.int64))[0])
    by = {}
    for d in ds:
        by.setdefault(d["position_id"], []).append(d)
    out = []
    for pid, g in by.items():
        ins, outs = [d for d in g if d["entry"] == 0], [d for d in g if d["entry"] in (1, 3)]
        if not ins or not outs:
            continue  # still open, or opened before the deal window
        last = max(outs, key=lambda d: d["utc"])
        if not t0 <= last["utc"] < t1:
            continue
        e = ins[0]
        vol = sum(d["volume"] for d in outs)
        px_out = sum(d["price"] * d["volume"] for d in outs) / vol
        gross = sum(d["profit"] for d in outs)
        net = gross + sum(d.get("commission", 0) + d.get("swap", 0) + d.get("fee", 0) for d in g)
        out.append({"pid": pid, "magic": e["magic"], "strategy": NAMES.get(e["magic"], str(e["magic"])), "symbol": e["symbol"],
                    "dir": 1 if e["type"] == 0 else -1, "t_open": e["utc"], "t_close": last["utc"], "entry": e["price"],
                    "exit": px_out, "vol": vol, "gross": gross, "pnl": net, "reason_code": last["reason"],
                    "reason": REASON.get(last["reason"], str(last["reason"])), "comment_in": e.get("comment", ""),
                    "comment_out": last.get("comment", "")})
    return sorted(out, key=lambda x: x["t_close"])


def enrich(tr: list[dict], c) -> None:
    cache: dict = {}
    for x in tr:
        sym, d = x["symbol"], x["dir"]
        if sym not in cache:
            cache[sym] = _bars(sym, min(y["t_open"] for y in tr if y["symbol"] == sym) - 86400 * 25)
        b = cache[sym]
        row = c.execute("select sl from live_trade_watch where ticket=%s and sl > 0 order by ts limit 1", (x["pid"],)).fetchone()
        sl = row[0] if row else None
        if sl is None and "[sl " in x["comment_out"]:
            sl = float(x["comment_out"].split("[sl ")[1].rstrip("]"))
        risk_px = abs(x["entry"] - sl) if sl else 0.0
        move = (x["exit"] - x["entry"]) * d
        usd_per_px = abs(x["gross"]) / abs(move) if abs(move) > 1e-12 else None
        x["risk_usd"] = risk_px * usd_per_px if risk_px and usd_per_px else None
        x["r"] = x["pnl"] / x["risk_usd"] if x["risk_usd"] else float("nan")
        seg = (b["t"] >= x["t_open"] - 300) & (b["t"] < x["t_close"])
        if risk_px and seg.any():
            best = (b["high"][seg].max() - x["entry"]) if d == 1 else (x["entry"] - b["low"][seg].min())
            worst = (b["low"][seg].min() - x["entry"]) if d == 1 else (x["entry"] - b["high"][seg].max())
            x["best_r"], x["worst_r"] = best / risk_px, worst / risk_px
        else:
            x["best_r"] = x["worst_r"] = float("nan")
        x["h4"], x["h1"], x["m15"] = (_trend_at(b, s, x["t_open"]) * d for s in (14400, 3600, 900))  # +1 with us, -1 against
        ccys = {"USD" if k == "XAU" else k for k in (sym[:3], sym[3:6])}
        nd = [n for n in _news(c, x["t_open"] - 1800, x["t_close"]) if n[1] in ccys]
        nb = [n for n in _news(c, x["t_open"] - 3 * 3600, x["t_open"] - 3600) if n[1] in ccys]
        x["news"] = [f"{datetime.fromtimestamp(n[0], timezone.utc):%a %H:%M} {n[1]} {n[2]}" for n in nd]
        after = (b["t"] >= x["t_close"]) & (b["t"] < x["t_close"] + 2 * 86400)
        ran = bool(risk_px and after.any() and ((b["high"][after].max() if d == 1 else -b["low"][after].min()) - d * x["entry"]) >= 2 * risk_px)
        loss = x["pnl"] < 0
        tags = {
            "against_h4": x["h4"] == -1, "all_against": x["h4"] == x["h1"] == x["m15"] == -1, "news_during": bool(nd),
            "news_before": bool(nb), "fast_stop": x["reason_code"] == 4 and x["t_close"] - x["t_open"] < 3600,
            "stop_then_ran": loss and x["reason_code"] == 4 and ran,
            "never_positive": not np.isnan(x["best_r"]) and x["best_r"] < 0.5,
            "gave_back": loss and not np.isnan(x["best_r"]) and x["best_r"] >= 1.0,
            "gold": sym == "XAUUSD", "asia": datetime.fromtimestamp(x["t_open"], timezone.utc).hour < 7,
        }
        x["tags"] = [k for k, v in tags.items() if v]


def _store(tr: list[dict], monday: str, c) -> None:
    c.execute(SCHEMA)
    for x in tr:
        ctx = {k: x[k] for k in ("entry", "exit", "vol", "risk_usd", "best_r", "worst_r", "h4", "h1", "m15", "news")}
        c.execute("""insert into live_journal (position_id, week, strategy, symbol, dir, t_open, t_close, pnl, r, reason, tags, ctx)
                     values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (position_id) do update set
                     pnl=excluded.pnl, r=excluded.r, reason=excluded.reason, tags=excluded.tags, ctx=excluded.ctx""",
                  (x["pid"], monday, x["strategy"], x["symbol"], x["dir"], x["t_open"], x["t_close"], x["pnl"],
                   None if np.isnan(x["r"]) else x["r"], x["reason"], json.dumps(x["tags"]), json.dumps(ctx, default=float)))


def tag_table(rows: list[tuple[float, list]]) -> tuple[list[str], list[str]]:
    """rows = (pnl, tags). Returns markdown lines + candidate ideas (tag on >= 25 points more of the losers, >= 5 losers)."""
    los, win = [t for p, t in rows if p < 0], [t for p, t in rows if p >= 0]
    lines, ideas = ["| Tag | Losers with it | Winners with it | Net $ of trades with it |", "|---|---|---|---|"], []
    for k, label in TAGS.items():
        nl, nw = sum(k in t for t in los), sum(k in t for t in win)
        net = sum(p for p, t in rows if k in t)
        pl, pw = (100 * nl / len(los) if los else 0), (100 * nw / len(win) if win else 0)
        if nl or nw:
            lines.append(f"| {label} | {nl}/{len(los)} ({pl:.0f}%) | {nw}/{len(win)} ({pw:.0f}%) | {net:+,.0f} |")
        if k in ENTRY_TAGS and nl >= 5 and pl - pw >= 25:
            ideas.append(f"- **{label}**: {pl:.0f}% of losers vs {pw:.0f}% of winners ({nl} losers, net {net:+,.0f} $)")
    return lines, ideas


def report(monday: str) -> Path:
    m = datetime.fromisoformat(monday).replace(tzinfo=timezone.utc)
    t0, t1 = int(m.timestamp()), int((m + timedelta(days=7)).timestamp())
    tr = trades(t0, t1)
    with db.connect(autocommit=True) as c:
        enrich(tr, c)
        _store(tr, monday, c)
        news = _news(c, t0, t1)
        allrows = c.execute("select pnl, tags, strategy from live_journal").fetchall()
    L = [f"# Weekly trade review -- week of {monday}", "",
         f"Written {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python -m bensim.journal`. Observe only: nothing here "
         "changes a trade or a rule. Ideas go through the 5-10y backtest + untouched period + shadow run, then your GO.", ""]
    net = sum(x["pnl"] for x in tr)
    wins = sum(x["pnl"] >= 0 for x in tr)
    L += ["## 1. Summary", "", f"**{len(tr)} closed trades, {wins} winners, net {net:+,.2f} $** "
          f"(R known for {sum(not np.isnan(x['r']) for x in tr)}: {np.nansum([x['r'] for x in tr]):+.2f}R).", "",
          "| Strategy | Trades | Wins | Net $ | R |", "|---|---|---|---|---|"]
    for s in sorted({x["strategy"] for x in tr}):
        g = [x for x in tr if x["strategy"] == s]
        L.append(f"| {s} | {len(g)} | {sum(x['pnl'] >= 0 for x in g)} | {sum(x['pnl'] for x in g):+,.0f} | {np.nansum([x['r'] for x in g]):+.2f} |")
    L += ["", "## 2. Every trade", "", "| Closed (UTC) | Strategy | Pair | Side | $ | R | Best / worst R | Exit | H4/H1/M15 at entry | Tags |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    arrow = {1: "with", -1: "against", 0: "-"}
    for x in tr:
        L.append(f"| {datetime.fromtimestamp(x['t_close'], timezone.utc):%a %d %H:%M} | {x['strategy']} | {x['symbol']} | "
                 f"{'BUY' if x['dir'] == 1 else 'SELL'} | {x['pnl']:+,.0f} | {x['r']:+.2f} | {x['best_r']:+.1f} / {x['worst_r']:+.1f} | "
                 f"{x['reason']} | {arrow[x['h4']]} / {arrow[x['h1']]} / {arrow[x['m15']]} | {', '.join(x['tags']) or '-'} |")
    lines, ideas = tag_table([(x["pnl"], x["tags"]) for x in tr])
    L += ["", "## 3. What the losers have in common (this week)", ""] + lines
    L += ["", "## 4. Last week's news (high impact) and how price reacted", "",
          "| Time (UTC) | Ccy | Event | Actual / forecast / previous | Ccy move 1 h after | Our trades open then |", "|---|---|---|---|---|---|"]
    cache: dict = {}
    for ts, cur, name, act, fc, prev in news:
        if cur not in REF:
            continue
        sym, sgn = REF[cur]
        try:
            b = cache.get(sym) or cache.setdefault(sym, _bars(sym, t0))
            k0 = int(np.searchsorted(b["t"], ts)) - 1
            k1 = int(np.searchsorted(b["t"], ts + 3600)) - 1
            mv = (b["close"][k1] - b["close"][k0]) * sgn / pip(sym) if 0 <= k0 < k1 < len(b["t"]) else float("nan")
        except Exception:
            mv = float("nan")
        opn = [f"{x['strategy']} {x['symbol']} {'BUY' if x['dir'] == 1 else 'SELL'} ({x['pnl']:+,.0f})" for x in tr
               if x["t_open"] <= ts < x["t_close"] and cur in {"USD" if k == "XAU" else k for k in (x["symbol"][:3], x["symbol"][3:6])}]
        L.append(f"| {datetime.fromtimestamp(ts, timezone.utc):%a %d %H:%M} | {cur} | {name} | {act or '-'} / {fc or '-'} / {prev or '-'} | "
                 f"{mv:+.0f} pips ({sym}) | {', '.join(opn) or '-'} |")
    cum = [(p, t) for p, t, _ in allrows]
    cl, cideas = tag_table(cum)
    L += ["", f"## 5. All journaled trades so far ({len(cum)}, net {sum(p for p, _ in cum):+,.0f} $)", ""] + cl
    L += ["", "## 6. Ideas to check (candidates only)", ""]
    L += (["This week:"] + ideas if ideas else ["This week: none (no entry-time fact on >= 25 points more of the losers, with >= 5 losers)."])
    L += [""] + (["All trades so far:"] + cideas if cideas else ["All trades so far: none yet."])
    L += ["", "A candidate is only a question: one week is ~20-40 trades and at a 25-35% win rate 6 losses in a row happen by chance. "
          "Next step for any candidate = test it on the 5-10y backtest trades and the untouched period."]
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"trade_review_{monday}.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path


def schedule(state, log) -> None:
    """Engine hook: every Saturday from 06:00 UTC, review the finished week once, in a background process."""
    t = datetime.now(timezone.utc)
    if t.weekday() != 5 or t.hour < 6:
        return
    monday = (t - timedelta(days=5)).date().isoformat()
    if state("journal_week") == monday:
        return
    state("journal_week", monday)
    root = Path(__file__).resolve().parent.parent
    with open(root / "logs" / "journal.log", "a") as out:
        subprocess.Popen([sys.executable, "-m", "bensim.journal", "--week", monday], stdout=out, stderr=subprocess.STDOUT,
                         cwd=root, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log("INFO", f"JOURNAL weekly review started for the week of {monday} (reports/weekly)")


def _selfcheck() -> None:
    lines, ideas = tag_table([(-1, ["gold"])] * 5 + [(1, [])] * 5)
    assert ideas and "gold" in ideas[0] and len(lines) == 3
    assert not tag_table([(-1, ["fast_stop"])] * 5 + [(1, [])] * 5)[1]  # outcome tags never become ideas
    assert not tag_table([(-1, ["gold"])] * 4 + [(1, [])])[1]  # < 5 losers -> no idea


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", help="Monday (YYYY-MM-DD); default = last finished week")
    a = ap.parse_args()
    _selfcheck()
    today = datetime.now(timezone.utc).date()
    wk = a.week or (today - timedelta(days=today.weekday() + 7)).isoformat()
    print(report(wk))

