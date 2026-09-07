# BSI V2 bsi_order_flow Video Spec

Source basis: original Order Flow videos as reconstructed in the scratchpad audiovisual audits: `BSI_MENTOR_AUDIOVISUAL_RULEBOOK_orderflow_asian.md`, `BSI_MENTOR_EXAMPLE_CATALOG_orderflow_asian.md`, `BSI_VISUAL_FIDELITY_AUDIT_orderflow_asian.md`, `mentor_notes_order_flow.md`, `BSI_FIB_ZONE_CATALOG_orderflow_asian.md`, `BSI_SWING_LIQUIDITY_CATALOG_orderflow_asian.md`, and `BSI_SETUP_LIFECYCLE_orderflow_asian.md`.

This is a reconstruction document only. No V2 implementation is implied.

## 1. Purpose

`bsi_order_flow` is the mentor's base full-confluence SMC method: structure, liquidity, order blocks, FVGs, premium/discount, entry, SL, and TP used together. It is not a profitability optimization and not a generic ICT template.

Evidence: SPOKEN_EXPLICIT.

## 2. Concepts Used

Market structure, MSB/MSS, liquidity, FVG, mentor-defined OB, premium/discount, unmitigated arrays, structural SL, natural liquidity/structure TP, optional conservative stop, and spread-aware entry placement.

Evidence: SPOKEN_AND_VISUAL.

## 3. Required Timeframes

Examples show GBPUSD/EURUSD on 15m and 5m charts. Higher-timeframe bias is conceptually used, but the exact HTF source for this strategy is not established in the Order Flow videos alone.

Evidence: VISUAL_EXPLICIT for chart timeframes; NOT_ESTABLISHED for exact HTF source.

## 4. Session/Time Rules

No session gate is shown for Order Flow.

Evidence: VISUAL_EXPLICIT absence.

## 5. Direction/Bias Rules

Trade direction follows the structure event:

- MSS reversal: trade after break of prior opposing structure.
- MSB continuation: trade continuation after a fresh same-direction structure break.

Evidence: SPOKEN_AND_VISUAL.

## 6. Structure Rules

MSS examples: break of a prior higher-low for shorts or lower-high for longs after an existing trend. MSB examples: fresh lower-low or higher-high continuation.

The mentor visually labels `mss` in reversal examples. Continuation examples are described as market structure break but are not visibly labeled BOS.

Evidence: VISUAL_EXPLICIT for `mss`; SPOKEN_AND_VISUAL for break mechanics.

## 7. Swing-Selection Rules

The mentor selects visually significant HH/HL or LL/LH swings, not every mathematical pivot. In examples he ignores smaller intervening noise and builds the leg from the meaningful swing extreme to the break-causing move.

Exact deterministic significance criteria are NOT_ESTABLISHED.

Evidence: VISUAL_INFERENCE.

## 8. Liquidity Rules

Liquidity is mandatory as context/destination. The mentor states that without liquidity the trade has no reason to respect the entry.

Confirmed liquidity shapes:

- Diagonal trendline liquidity in Order Flow MSS examples.
- Horizontal EQH/resistance liquidity in at least one example.
- Opposing structural/liquidity pools for target selection.

Trendline liquidity is explicitly labeled `liquidity` on screen and must not be flattened into a horizontal swing level.

Evidence: SPOKEN_AND_VISUAL.

## 9. Sweep/Taken Rules

For Order Flow, the videos establish liquidity awareness and trendline liquidity, but do not consistently establish a hard wick-vs-close taken criterion for the liquidity event itself.

Evidence: NOT_ESTABLISHED for exact taken criterion.

## 10. MSB/MSS Rules

MSS = reversal break. MSB = continuation break. The video does not support imposing a separate displacement-based CHoCH/MSS severity taxonomy for this strategy.

Evidence: SPOKEN_AND_VISUAL for MSS/MSB usage; NOT_ESTABLISHED for displacement taxonomy.

## 11. Fib-Anchor Rules

MSS examples draw Fib on the break-causing leg:

- Short MSS: swing high/top to broken low/bottom.
- Long MSS: swing low/bottom to broken high/top.

Continuation MSB examples do not show a Fib tool in sampled frames.

Evidence: SPOKEN_AND_VISUAL for MSS anchors; VISUAL_EXPLICIT absence for MSB.

## 12. Premium/Discount Rules

MSS examples use the 50% division:

- Shorts enter in premium.
- Longs enter in discount.

The visible Fib includes 50%, 70.5%, 79%, 0%, and 100%, but there is no evidence that OTE/golden-pocket is a required rule. The 70.5-79 area appears as an extreme entry region, not a universal OTE gate.

