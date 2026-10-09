# BSI V3 Mentor Strategy Reconstruction

Source set: `F:\new faiz`, `125/125` videos, `125/125` transcripts, `125/125` visual contact sheets.

This document separates the mentor rule from the engine detector. The videos are the evidence. The detector registry is the mechanical translation used by backtests/live routing.

## Shared Mentor Entry Model

- Build higher-timeframe context first: daily, 4H, 1H, then M30/M15.
- Draw external liquidity, internal liquidity, range highs/lows, FVGs, orderblocks, premium/discount, and session levels.
- Wait for price to come into the POI. Do not enter just because a setup exists.
- Confirm on M1/M5 with sweep, MSS/BOS, displacement, and FVG/OB/BPR/breaker entry where applicable.
- Invalidate when price violates the protected swing/POI thesis, fails to return to the entry model, or moves outside the session window.
- SL belongs beyond the protected swing/POI/range extreme, not as a tiny arbitrary broker minimum.
- TP uses liquidity draw, opposite range side, fixed R, or model-specific target.
- Manage with consistent risk, break-even after favorable structure, partials at logical target/1R/2R, and reduced risk after losing streaks.

## Strategy Evidence Map

| Strategy | Mentor Videos / Examples Used | Detector Translation |
| --- | --- | --- |
| Risk Management | `27. Risk Management & Psychology`, `23. Risk Management For Prop Firm Accounts` | Fixed account risk, reduce after losing streak, no random risk jumps, BE/partials/trailing modeled in managed R. |
| Daily Bias | `26. DAILY BIAS MADE EASY`, `1. Finding Monthly, Weekly, & Daily Bias`, monthly/weekly/daily examples | HTF draw decides allowed direction; LTF MSS/FVG confirms only after liquidity context. |
| Order Flow | `1. Order Flow Trading Strategy`, MSB/MSS examples, `20. ORDERFLOW 101`, examples 1/2 | HTF bias plus structure/FVG/OB sequence; wait for POI retest before planned entry. |
| ABC | `10. ABC Trading Strategy`, examples 1/2, `19. ABC 101`, ABC POI combination, things to avoid | ABC structure, POI/FVG/OB entry, killzone and RR room, target B-leg liquidity or fixed R. |
| ABCD | `16. ABCD 101`, examples 1/2, `19. ABCD Trading Strategy`, ABCD example | D-leg structure, H4 bias, killzone, entry from FVG/supply-demand/OB. |
| Asian V2 | `23. ASIAN SESSION STRATEGY V2.0`, examples 1/2 | Mark Asian range, wait for sweep into POI, require M1 MSS/displacement, target opposite liquidity. |
| New York / 9:30 | `13. 930AM Trading Strategy (Updated)`, examples 1/2/3 | 9:30-11:59 NY only, liquidity sweep, M1 MSS/FVG, fixed 3R target model. |
| Orderblock 2.0 | `28. Orderblock 2.0`, `29. Orderblock 2.0 Example` | OB must be tied to displacement/liquidity, entry from valid POI, invalid below/above protected OB swing. |
| ICT Silver Bullet | `30. ICT Silver Bullet`, examples 1/2/3 | 10-11 or 14-15 NY, sweep before entry, M1 displacement/FVG, BE/partial behavior. |
| Silver Bullet With Bias | `24. Silver Bullet With Bias`, examples 1/2 | Same as Silver Bullet but only in direction of HTF bias/draw. |
| 4H OB | `34. 4 Hour OB Trading Strategy`, `35`, examples 2/3 | 4H OB creates POI, M15/M5 confirmation in killzone, target 2R/liquidity, BE after structure. |
| MMXM | `8. THE MMXM`, `9. THE MMXM 2`, `10. THE MMXM EXAMPLE 1`, `11. THE MMXM EXAMPLE 2`, `12. THE MMXM EXAMPLE 3` | HTF POI plus accumulation/distribution leg, consolidation liquidity, MSS, FVG/OB entry, target draw on liquidity. |
| MMXM Second Distribution | `13. MMXM 2ND DISTRIBUTION ENTRY`, examples 1/2/3 | Final fractal BOS/FVG, internal liquidity target, weekday/liquidity room filter. |
| Holy Grail | `17. The Holy Grail`, `18. The Holy Grail 2`, examples 1/2/3/4 | Daily external liquidity purge, HTF draw, M5 MMXM-style entry from FVG/OB. |
| Turtle Soups / Ranges | `40. Turtle Soups & Ranges Mastery` | Range high/low, liquidity buildup both sides, sweep, MSS, re-entry into range, target opposite range side. |
| Standard Deviations | `29. Standard Deviations`, `30. Standard Deviations 2` when present | PO3 manipulation, negative standard deviation levels, FVG/OB entry, liquidity target and partials. |

## Current Validation Result

Latest all-pair Jan-Aug 2026 planned-entry replay:

- Detector registry: `26/26` strategy IDs bound.
- Instant-entry same window: `24,033` trades, `64.85%` win rate, `6328.2157R`, PF `1.7805`, max DD `76.6369R`.
- Planned-entry: `10,621` trades, `70.04%` win rate, `4544.5328R`, PF `2.5305`, max DD `13.2343R`.
- Planned-entry plus adaptive bucket filter: `10,285` trades, `70.38%` win rate, `4476.7702R`, PF `2.5769`, max DD `11.5R`.

## Important Limitation

The planned-entry replay proves the queue concept and removes missing detector bindings. It is still a research replay until the live MT5 runtime has a persistent pending-setup queue that can store HTF plans, wait for POI touch, then execute only after M1/M5 confirmation.
