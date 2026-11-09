"""Live vs backtest reconciliation (approved 2026-11-03, READ-ONLY: DB selects, bridge /deals + /positions, backtest replay).

    python -m bensim.reconcile            # 2026-10-13 06:00 -> 2026-11-03 12:00 UTC (DB candle end)
-> reports/reconciliation_2026-11-03/{positions.csv, signals.csv, REPORT.md}

1. LIVE positions from MT5 deals (grouped by position_id): magic -> book, open/close time+price, lots, profit,
   commission, swap, exit reason (MT5 deal reason 3 expert, 4 SL, 5 TP, 0/1 client / mobile), planned risk from the
   engine's own decision record -> live R = (profit + commission + swap) / planned risk.
2. BACKTEST with the exact live rules: live.PARAMS on live.LIVE_SYMBOLS, bennett_strategy.LIVE on live.BN_SYMBOLS,
   books.IMB / books.ENIGMA on books.SYMBOLS; exec_profile("observed_account") (Friday 20:00 close, observed costs).
   Each strategy independent (its own one-trade-per-symbol), then compared with what live did.
3. MATCH on (book, symbol, dir, signal_time). For a backtest signal live skipped or never recorded, the actual live
   occupancy at that moment (every bensim position from the deals) says whether the pair slot / six-position limit
   explains it. sflow / parallax / cta / flow / manager have no shared-code backtest -> live-only rows (occupancy).
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import bennett_strategy as bstrat
from . import books, bridge, db, live, registry
from .backtest import exec_profile, run_symbol
from .mt5_time import server_to_utc

START, END = "2026-10-13T06:00:00+00:00", "2026-11-03T12:00:00+00:00"
OUT = Path("reports/reconciliation_2026-11-03")
BOOK = {770101: "fib618", 770102: "bennett", 770103: "flow", 770104: "imbalance", 770105: "enigma", 770106: "sflow",
        770107: "parallax", 770108: "cta", 770109: "manager"}
REASON = {0: "client (terminal)", 1: "client (mobile)", 2: "client (web)", 3: "engine/expert", 4: "SL", 5: "TP", 6: "stop-out"}
T0, T1 = int(pd.Timestamp(START).timestamp()), int(pd.Timestamp(END).timestamp())


def utc(server_t) -> int:
    return int(server_to_utc(np.array([int(server_t)], dtype=np.int64))[0])


def live_positions() -> pd.DataFrame:
    deals = bridge.deals(T0 - 7 * 86400, int(pd.Timestamp.now(tz="UTC").timestamp()) + 3600)
    pos: dict[int, dict] = {}
    for d in deals:
        if not d.get("symbol") or d.get("type") not in (0, 1):
            continue
        x = pos.setdefault(d["position_id"], {"ins": [], "outs": []})
        (x["ins"] if d["entry"] == 0 else x["outs"]).append(d)
    open_now = {p["ticket"]: p for p in bridge.positions()}
    rows = []
    for pid, x in pos.items():
        if not x["ins"]:
            continue
        i = x["ins"][0]
        vol_out = sum(o["volume"] for o in x["outs"])
        px_out = sum(o["price"] * o["volume"] for o in x["outs"]) / vol_out if vol_out else None
        last = max(x["outs"], key=lambda o: o["time"]) if x["outs"] else None
        op = open_now.get(pid)
        rows.append({"position_id": pid, "magic": i["magic"], "book": BOOK.get(i["magic"], f"unknown magic {i['magic']}"),
                     "symbol": i["symbol"], "dir": 1 if i["type"] == 0 else -1, "lots": i["volume"], "comment_in": i["comment"],
                     "t_open": utc(i["time"]), "price_open": i["price"],
                     "t_close": utc(last["time"]) if last and not op else None, "price_close": px_out if not op else None,
                     "exit_reason": (REASON.get(last["reason"], last["reason"]) + (f" ({last['comment']})" if last["comment"] else "")) if last and not op else "OPEN",
                     "profit": sum(o["profit"] for o in x["outs"]) + (op["profit"] if op else 0.0),
                     "commission": sum(d["commission"] + d.get("fee", 0) for d in x["ins"] + x["outs"]),
                     "swap": sum(o["swap"] for o in x["outs"]) + (op["swap"] if op else 0.0),
                     "unrealized": bool(op)})
    p = pd.DataFrame(rows)
    return p[(p.t_open >= T0) | (p.t_close.fillna(1e18) >= T0)].sort_values("t_open").reset_index(drop=True)


def decisions() -> pd.DataFrame:
    with db.connect() as c:
        f = c.execute("select 'fib618', symbol, signal_time, dir, decision, detail, extract(epoch from created)::bigint from live_signals").fetchall()
        b = c.execute("select 'bennett', symbol, signal_time, dir, decision, detail, extract(epoch from created)::bigint from live_signals_bennett").fetchall()
        k = c.execute("select book, symbol, signal_time, dir, status, extra->'decision_detail', extract(epoch from created)::bigint, ticket, kind from live_books").fetchall()
    rows = [dict(book=a, symbol=s, signal_time=int(t), dir=int(d), decision=dec, detail=det or {}, created=int(cr), ticket=(det or {}).get("result", {}).get("order"), kind="market")
            for a, s, t, d, dec, det, cr in f + b]
    rows += [dict(book=a, symbol=s, signal_time=int(t), dir=int(d), decision=dec, detail=det or {}, created=int(cr), ticket=tk, kind=kd)
             for a, s, t, d, dec, det, cr, tk, kd in k]
    return pd.DataFrame(rows)


def _bt(a):
    name, sym, p, kind = a
    t = run_symbol((sym, START, END, p, exec_profile("observed_account"), kind))
    return t.assign(book=name) if not t.empty else t


def backtest() -> pd.DataFrame:
    jobs = [("fib618", s, live.PARAMS, "imbalance") for s in live.LIVE_SYMBOLS]
    jobs += [("bennett", s, bstrat.LIVE, "bennett") for s in live.BN_SYMBOLS]
    jobs += [("imbalance", s, books.IMB, "imbalance") for s in books.SYMBOLS]
    jobs += [("enigma", s, books.ENIGMA, "htf_fvg") for s in books.SYMBOLS]
    with ProcessPoolExecutor(3) as pool:
        fr = [f for f in pool.map(_bt, jobs) if not f.empty]
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def occupancy(pos: pd.DataFrame, t: int, sym: str) -> tuple[list[str], int]:
    """Bensim positions open at time t: books holding `sym`, and the total count (guard MAX_OPEN = 6)."""
    bensim = pos[pos.magic.between(770101, 770199)]
    o = bensim[(bensim.t_open <= t) & (bensim.t_close.fillna(1e18) > t)]
    return list(o[o.symbol == sym].book), int(len(o))


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    OUT.mkdir(parents=True, exist_ok=True)
    pos, dec, bt = live_positions(), decisions(), backtest()
    dec = dec[(dec.created >= T0) & (dec.created < T1)]
    risk = {int(r.ticket): r.detail.get("risk_usd") for r in dec.itertuples() if pd.notna(r.ticket) and r.ticket and isinstance(r.detail, dict)}
    pos["planned_risk_usd"] = pos.position_id.map(risk)
    pos["net_usd"] = pos.profit + pos.commission + pos.swap
    pos["live_r"] = pos.net_usd / pos.planned_risk_usd
    pos.to_csv(OUT / "positions.csv", index=False)
    sent = {(r.book, r.symbol, r.dir, r.signal_time): r for r in dec.itertuples()}
    pos_by_ticket = {int(p.position_id): p for p in pos.itertuples()}
    rows, seen = [], set()
    for x in bt.itertuples():
        key = (x.book, x.symbol, int(x.dir), int(x.t_signal))
        lv = sent.get(key)
        hold, n_open = occupancy(pos, int(x.t_entry) - 1, x.symbol)
        row = {"book": x.book, "symbol": x.symbol, "dir": int(x.dir), "signal_time": pd.to_datetime(int(x.t_signal), unit="s", utc=True),
               "bt_entry_time": pd.to_datetime(int(x.t_entry), unit="s", utc=True), "bt_entry": x.entry, "bt_sl": x.sl, "bt_tp": x.tp,
               "bt_exit_time": pd.to_datetime(int(x.t_exit), unit="s", utc=True), "bt_exit": x.exit, "bt_exit_reason": x.exit_reason,
               "bt_r": round(float(x.r), 3), "live_decision": lv.decision if lv is not None else "NO LIVE RECORD",
               "slot_held_by_at_entry": ",".join(hold), "bensim_open_at_entry": n_open}
        if lv is not None:
            seen.add(key)
            p = pos_by_ticket.get(int(lv.ticket)) if pd.notna(lv.ticket) and lv.ticket else None
            if p is not None:
                row |= {"live_open_time": pd.to_datetime(p.t_open, unit="s", utc=True), "live_entry": p.price_open, "live_lots": p.lots,
                        "planned_lots": lv.detail.get("lots"), "quote_at_decision": lv.detail.get("price"),
                        "entry_diff_pips_vs_bt": round((p.price_open - x.entry) * int(x.dir) * -1 / (0.01 if "JPY" in x.symbol else (0.1 if x.symbol == "XAUUSD" else 0.0001)), 2),
                        "live_exit_time": pd.to_datetime(p.t_close, unit="s", utc=True) if p.t_close else None, "live_exit": p.price_close,
                        "live_exit_reason": p.exit_reason, "live_net_usd": round(p.net_usd, 2), "live_r": round(p.live_r, 3) if p.live_r == p.live_r else None,
                        "live_swap_usd": p.swap, "live_commission_usd": p.commission}
        rows.append(row)
    for r in dec.itertuples():  # live signals of the 4 backtested books the backtest did not produce
        key = (r.book, r.symbol, r.dir, r.signal_time)
        if r.book in ("fib618", "bennett", "imbalance", "enigma") and key not in seen:
            p = pos_by_ticket.get(int(r.ticket)) if pd.notna(r.ticket) and r.ticket else None
            rows.append({"book": r.book, "symbol": r.symbol, "dir": r.dir, "signal_time": pd.to_datetime(r.signal_time, unit="s", utc=True),
                         "live_decision": r.decision, "bt_exit_reason": "NOT IN BACKTEST",
                         **({"live_entry": p.price_open, "live_net_usd": round(p.net_usd, 2), "live_r": round(p.live_r, 3) if p.live_r == p.live_r else None,
                             "live_exit_reason": p.exit_reason} if p is not None else {})})
    sig = pd.DataFrame(rows).sort_values("signal_time")
    sig.to_csv(OUT / "signals.csv", index=False)
    registry.append({"kind": "live/backtest reconciliation (read-only)", "period": [START, END], "code": registry.code_version(),
                     "command": "python -m bensim.reconcile", "outputs": {"dir": str(OUT)},
                     "counts": {"live_positions": int(len(pos)), "backtest_trades": int(len(bt)), "signal_rows": int(len(sig))}})
    print(len(pos), "live positions;", len(bt), "backtest trades;", len(sig), "signal rows ->", OUT)


if __name__ == "__main__":
    main()
