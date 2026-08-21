"""READ-ONLY diagnostic segmentation for strategy-diagnostic-audit-2017-2026.md.

Pure SQL aggregation (GROUP BY / percentile_cont) against historical_pattern_fingerprints JOIN
historical_setup_outcomes -- no writes, no row-level Python loops over the full corpus (keeps
this light enough to run alongside live trading). Filters RESOLVED + non-UNTRUSTED throughout,
matching the established convention (statistics.py::pattern_statistics, strategy_robustness.py).
"""
from __future__ import annotations
import json
from sqlalchemy import text
from backend.shared.db import engine

STRATEGIES = [
    "mtfai1", "support_resistance_bounce", "momentum", "session_breakout", "vwap_reversion",
    "breakout", "ema_trend", "mean_reversion", "smc_continuation", "trend_pullback",
    "liquidity_sweep_reversal", "wyckoff",
]

BASE_FILTER = """
    hpf.anchor_strategy = :strategy
    AND hso.resolution_status = 'RESOLVED'
    AND hso.data_quality != 'UNTRUSTED'
    AND hso.outcome_r IS NOT NULL
"""

DIM_SQL = """
SELECT {dim} AS bucket, count(*) AS n,
  avg(hso.outcome_r) AS avg_r,
  avg(case when hso.outcome_r > 0 then 1.0 else 0.0 end) AS win_rate,
  avg(hso.mfe_r) AS avg_mfe, avg(hso.mae_r) AS avg_mae
FROM historical_pattern_fingerprints hpf
JOIN historical_setup_outcomes hso ON hso.fingerprint_id = hpf.fingerprint_id
WHERE {base}
GROUP BY {dim}
ORDER BY n DESC
"""

MAE_MFE_SQL = """
SELECT
  count(*) AS n,
  count(*) FILTER (WHERE hso.outcome_r > 0) AS n_wins,
  count(*) FILTER (WHERE hso.outcome_r < 0) AS n_losses,
  avg(hso.outcome_r) AS avg_r,
  avg(hso.mfe_r) FILTER (WHERE hso.outcome_r > 0) AS avg_mfe_win,
  avg(hso.mae_r) FILTER (WHERE hso.outcome_r > 0) AS avg_mae_win,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY hso.mfe_r) FILTER (WHERE hso.outcome_r > 0) AS median_mfe_win,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY hso.mae_r) FILTER (WHERE hso.outcome_r > 0) AS median_mae_win,
  avg(hso.mfe_r) FILTER (WHERE hso.outcome_r < 0) AS avg_mfe_loss,
  avg(hso.mae_r) FILTER (WHERE hso.outcome_r < 0) AS avg_mae_loss,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY hso.mfe_r) FILTER (WHERE hso.outcome_r < 0) AS median_mfe_loss,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY hso.mae_r) FILTER (WHERE hso.outcome_r < 0) AS median_mae_loss,
  avg(hso.time_to_0_5r_seconds) FILTER (WHERE hso.outcome_r > 0) AS avg_time_to_0_5r_win,
  avg(hso.time_to_1r_seconds) FILTER (WHERE hso.outcome_r > 0) AS avg_time_to_1r_win,
  avg(hso.time_to_mfe_seconds) AS avg_time_to_mfe,
  avg(hso.holding_duration_seconds) AS avg_holding_seconds,
  avg(case when hso.reached_0_25r then 1.0 else 0.0 end) AS reached_0_25r_rate,
  avg(case when hso.reached_0_5r then 1.0 else 0.0 end) AS reached_0_5r_rate,
  avg(case when hso.reached_0_75r then 1.0 else 0.0 end) AS reached_0_75r_rate,
  avg(case when hso.reached_1r then 1.0 else 0.0 end) AS reached_1r_rate,
  avg(case when hso.reached_1_5r then 1.0 else 0.0 end) AS reached_1_5r_rate,
  avg(case when hso.reached_2r then 1.0 else 0.0 end) AS reached_2r_rate,
  avg(case when hso.immediate_failure then 1.0 else 0.0 end) AS immediate_failure_rate,
  avg(case when hso.reached_0_5r and hso.outcome_r < 0 then 1.0 else 0.0 end) AS pct_reached_0_5r_then_lost,
  avg(case when hso.reached_1r and hso.outcome_r < 0 then 1.0 else 0.0 end) AS pct_reached_1r_then_lost,
  -- fixed-target counterfactuals using path-aware reached_Xr / sl_hit booleans (NOT a fabricated
  -- mfe/mae reconstruction): a trade counts as a T-R win if it actually touched +T R at some point
  -- (reached_Xr, computed by the real walk-forward outcome resolver); if it never reached T but did
  -- hit the original SL, it counts as a -1R loss under the T-target counterfactual; trades that
  -- neither reached T nor hit SL (e.g. actual smaller TP hit, or timed out) are excluded and counted
  -- separately as "undetermined" so the counterfactual average is never silently biased.
  count(*) FILTER (WHERE hso.reached_1r) AS cf1_reach_n,
  count(*) FILTER (WHERE NOT hso.reached_1r AND hso.sl_hit) AS cf1_slhit_n,
  count(*) FILTER (WHERE NOT hso.reached_1r AND NOT hso.sl_hit) AS cf1_undetermined_n,
  count(*) FILTER (WHERE hso.reached_1_5r) AS cf15_reach_n,
  count(*) FILTER (WHERE NOT hso.reached_1_5r AND hso.sl_hit) AS cf15_slhit_n,
  count(*) FILTER (WHERE NOT hso.reached_1_5r AND NOT hso.sl_hit) AS cf15_undetermined_n,
  count(*) FILTER (WHERE hso.reached_2r) AS cf2_reach_n,
  count(*) FILTER (WHERE NOT hso.reached_2r AND hso.sl_hit) AS cf2_slhit_n,
  count(*) FILTER (WHERE NOT hso.reached_2r AND NOT hso.sl_hit) AS cf2_undetermined_n
FROM historical_pattern_fingerprints hpf
JOIN historical_setup_outcomes hso ON hso.fingerprint_id = hpf.fingerprint_id
WHERE {base}
"""

