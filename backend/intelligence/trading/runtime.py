from __future__ import annotations

from backend.intelligence.trading.auto_paper import AIAutoPaperTradingService
from backend.intelligence.trading.service import ai_trading_service


auto_paper_service = AIAutoPaperTradingService(ai_trading_service)


__all__ = ["ai_trading_service", "auto_paper_service"]
