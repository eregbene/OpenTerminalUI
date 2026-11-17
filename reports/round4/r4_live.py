"""Round 4 Part 1 + Part 7: reconstruct the REAL DEMO performance per strategy from MT5 deals, and the real risk.
Read-only (bridge GET /deals, /positions, /account, /tick; Postgres SELECT). Nothing is sent or changed.

Attribution: every deal carries position_id; the position's OPENING deal (entry 0) carries the magic + comment the
engine set, so a manual close (MT5 files it with magic 0, reason CLIENT/MOBILE/WEB) inherits the strategy of its
opening deal. Flow ML vs Flow AM (both magic 770103) are split by the opening comment ("flowml" / "flowam") and
cross-checked against live_flow (kind 'ml' / 'am', order id in detail). Books are cross-checked against
live_books.ticket, fib618 against live_signals (order in detail). An opening deal with magic 0 (user-opened) or a
position whose open deal is missing from the window -> REVIEW.
Per position: one row (never one per deal): open, partial closes, final close, exit type, P&L = profit + commission
+ swap + fee over ALL its deals, holding time; MAE/MFE (R) from live_trade_watch 5-min snapshots.
    python reports/round4/r4_live.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
sys.path.insert(1, "D:/Devops/trading/bensim_phase20_dev")
from bensim import bridge, db  # noqa: E402
from bensim.mt5_time import server_to_utc  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round4/"
LIVE_FROM = int(pd.Timestamp("2026-10-10", tz="UTC").timestamp())
MAGIC = {770101: "fib618", 770102: "bennett", 770104: "imbalance", 770105: "enigma", 770106: "sflow", 770107: "parallax",
         770108: "cta", 770109: "manager", 770200: "demo_execution_test"}
REASON = {0: "MANUAL_DESKTOP", 1: "MANUAL_MOBILE", 2: "MANUAL_WEB", 3: "ENGINE", 4: "SL", 5: "TP", 6: "STOP_OUT", 7: "ROLLOVER",
          8: "VMARGIN", 9: "SPLIT"}
FLOW_SL_BP = {"flow_ml": 145.0, "flow_am": 68.0}  # disaster stops (audit §3)


def strategy_of(open_deal: dict) -> str:
    m, c = open_deal.get("magic") or 0, (open_deal.get("comment") or "").lower()
    if m == 770103:
        return "flow_am" if c.startswith("flowam") else ("flow_ml" if c.startswith("flowml") else "flow_REVIEW")
    return MAGIC.get(m, "REVIEW_magic_%d" % m)


def db_refs() -> dict:
    refs = defaultdict(set)
    with db.connect() as c:
        for kind, det in c.execute("select kind, detail from live_flow where decision='SENT'").fetchall():
            d = det if isinstance(det, dict) else json.loads(det or "{}")
            o = (d.get("result") or {}).get("order")
            if o:
                refs[int(o)].add(f"live_flow:{kind}")
        for book, ticket in c.execute("select book, ticket from live_books where ticket is not null").fetchall():
            refs[int(ticket)].add(f"live_books:{book}")
        for tbl, name in (("live_signals", "fib618"), ("live_signals_bennett", "bennett")):
            for (det,) in c.execute(f"select detail from {tbl} where decision='SENT'").fetchall():
                d = det if isinstance(det, dict) else json.loads(det or "{}")
                o = (d.get("result") or {}).get("order")
                if o:
                    refs[int(o)].add(f"{tbl}:{name}")
        for ticket, action in c.execute("select ticket, action from manager_actions where ticket is not null").fetchall():
            refs[int(ticket)].add("manager_actions")
        tw = pd.DataFrame(c.execute("select ticket, ts, symbol, magic, dir, entry, sl, r_now from live_trade_watch").fetchall(),
                          columns=["ticket", "ts", "symbol", "magic", "dir", "entry", "sl", "r_now"])
    return {"refs": refs, "watch": tw}


def main() -> None:
    now = int(time.time())
    deals = [d for d in bridge.deals(LIVE_FROM - 86400 * 3, now + 7200) if d.get("type") in (0, 1)]  # buys / sells only
    ref = db_refs()
    W = ref["watch"]
    by_pos = defaultdict(list)
    for d in deals:
        by_pos[int(d["position_id"])].append(d)
    open_now = {int(p["ticket"]) for p in bridge.positions()}
    rows = []
    for pid, ds in by_pos.items():
        ds = sorted(ds, key=lambda d: d["time_msc"])
        opens = [d for d in ds if d["entry"] == 0]
        closes = [d for d in ds if d["entry"] in (1, 3)]
        if not opens:
            rows.append({"position_id": pid, "strategy": "REVIEW_no_open_deal_in_window", "status": "review"})
            continue
        o = opens[0]
        strat = strategy_of(o)
        dbs = sorted(ref["refs"].get(pid, set()) | ref["refs"].get(int(o["order"]), set()))
        agree = any(strat.replace("flow_", "") in s or (strat == "fib618" and "live_signals:" in s) or
                    (strat == "bennett" and "bennett" in s) for s in dbs) if dbs else None
        side = 1 if o["type"] == 0 else -1
        t_open = int(server_to_utc(np.array([o["time"]], dtype=np.int64))[0])
        vol_in = sum(d["volume"] for d in opens)
        pnl = sum(d["profit"] + d["commission"] + d["swap"] + d.get("fee", 0.0) for d in ds)
        status = "open" if pid in open_now or not closes else ("closed" if abs(sum(d["volume"] for d in closes) - vol_in) < 1e-6 else "partial")
        reasons = [REASON.get(d["reason"], str(d["reason"])) for d in closes]
        manual = any(r.startswith("MANUAL") for r in reasons)
        exit_type = "OPEN" if not closes else ("MANUAL" if manual else reasons[-1])
        t_close = int(server_to_utc(np.array([closes[-1]["time"]], dtype=np.int64))[0]) if closes else None
        avg_exit = (sum(d["price"] * d["volume"] for d in closes) / sum(d["volume"] for d in closes)) if closes else None
        w = W[W.ticket == pid]
        sl = float(w.sl.iloc[0]) if len(w) and w.sl.iloc[0] else None
        if sl is None and strat in FLOW_SL_BP:
            sl = o["price"] * (1 - side * FLOW_SL_BP[strat] / 1e4)
        risk_px = abs(o["price"] - sl) if sl else None
        r_gross = (side * (avg_exit - o["price"]) / risk_px) if (closes and risk_px) else None
        gross_px_pnl = sum(d["profit"] for d in closes)
        risk_usd = abs(gross_px_pnl / r_gross) if (r_gross and abs(r_gross) > 1e-6) else None
        rows.append({"position_id": pid, "strategy": strat, "db_refs": ";".join(dbs), "db_agrees": agree, "symbol": o["symbol"], "side": side,
                     "open_utc": pd.Timestamp(t_open, unit="s"), "close_utc": pd.Timestamp(t_close, unit="s") if t_close else None,
                     "hold_h": round((t_close - t_open) / 3600, 2) if t_close else None, "volume": vol_in, "entry": o["price"], "exit_avg": avg_exit,
                     "sl_initial": sl, "n_deals": len(ds), "n_closes": len(closes), "partial_exits": max(0, len(closes) - 1),
                     "exit_reasons": "|".join(reasons), "exit_type": exit_type, "manual_close": manual,
                     "profit": round(sum(d["profit"] for d in ds), 2), "commission": round(sum(d["commission"] for d in ds), 2),
                     "swap": round(sum(d["swap"] for d in ds), 2), "fee": round(sum(d.get("fee", 0.0) for d in ds), 2), "pnl_net": round(pnl, 2),
                     "r_gross_price": round(r_gross, 3) if r_gross is not None else None, "risk_usd_est": round(risk_usd, 2) if risk_usd else None,
                     "r_net": round(pnl / risk_usd, 3) if risk_usd else None,
                     "mfe_r": round(float(w.r_now.max()), 3) if len(w) else None, "mae_r": round(float(w.r_now.min()), 3) if len(w) else None,
                     "status": status, "open_comment": o.get("comment", "")})
    P = pd.DataFrame(rows).sort_values("open_utc")
    P.to_csv(OUT + "r4_positions.csv", index=False)
    C = P[P.status == "closed"]
    review = P[P.strategy.str.startswith("REVIEW") | P.strategy.str.endswith("REVIEW") | (P.db_agrees == False)]  # noqa: E712
    summ = {}
    for s, g in C.groupby("strategy"):
        g = g.sort_values("close_utc")
        eq = g.pnl_net.cumsum()
        pos, neg = g.pnl_net[g.pnl_net > 0].sum(), -g.pnl_net[g.pnl_net < 0].sum()
        byp = g.groupby("symbol").pnl_net.sum().sort_values()
        summ[s] = {"positions": int(len(g)), "net_usd": round(float(g.pnl_net.sum()), 2), "win": round(float((g.pnl_net > 0).mean()), 3),
                   "exp_usd": round(float(g.pnl_net.mean()), 2), "exp_r_net": round(float(g.r_net.mean()), 3) if g.r_net.notna().any() else None,
                   "pf": round(float(pos / neg), 2) if neg else None, "max_dd_usd": round(float((eq - eq.cummax()).min()), 2),
                   "avg_hold_h": round(float(g.hold_h.mean()), 1), "median_mfe_r": g.mfe_r.median(), "median_mae_r": g.mae_r.median(),
                   "costs_usd": {"commission": round(float(g.commission.sum()), 2), "swap": round(float(g.swap.sum()), 2), "fee": round(float(g.fee.sum()), 2)},
                   "exits": g.exit_type.value_counts().to_dict(), "manual_closes": int(g.manual_close.sum()),
                   "manual_close_pnl": round(float(g.pnl_net[g.manual_close].sum()), 2), "engine_only_pnl": round(float(g.pnl_net[~g.manual_close].sum()), 2),
                   "best_pair": f"{byp.index[-1]} {byp.iloc[-1]:+.0f}", "worst_pair": f"{byp.index[0]} {byp.iloc[0]:+.0f}",
                   "first": str(g.open_utc.min())[:10], "last": str(g.close_utc.max())[:10]}
    acct = bridge.account()
    res = {"window_from": "2026-10-10", "deals": len(deals), "positions": int(len(P)), "closed": int(len(C)),
           "open": int((P.status == "open").sum()), "partial": int((P.status == "partial").sum()), "manual_closed_positions": int(C.manual_close.sum()),
           "manual_closed_pnl": round(float(C.pnl_net[C.manual_close].sum()), 2), "review": review[["position_id", "strategy", "symbol", "db_refs", "open_comment"]].to_dict(orient="records"),
           "by_strategy": summ, "account": {k: acct.get(k) for k in ("balance", "equity", "currency", "leverage")},
           "total_closed_pnl": round(float(C.pnl_net.sum()), 2)}
    # ---- Part 7: real money at risk of the fixed-lot flow books, at today's prices (pip value from skills.cost)
    from skills import cost, instrument
    specs = instrument.load()
    px = {}
    for s in ("EURUSD", "USDJPY", "USDCAD", "EURJPY", "CADJPY", "GBPUSD", "USDCHF", "AUDUSD", "XAUUSD"):
        tk = bridge.tick(s)
        px[s] = (tk.get("bid", 0) + tk.get("ask", 0)) / 2 if tk else None
    rates = {k: v for k, v in px.items() if v}
    eq = float(acct.get("equity") or 0)
    flow = {}
    for name, sym, lots, bp in [("flow_ml", s, 0.8, 145.0) for s in ("EURUSD", "USDJPY", "USDCAD", "EURJPY", "CADJPY")] + [("flow_am", "EURUSD", 2.5, 68.0)]:
        pv, st = cost.pip_value_per_lot(specs[sym], "USD", rates)
        stop_pips = px[sym] * bp / 1e4 / specs[sym].pip if px.get(sym) else None
        usd = stop_pips * pv * lots if (pv and stop_pips) else None
        flow[f"{name}|{sym}"] = {"lots": lots, "disaster_stop_bp": bp, "stop_pips": round(stop_pips, 1) if stop_pips else None,
                                 "usd_at_stop": round(usd, 0) if usd else None, "pct_equity": round(usd / eq * 100, 2) if (usd and eq) else None,
                                 "configured_0.5pct_usd": round(eq * 0.005, 0), "status": st}
    res["fixed_lot_risk"] = flow
    # concurrency over the live period: max simultaneous positions, per-currency legs, summed estimated risk
    ev = []
    for r in P.itertuples():
        if pd.isna(r.open_utc):
            continue
        end = r.close_utc if isinstance(r.close_utc, pd.Timestamp) else pd.Timestamp.utcnow().tz_localize(None)
        ev.append((r.open_utc, 1, r))
        ev.append((end, -1, r))
    ev.sort(key=lambda x: (x[0], x[1]))
    openp, mx = {}, {"positions": 0, "risk_usd": 0.0, "ccy_legs": {}}
    for t, k, r in ev:
        if k == 1:
            openp[r.position_id] = r
        else:
            openp.pop(r.position_id, None)
        n = len(openp)
        risk = sum((x.risk_usd_est or 0) for x in openp.values())
        legs = defaultdict(int)
        for x in openp.values():
            legs[x.symbol[:3] + ("+" if x.side > 0 else "-")] += 1
            legs[x.symbol[3:6] + ("-" if x.side > 0 else "+")] += 1
        mx["positions"] = max(mx["positions"], n)
        if risk > mx["risk_usd"]:
            mx["risk_usd"], mx["risk_at"] = round(risk, 0), str(t)
        for kk, v in legs.items():
            mx["ccy_legs"][kk] = max(mx["ccy_legs"].get(kk, 0), v)
    mx["ccy_legs"] = dict(sorted(mx["ccy_legs"].items(), key=lambda kv: -kv[1])[:8])
    res["max_concurrent"] = mx
    json.dump(res, open(OUT + "r4_live_results.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "review"}, indent=1, default=str))
    print("REVIEW", res["review"])


if __name__ == "__main__":
    main()
