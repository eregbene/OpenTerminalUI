# BSI V2 Timeframe Input Audit

## H1/H4 Reported As Zero

The `h1_loaded=0` and `h4_loaded=0` fields came from the fast August replay runner, not from live BSI V2 itself.

Reason: the fast runner was changed to a BSI-only lean context so it would not spend minutes rebuilding unused H1/H4/Daily indicators for every M15 bar. It explicitly set those report fields to zero.

This does not mean the DB lacks H1/H4. The DB has H1/H4 rows through September 3, 2026 for all 10 configured symbols, including XAUUSD.

## Strategy Timeframes From Video Evidence

| Strategy | Required timeframe evidence |
|---|---|
| Asian | Session range and entry can be read from intraday chart; Daily bias explicitly not required. |
| New York | Intraday NY session; no FVG/OB requirement; entry at original swept level retest. |
| ABC | Works on any timeframe per mentor; M15 replay is valid as an intraday test. |
| Under/Over | Intraday support/resistance/OB origin, close fakeout and retest. |
| 9:30 | Index scope and 1m execution concern; missing from August FX/gold test. |
| Reactionary | Same timeframe confirmation, not lower timeframe. |
| ABCD | Intraday ABCD/retest; fixed 1:2 in examples. |
| OB Liquidity | Mentor explicitly shows 5m examples; M15 can test structure but M5/M1 would be better. |

## Data Gaps

- No index symbols were included in the August replay universe.
- M1 data is required for strict 9:30 validation and was not part of the tested universe.
