"""V3 resting-limit-at-FVG-mid replay.

Same detectors as bsi_v3_planned_entry_validation, but the entry is a limit order resting at the
FVG midpoint from the moment the FVG closes. It fills only when M5 price trades *through* the level
by the round-trip cost (bars are bid; conservative), is cancelled if the 2R target prints first or
after MAX_PENDING_HOURS. Outcomes are simulated on M5 from the fill bar so nothing inside the fill
bar's parent candle is skipped. Costs/same-bar-loss come from v3.simulate_outcome (REALISTIC_FILLS).

Run: BSI_V3_CANDLE_PROVIDER=MT5 BSI_V3_BROKER_SERVER=MetaQuotes-Demo BSI_V3_REALISTIC_FILLS=1 \
     python scripts/bsi_v3_limit_fvg_mid_validation.py
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from bisect import bisect_right
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.bsi_v3_august_validation as v3

START = datetime.fromisoformat(os.getenv("BSI_V3_PLANNED_START", "2026-01-01T00:00:00+00:00"))
END = datetime.fromisoformat(os.getenv("BSI_V3_PLANNED_END", "2026-08-12T00:00:00+00:00"))
MIN_RR = float(os.getenv("BSI_V3_PLANNED_MIN_RR", "1.25"))
MAX_PENDING_HOURS = float(os.getenv("BSI_V3_PLANNED_MAX_PENDING_HOURS", "24"))
OUT_JSON = ROOT / "data" / "research" / "bsi_v3_limit_fvg_mid_jan_aug_2026.json"
OUT_DOC = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation" / "BSI_V3_LIMIT_FVG_MID_JAN_AUG_2026.md"


def find_limit_fill(op: v3.Opportunity, m1: list[v3.Bar], m1_times: list) -> int | None:
    """Index of the M1 bar that fills the resting limit, or None (target first / expired)."""
    through = v3.cost_in_price(op.symbol)
    expiry = op.entry_time + timedelta(hours=MAX_PENDING_HOURS)
    long = op.direction == "LONG"
    # FVG confirmed_at is the FVG candle's open time; the order can only rest after it closes.
    placed = op.entry_time + timedelta(seconds=v3._TF_SECONDS[op.timeframe_stack[-1]])
    for i in range(bisect_right(m1_times, placed - timedelta(microseconds=1)), len(m1)):
        bar = m1[i]
        if bar.time > expiry:
            return None
        if (bar.low <= op.entry - through) if long else (bar.high >= op.entry + through):
            return i
        if (bar.high >= op.target) if long else (bar.low <= op.target):
            return None  # price ran to target without us: missed
    return None


def simulate_from_fill(op: v3.Opportunity, m1: list[v3.Bar], fill_idx: int, horizon_bars: int) -> v3.Outcome:
    fill_bar = m1[fill_idx]
    stopped_in_fill_bar = fill_bar.low <= op.stop if op.direction == "LONG" else fill_bar.high >= op.stop
    filled = replace(op, entry_time=fill_bar.time)
    if stopped_in_fill_bar:  # intra-bar order unknown: pessimistically a full loss plus costs
        r = round(-1.0 - v3.cost_in_price(op.symbol) / op.risk_distance, 4)
        return v3.Outcome(op.opportunity_id, op.strategy_id, op.symbol, op.direction, fill_bar.time.isoformat(), fill_bar.time.isoformat(), "sl_fill_bar", r, r)
    return v3.simulate_outcome(filled, m1[fill_idx + 1 :], horizon_bars)


def main() -> None:
    if os.name == "nt":  # live trading shares this PC
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    v3.START, v3.END, v3.MIN_ENTRY_RR = START, END, MIN_RR
    store = v3.CandleStore()
    cache: dict = {}

    def bars(symbol: str, tf: str) -> list[v3.Bar]:
        if (symbol, tf) not in cache:
            cache[(symbol, tf)] = v3.load_bars(store, symbol, tf, START, END + timedelta(days=3))
        return cache[(symbol, tf)]

    per_strategy: dict = {}
    all_instant, all_limit = [], []
    unique: dict = {}  # (symbol, direction, entry_time) -> outcome, to expose duplicate detectors
    for spec in v3.strategy_registry():
        detector = v3.DETECTOR_REGISTRY.get(spec.strategy_id)
        if detector is None:
            continue
        instant, limit, missed = [], [], 0
        for symbol in [s for s in spec.eligible_symbols if s in v3.CORE_SYMBOLS]:
            by_tf = {tf: bars(symbol, tf) for tf in spec.required_timeframes}
            found, _ = detector(spec, symbol, by_tf)
            m1 = bars(symbol, "M5")  # clean MT5 source has no M1
            m1_times = [b.time for b in m1]
            primary = by_tf[spec.required_timeframes[-1]]
            primary_times = [b.time for b in primary]
            horizon = 96 * v3._TF_SECONDS[spec.required_timeframes[-1]] // 300
            for op in found:
                if op.rr < MIN_RR:
                    continue
                instant.append(v3.simulate_outcome(op, primary[bisect_right(primary_times, op.entry_time):]))
                idx = find_limit_fill(op, m1, m1_times)
                if idx is None:
                    missed += 1
                    continue
                out = simulate_from_fill(op, m1, idx, horizon)
                limit.append(out)
                unique.setdefault((op.symbol, op.direction, op.entry_time), out)
        print(f"{spec.strategy_id}: instant={len(instant)} filled={len(limit)} missed={missed}", flush=True)
        per_strategy[spec.strategy_id] = {
            "instant": v3.summarize_outcomes(instant, "managed_r"),
            "limit_filled": v3.summarize_outcomes(limit, "managed_r"),
            "limit_filled_baseline_r": v3.summarize_outcomes(limit, "baseline_r"),
            "missed": missed,
        }
        all_instant += instant
        all_limit += limit
    store.close()

    def by_month(outs):
        months: dict = {}
        for o in outs:
            months.setdefault(o.entry_time[:7], []).append(o)
        return {m: v3.summarize_outcomes(rows, "managed_r") for m, rows in sorted(months.items())}

    uniq = list(unique.values())
    result = {
        "window": [START.isoformat(), END.isoformat()],
        "rules": {"fill": "M5 trade-through of FVG mid by round-trip cost", "cancel": "target first or expiry", "expiry_hours": MAX_PENDING_HOURS, "min_rr": MIN_RR},
        "instant_reference": v3.summarize_outcomes(all_instant, "managed_r"),
        "limit_all": v3.summarize_outcomes(all_limit, "managed_r"),
        "limit_all_baseline_r": v3.summarize_outcomes(all_limit, "baseline_r"),
        "limit_unique_setups": v3.summarize_outcomes(uniq, "managed_r"),
        "limit_unique_by_month": by_month(uniq),
        "per_strategy": per_strategy,
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    cols = ["trades", "win_rate", "net_r", "expectancy", "t_stat", "profit_factor", "max_drawdown_r"]
    rows = [["Model"] + cols] + [[k] + [result[k].get(c) for c in cols] for k in ("instant_reference", "limit_all", "limit_all_baseline_r", "limit_unique_setups")]
    rows += [[f"unique {m}"] + [s.get(c) for c in cols] for m, s in result["limit_unique_by_month"].items()]
    rows += [[sid] + [s["limit_filled"].get(c) for c in cols] for sid, s in per_strategy.items()]
    OUT_DOC.write_text("# BSI V3 Resting Limit at FVG Mid\n\n" + json.dumps(result["rules"]) + "\n\n" + v3.table(rows) + "\n", encoding="utf-8")
    print(v3.table(rows))


if __name__ == "__main__":
    main()
