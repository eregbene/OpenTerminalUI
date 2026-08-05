from __future__ import annotations

from backend.ai_assistant.adapters.base import AdapterResult, EvidenceAdapter
from backend.ai_assistant.adapters.market_structure import MarketStructureEvidenceAdapter
from backend.ai_assistant.adapters.research import ResearchEvidenceAdapter
from backend.ai_assistant.adapters.strategy import StrategyEvidenceAdapter
from backend.ai_assistant.adapters.trading import TradingEvidenceAdapter

__all__ = [
    "AdapterResult",
    "EvidenceAdapter",
    "MarketStructureEvidenceAdapter",
    "ResearchEvidenceAdapter",
    "StrategyEvidenceAdapter",
    "TradingEvidenceAdapter",
]
