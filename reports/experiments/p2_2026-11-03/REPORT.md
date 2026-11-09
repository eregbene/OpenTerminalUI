# P2: 8 strategies, common dataset, frozen params

Period 2026-08-03 .. 2026-11-03 (end exclusive), symbols EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, XAUUSD, code {'commit': '6b1218c', 'dirty': True, 'bensim_sha256': '3a975329754fce5a'}, DSR trials N = 350.
Protocol: docs/audit/p2_protocol_2026-11-03.md. Costs per profile below.

## Summary table
| profile                | set                |   trades |   net_r |   gross_before_comm_swap |   total_r |   pf |   day_t |   sharpe_ann |     psr |     dsr |   p_boot_le0 |   max_dd_r | months_pos   |   holm_p |
|:-----------------------|:-------------------|---------:|--------:|-------------------------:|----------:|-----:|--------:|-------------:|--------:|--------:|-------------:|-----------:|:-------------|---------:|
| observed_account       | fib618             |       92 |   0.288 |                    0.3   |      26.5 | 1.47 |    1.28 |         2.48 |   0.938 |   0     |        0.066 |      10.02 | 3/4          |   1      |
| observed_account       | imbalance          |       80 |   0.063 |                    0.066 |       5   | 1.08 |    0.24 |         0.46 |   0.602 |   0     |        0.407 |      20.82 | 2/4          |   1      |
| observed_account       | enigma             |       24 |   0.43  |                    0.444 |      10.3 | 1.68 |    0.77 |         1.5  |   0.839 |   0.049 |        0.212 |       7.11 | 3/3          |   1      |
| observed_account       | h1_fvg             |       41 |  -0.464 |                   -0.457 |     -19   | 0.44 |   -2.14 |        -4.13 |   0.084 |   0.001 |        0.981 |      25.6  | 1/4          |   0.1941 |
| observed_account       | bennett            |        5 |   1.069 |                    1.16  |       5.3 | 3.66 |    0.99 |         1.93 |   0.859 |   0.547 |        0.158 |       2.01 | 2/2          |   1      |
| observed_account       | pullback           |     1065 |  -0.117 |                   -0.116 |    -124.1 | 0.83 |   -2.57 |        -4.68 |   0.003 |   0     |        0.999 |     131.49 | 0/4          |   0.0712 |
| observed_account       | orderflow          |       11 |  -1.006 |                   -1.003 |     -11.1 | 0    |   -3.24 |        -6.32 |   0.015 |   0.014 |        1     |      11.07 | 0/3          |   0.0096 |
| observed_account       | gf                 |        2 |   0.438 |                    0.434 |       0.9 | 5.06 |    1    |         1.95 | nan     | nan     |        0.358 |       0.22 | 1/1          |   1      |
| observed_account       | PORTFOLIO all 8    |     1320 |  -0.08  |                   -0.078 |    -106.1 | 0.88 |   -1.53 |        -2.77 |   0.033 |   0     |        0.959 |     138.14 | 0/4          | nan      |
| observed_account       | PORTFOLIO live set |      201 |   0.235 |                    0.246 |      47.2 | 1.35 |    1.24 |         2.39 |   0.955 |   0     |        0.078 |      23.68 | 3/4          | nan      |
| legacy                 | fib618             |       91 |   0.345 |                    0.39  |      31.4 | 1.51 |    1.47 |         2.81 |   0.955 |   0.001 |        0.065 |      15.37 | 2/4          |   0.7078 |
| legacy                 | imbalance          |       80 |  -0.012 |                    0.043 |      -1   | 0.99 |   -0.04 |        -0.08 |   0.482 |   0     |        0.531 |      27.81 | 1/4          |   1      |
| legacy                 | enigma             |       24 |   0.321 |                    0.379 |       7.7 | 1.44 |    0.57 |         1.11 |   0.754 |   0.035 |        0.283 |       8.45 | 2/3          |   1      |
| legacy                 | h1_fvg             |       41 |  -0.441 |                   -0.391 |     -18.1 | 0.48 |   -2.04 |        -3.93 |   0.087 |   0     |        0.975 |      24.57 | 1/4          |   0.2481 |
| legacy                 | bennett            |        5 |   1.198 |                    1.232 |       6   | 3.9  |    1.02 |         1.98 |   0.868 |   0.575 |        0.143 |       2.06 | 2/2          |   1      |
| legacy                 | pullback           |     1045 |  -0.154 |                   -0.102 |    -161.2 | 0.79 |   -3.17 |        -5.78 |   0     |   0     |        1     |     166.45 | 0/4          |   0.0107 |
| legacy                 | orderflow          |       11 |  -1.027 |                   -1.003 |     -11.3 | 0    |   -3.25 |        -6.35 |   0     |   0     |        1     |      11.3  | 0/3          |   0.0092 |
| legacy                 | gf                 |        2 |   0.247 |                    0.253 |       0.5 | 1.84 |    0.4  |         0.77 | nan     | nan     |        0.449 |       0.59 | 1/1          |   1      |
| legacy                 | PORTFOLIO all 8    |     1299 |  -0.112 |                   -0.061 |    -146   | 0.85 |   -1.99 |        -3.6  |   0.009 |   0     |        0.984 |     164.38 | 0/4          | nan      |
| legacy                 | PORTFOLIO live set |      200 |   0.221 |                    0.271 |      44.1 | 1.29 |    1.13 |         2.14 |   0.928 |   0     |        0.109 |      29.01 | 2/4          | nan      |
| observed_weekends_held | fib618             |       91 |   0.287 |                    0.301 |      26.1 | 1.43 |    1.21 |         2.3  |   0.924 |   0     |        0.103 |      16.11 | 2/4          |   1      |
| observed_weekends_held | imbalance          |       80 |   0.026 |                    0.031 |       2.1 | 1.03 |    0.09 |         0.18 |   0.54  |   0     |        0.478 |      25.41 | 1/4          |   1      |
| observed_weekends_held | enigma             |       24 |   0.352 |                    0.368 |       8.5 | 1.51 |    0.63 |         1.23 |   0.781 |   0.039 |        0.257 |       8.08 | 2/3          |   1      |
| observed_weekends_held | h1_fvg             |       41 |  -0.405 |                   -0.399 |     -16.6 | 0.51 |   -1.91 |        -3.67 |   0.102 |   0.001 |        0.969 |      23.18 | 1/4          |   0.3368 |
| observed_weekends_held | bennett            |        5 |   1.069 |                    1.16  |       5.3 | 3.66 |    0.99 |         1.93 |   0.859 |   0.547 |        0.158 |       2.01 | 2/2          |   1      |
| observed_weekends_held | pullback           |     1039 |  -0.142 |                   -0.141 |    -147.7 | 0.8  |   -3.14 |        -5.72 |   0.001 |   0     |        1     |     153.44 | 0/4          |   0.0118 |
| observed_weekends_held | orderflow          |       11 |  -1.006 |                   -1.003 |     -11.1 | 0    |   -3.24 |        -6.32 |   0.015 |   0.014 |        1     |      11.07 | 0/3          |   0.0096 |
| observed_weekends_held | gf                 |        2 |   0.254 |                    0.25  |       0.5 | 1.87 |    0.41 |         0.79 | nan     | nan     |        0.449 |       0.58 | 1/1          |   1      |
| observed_weekends_held | PORTFOLIO all 8    |     1293 |  -0.103 |                   -0.1   |    -132.8 | 0.86 |   -1.88 |        -3.38 |   0.013 |   0     |        0.982 |     155.24 | 0/4          | nan      |
| observed_weekends_held | PORTFOLIO live set |      200 |   0.21  |                    0.223 |      42   | 1.29 |    1.07 |         2.01 |   0.922 |   0     |        0.104 |      26.55 | 2/4          | nan      |

