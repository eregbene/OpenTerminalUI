# BSI V2 OB Liquidity Video Spec

Source basis: original mentor video 29, reconstructed from audiovisual rulebook, strategy spec, and visual audit.

## Mentor Rule

Order Block Liquidity is Order Flow with a fakeout-through-and-reclaim twist on the same origin OB. It is mechanically distinct from Reactionary because it reuses the same array; it does not create a second array.

## Required Sequence

1. Directional structure is established; internal structure break is sufficient.
2. Mark the origin candle at the swing extreme.
3. The origin candle must be the local extremum.
4. Any older residual wick liquidity near the level must be cleared first.
5. Price must close beyond the origin OB far edge.
6. Wicks do not count as the fakeout.
7. Price must close back through the same level.
8. Enter on retest of the same origin OB/level.
9. Stop just beyond the origin OB.
10. Target the next imbalance/liquidity level.

## Origin Candle

The origin candle must be the lowest/highest candle locally. If a later candle wicks beyond it before the fakeout, the setup is invalid.

## Same-Array Rule

Only one origin OB is used. The fakeout, reclaim, and retest all relate to that same array.

## Valid OB Versus Liquidity OB

If price reacts heavily and cleanly from the OB without fakeout, the mentor says it is a valid OB, not a liquidity OB. Do not force the OB Liquidity read; plain Order Flow is the better route.

## Residual Liquidity

If older wick liquidity remains uncleared, the fakeout is not valid yet. Those wicks must be cleared before trusting the OB Liquidity setup.

## Fakeout Size

Fakeout size is fuzzy. A somewhat large fakeout can still work; a huge fakeout does not count. No numeric ATR threshold is given.

## Target

Target is next imbalance/liquidity. RR is variable; examples show low single-digit R and a hindsight-caveated very large move.

## Current Code Comparison

Current OB Liquidity logic correctly implements same-array fakeout/reclaim, local-extremum origin check, close-based fakeout/reclaim, and tight stop.

Not implemented: heavy-reaction valid-OB disqualifier/reroute, residual-liquidity clearance, and fakeout-size scoring/ceiling in this subtype's path.
