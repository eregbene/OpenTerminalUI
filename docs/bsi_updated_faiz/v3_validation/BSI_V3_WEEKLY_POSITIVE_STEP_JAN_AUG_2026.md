# BSI V3 Weekly Positive Step Simulation Jan-Aug 2026

This is a historical positive-only selector, not a guarantee future trades can all be positive. It filters the canonical Jan-Aug BSI V3 replay to trades that already ended positive, then picks up to two per ISO week across all configured symbols.

- Selected trades: `72`
- Weeks with trades: `36`
- Total stepped profit: `$13320.0`
- Average per selected trade: `$185.0`
- Total risk sizing required if every selected trade had stopped: `$8836.5`

Weekly target: Week 1 $10 per trade, Week 2 $20 per trade, Week 3 $30 per trade, increasing by $10 every week. Max two trades per week.

## First 40 Selected Trades

| Week # | ISO Week | Date       | Day      | Symbol | Dir   | Strategy                 | R      | Target $ | Risk $ |
| ------ | -------- | ---------- | -------- | ------ | ----- | ------------------------ | ------ | -------- | ------ |
| 1      | 2026-W01 | 2026-01-02 | Friday   | EURUSD | LONG  | holy_grail               | 1.5    | 10.0     | 6.67   |
| 1      | 2026-W01 | 2026-01-02 | Friday   | NZDUSD | LONG  | mmxm                     | 1.5    | 10.0     | 6.67   |
| 2      | 2026-W02 | 2026-01-09 | Friday   | USDJPY | LONG  | mmxm                     | 1.5    | 20.0     | 13.33  |
| 2      | 2026-W02 | 2026-01-09 | Friday   | USDCHF | LONG  | mmxm                     | 1.5    | 20.0     | 13.33  |
| 3      | 2026-W03 | 2026-01-16 | Friday   | NZDUSD | LONG  | mmxm                     | 1.5    | 30.0     | 20.0   |
| 3      | 2026-W03 | 2026-01-16 | Friday   | EURUSD | LONG  | holy_grail               | 1.5    | 30.0     | 20.0   |
| 4      | 2026-W04 | 2026-01-23 | Friday   | EURJPY | LONG  | mmxm                     | 1.5    | 40.0     | 26.67  |
| 4      | 2026-W04 | 2026-01-23 | Friday   | NZDUSD | LONG  | mmxm                     | 1.5    | 40.0     | 26.67  |
| 5      | 2026-W05 | 2026-01-30 | Friday   | EURUSD | SHORT | mmxm                     | 1.5    | 50.0     | 33.33  |
| 5      | 2026-W05 | 2026-01-30 | Friday   | USDCHF | LONG  | mmxm                     | 1.5    | 50.0     | 33.33  |
| 6      | 2026-W06 | 2026-02-06 | Friday   | XAUUSD | LONG  | mmxm                     | 1.5    | 60.0     | 40.0   |
| 6      | 2026-W06 | 2026-02-06 | Friday   | GBPJPY | LONG  | mmxm                     | 1.5    | 60.0     | 40.0   |
| 7      | 2026-W07 | 2026-02-13 | Friday   | XAUUSD | LONG  | mmxm                     | 1.5    | 70.0     | 46.67  |
| 7      | 2026-W07 | 2026-02-13 | Friday   | GBPJPY | LONG  | mmxm                     | 1.5    | 70.0     | 46.67  |
| 8      | 2026-W08 | 2026-02-20 | Friday   | GBPJPY | LONG  | mmxm                     | 1.5    | 80.0     | 53.33  |
| 8      | 2026-W08 | 2026-02-20 | Friday   | NZDUSD | LONG  | mmxm                     | 1.5    | 80.0     | 53.33  |
| 9      | 2026-W09 | 2026-02-26 | Thursday | USDCHF | LONG  | mmxm                     | 1.5553 | 90.0     | 57.87  |
| 9      | 2026-W09 | 2026-02-27 | Friday   | XAUUSD | LONG  | mmxm                     | 1.5    | 90.0     | 60.0   |
| 10     | 2026-W10 | 2026-03-06 | Friday   | GBPJPY | LONG  | mmxm                     | 1.5    | 100.0    | 66.67  |
| 10     | 2026-W10 | 2026-03-06 | Friday   | AUDUSD | LONG  | mmxm                     | 1.5    | 100.0    | 66.67  |
| 11     | 2026-W11 | 2026-03-13 | Friday   | AUDUSD | LONG  | mmxm                     | 1.5    | 110.0    | 73.33  |
| 11     | 2026-W11 | 2026-03-13 | Friday   | NZDUSD | SHORT | holy_grail               | 1.5    | 110.0    | 73.33  |
| 12     | 2026-W12 | 2026-03-20 | Friday   | USDJPY | LONG  | mmxm                     | 1.5    | 120.0    | 80.0   |
| 12     | 2026-W12 | 2026-03-20 | Friday   | GBPUSD | SHORT | holy_grail               | 1.5    | 120.0    | 80.0   |
| 13     | 2026-W13 | 2026-03-27 | Friday   | USDCHF | LONG  | mmxm                     | 1.5    | 130.0    | 86.67  |
| 13     | 2026-W13 | 2026-03-27 | Friday   | XAUUSD | LONG  | mmxm                     | 1.5    | 130.0    | 86.67  |
| 14     | 2026-W14 | 2026-04-03 | Friday   | GBPJPY | LONG  | mmxm                     | 1.5    | 140.0    | 93.33  |
| 14     | 2026-W14 | 2026-04-03 | Friday   | EURUSD | LONG  | holy_grail               | 1.5    | 140.0    | 93.33  |
| 15     | 2026-W15 | 2026-04-10 | Friday   | NZDUSD | SHORT | holy_grail               | 1.8777 | 150.0    | 79.88  |
| 15     | 2026-W15 | 2026-04-10 | Friday   | XAUUSD | LONG  | mmxm                     | 1.5    | 150.0    | 100.0  |
| 16     | 2026-W16 | 2026-04-17 | Friday   | GBPUSD | SHORT | holy_grail               | 1.5    | 160.0    | 106.67 |
| 16     | 2026-W16 | 2026-04-17 | Friday   | AUDUSD | LONG  | mmxm                     | 1.5    | 160.0    | 106.67 |
| 17     | 2026-W17 | 2026-04-24 | Friday   | USDJPY | LONG  | mmxm                     | 1.5    | 170.0    | 113.33 |
| 17     | 2026-W17 | 2026-04-24 | Friday   | USDCAD | SHORT | mmxm                     | 1.6563 | 170.0    | 102.64 |
| 18     | 2026-W18 | 2026-05-01 | Friday   | GBPJPY | LONG  | mmxm                     | 1.5    | 180.0    | 120.0  |
| 18     | 2026-W18 | 2026-05-01 | Friday   | USDCHF | LONG  | mmxm                     | 1.5    | 180.0    | 120.0  |
| 19     | 2026-W19 | 2026-05-08 | Friday   | EURJPY | LONG  | mmxm                     | 1.5    | 190.0    | 126.67 |
| 19     | 2026-W19 | 2026-05-08 | Friday   | GBPJPY | LONG  | mmxm                     | 1.5    | 190.0    | 126.67 |
| 20     | 2026-W20 | 2026-05-11 | Monday   | USDCHF | LONG  | mmxm_second_distribution | 1.5238 | 200.0    | 131.25 |
| 20     | 2026-W20 | 2026-05-15 | Friday   | EURUSD | SHORT | mmxm                     | 1.5    | 200.0    | 133.33 |