## Profile `observed_account`
Cost sources: **spread**: observed (5 samples, one London/NY session 2026-11-03) applied to all hours = estimated off-hours; x4 at rollover = estimated; **commission**: observed ($0 on 150 days of demo deals); **slippage**: estimated (0.1 pip); **swap**: observed per lot-night (150 days of deals), applied to past dates = estimated; pairs/sides with no overnight deals = unknown (0); **gaps**: observed (bar opens); **friday_close**: as live.py (fib618/Bennett); books hold weekends live

### fib618
- trades 92, win 36%, **expectancy +0.288R net** (before commission+swap +0.300R; commission 0.000R; swap -0.012R, unknown-swap trades 4), total +26.5R, PF 1.47
- trade t 1.29, day-clustered t 1.28, daily Sharpe 2.48, Sortino 5.99, lag-1 autocorr -0.026, effective days 70.6 of 67
- PSR(>0) 0.938, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-2.1, 58.4], P(total<=0) 0.066, max DD median 9.4R / p95 18.0R
- drawdown: max 10.02R, peak->trough 26.0 d, recovery not recovered d, longest under water 32.4 d; months positive 3/4
- by symbol: EURUSD n19 +0.57R (+10.8); GBPUSD n24 +0.91R (+21.8); USDCAD n16 -0.01R (-0.2); USDCHF n9 +0.24R (+2.2); USDJPY n10 -0.41R (-4.1); XAUUSD n14 -0.29R (-4.0)
- by session: Asia 00-07 n5 -1.00R (-5.0); London 07-12 n17 +1.32R (+22.4); London/NY 12-16 n47 +0.06R (+2.7); NY 16-21 n16 +0.43R (+6.8); rollover 21-24 n7 -0.06R (-0.4)
- by trend regime: range n46 +0.47R (+21.8); trend n46 +0.10R (+4.8)
- by vol regime: high vol n29 +0.09R (+2.6); low vol n63 +0.38R (+23.9)
- by month frozen walk forward: 2026-08 n19 +0.24R (+4.5); 2026-09 n33 +0.65R (+21.3); 2026-10 n38 +0.05R (+1.8); 2026-11 n2 -0.55R (-1.1)
- by side: long n41 +0.03R (+1.3); short n51 +0.49R (+25.2)
- by exit: friday_close n14 +0.42R (+5.9); stop n53 -1.02R (-54.2); target n22 +3.30R (+72.6); time n3 +0.73R (+2.2)

### imbalance
- trades 80, win 22%, **expectancy +0.063R net** (before commission+swap +0.066R; commission 0.000R; swap -0.004R, unknown-swap trades 2), total +5.0R, PF 1.08
- trade t 0.25, day-clustered t 0.24, daily Sharpe 0.46, Sortino 1.03, lag-1 autocorr 0.031, effective days 62.0 of 66
- PSR(>0) 0.602, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-25.9, 37.7], P(total<=0) 0.407, max DD median 17.2R / p95 35.4R
- drawdown: max 20.82R, peak->trough 19.3 d, recovery not recovered d, longest under water 60.3 d; months positive 2/4
- by symbol: EURUSD n15 -0.03R (-0.4); GBPUSD n11 +0.08R (+0.8); USDCAD n5 -0.11R (-0.6); USDCHF n2 -1.01R (-2.0); USDJPY n22 +0.11R (+2.5); XAUUSD n25 +0.19R (+4.7)
- by session: Asia 00-07 n11 -0.07R (-0.8); London 07-12 n15 +0.89R (+13.4); London/NY 12-16 n34 +0.27R (+9.2); NY 16-21 n12 -0.73R (-8.7); rollover 21-24 n8 -1.00R (-8.0)
- by trend regime: range n38 +0.03R (+1.0); trend n42 +0.10R (+4.1)
- by vol regime: high vol n31 -0.26R (-8.1); low vol n49 +0.27R (+13.1)
- by month frozen walk forward: 2026-08 n13 +0.49R (+6.4); 2026-09 n34 -0.12R (-4.0); 2026-10 n31 +0.15R (+4.7); 2026-11 n2 -1.01R (-2.0)
- by side: long n36 +0.01R (+0.5); short n44 +0.10R (+4.6)
- by exit: friday_close n6 +2.21R (+13.3); stop n61 -1.01R (-61.4); target n13 +4.09R (+53.2)

### enigma
- trades 24, win 33%, **expectancy +0.430R net** (before commission+swap +0.444R; commission 0.000R; swap -0.014R, unknown-swap trades 1), total +10.3R, PF 1.68
- trade t 0.81, day-clustered t 0.77, daily Sharpe 1.5, Sortino 4.82, lag-1 autocorr -0.087, effective days 79.8 of 67
- PSR(>0) 0.839, **DSR 0.049** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-8.7, 33.0], P(total<=0) 0.212, max DD median 7.0R / p95 14.1R
- drawdown: max 7.11R, peak->trough 39.6 d, recovery not recovered d, longest under water 50.3 d; months positive 3/3
- by symbol: EURUSD n6 +0.22R (+1.3); GBPUSD n4 +2.55R (+10.2); USDCAD n5 +1.57R (+7.8); USDCHF n5 -1.00R (-5.0); USDJPY n2 -1.01R (-2.0); XAUUSD n2 -1.00R (-2.0)
- by session: Asia 00-07 n6 -0.46R (-2.8); London 07-12 n6 +1.35R (+8.1); London/NY 12-16 n7 +0.09R (+0.6); NY 16-21 n3 +0.49R (+1.5); rollover 21-24 n2 +1.45R (+2.9)
- by trend regime: range n13 +1.00R (+13.0); trend n11 -0.24R (-2.7)
- by vol regime: high vol n8 -0.04R (-0.3); low vol n16 +0.66R (+10.6)
- by month frozen walk forward: 2026-08 n9 +0.63R (+5.7); 2026-09 n8 +0.49R (+3.9); 2026-10 n7 +0.10R (+0.7)
- by side: long n11 -0.43R (-4.7); short n13 +1.15R (+15.0)
- by exit: friday_close n2 +0.14R (+0.3); stop n15 -1.01R (-15.1); target n6 +3.99R (+24.0); time n1 +1.18R (+1.2)

