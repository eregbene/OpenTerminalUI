# Faiz Updated Bias Model

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

## Evidence Extracted So Far

### Monthly Bias Flow

- Rule ID: `FAIZ_V3_BIAS_001`
- Sequence: mark closest relevant high/low as external liquidity on monthly, identify unmitigated internal FVG in discount/premium, wait for external liquidity purge, then drop to daily for MSS confirmation.
- Source: `1. Finding Monthly, Weekly, & Daily Bias.mp4` at `00:02:45-00:03:43`.
- Evidence: `SPOKEN_AND_VISUAL`
- State-machine note: the monthly liquidity purge occurs before daily MSS confirmation; entries are not valid before confirmation.

### Bias Duration

- Rule ID: `FAIZ_V3_BIAS_002`
- Rule: after daily MSS confirms the monthly draw, monthly bias remains in that direction until the target internal liquidity is hit.
- Source: `1. Finding Monthly, Weekly, & Daily Bias.mp4` at `00:04:41-00:05:05`.
- Evidence: `SPOKEN_EXPLICIT`

### Entry Timeframe Under Bias

- Rule ID: `FAIZ_V3_BIAS_003`
- Rule: after bias is established, entries/executions should be on lower timeframes; transcript explicitly begins saying entries/executions must be on the one-hour timeframe.
- Source: `1. Finding Monthly, Weekly, & Daily Bias.mp4` around `00:05:17-00:05:32`.
- Evidence: `SPOKEN_EXPLICIT`
- Status: incomplete sentence in inspected excerpt; pending rest of transcript review.

### Daily Bias Made Easy

- Rule ID: `FAIZ_V3_BIAS_004`
- Source: `26. DAILY BIAS MADE EASY.mp4`
- Method: use two moving averages, length `9` and `18`. When they cross and point upward / price is above, bias is bullish. When they cross downward, bias is bearish.
- Duration: after a cross, the next `2-3` days can carry that bias; after that, look for liquidity sweep before continuing the same bias.
- Context: FVG, volume imbalance, OB, and liquidity sweeps refine the daily-bias read.
- Alternative: ABC pattern with sweep, close back through level, and retest can also define daily bias. Align ABC direction with the MA bias when MA is used.
- Evidence: `SPOKEN_AND_VISUAL`
- Caveat: mentor says this is not as accurate as manual daily bias, but better than nothing and useful for beginners.

### FVG High/Low Bias

- Rule ID: `FAIZ_V3_BIAS_005`
- Source: `27. FVG LIQUIDITY.mp4`
- Method: use the high/low formed inside an FVG as draw-on-liquidity. A sweep of one side can project toward the other side of the FVG internal range.
- Evidence: `SPOKEN_AND_VISUAL`
