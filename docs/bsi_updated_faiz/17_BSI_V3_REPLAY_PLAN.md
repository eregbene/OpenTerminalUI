# BSI V3 Replay Plan

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

Status: partial. Do not use this as production approval.

## Replay Scope

Replay must validate each V3 strategy independently before any demo enablement:

- Signal frequency.
- Entry timing.
- SL placement.
- Target placement.
- Adaptive management behavior.
- Rejection reasons.

## Minimum Replay Cases

- One golden example from each source video where the mentor shows a completed trade.
- One negative example where the setup must be rejected.
- At least one live-market month across the broker symbols currently available.
- Separate symbol groups for forex, gold, indices, and crypto where the mentor allows them.

## Required Metrics

- Candidate count by strategy/symbol/day.
- Rejection count by exact strategy-specific reason.
- Filled trades by strategy/symbol/day.
- Win/loss, BE, partial, and full-target count.
- Average initial SL distance by strategy and symbol.
- Trades where RR was rejected because target room was insufficient.
- Trades where adaptive manager changed SL, partially closed, or closed.

## Failure Conditions

- Any generic rejection reason dominates without strategy-specific detail.
- Any strategy places stops inside the mentor invalidation structure.
- Any adaptive close exits before taught target logic without a matching source rule.
- Any instrument trades a strategy the mentor explicitly discouraged.
- Any visual golden example cannot be reproduced by candle/replay state.
