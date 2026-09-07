# Faiz Updated Risk Management

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

## Mentor Risk Rules Extracted So Far

### Baseline Risk Consistency

- Source: `27. Risk Management & Psychology.mp4`.
- Rule: use one consistent risk percentage for each trade. Do not switch randomly between 1%, 2%, 0.5%, etc.
- Mentor preference: 1% per trade is the practical default; 2% is high risk, especially for prop challenges.

### Losing Streak Risk Reduction

- Source: `27. Risk Management & Psychology.mp4`.
- Personal funds rule: after roughly 4-5 losses in a row, reduce from 1% to 0.5%. If losses continue for another block of trades, reduce to 0.25%.
- Recovery rule: after winning/recovering with reduced risk, scale back up gradually to 0.5%, then 1% once loss equity is recovered.
- Prop challenge caveat: for prop challenges, mentor says keep 1% because of time limits.

### Trade Management

- Source: `27. Risk Management & Psychology.mp4`.
- Breakeven: when price breaks the closest relevant lower high / higher low structure in profit, move SL to breakeven.
- Partials: at 1:2, close up to 40-70% depending on style, or take smaller 20-25% partials at POIs/FVG/supply-demand.
- Trailing: alternative to partials is trailing SL below/above each new higher low / lower high as structure progresses.
- Partial limit: mentor prefers at most two partials plus final TP.

### Psychology / Frequency

- Source: `27. Risk Management & Psychology.mp4`.
- Best discipline: one trade per day.
- Acceptable cap: maximum two trades per day.
- Variant: if using up to three attempts, stop after one loss; continue only after wins, and stop while still profitable.
- Prohibited behavior: revenge trades, random entries after a loss, and increasing risk to recover losses.

### Prop Firm Challenge Phase 1

- Source: `23. Risk Management For Prop Firm Accounts.mp4`.
- Goal: win two A+ trades in a row for most 8% targets.
- Trade 1: risk `1%`, target `1:2`.
- Trade 2 after first win: risk `2%`, target `1:3`.
- If first trade loses and account is below breakeven: keep risking `1%` until account is up `2%`, then risk `2%` for `1:3`.
- FTMO-style `10%` target variant: risk `1%` to `1:2`, risk `1%` again to build roughly `4%`, then risk `2%` for `1:3`.

### Prop Firm Challenge Phase 2

- Trade 1: risk `1%`, target `1:2`.
- Trade 2 after first win: risk `1%`, target `1:3`.
- If first trade loses and account is below breakeven: keep risking `1%` until up `2%`, then risk `1%` for `1:3`.

### Funded Account

- Base risk: `0.5%` per trade.
- Once up `1-2%`, risk `1%`.
- If that trade loses back to breakeven, return to `0.5%` until up `1%` again.
- Preferred monthly behavior: take small profits, withdraw around `2-3%`, avoid greed, and continue building accounts.

## Bensim Separation

These are mentor risk tactics, not automatic permission to disable Bensim account safety. V3 should model them as an optional mentor-risk profile while Bensim engineering drawdown, broker, and account protection remain separate controls.
