"""Re-simulate historical_analog_v1 (and every other named policy, via merge-idempotent
replay()) for the exact 126 real closed trades already covered, now that
service.py's historical_analog family normalizes strategy_id (case mismatch bug fix). Same
throttling as the main backfill script -- this set is already known-replayable (real deal data +
valid candles), so no time wasted on skips."""
import asyncio
import os

from backend.adaptive_management.orm import AdaptivePositionStateORM, AdaptiveTradeEventORM, CounterfactualOutcomeORM
from backend.adaptive_management.service import AdaptiveManagementService, _replay_candles
from backend.brokers.mt5.trading_costs import compute_trade_costs
from backend.shared.db import SessionLocal

_SLEEP_SECONDS = float(os.getenv("BACKFILL_SLEEP_SECONDS", "1.5"))


async def main() -> None:
    svc = AdaptiveManagementService()
    with SessionLocal() as db:
        trade_ids = [r[0] for r in db.query(CounterfactualOutcomeORM.trade_id).filter(CounterfactualOutcomeORM.policy_id == "historical_analog_v1").distinct().all()]
        closed = db.query(AdaptivePositionStateORM).filter(AdaptivePositionStateORM.position_id.in_(trade_ids)).all()
        rows = [
            {
                "position_id": r.position_id, "symbol": r.symbol, "direction": r.direction,
                "original_volume": r.original_volume, "entry_price": r.entry_price, "original_sl": r.original_sl,
                "original_tp": r.original_tp, "opened_at": r.opened_at, "closed_detected_at": r.closed_detected_at,
                "strategy_id": r.strategy_id, "timeframe": r.timeframe,
            }
            for r in closed
        ]
    print(f"resimulating={len(rows)}", flush=True)

    ok, skipped, failed = 0, 0, 0
    for i, r in enumerate(rows):
        try:
            with SessionLocal() as db:
                deals = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.position_id == r["position_id"], AdaptiveTradeEventORM.event_type == "DEAL").all()
            if not deals:
                skipped += 1
                continue
            exit_deal = max(deals, key=lambda d: d.utc_time or r["closed_detected_at"])
            realized_pnl = compute_trade_costs([{"profit": d.realized_pnl, "commission": d.commission, "swap": d.swap, "fee": d.fee, "volume": d.volume} for d in deals]).net_pnl
            trade = {
                "trade_id": r["position_id"], "symbol": r["symbol"], "direction": r["direction"],
                "volume": r["original_volume"], "entry": r["entry_price"], "stop_loss": r["original_sl"],
                "take_profit": r["original_tp"], "entry_time": r["opened_at"],
                "exit_time": exit_deal.utc_time or r["closed_detected_at"], "actual_pnl": realized_pnl,
                "strategy_id": r["strategy_id"], "timeframe": r["timeframe"],
            }
            candles = await _replay_candles(r["symbol"], r["timeframe"] or "M5", r["opened_at"], trade["exit_time"], adapter=svc.adapter)
            if not candles:
                skipped += 1
                continue
            svc.replay(trade, candles, policy_ids=None)
            ok += 1
        except Exception as exc:
            failed += 1
            print(f"FAILED {r['position_id']}: {exc.__class__.__name__}: {exc}", flush=True)
        await asyncio.sleep(_SLEEP_SECONDS)
        if (i + 1) % 20 == 0:
            print(f"progress {i+1}/{len(rows)} ok={ok} skipped={skipped} failed={failed}", flush=True)

    print(f"DONE ok={ok} skipped={skipped} failed={failed}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