YEARLY_SQL = """
SELECT date_trunc('year', hpf.entry_time) AS yr, count(*) AS n,
  avg(hso.outcome_r) AS avg_r,
  avg(case when hso.outcome_r > 0 then 1.0 else 0.0 end) AS win_rate
FROM historical_pattern_fingerprints hpf
JOIN historical_setup_outcomes hso ON hso.fingerprint_id = hpf.fingerprint_id
WHERE {base}
GROUP BY 1 ORDER BY 1
"""

VERSION_SQL = """
SELECT hpf.strategy_version, count(*) AS n, min(hpf.entry_time) AS first_seen, max(hpf.entry_time) AS last_seen,
  avg(hso.outcome_r) AS avg_r
FROM historical_pattern_fingerprints hpf
JOIN historical_setup_outcomes hso ON hso.fingerprint_id = hpf.fingerprint_id
WHERE {base}
GROUP BY hpf.strategy_version ORDER BY min(hpf.entry_time)
"""

DIMENSIONS = {
    "symbol": "hpf.canonical_symbol",
    "session": "hpf.session",
    "regime_broad": "hpf.regime_broad",
    "confidence_band": "hpf.confidence_band",
    "direction": "hpf.direction",
    "atr_regime": "hpf.atr_regime",
}


def _rows_to_list(result):
    return [dict(r) for r in result.mappings().all()]


def main():
    out = {}
    with engine.connect() as conn:
        for strategy in STRATEGIES:
            print(f"=== {strategy} ===", flush=True)
            base = BASE_FILTER.replace(":strategy", "'" + strategy + "'")  # not used; keep param binding below
            strat_out = {}

            mae_mfe = conn.execute(text(MAE_MFE_SQL.format(base=BASE_FILTER)), {"strategy": strategy}).mappings().first()
            strat_out["mae_mfe"] = dict(mae_mfe) if mae_mfe else {}

            for dim_name, dim_col in DIMENSIONS.items():
                res = conn.execute(text(DIM_SQL.format(dim=dim_col, base=BASE_FILTER)), {"strategy": strategy})
                strat_out[f"by_{dim_name}"] = _rows_to_list(res)

            yearly = conn.execute(text(YEARLY_SQL.format(base=BASE_FILTER)), {"strategy": strategy})
            strat_out["yearly"] = _rows_to_list(yearly)

            versions = conn.execute(text(VERSION_SQL.format(base=BASE_FILTER)), {"strategy": strategy})
            strat_out["by_strategy_version"] = _rows_to_list(versions)

            out[strategy] = strat_out
            print(f"  n={mae_mfe['n'] if mae_mfe else 0}", flush=True)

    with open("/app/scratch_diagnostic_segmentation.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("written to /app/scratch_diagnostic_segmentation.json", flush=True)


if __name__ == "__main__":
    main()
