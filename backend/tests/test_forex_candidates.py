from pathlib import Path

from backend.forex_strategies.models import CandidateStatus
from backend.forex_strategies.service import ForexSignalService
from backend.forex_strategies.store import ForexSignalStore


def test_candidate_store_persists_candidates(tmp_path: Path):
    store = ForexSignalStore(tmp_path)
    service = ForexSignalService(store)

    candidate = service.generate({"symbol": "EURUSD", "timeframe": "1h", "price": 1.085, "regime": "trend", "data_quality": 1, "frameworks": ["trend_following", "price_action", "support_resistance"]})

    reloaded = ForexSignalStore(tmp_path).get_candidate(candidate.candidate_id)
    assert reloaded.candidate_id == candidate.candidate_id
    assert reloaded.status in {CandidateStatus.AWAITING_CONFIRMATION, CandidateStatus.REJECTED}

