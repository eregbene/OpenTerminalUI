# Weekly trade review -- week of 2026-11-09

Written 2026-11-14 06:00 UTC by `python -m bensim.journal`. Observe only: nothing here changes a trade or a rule. Ideas go through the 5-10y backtest + untouched period + shadow run, then your GO.

## 1. Summary

**15 closed trades, 4 winners, net -10,644.07 $** (R known for 15: -13.14R).

| Strategy | Trades | Wins | Net $ | R |
|---|---|---|---|---|
| channel | 4 | 0 | -10,750 | -12.57 |
| fib618 | 4 | 1 | -350 | -1.44 |
| flow | 2 | 1 | +94 | +0.18 |
| imbalance | 4 | 1 | -105 | -0.38 |
| sflow | 1 | 1 | +467 | +1.06 |

## 2. Every trade

| Closed (UTC) | Strategy | Pair | Side | $ | R | Best / worst R | Exit | H4/H1/M15 at entry | Tags |
|---|---|---|---|---|---|---|---|---|---|
| Mon 09 07:14 | imbalance | XAUUSD | BUY | -247 | -1.04 | +0.5 / -1.0 | stop loss | with / with / against | fast_stop, stop_then_ran, gold, asia |
| Mon 09 12:00 | flow | EURUSD | SELL | -22 | -0.05 | +0.0 / -0.2 | engine (time / Friday / kill) | with / against / against | never_positive |
| Mon 09 14:49 | sflow | AUDUSD | BUY | +467 | +1.06 | +1.1 / -0.1 | take profit | against / with / against | against_h4 |
| Tue 10 07:22 | fib618 | USDCHF | BUY | -436 | -0.97 | +0.4 / -1.0 | stop loss | with / with / against | stop_then_ran, never_positive |
| Tue 10 12:05 | flow | EURUSD | SELL | +116 | +0.23 | +0.3 / -0.2 | engine (time / Friday / kill) | with / against / against | never_positive |
| Tue 10 13:30 | fib618 | XAUUSD | BUY | -232 | -1.07 | +1.7 / -1.4 | stop loss | with / with / against | news_during, gave_back, gold |
| Tue 10 13:30 | imbalance | GBPUSD | SELL | +1,126 | +2.64 | +3.4 / -0.2 | take profit | with / with / with | news_during |
| Wed 11 05:14 | imbalance | XAUUSD | SELL | -492 | -0.98 | +1.2 / -1.1 | stop loss | with / with / against | stop_then_ran, gave_back, gold, asia |
| Thu 12 15:20 | channel | GBPUSD | BUY | -8,060 | -9.37 | +0.3 / -9.6 | engine (time / Friday / kill) | against / with / against | against_h4, news_during, never_positive, asia |
| Fri 13 01:22 | imbalance | XAUUSD | SELL | -493 | -1.00 | +0.3 / -1.3 | stop loss | with / with / against | fast_stop, never_positive, gold, asia |
| Fri 13 10:49 | channel | GBPUSD | BUY | -836 | -1.02 | +1.6 / -1.0 | stop loss | against / against / against | against_h4, all_against, stop_then_ran, gave_back, asia |
| Fri 13 15:29 | channel | EURUSD | BUY | -990 | -1.18 | +2.5 / -1.4 | stop loss | against / with / against | against_h4, news_during, gave_back |
| Fri 13 16:44 | channel | GBPUSD | BUY | -863 | -1.00 | +0.4 / -1.0 | stop loss | against / with / with | against_h4, news_before, never_positive |
| Fri 13 20:00 | fib618 | XAUUSD | SELL | +682 | +1.42 | +1.6 / -0.0 | engine (time / Friday / kill) | with / against / against | news_during, gold, asia |
| Fri 13 20:00 | fib618 | USDCAD | SELL | -364 | -0.81 | +0.2 / -0.9 | engine (time / Friday / kill) | against / with / with | against_h4, news_before, never_positive |

## 3. What the losers have in common (this week)

