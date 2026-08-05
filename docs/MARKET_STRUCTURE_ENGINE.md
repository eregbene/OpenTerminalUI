# Market Structure Engine

The Phase 5 engine lives in `backend/market_structure`.

Data flow:

```text
validated OHLCV bars
-> normalized StructureBar rows
-> confirmed swings
-> trend state
-> structure breaks
-> displacement
-> liquidity references and sweeps
-> FVG lifecycle
-> order-block lifecycle
-> dealing range and premium/discount
-> overlays, events, scores and feature rows
```

Core calculations are pure Python and do not require FastAPI, Redis, database access or provider calls. The public wrapper is `MarketStructureEngine.analyze`.

The engine supports historical batch mode and an incremental resume path through serializable `EngineState`. Incremental mode merges restored recent bars with new completed bars and reruns deterministic analysis for equivalent confirmed results.

The first vertical slice is intentionally conservative. It detects measurable references; it does not create orders or strategies.
# Strategy Integration

Phase 6 strategy inputs call the Phase 5 market-structure engine with the internal profile and expose selected SMC evidence through the strategy feature namespace.

The market-structure engine remains unchanged; strategy code consumes its snapshot output.
