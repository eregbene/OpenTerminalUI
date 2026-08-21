"""READ-ONLY: compare ATM-managed REAL closed-position outcomes (adaptive_position_states) against
the static (SL/TP-touch-replay) historical corpus baseline, per strategy, all-time (not just the
performance monitor's rolling 14d window). Solo (non-fused) positions only, excluding engineering-
contaminated rows, matching performance_monitor.py's own methodology (max_achieved_r -
current_giveback_r as the realized R). No writes."""
from __future__ import annotations
import json
from sqlalchemy import text
from backend.shared.db import engine

# Canonical strategy_id -> known solo-label aliases actually observed in adaptive_position_states
# (see distinct strategy_id survey run manually before this script). Multi-strategy "fused" labels
# (containing '+') are always excluded by the query itself, never by this map.
ALIASES = {
    "mtfai1": ["mtfai1", "MTFAI1"],
    "support_resistance_bounce": ["support_resistance_bounce", "srbounce"],
    "momentum": ["momentum"],
    "session_breakout": ["session_breakout"],
    "vwap_reversion": ["vwap_reversion"],
    "breakout": ["breakout"],
    "ema_trend": ["ema_trend"],
    "mean_reversion": ["mean_reversion"],
    "smc_continuation": ["smc_continuation", "smc_cont"],
    "trend_pullback": ["trend_pullback", "pullback"],
    "liquidity_sweep_reversal": ["liquidity_sweep_reversal", "liqsweep"],
    "wyckoff": ["wyckoff"],
}

SQL = """
SELECT count(*) AS n,
  avg(max_achieved_r - current_giveback_r) AS avg_managed_r,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY max_achieved_r - current_giveback_r) AS median_managed_r,
  avg(max_achieved_r) AS avg_max_achieved_r,
  avg(current_giveback_r) AS avg_giveback_r,
  min(opened_at) AS first_trade, max(closed_detected_at) AS last_trade,
  avg(case when (max_achieved_r - current_giveback_r) > 0 then 1.0 else 0.0 end) AS win_rate
FROM adaptive_position_states
WHERE strategy_id = ANY(:aliases)
  AND closed_detected_at IS NOT NULL
  AND contaminated = false
  AND max_achieved_r IS NOT NULL
"""


def main():
    out = {}
    with engine.connect() as conn:
        for strategy, aliases in ALIASES.items():
            row = conn.execute(text(SQL), {"aliases": aliases}).mappings().first()
            out[strategy] = dict(row) if row else {}
            print(strategy, dict(row) if row else None, flush=True)
    with open("/app/scratch_diagnostic_atm_vs_static.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("written", flush=True)


if __name__ == "__main__":
    main()
