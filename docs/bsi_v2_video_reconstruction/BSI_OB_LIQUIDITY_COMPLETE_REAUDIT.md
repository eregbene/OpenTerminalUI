# BSI OB Liquidity Complete Reaudit

Sources: local video 29, transcript, contact sheet.

## Finding

Order Block Liquidity is not Reactionary. Reactionary uses a second array. OB Liquidity uses the same origin OB as the liquidity object: fakeout through it, reclaim it, then retest it.

## Chronology

STRUCTURE SHIFT/BREAK -> MARK LAST QUALIFYING CANDLE OB -> WAIT FOR SAME OB FAKEOUT -> RECLAIM -> RETEST SAME OB -> ENTRY -> TARGET.

## Rules

- Qualifying OB: last bullish candle before bearish shift, or last bearish candle before bullish shift.
- OB must be local extremum: for bullish setup, last bearish candle must be the lowest; if a later candle wicks lower, setup invalid. Opposite for bearish.
- Internal structure break is enough; full external structure break is not mandatory.
- Fakeout: body close beyond OB far edge. Wicks do not count.
- Reclaim: close back through the same OB/level.
- Entry: retest of same OB/level.
- Stop: below/above same OB or previous low/high depending risk preference.
- Target: next liquidity or imbalance.
- Heavy clean reaction from OB before fakeout means it was a valid OB, not an OB-liquidity setup.
- Residual wick liquidity must be cleared first.
- Huge fakeout is disfavored/not valid; no numeric threshold given.

## Why August OB Liquidity Count Was High

The original high count likely came from treating ordinary OB mitigations as OB Liquidity. The current V2 raw extractor now includes close fakeout/reclaim/retest and skips heavy early reaction, but it still needs stronger proof of:

- local-extremum OB candle,
- residual wick liquidity clearance,
- fakeout-size quality,
- exact same-array sequencing.

August after video recheck showed 114 setups, not the older 1,088 cited in the brief.
