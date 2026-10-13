# DXY research (2026-10-12/13)

**Data**: synthetic DXY = ICE formula from MT5 pairs (`bensim/dxy.py`). Full formula matches the real ICE DXY
(Yahoo `DX-Y.NYB` = TradingView `TVC:DXY`) to 0.004 points over 10,577 hours; the broker has no DXY symbol and no
USDSEK before 2025 / USDCAD before Feb 2024, so multi-year tests use reduced baskets: without SEK (0.999 hourly corr.
with the full index) and without SEK+CAD (EURUSD/USDJPY/GBPUSD/USDCHF, weights renormalised: 0.996 corr., same 1H fast
trend 94% of bars). Agreement: EURUSD/GBPUSD/AUDUSD/XAUUSD trade opposite the DXY, USDxxx with it.

**Bennett setup database (1H legs, 3 years)**: DXY agreeing did *not* help -- setups with the DXY against were better.

**fib618 -- pre-registered test** (hypothesis from Oct 2023 - Oct 2026: trades with the DXY 1H trend AGAINST them do
better; untouched test period Nov 2021 - Sep 2023; live pairs; $ at live sizing):
| period | DXY 1H against | DXY 1H agrees |
|---|---|---|
| untouched 2021-11..2023-09 (live settings) | 146 trades +0.03R (t 0.2) +$142 | 408 trades -0.06R (t -0.6) -$1,979 |
| found on 2023-10..2026-10 (live settings) | 207 trades +0.24R (t 1.7) +$17,875 | 663 trades -0.03R -$4,098 |
| untouched (plain strategy) | 220 trades +0.10R (t 0.8) | 754 trades +0.03R (t 0.4) |
Direction holds in both periods and both configurations, but on the untouched years the difference is small (~0.07-0.09R)
and not significant; 2022 (strong USD trend) favoured *agreeing* trades (+$10.3k vs +$0.8k). Verdict: a weak,
regime-dependent tilt, **not a deployable filter** -- keeping only "against" trades would have made ~$0 in 2021-23.
DXY stays as context on the plan page, not as a trade rule.
