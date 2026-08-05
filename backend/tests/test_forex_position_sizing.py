from pathlib import Path

from backend.forex_strategies.service import ForexSignalService
from backend.forex_strategies.store import ForexSignalStore


def test_position_sizing_caps_default_fx_quantity(tmp_path: Path):
    service = ForexSignalService(ForexSignalStore(tmp_path))
    candidate = service.generate({"symbol": "EURUSD", "timeframe": "1h", "price": 1.085, "regime": "trend", "data_quality": 1, "frameworks": ["trend_following", "price_action", "support_resistance"]})

    assert candidate.risk_decision
    assert candidate.risk_decision.position_size <= 50000
    assert candidate.risk_decision.estimated_risk <= 250

