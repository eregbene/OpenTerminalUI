"""One-off backfill: re-replay every real closed DEMO position so the newly-added
historical_analog_v1 shadow policy gets real counterfactual data alongside the existing
policies. Reuses AdaptiveManagementService.replay()/_replay_candles exactly like
_auto_replay_recently_closed does, just without its 5-per-cycle/60s-cooldown limits (this is a
one-time backfill, not the live per-cycle path). Never touches the broker (replay()'s own
broker_mutation_calls: 0 guarantee); idempotent (CounterfactualOutcomeORM merges on a
deterministic (trade_id, policy_id) id), so safe to run more than once."""
import asyncio
import os
import sys

from backend.adaptive_management.orm import AdaptivePositionStateORM, AdaptiveTradeEventORM, CounterfactualOutcomeORM
from backend.adaptive_management.service import AdaptiveManagementService, _replay_candles
from backend.brokers.mt5.trading_costs import compute_trade_costs
from backend.shared.db import SessionLocal

# Real, measured finding: running this alongside the two other already-running background
# workers (entry-side corpus replay + adaptive state backfill) pushed the live health-check
# latency past 8s (timeout) -- the SAME class of incident already documented earlier this
# session from excessive replay parallelism. Throttled per the explicit safety mandate: small
# batch, a sleep between every trade, and resumable (skips trades that already have a
# historical_analog_v1 row) so repeated small runs make cumulative progress without repeating
# that incident.
_BATCH_LIMIT = int(os.getenv("BACKFILL_BATCH_LIMIT", "30"))
_SLEEP_SECONDS = float(os.getenv("BACKFILL_SLEEP_SECONDS", "1.5"))


async def main() -> None:
    svc = AdaptiveManagementService()
    with SessionLocal() as db:
        already_done = {row[0] for row in db.query(CounterfactualOutcomeORM.trade_id).filter(CounterfactualOutcomeORM.policy_id == "historical_analog_v1").all()}
        # Real, measured finding: most of the OLDEST closed positions in this corpus have no
        # AdaptiveTradeEventORM DEAL rows at all (pre-dates reliable deal capture) and would
        # only ever hit the "skip" branch below -- filtering to positions that actually HAVE
        # deal data up front avoids wasting whole batches walking through unreplayable history.
        has_deals = {r[0] for r in db.query(AdaptiveTradeEventORM.position_id).filter(AdaptiveTradeEventORM.event_type == "DEAL").distinct().all()}
        closed = (
            db.query(AdaptivePositionStateORM)
            .filter(AdaptivePositionStateORM.closed_detected_at.isnot(None), AdaptivePositionStateORM.position_id.in_(has_deals))
            .order_by(AdaptivePositionStateORM.closed_detected_at.asc())
            .all()
        )
        rows = [
            {
                "position_id": r.position_id, "symbol": r.symbol, "direction": r.direction,
                "original_volume": r.original_volume, "entry_price": r.entry_price, "original_sl": r.original_sl,
                "original_tp": r.original_tp, "opened_at": r.opened_at, "closed_detected_at": r.closed_detected_at,
                "strategy_id": r.strategy_id, "timeframe": r.timeframe,
            }
            for r in closed if r.position_id not in already_done
        ][:_BATCH_LIMIT]
    print(f"already_done={len(already_done)} remaining_in_this_batch={len(rows)}", flush=True)

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
        if (i + 1) % 10 == 0:
            print(f"progress {i+1}/{len(rows)} ok={ok} skipped={skipped} failed={failed}", flush=True)

    print(f"DONE ok={ok} skipped={skipped} failed={failed}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
