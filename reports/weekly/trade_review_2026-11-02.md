# Weekly trade review -- week of 2026-11-02

Written 2026-11-10 08:26 UTC by `python -m bensim.journal`. Observe only: nothing here changes a trade or a rule. Ideas go through the 5-10y backtest + untouched period + shadow run, then your GO.

## 1. Summary

**33 closed trades, 9 winners, net -2,247.05 $** (R known for 33: -7.47R).

| Strategy | Trades | Wins | Net $ | R |
|---|---|---|---|---|
| enigma | 1 | 0 | -243 | -1.02 |
| fib618 | 5 | 2 | -200 | +0.18 |
| flow | 18 | 6 | -292 | -0.57 |
| imbalance | 5 | 1 | -461 | -1.87 |
| sflow | 4 | 0 | -1,051 | -4.19 |

## 2. Every trade

| Closed (UTC) | Strategy | Pair | Side | $ | R | Best / worst R | Exit | H4/H1/M15 at entry | Tags |
|---|---|---|---|---|---|---|---|---|---|
| Mon 02 08:05 | flow | EURJPY | BUY | +170 | +0.13 | +0.2 / -0.0 | engine (time / Friday / kill) | with / with / with | never_positive, asia |
| Mon 02 08:05 | flow | USDJPY | BUY | -28 | -0.02 | +0.1 / -0.1 | engine (time / Friday / kill) | against / with / with | against_h4, never_positive, asia |
| Mon 02 08:40 | fib618 | USDCHF | BUY | -487 | -1.00 | +0.0 / -1.0 | stop loss | against / with / with | against_h4, never_positive |
| Mon 02 08:47 | sflow | USDCAD | BUY | -252 | -1.03 | -0.1 / -0.9 | stop loss | against / with / against | against_h4, fast_stop, stop_then_ran, never_positive |
| Mon 02 15:05 | flow | EURUSD | BUY | -131 | -0.10 | +0.1 / -0.2 | engine (time / Friday / kill) | with / with / with | never_positive |
| Mon 02 16:05 | imbalance | EURUSD | BUY | -243 | -1.00 | +0.4 / -1.2 | stop loss | with / with / with | fast_stop, stop_then_ran, never_positive |
| Mon 02 17:05 | flow | EURJPY | BUY | -26 | -0.02 | +0.1 / -0.1 | engine (time / Friday / kill) | with / with / with | never_positive |
| Mon 02 17:05 | flow | CADJPY | BUY | +2 | +0.00 | +0.1 / -0.1 | engine (time / Friday / kill) | against / with / with | against_h4, never_positive |
| Mon 02 17:45 | imbalance | XAUUSD | BUY | -250 | -1.02 | +0.0 / -1.3 | stop loss | against / with / with | against_h4, fast_stop, stop_then_ran, never_positive, gold |
| Tue 03 12:00 | flow | EURUSD | SELL | +282 | +0.15 | +0.2 / -0.0 | engine (time / Friday / kill) | against / against / against | against_h4, all_against, never_positive |
| Tue 03 12:26 | fib618 | USDCAD | SELL | -246 | -1.00 | -0.0 / -1.5 | stop loss | with / with / with | fast_stop, stop_then_ran, never_positive |
| Tue 03 13:30 | sflow | AUDUSD | SELL | -310 | -1.14 | +0.0 / -5.4 | stop loss | with / with / against | fast_stop, stop_then_ran, never_positive |
| Wed 04 01:30 | imbalance | USDJPY | BUY | -239 | -0.97 | +0.7 / -1.7 | stop loss | with / with / against | fast_stop, stop_then_ran, asia |
| Wed 04 01:39 | fib618 | GBPUSD | BUY | +654 | +2.69 | +2.8 / -0.6 | take profit | with / with / with | news_during |
| Wed 04 07:05 | flow | EURUSD | SELL | -82 | -0.06 | +0.0 / -0.3 | engine (time / Friday / kill) | against / against / against | against_h4, all_against, never_positive, asia |
| Wed 04 08:05 | flow | USDJPY | SELL | -237 | -0.20 | +0.1 / -0.2 | engine (time / Friday / kill) | against / with / with | against_h4, never_positive, asia |
| Wed 04 09:35 | sflow | USDCAD | BUY | -241 | -0.99 | +1.9 / -1.0 | stop loss | with / with / against | stop_then_ran, gave_back, asia |
| Wed 04 11:05 | imbalance | USDJPY | SELL | -244 | -1.00 | +0.4 / -1.1 | stop loss | with / with / against | stop_then_ran, never_positive |
| Wed 04 12:00 | flow | EURUSD | SELL | -232 | -0.12 | +0.1 / -0.3 | engine (time / Friday / kill) | against / against / against | against_h4, all_against, never_positive |
| Wed 04 15:05 | flow | EURJPY | BUY | +149 | +0.11 | +0.2 / -0.0 | engine (time / Friday / kill) | with / with / with | never_positive |
| Wed 04 15:07 | enigma | USDCHF | SELL | -243 | -1.02 | +0.1 / -1.2 | stop loss | with / with / with | fast_stop, stop_then_ran, never_positive |
| Thu 05 05:55 | flow | CADJPY | BUY | -182 | -0.22 | +0.0 / -0.3 | engine (time / Friday / kill) | with / with / with | never_positive |
| Thu 05 05:55 | flow | USDJPY | BUY | -232 | -0.20 | +0.1 / -0.3 | engine (time / Friday / kill) | against / against / against | against_h4, all_against, never_positive |
| Thu 05 12:00 | flow | EURUSD | SELL | +540 | +0.28 | +0.4 / -0.1 | engine (time / Friday / kill) | against / against / with | against_h4, never_positive |
| Thu 05 15:53 | sflow | AUDUSD | BUY | -249 | -1.03 | +0.2 / -1.1 | stop loss | with / against / against | fast_stop, never_positive |
| Thu 05 20:36 | imbalance | USDCAD | BUY | +515 | +2.12 | +2.2 / -0.3 | take profit | with / with / with | - |
| Fri 06 03:05 | flow | CADJPY | BUY | -71 | -0.09 | +0.0 / -0.3 | engine (time / Friday / kill) | with / against / against | never_positive |
| Fri 06 05:05 | flow | EURJPY | BUY | -46 | -0.04 | +0.1 / -0.2 | engine (time / Friday / kill) | against / against / against | against_h4, all_against, never_positive |
| Fri 06 09:09 | fib618 | USDCHF | SELL | -260 | -1.07 | +1.1 / -1.0 | stop loss | with / with / against | gave_back |
| Fri 06 12:00 | flow | EURUSD | SELL | +118 | +0.06 | +0.1 / -0.0 | engine (time / Friday / kill) | with / with / with | never_positive |
| Fri 06 13:05 | flow | CADJPY | SELL | -49 | -0.06 | +0.2 / -0.1 | engine (time / Friday / kill) | with / with / against | never_positive |
| Fri 06 16:05 | flow | EURJPY | BUY | -236 | -0.18 | +0.0 / -0.2 | engine (time / Friday / kill) | against / against / against | against_h4, all_against, news_during, never_positive |
| Fri 06 20:00 | fib618 | USDCHF | BUY | +138 | +0.57 | +0.7 / -0.6 | engine (time / Friday / kill) | against / with / with | against_h4, news_before |

