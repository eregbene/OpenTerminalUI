# Faiz Updated Course To BSI V2 Gap

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

Status: partial, based only on `F:\new faiz` transcript and visual extraction. V3 is not deployed.

## Confirmed Gaps Against BSI V2

1. Strategy inventory changed.
   - V2 centered around the original BSI strategy set.
   - Updated Faiz includes additional independent models: `juggernaut`, `spectre`, `4h_order_block`, `monday_range`, `weaver`, `ar50`, `standard_deviation_po3`, `holy_grail`, `mmxm_second_distribution`, plus possible late models still being extracted.

2. Order block definition is strategy-specific.
   - Order Flow and 4H OB use the classic last/consecutive opposite candle body before continuation or structure break.
   - Asian V2 and some entry-array contexts use the first candle of the FVG sequence.
   - V3 cannot use one global OB detector for all strategies.

3. Entries require mentor-specific sequencing, not generic SMC confluence.
   - Many models require exact session or weekday state: 09:30, Silver Bullet, Asian V2, Monday Range, Quarterly Theory.
   - Some models require exact liquidity order: previous-day high/low sweep, Monday high/low sweep, daily external purge, or H4 OB return before lower-timeframe MSS.

4. Stops and management must be source-led.
   - Tiny stops used only to satisfy RR are not mentor-compatible.
   - 4H OB explicitly uses BE at `1R` and TP at `2R`.
   - Risk lessons favor BE after structure break, partials at POI/2R, and final targets at liquidity/draw.

5. Instrument scope is not universal.
   - 4H OB: forex main pairs; indices not recommended.
   - 9:30: indices preferred, forex allowed but not recommended.
   - Monday Range: gold, NAS100, EURUSD explicitly named.
   - Weaver: all pairs/assets explicitly stated.
   - SMT: only correlated groups explicitly named so far.

## Likely V2 Behavior That Must Not Be Carried Forward Blindly

- A global "price in entry zone" filter that rejects valid updated models before checking their own setup sequence.
- A single Asian or New York liquidity model applied across all updated strategies.
- A generic adaptive close/partial manager that exits before the model target logic has played out.
- A single SL formula across all models.
- All-pair enablement without strategy-specific instrument tags.

## Required V3 Migration Decision

Build V3 as a separate methodology profile:

- `BSI_BASELINE_V3_UPDATED_FAIZ`
- Separate strategy registry.
- Separate detectors per model.
- Separate source-tagged management rules.
- Replay validation before any live/demo activation.