### h1_fvg
- trades 41, win 17%, **expectancy -0.464R net** (before commission+swap -0.457R; commission 0.000R; swap -0.007R, unknown-swap trades 3), total -19.0R, PF 0.44
- trade t -2.14, day-clustered t -2.14, daily Sharpe -4.13, Sortino -5.6, lag-1 autocorr -0.025, effective days 71.4 of 68
- PSR(>0) 0.084, **DSR 0.001** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-33.3, -4.2], P(total<=0) 0.981, max DD median 21.5R / p95 34.6R
- drawdown: max 25.6R, peak->trough 80.0 d, recovery not recovered d, longest under water 80.5 d; months positive 1/4
- by symbol: EURUSD n5 -1.05R (-5.3); GBPUSD n8 -0.61R (-4.9); USDCAD n3 -0.51R (-1.5); USDCHF n9 -1.01R (-9.1); USDJPY n9 +0.27R (+2.4); XAUUSD n7 -0.10R (-0.7)
- by session: Asia 00-07 n13 -0.51R (-6.6); London 07-12 n9 -0.09R (-0.8); London/NY 12-16 n15 -0.50R (-7.5); NY 16-21 n2 -1.03R (-2.1); rollover 21-24 n2 -1.00R (-2.0)
- by trend regime: range n21 -0.50R (-10.5); trend n20 -0.43R (-8.5)
- by vol regime: high vol n11 -0.38R (-4.2); low vol n30 -0.49R (-14.8)
- by month frozen walk forward: 2026-08 n9 +0.10R (+0.9); 2026-09 n13 -0.86R (-11.2); 2026-10 n16 -0.46R (-7.3); 2026-11 n3 -0.47R (-1.4)
- by side: long n19 -0.67R (-12.7); short n22 -0.29R (-6.3)
- by exit: friday_close n2 -0.08R (-0.2); stop n33 -1.02R (-33.5); target n5 +2.82R (+14.1); time n1 +0.60R (+0.6)

### bennett
- trades 5, win 60%, **expectancy +1.069R net** (before commission+swap +1.160R; commission 0.000R; swap -0.09R, unknown-swap trades 0), total +5.3R, PF 3.66
- trade t 1.11, day-clustered t 0.99, daily Sharpe 1.93, Sortino 7.35, lag-1 autocorr -0.015, effective days 68.0 of 66
- PSR(>0) 0.859, **DSR 0.547** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-2.0, 14.3], P(total<=0) 0.158, max DD median 1.0R / p95 3.0R
- drawdown: max 2.01R, peak->trough 62.1 d, recovery 3.0 d, longest under water 65.1 d; months positive 2/2
- by symbol: EURUSD n1 +3.36R (+3.4); USDCHF n2 -0.10R (-0.2); XAUUSD n2 +1.10R (+2.2)
- by session: London 07-12 n4 +0.54R (+2.1); London/NY 12-16 n1 +3.20R (+3.2)
- by trend regime: range n4 +0.50R (+2.0); trend n1 +3.36R (+3.4)
- by vol regime: low vol n5 +1.07R (+5.3)
- by month frozen walk forward: 2026-08 n1 +3.36R (+3.4); 2026-10 n4 +0.50R (+2.0)
- by side: long n1 +3.36R (+3.4); short n4 +0.50R (+2.0)
- by exit: stop n2 -1.00R (-2.0); target n2 +3.28R (+6.6); time n1 +0.80R (+0.8)

### pullback
- trades 1065, win 30%, **expectancy -0.117R net** (before commission+swap -0.116R; commission 0.000R; swap -0.001R, unknown-swap trades 32), total -124.1R, PF 0.83
- trade t -2.84, day-clustered t -2.57, daily Sharpe -4.68, Sortino -5.24, lag-1 autocorr -0.114, effective days 95.6 of 76
- PSR(>0) 0.003, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-195.1, -55.4], P(total<=0) 0.999, max DD median 131.6R / p95 200.9R
- drawdown: max 131.49R, peak->trough 73.5 d, recovery not recovered d, longest under water 92.0 d; months positive 0/4
- by symbol: EURUSD n136 -0.28R (-38.4); GBPUSD n93 -0.20R (-18.8); USDCAD n54 +0.05R (+2.9); USDCHF n49 +0.03R (+1.6); USDJPY n310 -0.14R (-41.9); XAUUSD n423 -0.07R (-29.4)
- by session: Asia 00-07 n230 -0.16R (-36.6); London 07-12 n214 -0.13R (-28.3); London/NY 12-16 n377 -0.09R (-35.1); NY 16-21 n172 -0.07R (-11.4); rollover 21-24 n72 -0.17R (-12.6)
- by trend regime: range n552 -0.09R (-49.4); trend n513 -0.15R (-74.7)
- by vol regime: high vol n405 -0.11R (-44.0); low vol n660 -0.12R (-80.1)
- by month frozen walk forward: 2026-08 n258 -0.12R (-31.8); 2026-09 n364 -0.08R (-29.6); 2026-10 n420 -0.12R (-52.1); 2026-11 n23 -0.46R (-10.6)
- by side: long n528 -0.13R (-68.9); short n537 -0.10R (-55.2)
- by exit: friday_close n38 +0.13R (+4.9); stop n722 -1.00R (-725.8); target n302 +1.97R (+595.6); time n3 +0.40R (+1.2)

### orderflow
- trades 11, win 0%, **expectancy -1.006R net** (before commission+swap -1.003R; commission 0.000R; swap -0.003R, unknown-swap trades 0), total -11.1R, PF 0.0
- trade t -87.73, day-clustered t -3.24, daily Sharpe -6.32, Sortino -5.91, lag-1 autocorr -0.164, effective days 91.9 of 66
- PSR(>0) 0.015, **DSR 0.014** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-16.9, -6.0], P(total<=0) 1.0, max DD median 11.0R / p95 16.9R
- drawdown: max 11.07R, peak->trough 83.9 d, recovery not recovered d, longest under water 83.9 d; months positive 0/3
- by symbol: USDCAD n1 -1.11R (-1.1); USDJPY n1 -0.94R (-0.9); XAUUSD n9 -1.00R (-9.0)
- by session: Asia 00-07 n7 -0.99R (-7.0); London 07-12 n1 -1.00R (-1.0); NY 16-21 n3 -1.04R (-3.1)
- by trend regime: range n6 -0.99R (-6.0); trend n5 -1.02R (-5.1)
- by vol regime: high vol n1 -1.00R (-1.0); low vol n10 -1.01R (-10.1)
- by month frozen walk forward: 2026-08 n6 -1.02R (-6.1); 2026-09 n2 -1.00R (-2.0); 2026-10 n3 -0.98R (-2.9)
- by side: long n3 -0.98R (-2.9); short n8 -1.01R (-8.1)
- by exit: stop n11 -1.01R (-11.1)

### gf
- trades 2, win 50%, **expectancy +0.438R net** (before commission+swap +0.434R; commission 0.000R; swap 0.004R, unknown-swap trades 0), total +0.9R, PF 5.06
- trade t 0.67, day-clustered t 1.0, daily Sharpe 1.95, Sortino None, lag-1 autocorr -0.016, effective days 68.1 of 66
- PSR(>0) nan, **DSR nan** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [0.0, 2.6], P(total<=0) 0.358, max DD median 0.0R / p95 0.0R
- drawdown: max 0.22R, peak->trough 0.5 d, recovery not recovered d, longest under water 0.5 d; months positive 1/1
- by symbol: USDJPY n1 -0.22R (-0.2); XAUUSD n1 +1.09R (+1.1)
- by session: Asia 00-07 n1 -0.22R (-0.2); London 07-12 n1 +1.09R (+1.1)
- by trend regime: range n2 +0.44R (+0.9)
- by vol regime: low vol n2 +0.44R (+0.9)
- by month frozen walk forward: 2026-10 n2 +0.44R (+0.9)
- by side: short n2 +0.44R (+0.9)
- by exit: friday_close n1 -0.22R (-0.2); target n1 +1.09R (+1.1)

