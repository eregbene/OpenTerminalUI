"""Bensim -- Adaptive Manager V3 continuation, Part 11: verify strategy risk tiers survive real
broker order sizing (not just the env-var multiplier in isolation).

Joins REAL executed trades (mt5_trade_records.lot_size, .projected_risk) to their originating
candidate (via candidate_id -> mt5_candidate_evaluations.candidate_id) to get strategy_id, then
reports avg lot_size and avg projected_risk PER STRATEGY PER ACCOUNT (account matters: the 4 DEMO
accounts have different equity: 10k/25k/50k/100k, so lot sizes aren't comparable across accounts
without grouping). If Tier C's 0.25x multiplier were being erased by the broker's minimum-lot
rounding (e.g. MT5's 0.01 lot floor), Tier C strategies' avg lot_size on a given account would
converge toward Tier A's rather than sitting near ~1/4 of it.

Cheap, pure read -- safe to re-run any time.
"""
import statistics as st
from collections import defaultdict

from backend.brokers.mt5.orm import MT5CandidateEvaluationORM, MT5TradeRecordORM
from backend.shared.db import SessionLocal

TIER = {
    "mtfai1": "A", "trend_pullback": "A", "mean_reversion": "A",
    "vwap_reversion": "B", "liquidity_sweep_reversal": "B", "smc_continuation": "B",
    "support_resistance_bounce": "C", "breakout": "C", "ema_trend": "C", "session_breakout": "C",
}


def main():
    with SessionLocal() as db:
        candidate_strategy = dict(db.query(MT5CandidateEvaluationORM.candidate_id, MT5CandidateEvaluationORM.strategy).filter(MT5CandidateEvaluationORM.strategy.isnot(None)).all())
        trades = db.query(MT5TradeRecordORM.candidate_id, MT5TradeRecordORM.account_id, MT5TradeRecordORM.lot_size, MT5TradeRecordORM.projected_risk).filter(MT5TradeRecordORM.lot_size.isnot(None)).all()

    by_account_tier = defaultdict(lambda: defaultdict(list))  # account_id -> tier -> [(strategy_id, lot_size, projected_risk)]
    for candidate_id, account_id, lot_size, projected_risk in trades:
        strategy_id = candidate_strategy.get(candidate_id)
        if not strategy_id or strategy_id not in TIER:
            continue
        by_account_tier[account_id][TIER[strategy_id]].append((strategy_id, lot_size, projected_risk))

    for account_id in sorted(by_account_tier):
        print(f"\n{'='*90}\naccount: {account_id}\n{'='*90}")
        print(f"  {'TIER':<6}{'N':>6}{'AVG_LOT_SIZE':>16}{'AVG_PROJECTED_RISK_USD':>24}")
        tier_avgs = {}
        for tier in ("A", "B", "C"):
            rows = by_account_tier[account_id].get(tier, [])
            if not rows:
                print(f"  {tier:<6}{'0':>6}   (no executed trades yet)")
                continue
            lots = [r[1] for r in rows]
            risks = [r[2] for r in rows if r[2] is not None]
            avg_lot = st.fmean(lots)
            avg_risk = st.fmean(risks) if risks else None
            tier_avgs[tier] = avg_lot
            risk_s = f"{avg_risk:.2f}" if avg_risk is not None else "n/a"
            print(f"  {tier:<6}{len(rows):>6}{avg_lot:>16.4f}{risk_s:>24}")
        if "A" in tier_avgs and "C" in tier_avgs and tier_avgs["A"] > 0:
            ratio = tier_avgs["C"] / tier_avgs["A"]
            expected = 0.25
            verdict = "OK -- close to the intended 0.25x" if 0.10 <= ratio <= 0.45 else ("EXPECTED >>REDUCED<< NOT VISIBLE (min-lot floor likely erasing it)" if ratio > 0.45 else "SMALLER THAN EXPECTED (investigate)")
            print(f"  Tier C / Tier A avg lot ratio: {ratio:.3f}  (expected ~{expected})  -> {verdict}")


if __name__ == "__main__":
    main()
