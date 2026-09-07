# BSI V2 Under/Over Video Spec

Source basis: original mentor videos 19-20, reconstructed from audiovisual rulebook and strategy spec.

## Mentor Rule

Under/Over fades a close-based fakeout through a well-tested support/resistance level, then trades the reclaim back toward the next opposing liquidity level.

## Required Sequence

1. Identify a clear support or resistance level.
2. Confirm at least three touches of that level.
3. Treat the origin/first-touch candle as the order-block-quality anchor.
4. Wait for a candle close beyond the level.
5. Wicks do not count as the break.
6. Wait for a candle close reclaim back through the level.
7. Enter at or near the reclaimed level.
8. Stop beyond the fakeout extreme / level box.
9. Take partials at intermediate levels.
10. Fully close when the next opposing level is taken out.

## Liquidity

The repeated support/resistance level is the liquidity pool. Touch count is explicit: at least three.

## Structure

No MSS, CHoCH, BOS, or swing-break confirmation is required in the dedicated Under/Over videos.

## Premium/Discount

No Fib, 50%, premium/discount, OTE, or zone check is shown or spoken.

## Entry

Entry is level-reclaim based, not FVG-entry based. A later FVG may be used as a partial target, not the entry trigger.

## Stop

Stop placement is discretionary but must be beyond the level/fakeout side. The mentor shows tighter and wider variants and explicitly leaves distance to trader judgment.

## Target

Target is the natural opposing structural/liquidity level. There is no fixed RR cap or fixed RR target.

## No-Trade Conditions

If the fakeout is unusually large, skip the setup because the stop becomes too large.

## Current Code Comparison

Current `evaluate_bsi_under_over` matches no-zone behavior, minimum three touches, close-based fakeout/reclaim, dynamic stop, and opposing-structure target.

Gap: partial/full-exit management appears recorded as metadata only and is not wired into live management.
