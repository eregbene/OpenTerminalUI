"""Backfill BSI V3 planned-entry setups into historical_pattern_fingerprints / historical_setup_outcomes.

Same tables, same feature builder (fingerprint.build_fingerprint) and same outcome labeler
(outcomes.label_outcome) as the old-strategy corpus, so V3 rows are directly comparable. Rows are tagged
strategy_version=STRATEGY_VERSION and strategy_family "BSI_V3"; nothing existing is modified or deleted.

Setups come from the clean V3 replay (scripts/bsi_v3_planned_entry_validation.py logic, no lookahead,
market fill at confirmation). No clean M5/M1 exists for these years, so confirmation runs on M15 (M5 := M15).

Candles: FOREXSB M15 has holes (e.g. EURUSD Aug 2022 - mid 2024), so M15 is stitched per timestamp --
FOREXSB first, else MT5 MetaQuotes-Demo -- on the 15-minute grid; H1/H4/D1 are built from that M15 so a
bar never mixes two sources' duplicates. Each row's `provider` is the source that labels its outcome.
The window ends 2026-08-01 so the 800-bar outcome lookforward stays clear of the test-fixture rows
written to the MT5 feed from 2026-08-17.

Idempotent: fingerprint ids are deterministic, so a re-run resumes where it stopped.

Run (one process per symbol list):
  DATABASE_URL=postgresql://openterminalui:openterminalui@localhost:5432/openterminalui \
  python scripts/bsi_v3_fingerprint_backfill.py EURUSD GBPUSD
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("BSI_V3_REALISTIC_FILLS", "1")
os.environ.setdefault("BSI_V3_PLANNED_START", "2021-09-01T00:00:00+00:00")
os.environ.setdefault("BSI_V3_PLANNED_END", "2026-08-01T00:00:00+00:00")
# Confirmation needs >= 8 lower-TF bars; live's 90 min is 18 M5 bars. On M15 keep the same 18 bars (270 min).
os.environ.setdefault("BSI_V3_PLANNED_CONFIRMATION_LOOKAHEAD_MINUTES", "270")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.bsi_v3_august_validation as v3  # noqa: E402
import scripts.bsi_v3_planned_entry_validation as pev  # noqa: E402
from backend.brokers.mt5.models import MT5Candle  # noqa: E402
from backend.historical_intelligence import outcomes  # noqa: E402
from backend.historical_intelligence.fingerprint import build_fingerprint  # noqa: E402
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM  # noqa: E402
from backend.historical_intelligence.replay import _MIN_BARS, build_strategy_context, required_lookback  # noqa: E402
from backend.shared.db import SessionLocal  # noqa: E402

STRATEGY_VERSION = "bsi-v3-planned-m15conf-v1"
TF_SECONDS = {"M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
HISTORY_PAD = timedelta(days=40)  # >= 100 H4 bars of lookback before the window starts


def fingerprint_id(symbol: str, at: datetime, strategy_id: str) -> str:
    return "HPF_" + hashlib.sha256(f"V3:{STRATEGY_VERSION}:{symbol}:{at.isoformat()}:{strategy_id}".encode()).hexdigest()[:40]


def stitched_m15(store: v3.CandleStore, symbol: str) -> tuple[list[v3.Bar], set[datetime]]:
    """M15 bars on the 15-minute grid, FOREXSB preferred per timestamp, MT5 MetaQuotes-Demo filling gaps."""
    rows = store._pg.execute(
        """
        select timestamp, open, high, low, close, tick_volume, provider from mt5_canonical_candles
        where broker_symbol=%s and timeframe='M15' and timestamp >= %s and timestamp < %s
          and extract(epoch from timestamp)::bigint %% 900 = 0
          and (provider='FOREXSB' or (provider='MT5' and broker_server='MetaQuotes-Demo'))
        order by timestamp
        """,
        (symbol, pev.START - HISTORY_PAD, pev.END + timedelta(days=10)),
    ).fetchall()
    merged: dict[datetime, tuple[v3.Bar, str]] = {}
    for ts, o, h, l, c, vol, provider in rows:
        if ts in merged and merged[ts][1] == "FOREXSB":
            continue
        merged[ts] = (v3.Bar(v3.utc(ts), float(o), float(h), float(l), float(c), float(vol or 0)), provider)
    bars = [merged[t][0] for t in sorted(merged)]
    return bars, {bar.time for bar, provider in merged.values() if provider == "FOREXSB"}


def resample(m15: list[v3.Bar], step: int) -> list[v3.Bar]:
    buckets: dict[int, list[v3.Bar]] = defaultdict(list)
    for b in m15:
        buckets[int(b.time.timestamp()) // step * step].append(b)
    return [
        v3.Bar(datetime.fromtimestamp(t, timezone.utc), g[0].open, max(x.high for x in g), min(x.low for x in g), g[-1].close, sum(x.volume for x in g))
        for t, g in sorted(buckets.items())
    ]


def rows_as_of(symbol: str, tf: str, bars: list[v3.Bar], times: list[datetime], at: datetime) -> list[dict]:
    """Point-in-time like replay.bars_as_of: only bars fully closed by `at`, the last required_lookback of them."""
    i = bisect_right(times, at - timedelta(seconds=TF_SECONDS[tf]))
    return [
        MT5Candle(
            symbol=symbol, timeframe=tf, time=b.time, open=Decimal(str(b.open)), high=Decimal(str(b.high)), low=Decimal(str(b.low)),
            close=Decimal(str(b.close)), tick_volume=int(b.volume), spread=0, real_volume=0,
        ).model_dump(mode="json")
        for b in bars[max(0, i - required_lookback(timeframe=tf)) : i]
    ]


def planned_setups(bars: dict[str, list[v3.Bar]], symbol: str) -> list[tuple[str, v3.Opportunity]]:
    """(strategy_id, planned opportunity) for every confirmed V3 planned entry on `symbol`."""
    bars = {**bars, "M5": bars["M15"], "M1": []}  # option A: no clean M5 for these years, confirm on M15
    times = {tf: [b.time for b in rows] for tf, rows in bars.items()}
    out = []
    for spec in v3.strategy_registry():
        detector = v3.DETECTOR_REGISTRY.get(spec.strategy_id)
        if detector is None or symbol not in spec.eligible_symbols:
            continue
        found, _ = detector(spec, symbol, {tf: bars.get(tf, []) for tf in spec.required_timeframes})
        primary_tf = spec.required_timeframes[-1] if bars.get(spec.required_timeframes[-1]) else "M15"
        for op in found:
            if op.rr < pev.MIN_RR:
                continue
            planned = pev._find_confirmed_planned_entry(
                op, primary_bars=bars[primary_tf], primary_times=times[primary_tf],
                m1_bars=[], m1_times=[], m5_bars=bars["M5"], m5_times=times["M5"],
            )
            if planned is not None:
                # The confirmation bar is really M15 (M5 := M15), so the fill happens at its M15 close.
                confirmed = datetime.fromisoformat(planned["confirmation_time"])
                out.append((spec.strategy_id, replace(planned["opportunity"], entry_time=confirmed + timedelta(minutes=15, microseconds=-1))))
    return out


def persist(symbol: str, setups: list[tuple[str, v3.Opportunity]], bars: dict[str, list[v3.Bar]], forexsb_times: set[datetime]) -> dict[str, int]:
    by_instant: dict[datetime, list[tuple[str, v3.Opportunity]]] = defaultdict(list)
    for sid, op in setups:
        # entry_time is the confirmation close minus 1us; the context must include that closed bar.
        by_instant[op.entry_time + timedelta(microseconds=1)].append((sid, op))
    times = {tf: [b.time for b in bars[tf]] for tf in ("M15", "H1", "H4")}
    counts = {"new": 0, "existing": 0, "insufficient": 0, "errors": 0}
    db = SessionLocal()
    t0 = time.perf_counter()
    try:
        for n, (at, items) in enumerate(sorted(by_instant.items()), 1):
            # One row per (instant, strategy): a strategy can confirm two setups on the same bar; keep the first.
            first = {sid: op for sid, op in reversed(items)}
            items = list(first.items())
            todo = [(sid, op) for sid, op in items if db.get(HistoricalPatternFingerprintORM, fingerprint_id(symbol, at, sid)) is None]
            counts["existing"] += len(items) - len(todo)
            if not todo:
                continue
            try:
                rows = {tf: rows_as_of(symbol, tf, bars[tf], times[tf], at) for tf in ("M15", "H1", "H4")}
                if any(len(rows[tf]) < _MIN_BARS[tf] for tf in rows):
                    counts["insufficient"] += len(todo)
                    continue
                last = Decimal(str(rows["M15"][-1]["close"]))
                ctx = build_strategy_context(
                    symbol=symbol, broker_symbol=symbol, m15_rows=rows["M15"], h1_rows=rows["H1"], h4_rows=rows["H4"],
                    bid=last, ask=last, spread=Decimal("0"), now=at,
                )
                if ctx is None:
                    counts["insufficient"] += len(todo)
                    continue
                # Label from the source that has this stretch of M15 (the fill bar is the one closing at `at`).
                provider = "FOREXSB" if at - timedelta(minutes=15) in forexsb_times else "MT5"
                for sid, op in todo:
                    fields = build_fingerprint(
                        ctx=ctx, strategy_id=sid, contributing_strategies=[sid], strategy_family="BSI_V3",
                        strategy_version=STRATEGY_VERSION, source_quality_tier="RECONSTRUCTED", provider=provider, proxy=False,
                        entry=op.entry, stop_loss=op.stop, take_profit=op.target, entry_time=at,
                    )
                    fid = fingerprint_id(symbol, at, sid)
                    row = HistoricalPatternFingerprintORM(fingerprint_id=fid, source_evaluation_id=None, created_at=datetime.now(timezone.utc))
                    for key, value in fields.items():
                        setattr(row, key, value)
                    db.add(row)
                    outcomes.label_outcome(
                        fingerprint_id=fid, canonical_symbol=symbol, broker_symbol=symbol, direction=fields["direction"],
                        entry=fields["entry"], stop_loss=fields["stop_loss"], take_profit=fields["take_profit"],
                        entry_time=fields["entry_time"], provider=provider, db=db,
                    )
                    counts["new"] += 1
                db.commit()
            except Exception as exc:  # one bad instant must not kill an overnight run
                db.rollback()
                counts["errors"] += len(todo)
                print(f"  {symbol} {at.isoformat()} error {exc.__class__.__name__}: {exc}", flush=True)
                continue
            if n % 500 == 0:
                print(f"  {symbol}: {n}/{len(by_instant)} instants, {counts}, {time.perf_counter() - t0:.0f}s", flush=True)
    finally:
        db.close()
    return counts


def main() -> None:
    symbols = [s.upper() for s in sys.argv[1:]] or pev.SYMBOLS
    v3.START, v3.END = pev.START, pev.END
    store = v3.CandleStore()
    print(f"V3 fingerprint backfill {pev.START.date()}..{pev.END.date()} version={STRATEGY_VERSION} symbols={symbols}", flush=True)
    for symbol in symbols:
        t0 = time.perf_counter()
        m15, forexsb_times = stitched_m15(store, symbol)
        bars = {"M15": m15, **{tf: resample(m15, TF_SECONDS[tf]) for tf in ("H1", "H4", "D1")}}
        print(f"{symbol}: {len(m15)} M15 bars ({len(forexsb_times)} FOREXSB, {len(m15) - len(forexsb_times)} MT5 MetaQuotes)", flush=True)
        setups = planned_setups(bars, symbol)
        per_strategy: dict[str, int] = defaultdict(int)
        for sid, _ in setups:
            per_strategy[sid] += 1
        print(f"{symbol}: {len(setups)} planned setups in {time.perf_counter() - t0:.0f}s {dict(per_strategy)}", flush=True)
        counts = persist(symbol, setups, bars, forexsb_times)
        print(f"{symbol} DONE {counts} total {time.perf_counter() - t0:.0f}s", flush=True)
        v3.ANALYSIS_CACHE.clear()
    store.close()


if __name__ == "__main__":
    main()