MSB continuation PD gating remains AMBIGUOUS.

Evidence: SPOKEN_AND_VISUAL for MSS PD; NOT_ESTABLISHED for OTE; AMBIGUOUS for MSB PD.

## 13. FVG Rules

FVGs are entry arrays only when unmitigated. The mentor explicitly rejects filled imbalances.

Evidence: SPOKEN_EXPLICIT, VISUAL_INFERENCE.

## 14. OB Rules

Mentor OB = the first candle that creates the imbalance/FVG. It is not the generic last opposite candle before the break.

Evidence: SPOKEN_EXPLICIT, repeated example supported.

## 15. Entry-Array Rules

Choose the most extreme valid FVG/OB in the correct region. If the FVG is very short/tight, the mentor may enter from the imbalance itself. Otherwise he uses the OB tied to the FVG origin.

The numeric threshold for `very short` is NOT_ESTABLISHED.

Evidence: SPOKEN_AND_VISUAL; BENSIM_ENGINEERING required later for numeric implementation.

## 16. Entry Trigger

Entry is taken when price returns to the selected unmitigated entry array. Exact market-vs-limit mechanics are only partially established.

Evidence: VISUAL_INFERENCE.

## 17. Entry Price

The intended entry is at the selected FVG/OB level, with the mentor stating entries should be placed slightly shy of the exact level to account for spread.

Evidence: SPOKEN_EXPLICIT for spread adjustment; VISUAL_INFERENCE for array touch.

## 18. Spread Handling

Spread handling is an entry-price adjustment, not merely a reject-if-spread-wide safety gate. Exact offset size is NOT_ESTABLISHED.

Evidence: SPOKEN_EXPLICIT.

## 19. Stop Loss

Default stop is just beyond the far edge of the selected entry array:

- Long: below OB/FVG.
- Short: above OB/FVG.

Evidence: SPOKEN_AND_VISUAL.

## 20. Structural Invalidation

The thesis is invalidated when price violates the protected side of the selected array/structure. Exact lifecycle beyond that is NOT_ESTABLISHED.

Evidence: VISUAL_INFERENCE.

## 21. Conservative Stop

The mentor teaches an optional conservative stop beyond a wider structural low/high when lower-timeframe noise may exist.

Evidence: SPOKEN_EXPLICIT.

## 22. Target

Order Flow targets the next opposing liquidity or structural level, not a fixed RR. EQH/resistance liquidity is explicitly used as target context.

Evidence: SPOKEN_AND_VISUAL.

## 23. RR

RR is an output of entry/SL/TP geometry. Examples include about 1.39R, 1.84R, 3.73R, and 5.72R. Do not impose a universal 1:2 or 1.5R floor for Order Flow.

Evidence: VISUAL_EXPLICIT where RR tool is legible; SPOKEN_EXPLICIT for some figures.

## 24. Partials

No Order Flow-specific partial rule is established in these videos.

Evidence: NOT_ESTABLISHED.

## 25. Management

Management after entry is not fully specified for Order Flow beyond respecting the structural SL/TP logic.

Evidence: NOT_ESTABLISHED.

## 26. Re-Entry

MSS/reversal examples are one-shot. MSB/continuation examples show multiple fresh entries within the same trend, each tied to a new structure break and new entry array.

Evidence: REPEATED_EXAMPLE_SUPPORTED.

## 27. Multiple Opportunities

A fresh MSB plus fresh entry array can create a new opportunity in the same trend. Re-evaluating the same structure and same array on later scheduler cycles should remain the same opportunity.

Evidence: VISUAL_EXPLICIT for fresh MSBs; BENSIM_ENGINEERING later for scheduler dedup.

## 28. Expiration

Explicit time expiry is NOT_ESTABLISHED. Array mitigation and structural invalidation are the only evidence-supported death conditions.

Evidence: NOT_ESTABLISHED / VISUAL_INFERENCE.

## 29. No-Trade Conditions

No trade when:

- no meaningful structure break;
- no liquidity context/destination;
- selected FVG/OB is already mitigated;
- no valid entry array;
- price is not at the entry array;
- MSS entry is not in correct 50% premium/discount side;
- spread-adjusted entry cannot be expressed without changing the mentor method.

Evidence: mixed; see evidence table.

## 30. Literal Numbered Trading Algorithm

