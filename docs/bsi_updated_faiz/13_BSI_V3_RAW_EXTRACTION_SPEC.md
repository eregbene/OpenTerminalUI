# BSI V3 Raw Extraction Spec

Required path: raw bars -> swings -> structure -> liquidity -> PD/bias -> POI/arrays -> strategy state machine -> entry opportunity -> execution.

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

Status: partial extraction spec. V3 is not deployed.

## Bar Inputs

- Timeframes required so far: MN, W1, D1, H4, H1, M15, M5, M1, plus optional M2-M4 for IFVG fallback.
- All timestamps must be convertible to New York time for London, New York, 09:30, Silver Bullet, Asian, Monday, and Tuesday rules.
- Spread/slippage must be available for low-timeframe models before any automated BE/TP behavior is trusted.

## Primitive Extraction Order

1. Normalize candles and sessions.
2. Detect swing highs/lows and equal highs/lows.
3. Detect MSB/MSS with displacement, not just level break.
4. Detect liquidity sweeps: previous-day, Monday, session high/low, trendline, external high/low, and FVG internal liquidity.
5. Detect FVG, IFVG, BPR, breaker block, volume imbalance.
6. Detect premium/discount and dealing range.
7. Detect strategy-specific order blocks:
   - classic last/consecutive opposite candle body.
   - first candle of FVG sequence.
8. Build strategy state machines.
9. Emit source-tagged candidate or source-tagged rejection.

## Strategy State Examples

- 4H OB: H4 trend -> H4 OB return -> M15 MSS in killzone -> M15 OB takes liquidity -> entry -> BE at 1R -> TP at 2R.
- Weaver: previous-day high/low sweep -> H1 dealing-range FVG draw -> M15 MSS -> entry -> target H1 draw.
- IFVG/PO3: M1 accumulation/manipulation -> exactly one FVG -> body-close inverse -> close/retest entry -> closest liquidity BE -> 50% at 1R.
- Yin Yang: first M15 London FVG with second candle at 03:00 NY -> IFVG -> close/retest entry -> body stop -> 1:2.
- Monday Range: Monday daily high/low -> Tuesday sweep -> M15 MSS -> FVG/OB entry -> opposite Monday side or 50%.

## Rejection Contract

Every rejection must name the model and failed source rule:

- `4h_ob_no_h4_trend`
- `4h_ob_no_m15_mss`
- `weaver_no_pdh_pdl_sweep`
- `weaver_no_h1_fvg_draw`
- `ifvg_multiple_fvgs_in_manipulation`
- `ifvg_closest_liquidity_taken_before_entry`
- `yin_yang_no_first_london_fvg`
- `yin_yang_news_filter`
- `monday_range_no_tuesday_sweep`

Generic rejection strings are not allowed in V3 validation.
