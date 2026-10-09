"""bsi_v3_enigma_range over ~5 years (Sep 2021 - Aug 2026), three entry models, by year and symbol.

Candles: M15 stitched per timestamp (FOREXSB first, else MT5 MetaQuotes-Demo), like
bsi_v3_fingerprint_backfill; H1/H4 resampled from it. No clean M5/M1 exists for these years, so
confirmation, limit fills and exits all run on M15. Costs + same-bar loss from REALISTIC_FILLS.
ponytail: MT5-filled stretches are broker-clock (2-3h off UTC); H4 bucket edges shift there.

Run: BSI_V3_REALISTIC_FILLS=1 python scripts/bsi_v3_enigma_5y_validation.py
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BSI_V3_REALISTIC_FILLS", "1")
os.environ.setdefault("BSI_V3_PLANNED_CONFIRMATION_LOOKAHEAD_MINUTES", "270")  # >=8 M15 bars, as the backfill used

import scripts.bsi_v3_august_validation as v3
import scripts.bsi_v3_planned_entry_validation as pev
import scripts.bsi_v3_limit_fvg_mid_validation as lim
from scripts.bsi_v3_fingerprint_backfill import resample

START = datetime.fromisoformat("2021-09-01T00:00:00+00:00")
END = datetime.fromisoformat("2026-08-01T00:00:00+00:00")
OUT_JSON = ROOT / "data" / "research" / "bsi_v3_enigma_5y.json"
OUT_DOC = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation" / "BSI_V3_ENIGMA_5Y.md"


def stitched_m15(store: v3.CandleStore, symbol: str) -> list[v3.Bar]:
    rows = store._pg.execute(
        """
        select timestamp, open, high, low, close, tick_volume, provider from mt5_canonical_candles
        where broker_symbol=%s and timeframe='M15' and timestamp >= %s and timestamp < %s
          and extract(epoch from timestamp)::bigint %% 900 = 0
          and (provider='FOREXSB' or (provider='MT5' and broker_server='MetaQuotes-Demo'))
        order by timestamp
        """,
        (symbol, START - timedelta(days=30), END + timedelta(days=20)),
    ).fetchall()
    merged: dict = {}
    for ts, o, h, l, c, vol, provider in rows:
        if ts in merged and merged[ts][1] == "FOREXSB":
            continue
        merged[ts] = (v3.Bar(v3.utc(ts), float(o), float(h), float(l), float(c), float(vol or 0)), provider)
    return [merged[t][0] for t in sorted(merged)]


def main() -> None:
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    v3.START, v3.END, v3.MIN_ENTRY_RR = START, END, pev.MIN_RR
    spec = next(s for s in v3.strategy_registry() if s.strategy_id == "bsi_v3_enigma_range")
    store = v3.CandleStore()
    models: dict[str, list[v3.Outcome]] = {"instant": [], "market_confirm": [], "limit_mid": []}
    for symbol in v3.CORE_SYMBOLS:
        m15 = stitched_m15(store, symbol)
        if len(m15) < 1000:
            print(f"{symbol}: no data", flush=True)
            continue
        by_tf = {"M15": m15, "H1": resample(m15, 3600), "H4": resample(m15, 14400)}
        times = {tf: [b.time for b in bars] for tf, bars in by_tf.items()}
        found, _ = v3.detect_enigma_range(spec, symbol, by_tf)
        counts = dict.fromkeys(models, 0)
        for op in found:
            if op.rr < pev.MIN_RR:
                continue
            models["instant"].append(v3.simulate_outcome(op, by_tf["H4"][bisect_right(times["H4"], op.entry_time):]))
            planned = pev._find_confirmed_planned_entry(
                op, primary_bars=by_tf["H4"], primary_times=times["H4"],
                m1_bars=[], m1_times=[], m5_bars=m15, m5_times=times["M15"],
            )
            if planned:
                p = planned["opportunity"]
                models["market_confirm"].append(v3.simulate_outcome(p, m15[bisect_right(times["M15"], p.entry_time):], 96 * 16))
                counts["market_confirm"] += 1
            idx = lim.find_limit_fill(op, m15, times["M15"])
            if idx is not None:
                models["limit_mid"].append(lim.simulate_from_fill(op, m15, idx, 96 * 16))
                counts["limit_mid"] += 1
            counts["instant"] += 1
        print(f"{symbol}: {len(m15)} M15 bars, {counts}", flush=True)
    store.close()

    def split(outs, key):
        groups: dict = {}
        for o in outs:
            groups.setdefault(key(o), []).append(o)
        return {k: v3.summarize_outcomes(g, "managed_r") for k, g in sorted(groups.items())}

    result = {
        "window": [START.isoformat(), END.isoformat()],
        "overall": {m: v3.summarize_outcomes(o, "managed_r") for m, o in models.items()},
        "by_year": {m: split(o, lambda x: x.entry_time[:4]) for m, o in models.items()},
        "by_symbol": {m: split(o, lambda x: x.symbol) for m, o in models.items()},
        "trades": {m: [[x.symbol, x.entry_time, x.managed_r] for x in o if x.managed_r is not None] for m, o in models.items()},
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    cols = ["trades", "win_rate", "net_r", "expectancy", "t_stat", "profit_factor", "max_drawdown_r"]
    rows = [["Model / slice"] + cols]
    for m in models:
        rows.append([m] + [result["overall"][m].get(c) for c in cols])
        rows += [[f"  {m} {y}"] + [s.get(c) for c in cols] for y, s in result["by_year"][m].items()]
    for m in ("market_confirm", "limit_mid"):
        rows += [[f"  {m} {sym}"] + [s.get(c) for c in cols] for sym, s in result["by_symbol"][m].items()]
    OUT_DOC.write_text("# BSI V3 Enigma Range, 5 years\n\nStitched M15 (FOREXSB first, MT5 MetaQuotes-Demo fill), costs charged.\n\n" + v3.table(rows) + "\n", encoding="utf-8")
    print(v3.table(rows))


if __name__ == "__main__":
    main()
