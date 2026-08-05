from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.trading.serialization import model_to_jsonable, read_json, write_json
from backend.forex_strategies.models import ActiveTrade, TradeCandidate


class ForexSignalStore:
    def __init__(self, root: Path | str = "data/forex_strategies") -> None:
        self.root = Path(root)
        self.path = self.root / "forex_signal_store.json"

    def load(self) -> dict[str, Any]:
        data = read_json(self.path)
        return {
            "candidates": data.get("candidates", {}),
            "active_trades": data.get("active_trades", {}),
            "audits": data.get("audits", []),
        }

    def save(self, data: dict[str, Any]) -> None:
        write_json(self.path, data)

    def upsert_candidate(self, candidate: TradeCandidate) -> None:
        data = self.load()
        data["candidates"][candidate.candidate_id] = model_to_jsonable(candidate)
        self.save(data)

    def upsert_trade(self, trade: ActiveTrade) -> None:
        data = self.load()
        data["active_trades"][trade.trade_id] = model_to_jsonable(trade)
        self.save(data)

    def candidates(self) -> list[TradeCandidate]:
        return [TradeCandidate.model_validate(row) for row in self.load()["candidates"].values()]

    def trades(self) -> list[ActiveTrade]:
        return [ActiveTrade.model_validate(row) for row in self.load()["active_trades"].values()]

    def get_candidate(self, candidate_id: str) -> TradeCandidate:
        row = self.load()["candidates"].get(candidate_id)
        if not row:
            raise KeyError(candidate_id)
        return TradeCandidate.model_validate(row)

