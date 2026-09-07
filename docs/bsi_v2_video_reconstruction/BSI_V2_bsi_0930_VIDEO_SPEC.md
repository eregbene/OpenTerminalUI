# BSI V2 9:30 Video Spec

Source basis: original mentor videos 21-24, reconstructed from audiovisual rulebook, strategy spec, and visual audit.

## Mentor Rule

9:30 trades the New York market-open liquidity sweep on indices, aligned with daily/HTF bias, using lower-timeframe displacement/MSS and preferably an extreme FVG entry.

## Required Sequence

1. Before 09:30 New York time, identify liquidity on 15m or similar higher timeframe.
2. Establish daily/HTF directional bias.
3. Only trade in the bias direction.
4. At 09:30, switch to 1m execution, or 2m if no clean FVG is visible on 1m.
5. Wait for liquidity sweep.
6. Wait for MSS with displacement.
7. Reject clumsy, low-momentum structure breaks.
8. Select the FVG from the displacement leg, preferring the extreme FVG over the first.
9. Enter when price returns into the chosen FVG.
10. Stop beyond the origin/swept side.
11. Set target using natural opposing structure/liquidity bounded by 3R-5R.
12. Take partials in play and move to breakeven after favorable structure break.

## Time Window

Trades are only valid from 09:30 to 11:59 New York time. If price reaches the entry zone before 09:30, the trade is invalid.

## Instruments

The mentor says this works best with indices and recommends sticking to indices. All examples are NAS100 / US Nas 100.

## Liquidity

Liquidity may be pre-930 swing high/low, equal highs, or midnight-open-derived high/low. The exact form varies by example.

## Bias

Daily/HTF directional bias is required. Video 24 explicitly says a previous long should not have been taken because the larger bias was bearish.

## Entry Array

Extreme FVG is preferred. If the first MSS FVG never fills, a later MSS and its FVG can become the valid entry.

Exception: if no clean MSS forms but displacement/imbalance is very large, the mentor allows entry from the order block created by that displacement.

## Premium/Discount

No premium/discount, Fib, equilibrium, or OTE filter appears in the 9:30 videos.

## Target

Target-setting rule is minimum 3R, maximum 5R. Realized trade readouts can exceed 5R after trade management, as seen with the 8.42R outcome in video 22.

## Current Code Comparison

Current `evaluate_bsi_0930` matches the clock window, daily-bias gate, MSS-or-strong-displacement logic, extreme-FVG selection, and bounded 3R-5R target.

Gaps or ambiguities: instrument-class restriction is not implemented; partial/breakeven management appears metadata-only; second-MSS-after-unfilled-first-FVG sequencing is ambiguous if code simply picks latest break.