## 3. What the losers have in common (this week)

| Tag | Losers with it | Winners with it | Net $ of trades with it |
|---|---|---|---|
| against the H4 trend at entry | 10/24 (42%) | 4/9 (44%) | -1,119 |
| H4, H1 and M15 all against at entry | 5/24 (21%) | 1/9 (11%) | -546 |
| high-impact news of its currencies during the trade | 1/24 (4%) | 1/9 (11%) | +418 |
| high-impact news 1-3 h before entry | 0/24 (0%) | 1/9 (11%) | +138 |
| stopped within 1 hour | 8/24 (33%) | 0/9 (0%) | -2,031 |
| stopped, then price went +2R our way within 48 h (stop too tight / too early) | 9/24 (38%) | 0/9 (0%) | -2,267 |
| never got to +0.5R | 21/24 (88%) | 6/9 (67%) | -2,816 |
| was +1R or more, ended a loss | 2/24 (8%) | 0/9 (0%) | -501 |
| gold (XAUUSD) | 1/24 (4%) | 0/9 (0%) | -250 |
| entered in the Asian session (before 07:00 UTC) | 5/24 (21%) | 1/9 (11%) | -656 |

## 4. Last week's news (high impact) and how price reacted

| Time (UTC) | Ccy | Event | Actual / forecast / previous | Ccy move 1 h after | Our trades open then |
|---|---|---|---|---|---|
| Tue 03 00:00 | USD | Congressional Elections [All Day] | - / - / - | +3 pips (EURUSD) | fib618 GBPUSD BUY (+654) |
| Tue 03 03:30 | AUD | Cash Rate | 4.85% / 4.85% / 4.60% | -8 pips (AUDUSD) | - |
| Tue 03 03:30 | AUD | RBA Rate Statement | - / - / - | -8 pips (AUDUSD) | - |
| Tue 03 03:30 | AUD | RBA Monetary Policy Statement | - / - / - | -8 pips (AUDUSD) | - |
| Tue 03 04:30 | AUD | RBA Press Conference | - / - / - | -6 pips (AUDUSD) | - |
| Tue 03 21:45 | NZD | Unemployment Rate | 5.7% / 5.5% / 5.6% | -28 pips (NZDUSD) | - |
| Tue 03 21:45 | NZD | Employment Change q/q | -0.1% / 0.4% / 0.3% | -28 pips (NZDUSD) | - |
| Thu 05 12:00 | GBP | MPC Official Bank Rate Votes | 9-0-0 / 7-0-2 / 3-0-6 | +16 pips (GBPUSD) | - |
| Thu 05 12:00 | GBP | Official Bank Rate | 4.00% / 4.00% / 3.75% | +16 pips (GBPUSD) | - |
| Thu 05 12:00 | GBP | BOE Monetary Policy Report | - / - / - | +16 pips (GBPUSD) | - |
| Thu 05 12:00 | GBP | Monetary Policy Summary | - / - / - | +16 pips (GBPUSD) | - |
| Thu 05 12:30 | GBP | BOE Gov Bailey Speaks | - / - / - | -13 pips (GBPUSD) | - |
| Fri 06 00:00 | NZD | Parliamentary Elections [All Day] | - / - / - | +1 pips (NZDUSD) | - |
| Fri 06 13:30 | USD | Unemployment Rate | 4.1% / 4.2% / 4.2% | +31 pips (EURUSD) | - |
| Fri 06 13:30 | USD | Non-Farm Employment Change | 165K / 65K / 60K | +31 pips (EURUSD) | - |
| Fri 06 13:30 | USD | Average Hourly Earnings m/m | 0.4% / 0.3% / 0.3% | +31 pips (EURUSD) | - |
| Fri 06 13:30 | CAD | Unemployment Rate | 6.7% / 6.5% / 6.5% | -61 pips (USDCAD) | - |
| Fri 06 13:30 | CAD | Employment Change | -4.7K / 14.6K / -68.3K | -61 pips (USDCAD) | - |
| Fri 06 16:00 | JPY | BOJ Gov Ueda Speaks | - / - / - | +2 pips (USDJPY) | flow EURJPY BUY (-236) |

