# Market Structure Inventory

Phase 5 audit searched backend, frontend, docs, legacy desktop modules and plugins for pivot, fractal, ZigZag, support/resistance, BOS, CHoCH, MSS, liquidity, sweeps, equal highs/lows, FVG, order blocks, supply/demand, session levels, Fibonacci, SMC and ICT terms.

## Existing Implementations

- `backend/services/pattern_recognition_service.py`: backend-only chart-pattern detector. Complete for its scoped patterns, includes pivot highs/lows, support/resistance trendlines for triangles, double tops, head-and-shoulders, bull flags and cup-handle. It is not a point-in-time SMC engine and uses symmetric pivots for pattern recognition, so it was left untouched and documented as reusable reference logic only.
- `backend/api/routes/patterns.py`: API wrapper for pattern recognition. Working existing endpoint. Left untouched.
- `backend/breakout_engine/detectors.py` and `backend/scanner_engine/detectors.py`: breakout/trend-retest scanner rules. Complete for screeners, not canonical SMC. Left untouched.
- `backend/core/strategy_runner.py`: strategy trend indicators such as SMA, EMA, MACD, Supertrend and candlestick reversal models. Strategy-oriented and not reused.
- `backend/tests/test_patterns.py`: pivot tests for pattern recognition. Useful evidence for existing behavior, not reused directly.
- `frontend/src/shared/chart/drawingEngine.ts`: user drawing tools for manual trendlines/zones. Frontend-only and not deterministic detection.
- `frontend/src/shared/chart/contextOverlays.ts`: event/fundamental overlays. Reused conceptually for chart overlay style, not coupled.
- `trade_screens/*` and root `core/*`: legacy desktop technical/fundamental analysis helpers with trend/momentum labels. Left untouched.

## Gaps

No existing implementation provided canonical BOS, CHoCH, MSS, liquidity sweeps, FVG lifecycle, order-block lifecycle, dealing ranges, no-look-ahead confirmation timing, SMC event schemas or research feature rows.

## Phase 5 Decision

Created `backend/market_structure` as a new pure calculation package. Existing pattern/scanner code remains available but was not duplicated into the core engine because it has different semantics and consumers.
