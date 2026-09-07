# BSI V2 Implementation Plan

Target version: `BSI_BASELINE_V2_AUDIOVISUAL`. This is a plan only. Do not implement in this phase.

## Architecture

Flow:

`MARKET DATA -> SHARED RAW STRUCTURAL PRIMITIVES -> BSI MENTOR INTERPRETATION -> STRATEGY STATE MACHINE -> ENTRY OPPORTUNITY -> FRESH EXECUTION GEOMETRY -> RISK / PORTFOLIO -> BROKER`

File-level target:

| Layer | File | Role | V2 Action |
|---|---|---|---|
| Raw bars/snapshots | `backend/market_structure/*` | Shared primitives used by many strategies | Reuse; do not globally redefine |
| BSI primitive models | `backend/mt5_strategies/families/bsi_v2_primitives.py` | BSI liquidity, mentor OB, dealing leg, opportunity IDs | New BSI-only module |
| BSI interpretation | `backend/mt5_strategies/families/bsi_v2_interpretation.py` | Wrap raw swings/FVGs/liquidity into video-derived semantics | New |
| BSI state/lifecycle | `backend/mt5_strategies/families/bsi_v2_lifecycle.py` | thesis/opportunity/executable-entry state | New |
| Strategy evaluators | `backend/mt5_strategies/families/bsi_engine.py` or split `bsi_v2_engine.py` | Existing BSI subtype dispatch | Prefer new `bsi_v2_engine.py`, leave V1 frozen |
| Persistence | `backend/historical_intelligence/bsi_canonical_fingerprint.py` | BSI thesis records | Extend additive fields only |
| Migration | `backend/alembic/versions/*` | schema additions | Add V2 fields/tables if needed |
| Management | `backend/adaptive_management/bsi_thesis.py`, `service.py` | BSI management rules | Wire only after entry fidelity passes |
| Tests | `backend/tests/test_bsi_v2_*.py` | rule/golden/regression | New V2 suite |

## Shared Versus Strategy-Specific

Shared BSI primitives:

- Mentor structure event wrapper: MSS/MSB with source break id and evidence.
- BSI liquidity object: swing, equal, trendline, session box, origin OB edge, residual wick liquidity.
- BSI liquidity-taken event: original level, wick extreme, close/reclaim prices kept separate.
- Mentor FVG and mentor OB: OB is FVG-anchored first candle.
- Dealing leg and premium/discount midpoint.
- Thesis/opportunity/executable-entry identity.
- Fresh execution geometry and quote recheck.
- Persisted evidence schema: rule IDs, evidence class, ambiguity flags.

Strategy-specific logic:

- Order Flow sequence and trendline liquidity use.
- Asian session box and lunch-gap engineering.
- New York original-level retest.
- ABC leg geometry.
- Under/Over touch/fakeout sequence.
- 9:30 timing, instrument evidence, displacement substitute, 3R-5R target.
- Reactionary second-array sequence.
- ABCD D-leg retest.
- OB Liquidity same-array fakeout plus residual-liquidity checks.

Do not create one generic strategy engine. The reconstructed docs show real differences.

## What Must Not Change Globally

| Primitive | Decision | Reason |
|---|---|---|
| Generic order blocks in `market_structure/zones.py` | C. keep legacy | Non-BSI consumers may expect last-opposite-candle behavior |
| Generic OTE/golden-pocket helper in `_shared.py` | C. keep legacy | BSI V2 must not use unsupported OTE, but other strategies may |
| CHoCH/MSS/BOS taxonomy in `market_structure/structure.py` | B. BSI wrapper | Course semantics are mentor-specific |
| Liquidity sweep `swept_price` | C + B | Keep raw sweep; BSI resolves original level through `level_id` |
| Equal-level detection | A/B | Raw detection reusable; BSI thresholds strategy-specific |
| Pivot trendlines | B | Reuse detection but add BSI trendline liquidity model |
| Adaptive manager generic policies | C | BSI management must not globally alter all trades |
| Historical fingerprints | B | V2 isolated by `bsi_version` and fingerprint version |

## New York Critical Fix

Problem:

Current `evaluate_bsi_new_york` anchors entry/stop metadata to `sweep.swept_price`. The video rule requires the original swept swing level.

