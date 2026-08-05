from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QuoteProvider(Protocol):
    async def get_quote(self, symbol: str) -> Mapping[str, Any]: ...


@runtime_checkable
class HistoricalDataProvider(Protocol):
    async def get_history(self, symbol: str, **kwargs: Any) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class NewsProvider(Protocol):
    async def get_news(self, symbol: str | None = None, **kwargs: Any) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class EconomicDataProvider(Protocol):
    async def get_series(self, series_id: str, **kwargs: Any) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class BrokerAdapter(Protocol):
    async def account(self) -> Mapping[str, Any]: ...


@runtime_checkable
class OrderExecutionService(Protocol):
    async def submit_order(self, order: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class PortfolioService(Protocol):
    async def get_portfolio(self, portfolio_id: str) -> Mapping[str, Any]: ...


@runtime_checkable
class RiskService(Protocol):
    async def evaluate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class StrategyService(Protocol):
    async def run(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class BacktestService(Protocol):
    async def run_backtest(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class AIResearchService(Protocol):
    async def analyze(self, prompt: str, context: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...