### PORTFOLIO all 8
- trades 1320, win 30%, **expectancy -0.080R net** (before commission+swap -0.078R; commission 0.000R; swap -0.003R, unknown-swap trades 42), total -106.1R, PF 0.88
- trade t -1.93, day-clustered t -1.53, daily Sharpe -2.77, Sortino -3.58, lag-1 autocorr -0.072, effective days 89.0 of 77
- PSR(>0) 0.033, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-209.1, -6.1], P(total<=0) 0.959, max DD median 128.4R / p95 221.5R
- drawdown: max 138.14R, peak->trough 41.0 d, recovery not recovered d, longest under water 52.5 d; months positive 0/4
- by symbol: EURUSD n182 -0.16R (-28.6); GBPUSD n140 +0.07R (+9.2); USDCAD n84 +0.09R (+7.3); USDCHF n76 -0.17R (-12.5); USDJPY n355 -0.12R (-44.3); XAUUSD n483 -0.08R (-37.2)
- by session: Asia 00-07 n273 -0.22R (-58.9); London 07-12 n267 +0.06R (+17.0); London/NY 12-16 n481 -0.06R (-26.9); NY 16-21 n208 -0.08R (-17.0); rollover 21-24 n91 -0.22R (-20.1)
- by trend regime: range n682 -0.04R (-27.3); trend n638 -0.12R (-78.8)
- by vol regime: high vol n485 -0.11R (-55.0); low vol n835 -0.06R (-51.1)
- by month frozen walk forward: 2026-08 n315 -0.05R (-17.0); 2026-09 n454 -0.05R (-21.6); 2026-10 n521 -0.10R (-52.2); 2026-11 n30 -0.51R (-15.2)
- by side: long n639 -0.13R (-84.1); short n681 -0.03R (-21.9)
- by exit: friday_close n63 +0.38R (+24.0); stop n897 -1.01R (-903.1); target n351 +2.19R (+767.0); time n9 +0.66R (+6.0)

### PORTFOLIO live set
- trades 201, win 31%, **expectancy +0.235R net** (before commission+swap +0.246R; commission 0.000R; swap -0.011R, unknown-swap trades 7), total +47.2R, PF 1.35
- trade t 1.5, day-clustered t 1.24, daily Sharpe 2.39, Sortino 5.3, lag-1 autocorr -0.021, effective days 71.0 of 68
- PSR(>0) 0.955, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-7.9, 103.0], P(total<=0) 0.078, max DD median 21.0R / p95 42.7R
- drawdown: max 23.68R, peak->trough 11.0 d, recovery 14.0 d, longest under water 25.0 d; months positive 3/4
- by symbol: EURUSD n41 +0.37R (+15.1); GBPUSD n39 +0.84R (+32.9); USDCAD n26 +0.27R (+7.0); USDCHF n18 -0.28R (-5.0); USDJPY n34 -0.11R (-3.6); XAUUSD n43 +0.02R (+0.9)
- by session: Asia 00-07 n22 -0.39R (-8.5); London 07-12 n42 +1.10R (+46.0); London/NY 12-16 n89 +0.18R (+15.7); NY 16-21 n31 -0.01R (-0.4); rollover 21-24 n17 -0.33R (-5.6)
- by trend regime: range n101 +0.37R (+37.7); trend n100 +0.10R (+9.5)
- by vol regime: high vol n68 -0.09R (-5.8); low vol n133 +0.40R (+53.0)
- by month frozen walk forward: 2026-08 n42 +0.47R (+20.0); 2026-09 n75 +0.28R (+21.2); 2026-10 n80 +0.12R (+9.2); 2026-11 n4 -0.78R (-3.1)
- by side: long n89 +0.01R (+0.4); short n112 +0.42R (+46.8)
- by exit: friday_close n22 +0.89R (+19.5); stop n131 -1.01R (-132.7); target n43 +3.63R (+156.3); time n5 +0.83R (+4.2)

**Holm-adjusted p (day-clustered t, 8 strategies):** orderflow 0.0096, pullback 0.0712, h1_fvg 0.1941, fib618 1.0, gf 1.0, bennett 1.0, enigma 1.0, imbalance 1.0
**PBO (CSCV over the 8 strategies' daily R):** {'splits': 70, 'blocks': 8, 'pbo': 0.5}

## Profile `legacy`
Cost sources: **spread**: estimated (raw-account guess, x4 at rollover); **commission**: estimated ($7/lot rt broker schedule); **slippage**: estimated (0.1 pip); **swap**: unknown (not charged); **gaps**: observed (bar opens)

### fib618
- trades 91, win 37%, **expectancy +0.345R net** (before commission+swap +0.390R; commission 0.045R; swap 0.0R, unknown-swap trades 0), total +31.4R, PF 1.51
- trade t 1.44, day-clustered t 1.47, daily Sharpe 2.81, Sortino 6.21, lag-1 autocorr 0.026, effective days 65.4 of 69
- PSR(>0) 0.955, **DSR 0.001** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-3.1, 67.4], P(total<=0) 0.065, max DD median 10.4R / p95 20.9R
- drawdown: max 15.37R, peak->trough 25.1 d, recovery not recovered d, longest under water 32.4 d; months positive 2/4
- by symbol: EURUSD n19 +0.34R (+6.4); GBPUSD n23 +1.07R (+24.7); USDCAD n16 -0.17R (-2.7); USDCHF n9 +0.28R (+2.5); USDJPY n10 +0.27R (+2.7); XAUUSD n14 -0.16R (-2.3)
- by session: Asia 00-07 n5 -1.04R (-5.2); London 07-12 n17 +1.31R (+22.3); London/NY 12-16 n46 +0.12R (+5.8); NY 16-21 n16 +0.50R (+8.0); rollover 21-24 n7 +0.07R (+0.5)
- by month frozen walk forward: 2026-08 n19 +0.53R (+10.0); 2026-09 n33 +0.77R (+25.5); 2026-10 n37 -0.08R (-3.0); 2026-11 n2 -0.56R (-1.1)
- by side: long n41 -0.01R (-0.2); short n50 +0.63R (+31.6)
- by exit: stop n56 -1.10R (-61.7); target n26 +3.27R (+84.9); time n9 +0.90R (+8.1)

### imbalance
- trades 80, win 19%, **expectancy -0.012R net** (before commission+swap +0.043R; commission 0.055R; swap 0.0R, unknown-swap trades 0), total -1.0R, PF 0.99
- trade t -0.05, day-clustered t -0.04, daily Sharpe -0.08, Sortino -0.17, lag-1 autocorr 0.022, effective days 63.1 of 66
- PSR(>0) 0.482, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-36.5, 37.1], P(total<=0) 0.531, max DD median 22.8R / p95 45.1R
- drawdown: max 27.81R, peak->trough 47.8 d, recovery not recovered d, longest under water 60.3 d; months positive 1/4
- by symbol: EURUSD n15 -0.14R (-2.1); GBPUSD n11 -0.18R (-2.0); USDCAD n6 -0.37R (-2.2); USDCHF n2 -1.06R (-2.1); USDJPY n21 +0.05R (+1.1); XAUUSD n25 +0.26R (+6.4)
- by session: Asia 00-07 n10 +0.00R (+0.0); London 07-12 n15 +0.85R (+12.7); London/NY 12-16 n34 +0.25R (+8.4); NY 16-21 n13 -1.06R (-13.7); rollover 21-24 n8 -1.05R (-8.4)
- by month frozen walk forward: 2026-08 n12 +0.93R (+11.2); 2026-09 n34 -0.21R (-7.0); 2026-10 n32 -0.10R (-3.0); 2026-11 n2 -1.06R (-2.1)
- by side: long n35 -0.09R (-3.1); short n45 +0.05R (+2.1)
- by exit: stop n65 -1.06R (-69.2); target n14 +4.63R (+64.8); time n1 +3.39R (+3.4)

