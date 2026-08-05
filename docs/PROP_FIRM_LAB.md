# Prop-Firm Challenge Training Laboratory

The Prop-Firm Laboratory is a research-only simulator for evaluating whether current deterministic FX strategy candidates could survive common challenge rules.

It does not submit broker orders, call OpenAI, change live scheduler state, consume acceptance entries, or alter production thresholds.

## Profiles

Profiles are data-driven and versioned under `backend/research/prop_firms/profiles.py`.

Included research defaults:

- FTMO 2-Step Phase 1
- FTMO 2-Step Phase 2
- FTMO 1-Step
- FundedNext Stellar 2-Step Phase 1
- FundedNext Stellar 2-Step Phase 2
- The5ers High Stakes Phase 1
- The5ers High Stakes Phase 2

All profiles are marked as requiring external verification before any real-world use. Prop-firm rules can change.

## Internal Safety

Internal limits are stricter than firm limits and are configurable with `PROP_INTERNAL_*` environment variables.

Defaults include:

- 0.25% risk per trade
- 1.00% max daily loss
- 2.00% max weekly loss
- 4.00% max total drawdown
- one open position
- two trades per day
- two consecutive losses

## Simulation

The simulator uses available canonical EURUSD 15m history and current deterministic strategy/consensus logic. If fewer than 100 simulated trades are available, reports are labeled `INSUFFICIENT_DATA` and no production configuration is recommended.

Execution scenarios include spread, commission, slippage, stop slippage, swap, delayed fills, partial fills, and deterministic rejected-entry assumptions.

## API

- `GET /api/research/prop-firms/profiles`
- `POST /api/research/prop-firms/profiles`
- `GET /api/research/prop-firms/profiles/{profile_id}`
- `POST /api/research/prop-firms/simulations`
- `GET /api/research/prop-firms/simulations`
- `GET /api/research/prop-firms/simulations/{job_id}`
- `POST /api/research/prop-firms/simulations/{job_id}/cancel`
- `POST /api/research/prop-firms/simulations/{job_id}/resume`
- `GET /api/research/prop-firms/simulations/{job_id}/report`
- `GET /api/research/prop-firms/simulations/{job_id}/trades`
- `GET /api/research/prop-firms/simulations/{job_id}/attempts`
- `GET /api/research/prop-firms/simulations/{job_id}/artifacts`
- `GET /api/research/prop-firms/leaderboard`
- `GET /api/research/prop-firms/compatibility`

## Safety

Every simulation report includes safety verification:

- OpenAI calls equal zero
- IBKR calls equal zero
- live acceptance state unchanged
- live processed candles unchanged
- production thresholds unchanged
- validation thresholds unchanged
- live trading disabled

Simulation results are research outputs, not guarantees of passing any external prop-firm challenge.
