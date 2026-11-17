# Weekly trade review -- week of 2026-10-26

Written 2026-11-10 08:26 UTC by `python -m bensim.journal`. Observe only: nothing here changes a trade or a rule. Ideas go through the 5-10y backtest + untouched period + shadow run, then your GO.

## 1. Summary

**18 closed trades, 8 winners, net -1,088.04 $** (R known for 18: -5.82R).

| Strategy | Trades | Wins | Net $ | R |
|---|---|---|---|---|
| enigma | 2 | 1 | +268 | +1.11 |
| fib618 | 4 | 1 | -528 | -2.17 |
| flow | 7 | 6 | +388 | +0.26 |
| imbalance | 4 | 0 | -970 | -4.01 |
| sflow | 1 | 0 | -246 | -1.00 |

## 2. Every trade

| Closed (UTC) | Strategy | Pair | Side | $ | R | Best / worst R | Exit | H4/H1/M15 at entry | Tags |
|---|---|---|---|---|---|---|---|---|---|
| Mon 26 12:05 | flow | EURUSD | SELL | +335 | +0.18 | +0.2 / -0.1 | engine (time / Friday / kill) | with / against / with | never_positive |
| Mon 26 15:55 | imbalance | USDCAD | BUY | -244 | -1.00 | +0.5 / -1.3 | stop loss | against / with / with | against_h4, fast_stop, stop_then_ran |
| Mon 26 17:26 | fib618 | GBPUSD | SELL | -245 | -1.03 | +0.5 / -1.0 | stop loss | with / with / with | stop_then_ran |
| Mon 26 17:32 | fib618 | USDCAD | BUY | -250 | -1.02 | +0.0 / -1.1 | stop loss | against / with / with | against_h4, stop_then_ran, never_positive |
| Tue 27 12:05 | flow | EURUSD | SELL | +108 | +0.06 | +0.2 / -0.0 | engine (time / Friday / kill) | with / with / with | never_positive |
| Wed 28 05:55 | imbalance | XAUUSD | BUY | -237 | -1.00 | +0.2 / -1.1 | stop loss | with / against / against | stop_then_ran, never_positive, gold, asia |
| Wed 28 10:05 | flow | CADJPY | SELL | +11 | +0.01 | +0.1 / -0.0 | engine (time / Friday / kill) | against / with / with | against_h4, never_positive, asia |
| Wed 28 12:05 | flow | EURUSD | SELL | -248 | -0.13 | +0.0 / -0.1 | engine (time / Friday / kill) | with / with / against | never_positive |
| Wed 28 13:45 | fib618 | USDCAD | SELL | -312 | -1.27 | +1.6 / -1.1 | stop loss | against / with / with | against_h4, news_during, fast_stop, stop_then_ran, gave_back |
| Wed 28 18:20 | imbalance | EURUSD | BUY | -247 | -1.01 | +0.7 / -1.3 | stop loss | against / with / with | against_h4, news_during, fast_stop, stop_then_ran |
| Thu 29 00:32 | imbalance | XAUUSD | BUY | -241 | -1.00 | +0.2 / -1.1 | stop loss | with / with / against | never_positive, gold |
| Thu 29 07:05 | flow | CADJPY | SELL | +29 | +0.04 | +0.1 / -0.1 | engine (time / Friday / kill) | against / against / against | against_h4, all_against, never_positive, asia |
| Thu 29 08:13 | enigma | USDCAD | SELL | -242 | -0.99 | +0.1 / -1.1 | stop loss | with / with / with | stop_then_ran, never_positive, asia |
| Thu 29 12:05 | flow | EURUSD | SELL | +45 | +0.02 | +0.2 / -0.2 | engine (time / Friday / kill) | with / with / against | never_positive |
| Thu 29 13:01 | sflow | AUDUSD | BUY | -246 | -1.00 | +1.4 / -1.0 | stop loss | with / against / against | news_during, fast_stop, stop_then_ran, gave_back |
| Thu 29 14:22 | fib618 | GBPUSD | BUY | +278 | +1.14 | +1.3 / -0.8 | take profit | against / with / with | against_h4, news_during |
| Fri 30 13:41 | enigma | EURUSD | SELL | +509 | +2.10 | +2.5 / -0.5 | take profit | against / against / with | against_h4 |
| Fri 30 20:00 | flow | EURUSD | BUY | +108 | +0.08 | +0.1 / -0.0 | engine (time / Friday / kill) | with / against / with | never_positive |

## 3. What the losers have in common (this week)