### enigma
- trades 24, win 29%, **expectancy +0.321R net** (before commission+swap +0.379R; commission 0.057R; swap 0.0R, unknown-swap trades 0), total +7.7R, PF 1.44
- trade t 0.6, day-clustered t 0.57, daily Sharpe 1.11, Sortino 3.33, lag-1 autocorr -0.085, effective days 79.5 of 67
- PSR(>0) 0.754, **DSR 0.035** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-11.0, 30.1], P(total<=0) 0.283, max DD median 7.8R / p95 15.8R
- drawdown: max 8.45R, peak->trough 39.6 d, recovery not recovered d, longest under water 53.4 d; months positive 2/3
- by symbol: EURUSD n6 +0.00R (+0.0); GBPUSD n4 +2.58R (+10.3); USDCAD n5 +1.37R (+6.8); USDCHF n5 -1.06R (-5.3); USDJPY n2 -1.08R (-2.2); XAUUSD n2 -1.01R (-2.0)
- by session: Asia 00-07 n6 -0.51R (-3.1); London 07-12 n6 +1.31R (+7.9); London/NY 12-16 n7 +0.07R (+0.5); NY 16-21 n3 -0.11R (-0.3); rollover 21-24 n2 +1.36R (+2.7)
- by month frozen walk forward: 2026-08 n9 +0.60R (+5.4); 2026-09 n8 +0.34R (+2.7); 2026-10 n7 -0.06R (-0.4)
- by side: long n11 -0.55R (-6.0); short n13 +1.06R (+13.7)
- by exit: stop n16 -1.06R (-16.9); target n6 +3.98R (+23.9); time n2 +0.35R (+0.7)

### h1_fvg
- trades 41, win 20%, **expectancy -0.441R net** (before commission+swap -0.391R; commission 0.051R; swap 0.0R, unknown-swap trades 0), total -18.1R, PF 0.48
- trade t -1.96, day-clustered t -2.04, daily Sharpe -3.93, Sortino -5.39, lag-1 autocorr -0.019, effective days 70.6 of 68
- PSR(>0) 0.087, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-32.7, -3.1], P(total<=0) 0.975, max DD median 20.9R / p95 33.9R
- drawdown: max 24.57R, peak->trough 80.0 d, recovery not recovered d, longest under water 80.5 d; months positive 1/4
- by symbol: EURUSD n5 -1.07R (-5.4); GBPUSD n8 -0.63R (-5.1); USDCAD n3 -0.07R (-0.2); USDCHF n9 -1.06R (-9.5); USDJPY n9 +0.31R (+2.8); XAUUSD n7 -0.11R (-0.7)
- by session: Asia 00-07 n13 -0.56R (-7.2); London 07-12 n9 +0.01R (+0.1); London/NY 12-16 n15 -0.46R (-6.8); NY 16-21 n2 -1.06R (-2.1); rollover 21-24 n2 -1.04R (-2.1)
- by month frozen walk forward: 2026-08 n9 +0.07R (+0.6); 2026-09 n13 -0.71R (-9.3); 2026-10 n16 -0.49R (-7.9); 2026-11 n3 -0.50R (-1.5)
- by side: long n19 -0.56R (-10.7); short n22 -0.34R (-7.4)
- by exit: stop n33 -1.06R (-35.0); target n6 +2.66R (+16.0); time n2 +0.46R (+0.9)

### bennett
- trades 5, win 60%, **expectancy +1.198R net** (before commission+swap +1.232R; commission 0.034R; swap 0.0R, unknown-swap trades 0), total +6.0R, PF 3.9
- trade t 1.19, day-clustered t 1.02, daily Sharpe 1.98, Sortino 8.02, lag-1 autocorr -0.016, effective days 68.2 of 66
- PSR(>0) 0.868, **DSR 0.575** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-2.1, 15.8], P(total<=0) 0.143, max DD median 1.1R / p95 3.2R
- drawdown: max 2.06R, peak->trough 62.1 d, recovery 3.0 d, longest under water 65.1 d; months positive 2/2
- by symbol: EURUSD n1 +3.69R (+3.7); USDCHF n2 +0.06R (+0.1); XAUUSD n2 +1.09R (+2.2)
- by session: London 07-12 n4 +0.70R (+2.8); London/NY 12-16 n1 +3.19R (+3.2)
- by month frozen walk forward: 2026-08 n1 +3.69R (+3.7); 2026-10 n4 +0.58R (+2.3)
- by side: long n1 +3.69R (+3.7); short n4 +0.58R (+2.3)
- by exit: stop n2 -1.03R (-2.1); target n2 +3.44R (+6.9); time n1 +1.18R (+1.2)

### pullback
- trades 1045, win 30%, **expectancy -0.154R net** (before commission+swap -0.102R; commission 0.052R; swap 0.0R, unknown-swap trades 0), total -161.2R, PF 0.79
- trade t -3.57, day-clustered t -3.17, daily Sharpe -5.78, Sortino -6.07, lag-1 autocorr -0.061, effective days 85.9 of 76
- PSR(>0) 0.0, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-238.8, -88.4], P(total<=0) 1.0, max DD median 166.1R / p95 244.0R
- drawdown: max 166.45R, peak->trough 92.0 d, recovery not recovered d, longest under water 92.0 d; months positive 0/4
- by symbol: EURUSD n130 -0.30R (-39.2); GBPUSD n94 -0.31R (-29.2); USDCAD n54 +0.02R (+0.9); USDCHF n48 -0.02R (-1.0); USDJPY n306 -0.19R (-56.8); XAUUSD n413 -0.09R (-35.9)
- by session: Asia 00-07 n230 -0.17R (-39.4); London 07-12 n216 -0.18R (-39.4); London/NY 12-16 n373 -0.11R (-40.0); NY 16-21 n174 -0.17R (-28.8); rollover 21-24 n52 -0.26R (-13.6)
- by month frozen walk forward: 2026-08 n259 -0.17R (-43.2); 2026-09 n347 -0.10R (-33.5); 2026-10 n421 -0.19R (-78.7); 2026-11 n18 -0.32R (-5.7)
- by side: long n520 -0.19R (-98.8); short n525 -0.12R (-62.3)
- by exit: stop n732 -1.06R (-778.7); target n310 +1.99R (+616.3); time n3 +0.42R (+1.3)