## 5. All journaled trades so far (51, net -3,335 $)

| Tag | Losers with it | Winners with it | Net $ of trades with it |
|---|---|---|---|
| against the H4 trend at entry | 14/34 (41%) | 8/17 (47%) | -1,344 |
| H4, H1 and M15 all against at entry | 5/34 (15%) | 2/17 (12%) | -516 |
| high-impact news of its currencies during the trade | 4/34 (12%) | 2/17 (12%) | -108 |
| high-impact news 1-3 h before entry | 0/34 (0%) | 1/17 (6%) | +138 |
| stopped within 1 hour | 12/34 (35%) | 0/17 (0%) | -3,080 |
| stopped, then price went +2R our way within 48 h (stop too tight / too early) | 17/34 (50%) | 0/17 (0%) | -4,289 |
| never got to +0.5R | 26/34 (76%) | 12/17 (71%) | -3,397 |
| was +1R or more, ended a loss | 4/34 (12%) | 0/17 (0%) | -1,059 |
| gold (XAUUSD) | 3/34 (9%) | 0/17 (0%) | -729 |
| entered in the Asian session (before 07:00 UTC) | 7/34 (21%) | 3/17 (18%) | -1,095 |

## 6. Ideas to check (candidates only)

This week: none (no entry-time fact on >= 25 points more of the losers, with >= 5 losers).

All trades so far: none yet.

A candidate is only a question: one week is ~20-40 trades and at a 25-35% win rate 6 losses in a row happen by chance. Next step for any candidate = test it on the 5-10y backtest trades and the untouched period.