Required model:

- `original_liquidity_level`: resolved by `sweep.level_id -> LiquidityLevel.id -> LiquidityLevel.level`.
- `sweep_wick_extreme`: `sweep.swept_price`.
- `retest_price`: touch of `original_liquidity_level`.
- `entry_price`: broker executable quote near retest, never wick tip.
- `invalidation`: fakeout extreme beyond original level.
- `broker_protective_stop`: stop after symbol rounding and risk floors, without changing thesis geometry.

Tests:

- Wick tip far beyond level cannot become entry.
- Stop uses fakeout extreme but entry uses original level.
- Fixed 1:2 target derives from original-level entry.
- Missing `level_id` fails closed.
- Price runs to 1:2 without retest emits no opportunity.

## Mentor Order Block Plan

Representation: `BSIMentorOrderBlock`.

Fields:

- `id`: hash of symbol, timeframe, fvg id, first-candle index, direction, bounds, bsi version.
- `source_fvg_id`.
- `first_candle_index`.
- `direction`.
- `low`, `high`.
- `created_by_break_id`.
- `premium_discount_requirement`: mandatory/not_used/ambiguous per strategy.
- `rejected_competing_ob_ids`: generic OBs ignored by BSI.
- `evidence_rule_ids`: include `BSI2-OB-001`, `BSI2-FVG-001`.

Legacy OB remains in `market_structure/zones.py`; BSI V2 consumes only mentor OB wrappers.

## Liquidity Model Plan

Representation: `BSILiquidityObject`.

Supported types:

- `swing_level`.
- `equal_level`.
- `trendline_liquidity`.
- `session_box_edge`.
- `origin_ob_edge`.
- `residual_wick_liquidity`.

Trendline fields:

- `anchor_point_ids`.
- `anchor_bar_indexes`.
- `anchor_prices`.
- `slope` and `intercept`, or equivalent two-point projection.
- `active_from_bar`, `active_until_bar`.
- `projected_level_at_bar`.
- `touch_count`.
- `breach_bar_index`.
- `taken_rule`: price crosses projected level with strategy-specific wick/close rule.
- `invalidated_reason`: broken too early, anchors stale, insufficient touches.
- `strategy_usage`: Order Flow confirmed; others not used unless future evidence.

Do not flatten diagonal liquidity into horizontal `LiquidityLevel.level`.

## Premium/Discount Plan

Dealing leg:

- `break_causing_leg_start`.
- `break_causing_leg_end`.
- `midpoint`.
- `premium_zone`.
- `discount_zone`.
- `source_break_id`.

No dependency on 62-79% OTE.

Per-strategy classification:

| Strategy | PD Classification |
|---|---|
| bsi_order_flow | MANDATORY for MSS; MSB ambiguous |
| bsi_new_york | NOT_USED |
| bsi_abc | NOT_USED |
| bsi_asian | NOT_USED |
| bsi_under_over | NOT_USED |
| bsi_0930 | NOT_USED |
| bsi_reactionary | AMBIGUOUS/inherited from Order Flow |
| bsi_abcd | NOT_USED |
| bsi_ob_liquidity | AMBIGUOUS/inherited from Order Flow |

## Setup Lifecycle Plan

This is `BENSIM_ENGINEERING`; the mentor does not teach scanner lifecycle semantics.

States:

- `DETECTED`: video-derived setup exists.
- `ARMED`: required confirmation exists, waiting for price.
- `AVAILABLE`: fresh broker quote is executable within geometry tolerance.
- `CONSUMED`: opportunity emitted/executed once.
- `INVALIDATED`: mentor invalidation or thesis break.
- `EXPIRED`: stale, missed retest, outside window, or price moved too far.

Identifiers:

- `bsi_thesis_id`: version, subtype, symbol, timeframe context, direction, source structural/liquidity anchors.
- `bsi_entry_opportunity_id`: thesis id plus entry array/retest level, confirmation event id, opportunity bar/time.

Rules:

- Same structural event plus same entry array on next scheduler cycle = same opportunity.
- New MSB plus new entry array in same continuation trend = new opportunity.
- Do not deduplicate an entire trend.

## Entry Freshness Plan