### orderflow
- trades 11, win 0%, **expectancy -1.027R net** (before commission+swap -1.003R; commission 0.025R; swap 0.0R, unknown-swap trades 0), total -11.3R, PF 0.0
- trade t -93.19, day-clustered t -3.25, daily Sharpe -6.35, Sortino -5.94, lag-1 autocorr -0.165, effective days 92.2 of 66
- PSR(>0) 0.0, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-17.3, -6.2], P(total<=0) 1.0, max DD median 11.3R / p95 17.3R
- drawdown: max 11.3R, peak->trough 83.9 d, recovery not recovered d, longest under water 83.9 d; months positive 0/3
- by symbol: USDCAD n1 -1.10R (-1.1); USDJPY n1 -1.10R (-1.1); XAUUSD n9 -1.01R (-9.1)
- by session: Asia 00-07 n7 -1.02R (-7.2); London 07-12 n1 -1.02R (-1.0); NY 16-21 n3 -1.04R (-3.1)
- by month frozen walk forward: 2026-08 n6 -1.02R (-6.2); 2026-09 n2 -1.01R (-2.0); 2026-10 n3 -1.04R (-3.1)
- by side: long n3 -1.04R (-3.1); short n8 -1.02R (-8.2)
- by exit: stop n11 -1.03R (-11.3)

### gf
- trades 2, win 50%, **expectancy +0.247R net** (before commission+swap +0.253R; commission 0.006R; swap 0.0R, unknown-swap trades 0), total +0.5R, PF 1.84
- trade t 0.3, day-clustered t 0.4, daily Sharpe 0.77, Sortino 1.63, lag-1 autocorr -0.006, effective days 67.8 of 67
- PSR(>0) nan, **DSR nan** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-0.6, 2.2], P(total<=0) 0.449, max DD median 0.6R / p95 1.2R
- drawdown: max 0.59R, peak->trough 3.7 d, recovery not recovered d, longest under water 3.7 d; months positive 1/1
- by symbol: USDJPY n1 -0.59R (-0.6); XAUUSD n1 +1.08R (+1.1)
- by session: Asia 00-07 n1 -0.59R (-0.6); London 07-12 n1 +1.08R (+1.1)
- by month frozen walk forward: 2026-10 n2 +0.25R (+0.5)
- by side: short n2 +0.25R (+0.5)
- by exit: target n1 +1.08R (+1.1); time n1 -0.59R (-0.6)

### PORTFOLIO all 8
- trades 1299, win 29%, **expectancy -0.112R net** (before commission+swap -0.061R; commission 0.051R; swap 0.0R, unknown-swap trades 0), total -146.0R, PF 0.85
- trade t -2.55, day-clustered t -1.99, daily Sharpe -3.6, Sortino -4.49, lag-1 autocorr -0.033, effective days 82.3 of 77
- PSR(>0) 0.009, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-256.2, -34.5], P(total<=0) 0.984, max DD median 166.6R / p95 266.7R
- drawdown: max 164.38R, peak->trough 52.9 d, recovery not recovered d, longest under water 52.9 d; months positive 0/4
- by symbol: EURUSD n176 -0.21R (-36.7); GBPUSD n140 -0.01R (-1.2); USDCAD n85 +0.02R (+1.6); USDCHF n75 -0.20R (-15.3); USDJPY n350 -0.15R (-54.0); XAUUSD n473 -0.09R (-40.4)
- by session: Asia 00-07 n272 -0.23R (-62.5); London 07-12 n269 +0.02R (+6.5); London/NY 12-16 n476 -0.06R (-29.0); NY 16-21 n211 -0.19R (-40.1); rollover 21-24 n71 -0.29R (-20.8)
- by month frozen walk forward: 2026-08 n315 -0.06R (-18.5); 2026-09 n437 -0.05R (-23.6); 2026-10 n522 -0.18R (-93.4); 2026-11 n25 -0.42R (-10.5)
- by side: long n630 -0.19R (-118.2); short n669 -0.04R (-27.7)
- by exit: stop n915 -1.06R (-974.8); target n365 +2.23R (+813.9); time n19 +0.79R (+15.0)

### PORTFOLIO live set
- trades 200, win 30%, **expectancy +0.221R net** (before commission+swap +0.271R; commission 0.051R; swap 0.0R, unknown-swap trades 0), total +44.1R, PF 1.29
- trade t 1.31, day-clustered t 1.13, daily Sharpe 2.14, Sortino 4.64, lag-1 autocorr 0.002, effective days 69.7 of 70
- PSR(>0) 0.928, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-14.1, 102.4], P(total<=0) 0.109, max DD median 23.2R / p95 46.4R
- drawdown: max 29.01R, peak->trough 40.9 d, recovery not recovered d, longest under water 52.5 d; months positive 2/4
- by symbol: EURUSD n41 +0.19R (+7.9); GBPUSD n38 +0.87R (+33.0); USDCAD n27 +0.07R (+2.0); USDCHF n18 -0.27R (-4.8); USDJPY n33 +0.05R (+1.7); XAUUSD n43 +0.10R (+4.3)
- by session: Asia 00-07 n21 -0.39R (-8.2); London 07-12 n42 +1.09R (+45.7); London/NY 12-16 n88 +0.20R (+17.8); NY 16-21 n32 -0.19R (-6.0); rollover 21-24 n17 -0.31R (-5.2)
- by month frozen walk forward: 2026-08 n41 +0.74R (+30.3); 2026-09 n75 +0.28R (+21.2); 2026-10 n80 -0.05R (-4.1); 2026-11 n4 -0.81R (-3.2)
- by side: long n88 -0.06R (-5.6); short n112 +0.44R (+49.7)
- by exit: stop n139 -1.08R (-149.8); target n48 +3.76R (+180.6); time n13 +1.03R (+13.4)

**Holm-adjusted p (day-clustered t, 8 strategies):** orderflow 0.0092, pullback 0.0107, h1_fvg 0.2481, fib618 0.7078, bennett 1.0, enigma 1.0, gf 1.0, imbalance 1.0
**PBO (CSCV over the 8 strategies' daily R):** {'splits': 70, 'blocks': 8, 'pbo': 0.171}

## Profile `observed_weekends_held`
Cost sources: **spread**: observed (5 samples, one London/NY session 2026-11-03) applied to all hours = estimated off-hours; x4 at rollover = estimated; **commission**: observed ($0 on 150 days of demo deals); **slippage**: estimated (0.1 pip); **swap**: observed per lot-night (150 days of deals), applied to past dates = estimated; pairs/sides with no overnight deals = unknown (0); **gaps**: observed (bar opens); **friday_close**: off (weekends held)

### fib618
- trades 91, win 36%, **expectancy +0.287R net** (before commission+swap +0.301R; commission 0.000R; swap -0.014R, unknown-swap trades 6), total +26.1R, PF 1.43
- trade t 1.23, day-clustered t 1.21, daily Sharpe 2.3, Sortino 5.13, lag-1 autocorr -0.03, effective days 74.3 of 70
- PSR(>0) 0.924, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-7.6, 62.2], P(total<=0) 0.103, max DD median 11.3R / p95 23.1R
- drawdown: max 16.11R, peak->trough 25.1 d, recovery not recovered d, longest under water 32.4 d; months positive 2/4
- by symbol: EURUSD n19 +0.28R (+5.4); GBPUSD n23 +1.02R (+23.6); USDCAD n16 -0.13R (-2.1); USDCHF n9 +0.28R (+2.5); USDJPY n10 -0.11R (-1.1); XAUUSD n14 -0.15R (-2.1)
- by session: Asia 00-07 n5 -1.00R (-5.0); London 07-12 n17 +1.32R (+22.4); London/NY 12-16 n46 +0.11R (+5.2); NY 16-21 n16 +0.24R (+3.8); rollover 21-24 n7 -0.06R (-0.4)
- by month frozen walk forward: 2026-08 n19 +0.26R (+4.9); 2026-09 n33 +0.79R (+26.1); 2026-10 n37 -0.10R (-3.8); 2026-11 n2 -0.55R (-1.1)
- by side: long n41 -0.04R (-1.5); short n50 +0.55R (+27.6)
- by exit: stop n57 -1.07R (-61.1); target n24 +3.22R (+77.2); time n10 +1.00R (+10.0)

