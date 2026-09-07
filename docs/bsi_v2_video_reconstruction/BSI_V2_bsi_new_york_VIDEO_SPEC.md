# BSI V2 New York Video Spec

Source basis: original mentor videos 10-14, reconstructed from audiovisual rulebooks and visual-fidelity notes. This is a video-first reconstruction. Current code is compared only after the mentor rules.

## Mentor Rule

New York is a swept-level retest strategy. It does not use OB, FVG, Fibonacci, premium/discount, MSS, or secondary structure confirmation in the examples.

## Required Sequence

1. Identify a significant prior swing high or swing low before/during the New York setup.
2. During the New York session, price must wick beyond that exact swing level.
3. The sweep should be a small fakeout, not a large displacement away from the level.
4. Price must reclaim and return to the exact swept level.
5. Entry is the retest touch of the original swept price level.
6. Stop goes tight beyond the fakeout extreme.
7. Target is fixed 1:2 RR.

## Liquidity

Liquidity is a single significant structural swing high/low. The videos do not establish equal highs/lows, Asian range edges, prior-day levels, or trendline liquidity as requirements for this strategy.

"Significant" is qualitative in the videos. The mentor contrasts valid levels with small insignificant lows, but gives no numeric minimum-distance or ATR rule.

## Sweep

The sweep is a wick poke beyond the original level. The videos do not require a candle close beyond the level.

Do not import the Under/Over close-only rule into New York.

## Reclaim And Entry

Entry is at the original drawn level that was swept, not at the wick tip. The retest itself is the trigger.

If price never comes back to the level and instead runs straight to target, the setup is invalid. Do not chase.

## Stops And Targets

Stops are tight beyond the fakeout extreme. Examples show small stops around 3.6, 9.1, 10, and 14.2 pips depending on the chart.

Target is fixed 1:2. The mentor repeatedly uses TradingView RR set to exactly 2.

## Session And Instruments

The sweep must occur in the New York session. The larger setup legs may form earlier, including London.

Best-pair list shown visually: EURUSD, GBPUSD, AUDUSD, NAS100, US30, NZDUSD, EURGBP, EURCAD.

## Non-Rules

- No OB/FVG entry.
- No premium/discount or Fib.
- No MSS confirmation after sweep.
- No Under/Over close-only break requirement.
- No variable natural-liquidity target.

## Current Code Comparison

Current `evaluate_bsi_new_york` matches the bare-retest shape, fixed 1:2 target, and NY-window sweep requirement.

Potential mismatch: code appears to use the sweep wick extreme as `swept_price`, while the mentor entry should be the original level that got swept. If true, this changes the entry/stop geometry materially.

Not implemented or unconfirmed: qualitative significant-swing filter, best-pair allowlist, small-fakeout filter, and no-chase invalidation when price never retests.
