from backend.forex_strategies.service import ForexSignalService
from backend.forex_strategies.store import ForexSignalStore


def test_strategy_selection_returns_rejection_reasons_for_unsupported_pair(tmp_path):
    service = ForexSignalService(ForexSignalStore(tmp_path))

    rows = service.eligibility({"symbol": "XAUUSD", "timeframe": "1h", "regime": "trend", "data_quality": 1, "frameworks": ["trend_following"]})

    assert any("XAUUSD_EXECUTION_DISABLED" in reason for row in rows for reason in row.reasons)
    assert all(row.eligible is False for row in rows if row.strategy_id != "xauusd_analysis_only_v1")