### imbalance
- trades 80, win 19%, **expectancy +0.026R net** (before commission+swap +0.031R; commission 0.000R; swap -0.005R, unknown-swap trades 4), total +2.1R, PF 1.03
- trade t 0.1, day-clustered t 0.09, daily Sharpe 0.18, Sortino 0.4, lag-1 autocorr 0.046, effective days 60.2 of 66
- PSR(>0) 0.54, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-32.5, 38.6], P(total<=0) 0.478, max DD median 20.8R / p95 41.0R
- drawdown: max 25.41R, peak->trough 47.8 d, recovery not recovered d, longest under water 60.3 d; months positive 1/4
- by symbol: EURUSD n15 -0.11R (-1.6); GBPUSD n11 -0.17R (-1.8); USDCAD n6 -0.26R (-1.6); USDCHF n2 -1.01R (-2.0); USDJPY n21 +0.13R (+2.7); XAUUSD n25 +0.26R (+6.5)
- by session: Asia 00-07 n10 +0.02R (+0.2); London 07-12 n15 +0.89R (+13.4); London/NY 12-16 n34 +0.28R (+9.6); NY 16-21 n13 -1.01R (-13.1); rollover 21-24 n8 -1.00R (-8.0)
- by month frozen walk forward: 2026-08 n12 +0.93R (+11.1); 2026-09 n34 -0.16R (-5.4); 2026-10 n32 -0.05R (-1.6); 2026-11 n2 -1.01R (-2.0)
- by side: long n35 -0.08R (-2.8); short n45 +0.11R (+4.9)
- by exit: stop n65 -1.01R (-65.5); target n14 +4.59R (+64.3); time n1 +3.36R (+3.4)

### enigma
- trades 24, win 29%, **expectancy +0.352R net** (before commission+swap +0.368R; commission 0.000R; swap -0.016R, unknown-swap trades 1), total +8.5R, PF 1.51
- trade t 0.66, day-clustered t 0.63, daily Sharpe 1.23, Sortino 3.8, lag-1 autocorr -0.086, effective days 79.5 of 67
- PSR(>0) 0.781, **DSR 0.039** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-10.2, 30.6], P(total<=0) 0.257, max DD median 7.5R / p95 15.0R
- drawdown: max 8.08R, peak->trough 39.6 d, recovery not recovered d, longest under water 53.4 d; months positive 2/3
- by symbol: EURUSD n6 +0.06R (+0.3); GBPUSD n4 +2.55R (+10.2); USDCAD n5 +1.39R (+6.9); USDCHF n5 -1.00R (-5.0); USDJPY n2 -1.01R (-2.0); XAUUSD n2 -1.00R (-2.0)
- by session: Asia 00-07 n6 -0.46R (-2.8); London 07-12 n6 +1.35R (+8.1); London/NY 12-16 n7 +0.09R (+0.6); NY 16-21 n3 -0.13R (-0.4); rollover 21-24 n2 +1.45R (+2.9)
- by month frozen walk forward: 2026-08 n9 +0.63R (+5.7); 2026-09 n8 +0.37R (+3.0); 2026-10 n7 -0.03R (-0.2)
- by side: long n11 -0.51R (-5.7); short n13 +1.09R (+14.1)
- by exit: stop n16 -1.01R (-16.1); target n6 +3.99R (+24.0); time n2 +0.32R (+0.6)

### h1_fvg
- trades 41, win 20%, **expectancy -0.405R net** (before commission+swap -0.399R; commission 0.000R; swap -0.006R, unknown-swap trades 3), total -16.6R, PF 0.51
- trade t -1.81, day-clustered t -1.91, daily Sharpe -3.67, Sortino -5.18, lag-1 autocorr -0.024, effective days 71.4 of 68
- PSR(>0) 0.102, **DSR 0.001** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-30.7, -1.9], P(total<=0) 0.969, max DD median 19.4R / p95 31.9R
- drawdown: max 23.18R, peak->trough 80.0 d, recovery not recovered d, longest under water 80.5 d; months positive 1/4
- by symbol: EURUSD n5 -1.05R (-5.3); GBPUSD n8 -0.61R (-4.9); USDCAD n3 -0.04R (-0.1); USDCHF n9 -1.01R (-9.1); USDJPY n9 +0.39R (+3.5); XAUUSD n7 -0.10R (-0.7)
- by session: Asia 00-07 n13 -0.51R (-6.6); London 07-12 n9 +0.06R (+0.6); London/NY 12-16 n15 -0.43R (-6.5); NY 16-21 n2 -1.03R (-2.1); rollover 21-24 n2 -1.00R (-2.0)
- by month frozen walk forward: 2026-08 n9 +0.10R (+0.9); 2026-09 n13 -0.68R (-8.8); 2026-10 n16 -0.46R (-7.3); 2026-11 n3 -0.47R (-1.4)
- by side: long n19 -0.54R (-10.3); short n22 -0.29R (-6.3)
- by exit: stop n33 -1.02R (-33.5); target n6 +2.66R (+16.0); time n2 +0.48R (+1.0)

### bennett
- trades 5, win 60%, **expectancy +1.069R net** (before commission+swap +1.160R; commission 0.000R; swap -0.09R, unknown-swap trades 0), total +5.3R, PF 3.66
- trade t 1.11, day-clustered t 0.99, daily Sharpe 1.93, Sortino 7.35, lag-1 autocorr -0.015, effective days 68.0 of 66
- PSR(>0) 0.859, **DSR 0.547** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-2.0, 14.3], P(total<=0) 0.158, max DD median 1.0R / p95 3.0R
- drawdown: max 2.01R, peak->trough 62.1 d, recovery 3.0 d, longest under water 65.1 d; months positive 2/2
- by symbol: EURUSD n1 +3.36R (+3.4); USDCHF n2 -0.10R (-0.2); XAUUSD n2 +1.10R (+2.2)
- by session: London 07-12 n4 +0.54R (+2.1); London/NY 12-16 n1 +3.20R (+3.2)
- by month frozen walk forward: 2026-08 n1 +3.36R (+3.4); 2026-10 n4 +0.50R (+2.0)
- by side: long n1 +3.36R (+3.4); short n4 +0.50R (+2.0)
- by exit: stop n2 -1.00R (-2.0); target n2 +3.28R (+6.6); time n1 +0.80R (+0.8)

