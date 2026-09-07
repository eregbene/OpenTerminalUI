# BSI V2 Video To Current Code Matrix

Read-only comparison. No production changes made.

| Strategy | Video Rule Match | Current Code Notes | V2 Watch Items |
|---|---|---|---|
| Order Flow | Partial | MSS/MSB, unmitigated arrays, OB/FVG selection, MSS premium/discount, variable target broadly match. | Add/verify trendline liquidity, spread-aware entry offset, MSB lifecycle/dedup, and avoid over-certifying MSB premium/discount if video remains ambiguous. |
| New York | Partial | Bare retest and fixed 1:2 match. | Entry should use original swept level, not wick tip. Add no-chase retest rule, small-fakeout quality, significant-swing filter, optional pair allowlist. |
| ABC | Mostly match | ABC geometry, reclaim/structure break, OB/FVG entry, B-leg target broadly match. | Keep no Fib/PD. Treat any HTF-bias inheritance as unconfirmed unless intentionally family-wide. |
| Asian | Mostly match if current shape follows known audit | Asian box sweep, MSS/CHoCH, OB/FVG entry, opposite-edge target should match. | Do not add daily bias or premium/discount. Treat clock boundaries as engineering, not video-certified. |
| Under/Over | Mostly match | Three touches, close fakeout/reclaim, no PD, dynamic stop, opposing target match. | Wire partial/full-exit management if V2 scope includes management; preserve close-only break. |
| 9:30 | Mostly match | Window, daily bias, MSS-or-displacement, extreme FVG, bounded 3R-5R match. | Add instrument-class index gating if desired; management metadata may be inert; second-MSS sequencing ambiguous. |
| Reactionary | Mostly match | Two-array mechanic, array-2 entry, no fresh break for array 2, stop modes match. | Array-2 kind hardcoded FVG is ambiguous; RR discipline not enforced and probably should not be hard-gated. |
| ABCD | Mostly match | Completed ABC, P2 D-break level, opposite D direction, fixed 1:2 match. | Add optional structure/1:3 target branch only if intended; close-based D break is plausible but not explicit. |
| OB Liquidity | Partial | Same-array fakeout/reclaim, local-extremum check, close-based rule, tight stop match. | Add heavy-reaction valid-OB reroute, residual-liquidity clearance, fakeout-size quality/ceiling for this subtype. |

## Highest-Priority Mismatches

1. New York entry level: mentor uses original swept level, not fakeout wick tip.
2. OB Liquidity residual liquidity: mentor requires older wick liquidity to clear first.
3. OB Liquidity valid-OB discriminator: heavy clean reaction means not an OB Liquidity fakeout setup.
4. Order Flow trendline liquidity: mentor confirms diagonal liquidity; flattening to horizontal loses the setup.
5. Management rules are often metadata-only: Under/Over partials and 9:30 breakeven/partials need live-path confirmation before claiming complete fidelity.

## Explicit Stop Point

This matrix stops before BSI V2 implementation. It is a reconstruction and comparison artifact only.
