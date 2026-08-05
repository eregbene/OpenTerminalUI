from pathlib import Path

from backend.forex_strategies.models import CandidateStatus
from backend.forex_strategies.service import ForexSignalService
from backend.forex_strategies.store import ForexSignalStore


def test_risk_gate_rejects_low_quality_data(tmp_path: Path):
    service = ForexSignalService(ForexSignalStore(tmp_path))
    candidate = service.generate({"symbol": "EURUSD", "timeframe": "1h", "price": 1.085, "regime": "trend", "data_quality": 0.5, "frameworks": ["trend_following", "price_action", "support_resistance"]})

    assert candidate.status == CandidateStatus.REJECTED
    assert any("DATA_QUALITY_BELOW_MINIMUM" in row.reasons for row in candidate.eligibility)

