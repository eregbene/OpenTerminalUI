# BSI V2 OB Liquidity Video Examples

Source basis: original mentor video 29.

## OB_LIQUIDITY_GOLDEN_01

Video 29, EURUSD 5m long.

- Setup: internal structure/bias context.
- Origin: candle at local swing extreme.
- Fakeout: price closes beyond origin OB edge.
- Reclaim: price closes back through same level.
- Entry: retest of same origin OB.
- Stop: just below the box.
- Target: next imbalance/liquidity.
- Observed RR: about 3R in course notes.

## OB_LIQUIDITY_REJECT_01

Video 29, heavy clean reaction.

- Price respects the OB strongly without fakeout.
- Mentor says this is a valid OB, not a liquidity OB.
- Rule impact: do not wait for fakeout under the OB Liquidity model; route to plain Order Flow logic instead.

## OB_LIQUIDITY_GOLDEN_02

Video 29, stacked residual liquidity.

- Several nearby wick-liquidity pockets exist.
- Mentor requires those wicks to be cleared first.
- Only after residual liquidity clears can a fakeout/reclaim of the intended origin OB count.
- Rule impact: the fakeout must not be declared while older nearby liquidity remains uncleared.

## OB_LIQUIDITY_NON_RULE_01

Video 29.

- No second array is drawn.
- No premium/discount rule is re-taught.
- No fixed RR.
- No session rule.