| Tag | Losers with it | Winners with it | Net $ of trades with it |
|---|---|---|---|
| against the H4 trend at entry | 5/11 (45%) | 1/4 (25%) | -10,647 |
| H4, H1 and M15 all against at entry | 1/11 (9%) | 0/4 (0%) | -836 |
| high-impact news of its currencies during the trade | 3/11 (27%) | 2/4 (50%) | -7,474 |
| high-impact news 1-3 h before entry | 2/11 (18%) | 0/4 (0%) | -1,227 |
| stopped within 1 hour | 2/11 (18%) | 0/4 (0%) | -739 |
| stopped, then price went +2R our way within 48 h (stop too tight / too early) | 4/11 (36%) | 0/4 (0%) | -2,011 |
| never got to +0.5R | 6/11 (55%) | 1/4 (25%) | -10,122 |
| was +1R or more, ended a loss | 4/11 (36%) | 0/4 (0%) | -2,550 |
| gold (XAUUSD) | 4/11 (36%) | 1/4 (25%) | -782 |
| entered in the Asian session (before 07:00 UTC) | 5/11 (45%) | 1/4 (25%) | -9,447 |

## 4. Last week's news (high impact) and how price reacted

| Time (UTC) | Ccy | Event | Actual / forecast / previous | Ccy move 1 h after | Our trades open then |
|---|---|---|---|---|---|
| Tue 10 13:30 | USD | Core CPI y/y | - / - / 2.6% | +20 pips (EURUSD) | fib618 XAUUSD BUY (-232), imbalance GBPUSD SELL (+1,126) |
| Tue 10 13:30 | USD | CPI y/y | - / - / 3.7% | +20 pips (EURUSD) | fib618 XAUUSD BUY (-232), imbalance GBPUSD SELL (+1,126) |
| Tue 10 13:30 | USD | Core CPI m/m | 0.3% / 0.2% / 0.3% | +20 pips (EURUSD) | fib618 XAUUSD BUY (-232), imbalance GBPUSD SELL (+1,126) |
| Tue 10 13:30 | USD | CPI m/m | 0.4% / 0.3% / 0.6% | +20 pips (EURUSD) | fib618 XAUUSD BUY (-232), imbalance GBPUSD SELL (+1,126) |
| Thu 12 07:00 | GBP | GDP m/m | 0.2% / 0.0% / 0.0% | +18 pips (GBPUSD) | channel GBPUSD BUY (-8,060) |
| Fri 13 13:30 | USD | Core PPI m/m | 0.3% / 0.3% / 0.8% | +8 pips (EURUSD) | channel EURUSD BUY (-990), fib618 XAUUSD SELL (+682) |
| Fri 13 13:30 | USD | PPI m/m | 0.3% / 0.4% / 1.4% | +8 pips (EURUSD) | channel EURUSD BUY (-990), fib618 XAUUSD SELL (+682) |

## 5. All journaled trades so far (66, net -13,979 $)

| Tag | Losers with it | Winners with it | Net $ of trades with it |
|---|---|---|---|
| against the H4 trend at entry | 19/45 (42%) | 9/21 (43%) | -11,991 |
| H4, H1 and M15 all against at entry | 6/45 (13%) | 2/21 (10%) | -1,353 |
| high-impact news of its currencies during the trade | 7/45 (16%) | 4/21 (19%) | -7,583 |
| high-impact news 1-3 h before entry | 2/45 (4%) | 1/21 (5%) | -1,089 |
| stopped within 1 hour | 14/45 (31%) | 0/21 (0%) | -3,819 |
| stopped, then price went +2R our way within 48 h (stop too tight / too early) | 21/45 (47%) | 0/21 (0%) | -6,300 |
| never got to +0.5R | 32/45 (71%) | 13/21 (62%) | -13,520 |
| was +1R or more, ended a loss | 8/45 (18%) | 0/21 (0%) | -3,609 |
| gold (XAUUSD) | 7/45 (16%) | 1/21 (5%) | -1,510 |
| entered in the Asian session (before 07:00 UTC) | 12/45 (27%) | 4/21 (19%) | -10,542 |

## 6. Ideas to check (candidates only)

This week: none (no entry-time fact on >= 25 points more of the losers, with >= 5 losers).

All trades so far: none yet.

A candidate is only a question: one week is ~20-40 trades and at a 25-35% win rate 6 losses in a row happen by chance. Next step for any candidate = test it on the 5-10y backtest trades and the untouched period.
