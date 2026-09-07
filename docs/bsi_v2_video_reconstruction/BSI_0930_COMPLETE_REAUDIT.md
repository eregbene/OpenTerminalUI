# BSI 9:30 Complete Reaudit

Sources: local videos 21-24, transcripts, contact sheets.

## Finding

The 9:30 strategy is primarily an index strategy. Faiz explicitly says it works best with indices such as NAS100 and S&P500, says it can work on forex, but recommends sticking to indices. Live BSI should keep the index eligibility gate until native index data exists.

## Rules

- Timezone: New York.
- Window: 09:30 to 11:59 New York time.
- Liquidity may be identified before 09:30, including around 09:25.
- Entry must occur after 09:30.
- Higher timeframe liquidity: 15m.
- Execution: 1m; 2m may be used when 1m has no FVG.
- Liquidity types: equal highs/lows, significant swing high/low, daily high/low, previous day high/low, weekly high/low.
- Sweep: price takes liquidity.
- Confirmation: MSS with displacement/momentum/volume. Clumsy MSS is rejected.
- Entry: first FVG or extreme FVG; mentor prefers extreme FVG but accepts first FVG to avoid missing.
- OB substitute: allowed when imbalance is too large/no clean FVG.
- Bias: trade with overall market structure/daily direction.
- Target: normally 3R to 5R; examples may show higher but mentor says not to chase large RR.
- Management: partials at FVG/liquidity, breakeven after structure break.

## Instrument Eligibility

Confirmed: NAS100 and S&P500.

Likely required historical symbols: NAS100/US100/USTEC/NQ equivalent, SPX500/US500/ES equivalent. US30 is not confirmed from the audited local 9:30 transcripts, so treat US30 as `VISUAL_INFERENCE` only unless a clearer frame/source is found.

## Raw Extractor Comparison

Current August FX/gold replay producing zero setups is expected because the raw extractor rejects non-index symbols with `EXPECTED_INSTRUMENT_SCOPE`.
