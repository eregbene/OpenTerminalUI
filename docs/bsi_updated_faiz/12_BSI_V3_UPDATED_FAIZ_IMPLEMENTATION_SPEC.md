# BSI V3 Updated Faiz Implementation Spec

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

Status: partial implementation specification. V3 is not deployed.

## Hard Boundary

Do not modify live/demo V2, adaptive manager, cTrader, or MT5 execution while building this profile. V3 must be selectable only after replay and golden visual tests pass.

## Proposed Modules

- `faiz_v3_primitives`
  - MSS/MSB with displacement.
  - Liquidity sweep detection.
  - FVG, IFVG, BPR, breaker block, volume imbalance.
  - Strategy-specific OB definitions.
  - Premium/discount and dealing-range selection.

- `faiz_v3_context`
  - Daily/weekly/monthly bias.
  - Previous-day high/low.
  - Monday high/low.
  - Asian range.
  - Session windows in New York time.
  - Quarterly Theory timing state.

- `faiz_v3_strategies`
  - `order_flow`
  - `smt_divergence`
  - `abc`
  - `abcd`
  - `asian_v2`
  - `0930`
  - `reactionary_block`
  - `holy_grail`
  - `juggernaut`
  - `spectre`
  - `silver_bullet_with_bias`
  - `ict_silver_bullet`
  - `standard_deviation_po3`
  - `4h_order_block`
  - `quarterly_theory`
  - `ar50`
  - `monday_range`
  - `weaver`

## Candidate Output Contract

Every V3 candidate should include:

- `methodology`: `BSI_BASELINE_V3_UPDATED_FAIZ`
- `strategy_id`
- `symbol`
- `direction`
- `timeframe_stack`
- `source_video_ids`
- `source_rule_ids`
- `liquidity_event`
- `poi`
- `entry_model`
- `stop_model`
- `target_model`
- `management_model`
- `evidence_status`
- `replay_confidence`

## Detector Rules

- Strategy detectors must run before global rejection messages.
- Rejection reason must be strategy-specific, e.g. `monday_range_no_tuesday_sweep`, `weaver_no_h1_draw_fvg`, `4h_ob_no_m15_mss`, not generic `price_not_in_entry_zone`.
- Multiple strategies may produce candidates on the same symbol; arbitration belongs to a later portfolio/risk layer.

## Management Rules

- Stops must be placed outside the mentor-defined invalidation structure for each model.
- RR may reject a trade if target room is too small, but V3 must not shrink stop beyond structure to force RR.
- Adaptive manager must be strategy-aware:
  - BE at structure break or explicit `1R`.
  - Partial only at POI, `2R`, or model-specific taught target.
  - Final target at liquidity/draw unless the model teaches fixed R.
