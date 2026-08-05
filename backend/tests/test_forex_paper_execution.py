from pathlib import Path

from backend.forex_strategies.models import CandidateStatus
from backend.forex_strategies.service import ForexSignalService
from backend.forex_strategies.store import ForexSignalStore
from backend.trading.persistence import TradingStore
from backend.trading.services import TradingControlService


def test_eurusd_paper_execution_vertical_slice(tmp_path: Path):
    service = ForexSignalService(ForexSignalStore(tmp_path / "fx"), TradingControlService(TradingStore(tmp_path / "trading")))
    candidate = service.generate({"symbol": "EURUSD", "timeframe": "1h", "price": 1.085, "regime": "trend", "data_quality": 1, "frameworks": ["trend_following", "price_action", "support_resistance"]})

    service.approve(candidate.candidate_id)
    filled = service.simulate_fill(candidate.candidate_id)

    assert filled.status == CandidateStatus.FILLED
    assert filled.oms_order_id
    assert filled.fill_ids
    assert service.list_trades()[0].symbol == "EURUSD"