| Tag | Losers with it | Winners with it | Net $ of trades with it |
|---|---|---|---|
| against the H4 trend at entry | 4/10 (40%) | 4/8 (50%) | -225 |
| H4, H1 and M15 all against at entry | 0/10 (0%) | 1/8 (12%) | +29 |
| high-impact news of its currencies during the trade | 3/10 (30%) | 1/8 (12%) | -526 |
| stopped within 1 hour | 4/10 (40%) | 0/8 (0%) | -1,049 |
| stopped, then price went +2R our way within 48 h (stop too tight / too early) | 8/10 (80%) | 0/8 (0%) | -2,022 |
| never got to +0.5R | 5/10 (50%) | 6/8 (75%) | -582 |
| was +1R or more, ended a loss | 2/10 (20%) | 0/8 (0%) | -558 |
| gold (XAUUSD) | 2/10 (20%) | 0/8 (0%) | -479 |
| entered in the Asian session (before 07:00 UTC) | 2/10 (20%) | 2/8 (25%) | -439 |

## 4. Last week's news (high impact) and how price reacted

| Time (UTC) | Ccy | Event | Actual / forecast / previous | Ccy move 1 h after | Our trades open then |
|---|---|---|---|---|---|
| Wed 28 00:30 | AUD | CPI m/m | 0.8% / 0.7% / 0.4% | +3 pips (AUDUSD) | - |
| Wed 28 00:30 | AUD | CPI y/y | 4.3% / 4.2% / 4.0% | +3 pips (AUDUSD) | - |
| Wed 28 00:30 | AUD | Trimmed Mean CPI m/m | 0.3% / 0.3% / 0.2% | +3 pips (AUDUSD) | - |
| Wed 28 01:00 | NZD | Official Cash Rate | 2.75% / 3.00% / 2.75% | -60 pips (NZDUSD) | - |
| Wed 28 01:00 | NZD | RBNZ Rate Statement | - / - / - | -60 pips (NZDUSD) | - |
| Wed 28 02:00 | NZD | RBNZ Press Conference | - / - / - | -7 pips (NZDUSD) | - |
| Wed 28 12:30 | GBP | Annual Budget Release | - / - / - | +2 pips (GBPUSD) | - |
| Wed 28 13:45 | CAD | BOC Monetary Policy Report | - / - / - | -7 pips (USDCAD) | fib618 USDCAD SELL (-312) |
| Wed 28 13:45 | CAD | BOC Rate Statement | - / - / - | -7 pips (USDCAD) | fib618 USDCAD SELL (-312) |
| Wed 28 13:45 | CAD | Overnight Rate | 2.50% / 2.50% / 2.25% | -7 pips (USDCAD) | fib618 USDCAD SELL (-312) |
| Wed 28 14:30 | CAD | BOC Press Conference | - / - / - | +13 pips (USDCAD) | - |
| Wed 28 18:00 | USD | Federal Funds Rate | 4.00% / 4.00% / 4.00% | -14 pips (EURUSD) | imbalance EURUSD BUY (-247) |
| Wed 28 18:00 | USD | FOMC Statement | - / - / - | -14 pips (EURUSD) | imbalance EURUSD BUY (-247) |
| Wed 28 18:30 | USD | FOMC Press Conference | - / - / - | -26 pips (EURUSD) | fib618 GBPUSD BUY (+278) |
| Thu 29 12:30 | USD | Advance GDP q/q | 3.4% / 3.2% / 2.2% | +16 pips (EURUSD) | sflow AUDUSD BUY (-246), fib618 GBPUSD BUY (+278) |
| Thu 29 12:30 | USD | Core PCE Price Index m/m | 0.3% / 0.3% / 0.3% | +16 pips (EURUSD) | sflow AUDUSD BUY (-246), fib618 GBPUSD BUY (+278) |
| Thu 29 13:15 | EUR | Main Refinancing Rate | 2.65% / 2.65% / 2.65% | +6 pips (EURUSD) | - |
| Thu 29 13:15 | EUR | Monetary Policy Statement | - / - / - | +6 pips (EURUSD) | - |
| Thu 29 13:45 | EUR | ECB Press Conference | - / - / - | +33 pips (EURUSD) | - |
| Fri 30 03:02 | JPY | BOJ Outlook Report | - / - / - | +10 pips (USDJPY) | - |
| Fri 30 03:02 | JPY | BOJ Policy Rate | <1.25% / <1.25% / <1.25% | +10 pips (USDJPY) | - |
| Fri 30 03:02 | JPY | Monetary Policy Statement | - / - / - | +10 pips (USDJPY) | - |
| Fri 30 06:30 | JPY | BOJ Press Conference | - / - / - | +10 pips (USDJPY) | - |

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
