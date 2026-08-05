# Service Interfaces

Capability-specific protocols live in `backend/core/contracts/services.py`.

Defined interfaces:

- `QuoteProvider`
- `HistoricalDataProvider`
- `NewsProvider`
- `EconomicDataProvider`
- `BrokerAdapter`
- `OrderExecutionService`
- `PortfolioService`
- `RiskService`
- `StrategyService`
- `BacktestService`
- `AIResearchService`

These are intentionally small. Providers should implement only the capabilities they actually support.
# Phase 4 Extension

Market-data interfaces are now capability-specific in `backend/market_data/capabilities.py`: quote, historical candles, streaming quotes, market depth, instrument reference, fundamentals, news, economic data, options chain, options flow and corporate actions. Providers implement only capabilities they actually support.
# Phase 5 Market Structure Service Interface

Core entry point: `MarketStructureEngine.analyze(bars, symbol, timeframe, ...)`.

The interface is intentionally pure and has no FastAPI, database, Redis or provider dependency. API routes and future persistence adapters sit outside the calculation package.
# Strategy Service Interface

`backend/strategies/engine.py` exposes `StrategyEngine.compile`, `StrategyEngine.evaluate_context`, `StrategyEngine.evaluate_bars`, and `StrategyEngine.evaluate_batch`.

The service boundary accepts a `StrategySpec` plus completed bars or a built `StrategyContext`, and returns `StrategyEvaluation`.
