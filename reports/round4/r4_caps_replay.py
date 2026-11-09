"""Deployment-impact replay of the PROPOSED deterministic portfolio protections on the real DEMO trades since
2026-10-10 (read-only). Chronological: at each real entry, the portfolio open at that moment (initial stops, broker tick
specs) + this trade is checked against: aggregate open risk <= 2.5 % of equity and <= 2 legs per currency side;
a trade with UNKNOWN risk would be rejected. Scenario A = sizes as traded; scenario B = Flow ML / Flow AM resized to
0.5 % risk (risk-based sizing, other books unchanged). A blocked trade's REAL net P&L (scaled by the size ratio in B)
is what the protection would have removed. Nothing is changed anywhere.
    cd bensim_phase20_dev && python ../OpenTerminalUI/reports/round4/r4_caps_replay.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "D:/Devops/trading/bensim_phase20_dev")
from intel import advisor as AD, agents as AG, manual_intervention as MI  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round4/r4_caps_replay.json"


def main() -> None:
    import time
    deals = MI.RO.deals(int(AD.START.timestamp()) - 86400, int(time.time()) + 7200)
    by = {}
    for d in deals:
        by.setdefault(int(d["position_id"]), []).append(d)
    sl0 = AD.initial_stops(by)
    P = AG.Portfolio()
    bal_now = float(MI.RO.account()["balance"])
    pos = []
    for pid, ds in by.items():
        o = next((d for d in ds if d["entry"] == 0), None)
        if o is None or MI.strategy_of(o).startswith("REVIEW"):
            continue
        closes = [d for d in ds if d["entry"] in (1, 3)]
        pos.append({"pid": pid, "strategy": MI.strategy_of(o), "symbol": o["symbol"], "side": 1 if o["type"] == 0 else -1, "entry": o["price"],
                    "lots": o["volume"], "t_open": MI._utc(o["time"]), "t_close": MI._utc(closes[-1]["time"]) if closes else 10**12,
                    "pnl": sum(d["profit"] + d["commission"] + d["swap"] + d.get("fee", 0) for d in ds), "sl": sl0.get(pid)})
    pos.sort(key=lambda p: p["t_open"])
    res = {}
    for scen in ("A_as_traded", "B_flow_risk_sized_0.5pct"):
        open_, kept, blocked, reasons = [], [], [], {}
        for p in pos:
            open_ = [q for q in open_ if q["t_close"] > p["t_open"]]
            eq = bal_now - sum(q["pnl"] for q in pos if q["t_close"] > p["t_open"] and q["t_close"] < 10**12)
            lots, scale = p["lots"], 1.0
            if scen.startswith("B") and p["strategy"] in ("flow_ml", "flow_am") and p["sl"]:
                rl = P.lots_for_risk(p["symbol"], p["entry"], p["sl"], eq * 0.005)
                if rl:
                    scale, lots = rl / p["lots"], rl
            r, _ = P.risk_usd(p["symbol"], p["entry"], p["sl"], lots)
            legs = {}
            for q in open_:
                for ccy, sgn in ((q["symbol"][:3], q["side"]), (q["symbol"][3:6], -q["side"])):
                    legs[f"{ccy}{'+' if sgn > 0 else '-'}"] = legs.get(f"{ccy}{'+' if sgn > 0 else '-'}", 0) + 1
            snap = {"equity": eq, "open_risk_usd": sum(q["risk"] or 0 for q in open_), "unknown_risk_positions": sum(q["risk"] is None for q in open_), "currency_legs": legs}
            chk = P.check_new(snap, p["symbol"], p["side"], r)
            rec = {**p, "lots_used": lots, "risk": r, "scaled_pnl": round(p["pnl"] * scale, 2)}
            if chk["would_block"]:
                blocked.append(rec)
                for b in chk["breaches"]:
                    k = b.split(" ")[0] + (" legs" if "legs" in b else "")
                    reasons[k] = reasons.get(k, 0) + 1
            else:
                kept.append(rec)
                open_.append(rec)
        by_s = {}
        for rec in blocked:
            s = by_s.setdefault(rec["strategy"], {"blocked": 0, "blocked_pnl": 0.0})
            s["blocked"] += 1
            s["blocked_pnl"] = round(s["blocked_pnl"] + rec["scaled_pnl"], 2)
        res[scen] = {"trades": len(pos), "kept": len(kept), "blocked": len(blocked), "kept_net_usd": round(sum(r["scaled_pnl"] for r in kept), 2),
                     "blocked_net_usd": round(sum(r["scaled_pnl"] for r in blocked), 2), "all_net_usd_at_this_sizing": round(sum(r["scaled_pnl"] for r in kept + blocked), 2),
                     "block_reasons": reasons, "blocked_by_strategy": by_s,
                     "max_open_risk_pct_kept": round(max([0] + [sum(q["risk"] or 0 for q in kept if q["t_open"] <= k["t_open"] < q["t_close"]) / bal_now * 100 for k in kept]), 2)}
    json.dump(res, open(OUT, "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
