"""READ-ONLY: dump current strategy_performance_recommendations rows + confidence calibration
component effectiveness + confidence band report. No writes."""
from __future__ import annotations
import json
from sqlalchemy import text
from backend.shared.db import engine
from backend.brokers.mt5 import confidence_calibration as cc

def main():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT strategy_id, data_source, sample_size, win_rate, expectancy_r, realized_usd, "
            "avg_realized_usd, current_activation, recommended_activation, reasoning, status, computed_at "
            "FROM strategy_performance_recommendations ORDER BY strategy_id, computed_at DESC"
        )).mappings().all()
    print("=== strategy_performance_recommendations (latest per strategy) ===")
    seen = set()
    for r in rows:
        if r["strategy_id"] in seen:
            continue
        seen.add(r["strategy_id"])
        print(json.dumps(dict(r), default=str))

    print("\n=== confidence band report (combined) ===")
    band = cc.confidence_band_report()
    print(json.dumps(band["combined"], indent=2, default=str))

    print("\n=== calibration reliability report ===")
    print(json.dumps(cc.calibration_reliability_report(), indent=2, default=str))

    print("\n=== component effectiveness report ===")
    print(json.dumps(cc.component_effectiveness_report(), indent=2, default=str))

if __name__ == "__main__":
    main()
