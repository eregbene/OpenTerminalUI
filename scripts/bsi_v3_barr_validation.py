"""Replay the Bump-and-Run Reversal (BARR) trendline detector on H1 FX bars.

Deliberately conservative, unlike the older V3 replays:
- round-trip costs (IC Markets Raw spread + $7/lot commission, estimated) charged in R;
- a bar touching both stop and target counts as a LOSS;
- duplicate bars per timestamp are dropped (the candle table holds several per hour).

Two entry variants: BREAK (close of the breaking bar) and RETEST (limit at the broken line within
RETEST_BARS, since Bulkowski measures a 61-64% throwback/pullback rate).

Usage: python scripts/bsi_v3_barr_validation.py  (env BARR_START / BARR_END / BARR_TF override)
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import stdev

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.bsi_v3_august_validation as v3  # noqa: E402
from backend.mt5_strategies.families.bsi_v3_barr import BarrSignal, scan_barr  # noqa: E402

START = datetime.fromisoformat(os.getenv("BARR_START", "2025-09-01T00:00:00+00:00"))
END = datetime.fromisoformat(os.getenv("BARR_END", "2026-09-01T00:00:00+00:00"))
TIMEFRAME = os.getenv("BARR_TF", "H1")
MAX_HOLD_BARS = 240
RETEST_BARS = 12
COST_PIPS = v3.COST_PIPS
cost_in_price = v3.cost_in_price
OUT_JSON = Path("data/research/bsi_v3_barr_trendline_validation.json")
OUT_DOC = Path("docs/bsi_updated_faiz/v3_validation/BSI_V3_BARR_TRENDLINE_VALIDATION.md")


def simulate(bars: list, start: int, entry: float, stop: float, target: float, direction: str) -> tuple[float, str]:
    """Gross R from bar `start` onward. Same-bar stop+target = loss (conservative)."""
    risk = abs(entry - stop)
    short = direction == "SHORT"
    for bar in bars[start : start + MAX_HOLD_BARS]:
        hit_stop = bar.high >= stop if short else bar.low <= stop
        hit_target = bar.low <= target if short else bar.high >= target
        if hit_stop:
            return -1.0, "sl" if not hit_target else "sl_same_bar"
        if hit_target:
            return abs(target - entry) / risk, "tp"
    last = bars[min(len(bars), start + MAX_HOLD_BARS) - 1]
    return ((entry - last.close) if short else (last.close - entry)) / risk, "timeout"


def trades_for(symbol: str, bars: list, signals: list[BarrSignal]) -> list[dict]:
    cost = cost_in_price(symbol)
    out = []
    for s in signals:
        variants = {"BREAK": (s.signal_index + 1, s.entry)}
        # RETEST: first bar within RETEST_BARS that trades back to the broken line.
        for k in range(s.signal_index + 1, min(len(bars), s.signal_index + 1 + RETEST_BARS)):
            level = s.line_at(k)
            b = bars[k]
            touched = b.high >= level if s.direction == "SHORT" else b.low <= level
            if touched:
                stopped_same_bar = b.high >= s.stop if s.direction == "SHORT" else b.low <= s.stop
                variants["RETEST"] = (None, level) if stopped_same_bar else (k + 1, level)
                break
        for name, (start, entry) in variants.items():
            risk = abs(entry - s.stop)
            if risk <= 0 or (s.direction == "SHORT" and not s.target < entry < s.stop) or (s.direction == "LONG" and not s.stop < entry < s.target):
                continue
            if start is None:
                gross, status = -1.0, "sl_on_fill_bar"
            else:
                gross, status = simulate(bars, start, entry, s.stop, s.target, s.direction)
            out.append({
                "symbol": symbol, "variant": name, "direction": s.direction,
                "time": bars[s.signal_index].time.isoformat(), "entry": entry, "stop": s.stop, "target": s.target,
                "rr": round(abs(s.target - entry) / risk, 3), "status": status,
                "gross_r": round(gross, 4), "net_r": round(gross - cost / risk, 4), "cost_r": round(cost / risk, 4),
            })
    return out


def summary(rows: list[dict], field: str = "net_r") -> dict:
    vals = [r[field] for r in rows]
    if not vals:
        return {"trades": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    mean = sum(vals) / len(vals)
    eq = peak = dd = 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {
        "trades": len(vals), "win_rate": round(100 * len(wins) / len(vals), 1), "expectancy_r": round(mean, 3),
        "net_r": round(sum(vals), 2), "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "t_stat": round(mean / (stdev(vals) / math.sqrt(len(vals))), 2) if len(vals) > 2 and stdev(vals) > 0 else None,
        "max_drawdown_r": round(dd, 2),
    }


def main() -> None:
    store = v3.CandleStore()
    all_trades: list[dict] = []
    try:
        for symbol in v3.CORE_SYMBOLS:
            raw = v3.load_bars(store, symbol, TIMEFRAME, START, END)
            bars = list({b.time: b for b in raw}.values())  # drop duplicate timestamps (last wins)
            bars.sort(key=lambda b: b.time)
            signals = scan_barr(bars)
            trades = trades_for(symbol, bars, signals)
            print(f"{symbol}: {len(raw)} raw -> {len(bars)} bars, {len(signals)} signals, {len(trades)} trades", flush=True)
            all_trades.extend(trades)
    finally:
        store.close()
    all_trades.sort(key=lambda t: t["time"])
    mid = START + (END - START) / 2
    result = {"window": [START.isoformat(), END.isoformat()], "timeframe": TIMEFRAME, "cost_pips": COST_PIPS, "variants": {}}
    for variant in ("BREAK", "RETEST"):
        rows = [t for t in all_trades if t["variant"] == variant]
        result["variants"][variant] = {
            "net": summary(rows), "gross": summary(rows, "gross_r"),
            "first_half_net": summary([t for t in rows if t["time"] < mid.isoformat()]),
            "second_half_net": summary([t for t in rows if t["time"] >= mid.isoformat()]),
            "by_symbol_net": {s: summary([t for t in rows if t["symbol"] == s]) for s in v3.CORE_SYMBOLS},
            "by_direction_net": {d: summary([t for t in rows if t["direction"] == d]) for d in ("LONG", "SHORT")},
        }
    result["trades"] = all_trades
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = [f"# BSI V3 BARR trendline validation ({TIMEFRAME}, {START.date()} to {END.date()})", "",
             "Costs charged; same-bar stop+target = loss; duplicate bars removed.", ""]
    for variant, block in result["variants"].items():
        lines += [f"## {variant}", "", "| Slice | Trades | Win % | Exp R | Net R | PF | t-stat | Max DD R |", "|---|---|---|---|---|---|---|---|"]
        slices = [("All (net)", block["net"]), ("All (gross)", block["gross"]), ("1st half (net)", block["first_half_net"]), ("2nd half (net)", block["second_half_net"])]
        slices += [(f"{k} (net)", v) for k, v in block["by_direction_net"].items()]
        slices += [(f"{k} (net)", v) for k, v in block["by_symbol_net"].items()]
        for name, s in slices:
            lines.append(f"| {name} | {s.get('trades', 0)} | {s.get('win_rate', '-')} | {s.get('expectancy_r', '-')} | {s.get('net_r', '-')} | {s.get('profit_factor', '-')} | {s.get('t_stat', '-')} | {s.get('max_drawdown_r', '-')} |")
        lines.append("")
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")
    for variant, block in result["variants"].items():
        print(variant, "net", block["net"], "| gross", block["gross"])
        print("   halves", block["first_half_net"], block["second_half_net"])


if __name__ == "__main__":
    main()