### pullback
- trades 1039, win 29%, **expectancy -0.142R net** (before commission+swap -0.141R; commission 0.000R; swap -0.001R, unknown-swap trades 44), total -147.7R, PF 0.8
- trade t -3.37, day-clustered t -3.14, daily Sharpe -5.72, Sortino -6.14, lag-1 autocorr -0.088, effective days 90.7 of 76
- PSR(>0) 0.001, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-218.8, -81.1], P(total<=0) 1.0, max DD median 152.0R / p95 223.1R
- drawdown: max 153.44R, peak->trough 87.6 d, recovery not recovered d, longest under water 92.0 d; months positive 0/4
- by symbol: EURUSD n130 -0.34R (-44.5); GBPUSD n93 -0.28R (-25.7); USDCAD n54 +0.03R (+1.3); USDCHF n49 -0.02R (-1.1); USDJPY n301 -0.14R (-43.2); XAUUSD n412 -0.08R (-34.4)
- by session: Asia 00-07 n226 -0.16R (-35.7); London 07-12 n213 -0.14R (-30.4); London/NY 12-16 n377 -0.12R (-44.7); NY 16-21 n175 -0.16R (-28.5); rollover 21-24 n48 -0.17R (-8.3)
- by month frozen walk forward: 2026-08 n255 -0.15R (-37.1); 2026-09 n351 -0.10R (-36.0); 2026-10 n414 -0.16R (-68.0); 2026-11 n19 -0.35R (-6.6)
- by side: long n520 -0.17R (-89.3); short n519 -0.11R (-58.4)
- by exit: stop n735 -1.01R (-743.5); target n301 +1.98R (+594.6); time n3 +0.40R (+1.2)

### orderflow
- trades 11, win 0%, **expectancy -1.006R net** (before commission+swap -1.003R; commission 0.000R; swap -0.003R, unknown-swap trades 0), total -11.1R, PF 0.0
- trade t -87.73, day-clustered t -3.24, daily Sharpe -6.32, Sortino -5.91, lag-1 autocorr -0.164, effective days 91.9 of 66
- PSR(>0) 0.015, **DSR 0.014** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-16.9, -6.0], P(total<=0) 1.0, max DD median 11.0R / p95 16.9R
- drawdown: max 11.07R, peak->trough 83.9 d, recovery not recovered d, longest under water 83.9 d; months positive 0/3
- by symbol: USDCAD n1 -1.11R (-1.1); USDJPY n1 -0.94R (-0.9); XAUUSD n9 -1.00R (-9.0)
- by session: Asia 00-07 n7 -0.99R (-7.0); London 07-12 n1 -1.00R (-1.0); NY 16-21 n3 -1.04R (-3.1)
- by month frozen walk forward: 2026-08 n6 -1.02R (-6.1); 2026-09 n2 -1.00R (-2.0); 2026-10 n3 -0.98R (-2.9)
- by side: long n3 -0.98R (-2.9); short n8 -1.01R (-8.1)
- by exit: stop n11 -1.01R (-11.1)

### gf
- trades 2, win 50%, **expectancy +0.254R net** (before commission+swap +0.250R; commission 0.000R; swap 0.004R, unknown-swap trades 1), total +0.5R, PF 1.87
- trade t 0.3, day-clustered t 0.41, daily Sharpe 0.79, Sortino 1.68, lag-1 autocorr -0.006, effective days 67.8 of 67
- PSR(>0) nan, **DSR nan** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-0.6, 2.2], P(total<=0) 0.449, max DD median 0.6R / p95 1.2R
- drawdown: max 0.58R, peak->trough 3.7 d, recovery not recovered d, longest under water 3.7 d; months positive 1/1
- by symbol: USDJPY n1 -0.58R (-0.6); XAUUSD n1 +1.09R (+1.1)
- by session: Asia 00-07 n1 -0.58R (-0.6); London 07-12 n1 +1.09R (+1.1)
- by month frozen walk forward: 2026-10 n2 +0.25R (+0.5)
- by side: short n2 +0.25R (+0.5)
- by exit: target n1 +1.09R (+1.1); time n1 -0.58R (-0.6)

### PORTFOLIO all 8
- trades 1293, win 29%, **expectancy -0.103R net** (before commission+swap -0.100R; commission 0.000R; swap -0.003R, unknown-swap trades 59), total -132.8R, PF 0.86
- trade t -2.38, day-clustered t -1.88, daily Sharpe -3.38, Sortino -4.36, lag-1 autocorr -0.051, effective days 86.4 of 78
- PSR(>0) 0.013, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-237.2, -28.0], P(total<=0) 0.982, max DD median 152.6R / p95 246.6R
- drawdown: max 155.24R, peak->trough 41.0 d, recovery not recovered d, longest under water 52.5 d; months positive 0/4
- by symbol: EURUSD n176 -0.24R (-42.3); GBPUSD n139 +0.01R (+1.3); USDCAD n85 +0.04R (+3.3); USDCHF n76 -0.20R (-15.0); USDJPY n345 -0.12R (-41.7); XAUUSD n472 -0.08R (-38.5)
- by session: Asia 00-07 n268 -0.21R (-57.3); London 07-12 n266 +0.06R (+16.3); London/NY 12-16 n480 -0.07R (-32.5); NY 16-21 n212 -0.20R (-43.3); rollover 21-24 n67 -0.24R (-15.9)
- by month frozen walk forward: 2026-08 n311 -0.06R (-17.3); 2026-09 n441 -0.05R (-23.1); 2026-10 n515 -0.16R (-81.3); 2026-11 n26 -0.43R (-11.1)
- by side: long n630 -0.17R (-109.1); short n663 -0.04R (-23.7)
- by exit: stop n919 -1.01R (-932.8); target n354 +2.21R (+783.7); time n20 +0.82R (+16.3)

### PORTFOLIO live set
- trades 200, win 29%, **expectancy +0.210R net** (before commission+swap +0.223R; commission 0.000R; swap -0.012R, unknown-swap trades 11), total +42.0R, PF 1.29
- trade t 1.28, day-clustered t 1.07, daily Sharpe 2.01, Sortino 4.36, lag-1 autocorr -0.021, effective days 74.1 of 71
- PSR(>0) 0.922, **DSR 0.0** (SR* 0.444 per trade from 350 registered trials)
- block bootstrap: total R 90% CI [-14.0, 97.7], P(total<=0) 0.104, max DD median 22.9R / p95 46.1R
- drawdown: max 26.55R, peak->trough 40.9 d, recovery not recovered d, longest under water 52.5 d; months positive 2/4
- by symbol: EURUSD n41 +0.18R (+7.5); GBPUSD n38 +0.84R (+31.9); USDCAD n27 +0.12R (+3.2); USDCHF n18 -0.26R (-4.7); USDJPY n33 -0.01R (-0.4); XAUUSD n43 +0.11R (+4.6)
- by session: Asia 00-07 n21 -0.36R (-7.5); London 07-12 n42 +1.10R (+46.0); London/NY 12-16 n88 +0.21R (+18.7); NY 16-21 n32 -0.30R (-9.6); rollover 21-24 n17 -0.33R (-5.6)
- by month frozen walk forward: 2026-08 n41 +0.61R (+25.0); 2026-09 n75 +0.32R (+23.7); 2026-10 n80 -0.04R (-3.6); 2026-11 n4 -0.78R (-3.1)
- by side: long n88 -0.07R (-6.6); short n112 +0.43R (+48.6)
- by exit: stop n140 -1.03R (-144.7); target n46 +3.74R (+172.0); time n14 +1.05R (+14.8)

**Holm-adjusted p (day-clustered t, 8 strategies):** orderflow 0.0096, pullback 0.0118, h1_fvg 0.3368, fib618 1.0, bennett 1.0, enigma 1.0, gf 1.0, imbalance 1.0
**PBO (CSCV over the 8 strategies' daily R):** {'splits': 70, 'blocks': 8, 'pbo': 0.257}