1. Identify current meaningful structure sequence.
2. Determine whether the event is MSS reversal or MSB continuation.
3. For MSS, draw Fib on the break-causing leg and require correct 50% premium/discount side.
4. For MSB, identify the fresh continuation break; PD requirement is AMBIGUOUS.
5. Identify liquidity context/destination, including trendline liquidity where visually present.
6. Scan only unmitigated FVG/OB arrays.
7. Use mentor OB definition: first candle of the FVG-forming sequence.
8. Prefer the most extreme qualifying array.
9. If the FVG is very short, entry may be from FVG; otherwise OB.
10. Place entry slightly shy of the exact level for spread, size NOT_ESTABLISHED.
11. Place default SL beyond the array far edge; use conservative structural stop only when that mode is intentionally selected.
12. Target next opposing structural/liquidity pool.
13. Treat each fresh MSB plus fresh array as a new opportunity; do not duplicate the same array every scan.

## 31. Evidence Table

| Rule | Evidence | Classification |
|---|---|---|
| Order Flow is the base full-confluence method | Mentor says it uses everything learned together | SPOKEN_EXPLICIT |
| MSS reversal examples use Fib and 50% PD | Fib tool visible in videos 1-3 | SPOKEN_AND_VISUAL |
| MSB continuation examples do not show Fib | No Fib in sampled frames videos 4-5 | VISUAL_EXPLICIT absence |
| Trendline liquidity is valid liquidity | Trendlines labeled `liquidity` in videos 1-3 | VISUAL_EXPLICIT |
| Mentor OB is FVG-origin candle | Mentor defines OB as first candle creating imbalance | SPOKEN_EXPLICIT |
| Filled imbalance rejected | Mentor rejects filled imbalances | SPOKEN_EXPLICIT |
| SL beyond array far edge | RR tools and narration show above/below OB | SPOKEN_AND_VISUAL |
| TP at opposing liquidity/structure | Equal-high/resistance TP and target-the-low examples | SPOKEN_AND_VISUAL |
| Variable RR | RR tools show non-fixed values | VISUAL_EXPLICIT |
| Spread-aware entry offset | Mentor states entry should be shy of exact level | SPOKEN_EXPLICIT |
| Multiple MSB opportunities in one trend | Multiple boxes/RR tools in videos 4-5 | REPEATED_EXAMPLE_SUPPORTED |

## 32. Open Ambiguities

- Exact swing significance detector.
- Exact trendline liquidity taken criterion.
- Whether PD is mandatory for MSB continuation.
- Exact `very short FVG` numeric cutoff.
- Exact spread offset size.
- Exact expiry rule after missed entry.
- Whether partials/runners apply to base Order Flow.

## Current BSI Comparison

| Mentor rule | Current code | Status | Impact |
|---|---|---|---|
| Mentor OB = first FVG candle | `bsi_engine.py:252`, `_mentor_order_block_for_fvg` | MATCH | Good primitive. |
| Most extreme FVG/OB selection | `bsi_engine.py:293`, `_select_entry_array` | MATCH qualitatively | Numeric cutoff is engineering. |
| Unmitigated FVG only | `bsi_engine.py:267`, `_unmitigated_fvgs_in_zone` | MATCH | Good. |
| MSS and MSB allowed | `bsi_engine.py:636`, `_MENTOR_ALL_BREAK_KINDS` | MATCH | Good for base method. |
| MSS PD 50% | `bsi_engine.py:240`, `_zone_favorable` | MATCH for MSS | Good. |
| MSB PD ambiguity | unconditional `_zone_favorable` in `evaluate_bsi_order_flow` | AMBIGUOUS / possible EXTRA_RULE | Could reject mentor-valid continuation entries. |
| Trendline liquidity | `_shared.py:333`, `_liquidity_sweep_precedes` horizontal sweeps only | MISSING | Misses video-labeled trendline liquidity. |
| Spread entry offset | `bsi_engine.py:689`, `entry = current price` | MISSING | Does not model mentor entry placement. |
| Variable TP | `bsi_engine.py:694`, `_opposing_structural_level` | MATCH | Good. |
| Multiple MSB opportunities | stateless evaluator, no trend lockout | PARTIAL_MATCH | Needs future lifecycle dedup to avoid duplicate cycles. |

Would current BSI take the exact mentor trades?

- ORDERFLOW_GOLDEN_01: UNCERTAIN. Likely misses trendline liquidity evidence; trade may still pass if other gates satisfy.
- ORDERFLOW_GOLDEN_02: UNCERTAIN. Same trendline liquidity gap; otherwise structure/OB/SL likely match.
- ORDERFLOW_GOLDEN_03: UNCERTAIN/NO if current generic RR validation rejects sub-1.5R; otherwise structural match is plausible.
- ORDERFLOW_GOLDEN_04: UNCERTAIN. Possible false rejection if unconditional PD gate fails on continuation.
- ORDERFLOW_GOLDEN_05: UNCERTAIN. Same MSB PD ambiguity plus missing spread-offset behavior.
