# BSI V3 Next Session Adaptive Prep Profile

As-of UTC date: `2026-09-07`

Next trading day UTC date: `2026-09-08`

Risk mode: `NORMAL`

Allowed next-day buckets: `18`

Watchlist long-memory buckets: `81`

Blocked recent-only buckets: `2`

Blocked previous-month-shock buckets: `0`

The profile uses accumulated year-to-date evidence plus month-to-date, week-to-date, and previous-trading-day confirmation.

| Bucket                            | Score | Windows                                   | Train Trades | Train Net R |
| --------------------------------- | ----- | ----------------------------------------- | ------------ | ----------- |
| bsi_v3_reactionary_block / AUDUSD | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_reactionary_block / EURJPY | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_reactionary_block / EURUSD | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_reactionary_block / GBPJPY | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_reactionary_block / GBPUSD | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_reactionary_block / NZDUSD | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_reactionary_block / USDCHF | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_reactionary_block / USDJPY | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_reactionary_block / XAUUSD | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / AUDUSD           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / EURJPY           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / EURUSD           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / GBPJPY           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / GBPUSD           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / NZDUSD           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / USDCHF           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / USDJPY           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
| bsi_v3_spectre / XAUUSD           | 5.5   | year_to_date,previous_month,month_to_date | 0            | 0.0         |
