# Portfolio Replay

Replay sessions are read-only.

Controls such as pause or step return `mutated_trading_state=false`. Replay events are ordered by sequence and do not write to OMS, broker, portfolio, or strategy state.
# Phase 12.1 Replay Update

Replay sessions are now durable records in `phase12_replay_sessions`; replay events remain append-only in `phase12_replay_events`. Replay controls mutate only session cursor/status and never current trading state.
