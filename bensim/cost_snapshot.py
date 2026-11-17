"""Observed broker costs (audit P0, 2026-11-03), read-only:

    python -m bensim.cost_snapshot            # -> data/costs/swap_snapshot_<date>.json + deals_costs_<date>.json

1. swap rates: its OWN read-only MetaTrader5 connection (same terminal as the bridge, MT5_PATH) -> symbol_info
   swap_long / swap_short / swap_mode / swap_rollover3days / point / spread. Nothing is sent, the bridge is untouched.
2. charged costs: the bridge's existing /deals route (last 120 days) -> swap and commission per lot actually booked
   on the demo account, per symbol.
Classification used by the backtest: swap = OBSERVED today, ESTIMATED when applied to past dates (rates change);
commission = OBSERVED if the deals' per-lot round trip matches Exec, else flagged.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import bridge
from .config import env
from .mt5_time import rollover_nights

SYMS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "XAUUSD", "AUDUSD"]
OUT = Path("data/costs")
SWAP_MODES = {0: "disabled", 1: "points", 2: "base currency", 3: "margin currency", 4: "deposit currency",
              5: "interest current", 6: "interest open", 7: "reopen current", 8: "reopen bid"}


def swap_rates() -> dict:
    import MetaTrader5 as mt5
    path = env("MT5_PATH")
    if not (mt5.initialize(path=path) if path else mt5.initialize()):
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        acc = mt5.account_info()
        out = {"observed_at": datetime.now(timezone.utc).isoformat(), "server": acc.server, "demo": acc.trade_mode == 0,
               "currency": acc.currency, "symbols": {}}
        for s in SYMS:
            i = mt5.symbol_info(s)
            if i is None:
                out["symbols"][s] = {"error": str(mt5.last_error())}
                continue
            out["symbols"][s] = {"swap_long": i.swap_long, "swap_short": i.swap_short, "swap_mode": i.swap_mode,
                                 "swap_mode_name": SWAP_MODES.get(i.swap_mode, "?"), "swap_rollover3days": i.swap_rollover3days,
                                 "point": i.point, "digits": i.digits, "spread_points_now": i.spread,
                                 "contract_size": i.trade_contract_size, "tick_value": i.trade_tick_value, "tick_size": i.trade_tick_size}
        return out
    finally:
        mt5.shutdown()


def deal_costs(days: int = 150) -> dict:
    """Observed per symbol and side: swap USD per lot-night, commission USD per lot round trip."""
    end = int(time.time())
    deals = bridge.deals(end - days * 86400, end)
    pos: dict[int, dict] = {}
    for d in deals:
        if not d.get("symbol") or d.get("type") not in (0, 1):
            continue
        x = pos.setdefault(d["position_id"], {"symbol": d["symbol"], "in": None, "out": [], "commission": 0.0})
        x["commission"] += float(d.get("commission", 0)) + float(d.get("fee", 0))
        if d["entry"] == 0:
            x["in"] = d
        elif d["entry"] == 1:
            x["out"].append(d)
    agg: dict[str, dict] = {}
    for x in pos.values():
        if not x["in"] or not x["out"]:
            continue
        side = "long" if x["in"]["type"] == 0 else "short"
        a = agg.setdefault(f"{x['symbol']}|{side}", {"positions": 0, "lot_nights": 0.0, "swap_usd": 0.0, "lots": 0.0, "commission_usd": 0.0})
        a["positions"] += 1
        a["lots"] += float(x["in"]["volume"])
        a["commission_usd"] += x["commission"]
        for o in x["out"]:
            a["lot_nights"] += float(o["volume"]) * rollover_nights(int(x["in"]["time"]), int(o["time"]))
            a["swap_usd"] += float(o.get("swap", 0))
    for a in agg.values():
        a["swap_usd_per_lot_night"] = round(a["swap_usd"] / a["lot_nights"], 3) if a["lot_nights"] else None
        a["commission_per_lot_rt"] = round(-a["commission_usd"] / a["lots"], 3) if a["lots"] else None
    return {"window_days": days, "observed_at": datetime.now(timezone.utc).isoformat(), "source": "demo account deals via bridge /deals",
            "by_symbol_side": agg}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    day = f"{datetime.now(timezone.utc):%Y-%m-%d}"
    try:
        s = swap_rates()
    except SystemExit as e:  # do not retry: never compete with the live bridge for the terminal
        s = {"observed_at": datetime.now(timezone.utc).isoformat(), "error": str(e), "symbols": {}}
    (OUT / f"swap_snapshot_{day}.json").write_text(json.dumps(s, indent=1))
    d = deal_costs()
    (OUT / f"deals_costs_{day}.json").write_text(json.dumps(d, indent=1))
    for k, v in s["symbols"].items():
        print(k, {x: v.get(x) for x in ("swap_long", "swap_short", "swap_mode_name", "swap_rollover3days", "spread_points_now")})
    for k, v in d["by_symbol_side"].items():
        print("deals", k, v)


if __name__ == "__main__":
    main()