Valid setup is not automatically executable.

Before execution recheck:

- current bid/ask.
- spread.
- price still in entry zone or retest tolerance.
- stop still beyond mentor invalidation.
- RR still meets strategy rule without moving stop/TP to force validity.
- session window still valid.
- opportunity state not consumed/expired.

Expire, do not rescue, when:

- price moved beyond entry tolerance.
- target was reached before retest.
- window closed.
- array was mitigated/invalidated.
- RR no longer valid under original geometry.

## Strategy Change Plan

| Strategy | Correct Today | Partial/Wrong/Missing | Required V2 Work | Risk | V1 Compatibility |
|---|---|---|---|---|---|
| bsi_order_flow | MSS/MSB, unmitigated arrays, mentor OB, variable target | trendline liquidity missing; spread offset missing; lifecycle missing; MSB PD ambiguous | Add BSI liquidity model, spread-aware entry, opportunity lifecycle | HIGH | Some V1 occurrences incompatible |
| bsi_asian | box sweep, no PD, opposite target likely correct | exact lunch window engineering; lifecycle evidence thin | Preserve no-bias/no-PD; stamp timing as engineering | MEDIUM | Mostly compatible |
| bsi_new_york | bare retest, fixed 1:2 | wick-tip entry wrong; no-chase missing; significance/fakeout partial | Critical original-level resolver and freshness tests | CRITICAL | V1 NY incompatible |
| bsi_abc | ABC geometry, OB/FVG, B target | HTF inheritance ambiguous if present | Preserve; add negative tests for no PD/fixed RR | MEDIUM | Mostly compatible |
| bsi_under_over | 3 touches, close reclaim, no PD | management metadata inert; fakeout size qualitative | Wire/label management, preserve close-only | MEDIUM | Mostly compatible |
| bsi_0930 | window, bias, displacement, extreme FVG, 3-5R | index scope missing; management inert; second-MSS sequencing ambiguous | Add instrument evidence/gate config, lifecycle, management | HIGH | Mostly compatible, management differs |
| bsi_reactionary | two-array sequence | array-2 kind ambiguous; RR discipline not coded | Preserve array-2; record ambiguity | MEDIUM | Compatible if current fix exists |
| bsi_abcd | completed ABC, P2 break, 1:2 | optional structure/1:3 missing; close-based break ambiguous | Preserve default; optional target branch later | MEDIUM | Mostly compatible |
| bsi_ob_liquidity | same-array close fakeout, local extremum | residual liquidity missing; heavy-reaction reroute missing; fakeout size missing | Add three subtype-specific checks | HIGH | V1 OB Liquidity incompatible on edge cases |

## Implementation Order

1. Add V2 version constants and disabled-by-default V2 dispatch.
2. Add BSI-only primitive/model wrappers.
3. Add liquidity model, including trendline liquidity.
4. Add mentor OB wrapper and persistence evidence.
5. Add dealing-leg midpoint model and remove BSI OTE dependency.
6. Add lifecycle/opportunity identity.
7. Apply New York critical original-level fix.
8. Implement Order Flow trendline/spread/lifecycle updates.
9. Implement OB Liquidity residual/heavy-reaction/fakeout checks.
10. Review Asian, ABC, Under/Over, 9:30, Reactionary, ABCD subtype deltas.
11. Extend persisted thesis/evidence schema.
12. Add golden and negative tests.
13. Run full BSI regression.
14. Run controlled historical V2 replay.
15. Full backfill only after replay parity/intentional-delta review.
16. DEMO readiness only after CRITICAL/HIGH risks closed.

Why this order: version isolation comes first so no V2 candidate contaminates V1/HI. Shared BSI primitives come before strategy rewrites. New York and OB Liquidity are the most dangerous semantic mismatches, but they should land after the identity/evidence foundation so tests can prove them.

## First Code Commit

First commit should be infrastructure only:

- `BSI_BASELINE_V2_AUDIOVISUAL` constant.
- V2 disabled-by-default dispatch hook.
- BSI V2 primitive dataclasses.
- No behavior change to V1.
- Tests proving V1 output unchanged and V2 disabled.

Commit title: `Add isolated BSI V2 audiovisual baseline scaffold`.
