"""Bensim -- Adaptive Manager V3 continuation, Part 12: top_k=1 competition measurement.

Only ONE candidate is submitted per account per cycle (autonomous.py: top_k = sorted(eligible,
key=ranking_score)[:confidence_top_k_candidates], confidence_top_k_candidates=1). This measures,
per strategy, whether MTFAI1's high candidate frequency is starving the other 9 strategies out of
that single slot -- grouping mt5_candidate_evaluations by cycle_id (+account_id, since each of the
4 DEMO accounts runs its own independent top_k=1 selection per cycle).

RAW_CANDIDATES: total rows for this strategy (every cycle it was even considered, pass or fail).
CONF_QUALIFIED: of those, overall_confidence >= 75.
CYCLES_PRESENT: distinct (cycle_id, account_id) this strategy had ANY candidate in.
TOP_K_WINS: of CYCLES_PRESENT, how many times this strategy's candidate was rank==1 in that
  cycle+account (the one eligible to be submitted).
TOP_K_LOSSES: CYCLES_PRESENT - TOP_K_WINS -- present but out-ranked by a different strategy.
EXECUTED: outcome_type == EXECUTED (actually submitted to the broker).

Cheap, pure read -- safe to re-run any time.
"""
from collections import defaultdict

from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.shared.db import SessionLocal

TIER = {
    "mtfai1": "A", "trend_pullback": "A", "mean_reversion": "A",
    "vwap_reversion": "B", "liquidity_sweep_reversal": "B", "smc_continuation": "B",
    "support_resistance_bounce": "C", "breakout": "C", "ema_trend": "C", "session_breakout": "C",
}
CONF_THRESHOLD = 75.0


def main():
    with SessionLocal() as db:
        rows = db.query(
            MT5CandidateEvaluationORM.strategy, MT5CandidateEvaluationORM.cycle_id, MT5CandidateEvaluationORM.account_id,
            MT5CandidateEvaluationORM.overall_confidence, MT5CandidateEvaluationORM.rank, MT5CandidateEvaluationORM.outcome_type,
        ).filter(MT5CandidateEvaluationORM.strategy.isnot(None)).all()

    raw = defaultdict(int)
    qualified = defaultdict(int)
    executed = defaultdict(int)
    cycles_present = defaultdict(set)
    cycles_won = defaultdict(set)
    cycle_best_rank = {}  # (cycle_id, account_id) -> best (lowest) rank seen this cycle, any strategy

    for strategy, cycle_id, account_id, confidence, rank, outcome_type in rows:
        raw[strategy] += 1
        if (confidence or 0) >= CONF_THRESHOLD:
            qualified[strategy] += 1
        if outcome_type == "EXECUTED":
            executed[strategy] += 1
        key = (cycle_id, account_id)
        cycles_present[strategy].add(key)
        if rank == 1:
            cycles_won[strategy].add(key)

    print(f"{'STRATEGY':<28}{'RAW_CAND':>10}{'CONF_QUAL':>11}{'CYCLES_PRESENT':>16}{'TOP_K_WINS':>12}{'TOP_K_LOSSES':>14}{'EXECUTED':>10}")
    for strategy_id, tier in TIER.items():
        present = len(cycles_present.get(strategy_id, ()))
        wins = len(cycles_won.get(strategy_id, ()))
        losses = present - wins
        print(f"{strategy_id:<28}{raw.get(strategy_id,0):>10}{qualified.get(strategy_id,0):>11}{present:>16}{wins:>12}{losses:>14}{executed.get(strategy_id,0):>10}")


if __name__ == "__main__":
    main()
