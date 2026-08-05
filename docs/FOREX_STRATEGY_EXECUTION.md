# Forex Strategy Execution

FX-4 adds a controlled paper-only workflow that converts deterministic forex evidence into paper trade candidates.

## Flow

```text
forex candles
-> forex intelligence
-> framework signals
-> strategy eligibility
-> deterministic candidate
-> FX risk record
-> existing paper OMS intent
-> existing simulator fill
-> active paper trade view
```

## Safety Boundary

- Paper only.
- Manual confirmation by default.
- AI and Research Agent have no execution authority.
- Framework signals remain evidence only.
- XAU/USD execution is blocked until a compatible spot or broker-backed paper contract exists.
- Live trading modes are not accepted.
- `LOCAL_SIMULATOR` and `IBKR_PAPER` are explicit providers. IBKR paper never silently falls back to the simulator.

## Initial Strategies

- `trend_pullback_v1`: paper candidate, manual confirmation, validation fixture present.
- `breakout_retest_v1`: paper candidate, manual confirmation, validation fixture present.
- `mean_reversion_range_v1`: research.
- `liquidity_sweep_reversal_v1`: research.
- `session_breakout_v1`: research.
- `xauusd_analysis_only_v1`: analysis only.

## First Execution Surface

- `EURUSD`: paper eligible.
- `GBPUSD`: paper eligible.
- `USDJPY`: paper eligible.
- `USDCHF`, `USDCAD`, `AUDUSD`, `NZDUSD`: analysis only by default.
- `XAUUSD`: contract unavailable.

## Limitations

The implementation is a controlled vertical slice. It uses the existing paper OMS and simulator, and FX-5 adds mocked IBKR acceptance state. Real TWS/Gateway paper acceptance, Playwright workflows, full journal/outcome analytics, and real broker contract acceptance remain operator-run gates.
