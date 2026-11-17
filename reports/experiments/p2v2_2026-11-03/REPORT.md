# P2 v2: 8 strategies, live rules, common dataset

Protocol docs/audit/p2_protocol_v2_2026-11-03.md. Period 2026-08-03 .. 2026-11-03 (exclusive), EURUSD GBPUSD USDJPY USDCHF USDCAD XAUUSD, costs `observed_account`, code {'commit': '6b1218c', 'dirty': True, 'bensim_sha256': 'c9373ba76cdb1166'}, DSR trials N = 357.

## Strategies (each run independently)
| strategy   | friday rule     |   trades |   gross R (pre comm+swap) |   net R |   swap R |   total R |   PF |   win |   day t |   Sharpe ann |   eff. days |     PSR |     DSR |   Holm p | boot 90% total R   |   P(total<=0) |   max DD R | months +   | recommendation   |
|:-----------|:----------------|---------:|--------------------------:|--------:|---------:|----------:|-----:|------:|--------:|-------------:|------------:|--------:|--------:|---------:|:-------------------|--------------:|-----------:|:-----------|:-----------------|
| fib618     | Fri 20:00 close |       92 |                     0.3   |   0.288 |   -0.012 |      26.5 | 1.47 | 0.359 |    1.28 |         2.48 |        70.6 |   0.938 |   0     |   1      | [-2.1, 58.4]       |         0.066 |      10.02 | 3/4        | investigate      |
| imbalance  | weekends held   |       80 |                     0.031 |   0.026 |   -0.005 |       2.1 | 1.03 | 0.188 |    0.09 |         0.18 |        60.2 |   0.54  |   0     |   1      | [-32.5, 38.6]      |         0.478 |      25.41 | 1/4        | reject           |
| enigma     | weekends held   |       24 |                     0.368 |   0.352 |   -0.016 |       8.5 | 1.51 | 0.292 |    0.63 |         1.23 |        79.5 |   0.781 |   0.034 |   1      | [-10.2, 30.6]      |         0.257 |       8.08 | 2/3        | reject           |
| h1_fvg     | Fri 20:00 close |       41 |                    -0.457 |  -0.464 |   -0.007 |     -19   | 0.44 | 0.171 |   -2.14 |        -4.13 |        71.4 |   0.084 |   0.001 |   0.1941 | [-33.3, -4.2]      |         0.981 |      25.6  | 1/4        | reject           |
| bennett    | Fri 20:00 close |        5 |                     1.16  |   1.069 |   -0.09  |       5.3 | 3.66 | 0.6   |    0.99 |         1.93 |        68   |   0.859 |   0.538 |   1      | [-2.0, 14.3]       |         0.158 |       2.01 | 2/2        | investigate      |
| pullback   | Fri 20:00 close |     1065 |                    -0.116 |  -0.117 |   -0.001 |    -124.1 | 0.83 | 0.305 |   -2.57 |        -4.68 |        95.6 |   0.003 |   0     |   0.0712 | [-195.1, -55.4]    |         0.999 |     131.49 | 0/4        | reject           |
| orderflow  | Fri 20:00 close |       11 |                    -1.003 |  -1.006 |   -0.003 |     -11.1 | 0    | 0     |   -3.24 |        -6.32 |        91.9 |   0.015 |   0.014 |   0.0096 | [-16.9, -6.0]      |         1     |      11.07 | 0/3        | reject           |
| gf         | Fri 20:00 close |        2 |                     0.434 |   0.438 |    0.004 |       0.9 | 5.06 | 0.5   |    1    |         1.95 |        68.1 | nan     | nan     |   1      | [0.0, 2.6]         |         0.358 |       0.22 | 1/1        | reject           |

### Recommendation reasons and best/worst regime
- **fib618**: investigate: positive but not significant (DSR 0, Holm p 1.0, P(total<=0) 0.066); longer run: negative (25 pairs x 14y -0.048R; 0/15 years). Regimes: best by_session: London 07-12 (+1.32R, n17); worst by_session: London/NY 12-16 (+0.06R, n47)
- **imbalance**: reject: net +0.026R, PF 1.03, P(total<=0) 0.478; longer run: flat (5y +0.048R, t 0.96). Regimes: best by_session: London 07-12 (+0.89R, n15); worst by_session: NY 16-21 (-1.01R, n13)
- **enigma**: reject: net +0.352R, PF 1.51, P(total<=0) 0.257; longer run: flat (5y ~ break-even). Regimes: best by_trend_regime: range (+0.92R, n13); worst by_trend_regime: trend (-0.32R, n11)
- **h1_fvg**: reject: net -0.464R, PF 0.44, P(total<=0) 0.981; longer run: none on file. Regimes: best by_vol_regime: high vol (-0.38R, n11); worst by_session: Asia 00-07 (-0.51R, n13)
- **bennett**: investigate: positive but not significant (DSR 0.538, Holm p 1.0, P(total<=0) 0.158); longer run: negative (5y setup DB after costs). Regimes: n < 10 in every cell
- **pullback**: reject: net -0.117R, PF 0.83, P(total<=0) 0.999; longer run: negative (earlier 3m 955 tr -0.17R t -3.8). Regimes: best by_session: NY 16-21 (-0.07R, n172); worst by_session: rollover 21-24 (-0.17R, n72)
- **orderflow**: reject: net -1.006R, PF 0, P(total<=0) 1.0; longer run: negative/flat (earlier 3m variants 0 to -0.22R). Regimes: best by_vol_regime: low vol (-1.01R, n10); worst by_vol_regime: low vol (-1.01R, n10)
- **gf**: reject: net +0.438R, PF 5.06, P(total<=0) 0.358; longer run: none on file. Regimes: n < 10 in every cell

**PBO (CSCV, 8 strategies, daily net R):** {'splits': 70, 'blocks': 8, 'pbo': 0.414}

## Portfolios (1R per trade)
| portfolio                        |   trades |   gross R |   net R |   total R |   PF |   day t |   Sharpe ann |   PSR |   DSR |   P(total<=0) | boot 90% total R   |   max DD R | DD recovery d   |   max concurrent | max |net| currency R       |
|:---------------------------------|---------:|----------:|--------:|----------:|-----:|--------:|-------------:|------:|------:|--------------:|:-------------------|-----------:|:----------------|-----------------:|:---------------------------|
| (a) all 8, independent           |     1320 |    -0.081 |  -0.084 |    -110.8 | 0.88 |   -1.54 |        -2.79 | 0.029 |     0 |         0.96  | [-217.7, -6.9]     |     145.69 | not recovered   |               11 | USD 9, EUR 4, GBP 4, JPY 4 |
| (b) live set, independent        |      201 |     0.223 |   0.211 |      42.5 | 1.3  |    1.08 |         2.08 | 0.928 |     0 |         0.106 | [-13.3, 97.6]      |      25.71 | not recovered   |                7 | USD 5, CAD 2, EUR 2, GBP 2 |
| (c) live set, one trade per pair |      165 |     0.274 |   0.264 |      43.6 | 1.39 |    1.42 |         2.74 | 0.956 |     0 |         0.049 | [0.3, 86.1]        |      17.56 | 7.6             |                4 | USD 4, CAD 1, EUR 1, GBP 1 |

## Correlation of daily net R
|           |   fib618 |   imbalance |   enigma |   h1_fvg |   bennett |   pullback |   orderflow |    gf |
|:----------|---------:|------------:|---------:|---------:|----------:|-----------:|------------:|------:|
| fib618    |     1    |       -0.07 |     0.66 |     0.11 |      0.04 |      -0.05 |       -0.05 |  0.13 |
| imbalance |    -0.07 |        1    |    -0.01 |     0.28 |      0.01 |       0.35 |        0.11 | -0.09 |
| enigma    |     0.66 |       -0.01 |     1    |     0.23 |      0.33 |       0.13 |       -0.1  |  0.15 |
| h1_fvg    |     0.11 |        0.28 |     0.23 |     1    |      0.07 |       0.08 |       -0.18 |  0.03 |
| bennett   |     0.04 |        0.01 |     0.33 |     0.07 |      1    |       0.12 |       -0.14 | -0.01 |
| pullback  |    -0.05 |        0.35 |     0.13 |     0.08 |      0.12 |       1    |       -0.04 |  0.14 |
| orderflow |    -0.05 |        0.11 |    -0.1  |    -0.18 |     -0.14 |      -0.04 |        1    | -0.24 |
| gf        |     0.13 |       -0.09 |     0.15 |     0.03 |     -0.01 |       0.14 |       -0.24 |  1    |

## Sensitivities (not used to choose anything)
| strategy   |   all Friday close |   all weekends held |   legacy costs (weekends held, no swap, raw spread + $7) |
|:-----------|-------------------:|--------------------:|---------------------------------------------------------:|
| bennett    |              1.069 |               1.069 |                                                    1.198 |
| enigma     |              0.43  |               0.352 |                                                    0.321 |
| fib618     |              0.288 |               0.287 |                                                    0.345 |
| gf         |              0.438 |               0.254 |                                                    0.247 |
| h1_fvg     |             -0.464 |              -0.405 |                                                   -0.441 |
| imbalance  |              0.063 |               0.026 |                                                   -0.012 |
| orderflow  |             -1.006 |              -1.006 |                                                   -1.027 |
| pullback   |             -0.117 |              -0.142 |                                                   -0.154 |

## Per-strategy detail
### fib618
- trades 92, win 36%, **expectancy +0.288R net** (before commission+swap +0.300R; commission 0.000R; swap -0.012R, unknown-swap trades 4), total +26.5R, PF 1.47
- trade t 1.29, day-clustered t 1.28, daily Sharpe 2.48, Sortino 5.99, lag-1 autocorr -0.026, effective days 70.6 of 67
- PSR(>0) 0.938, **DSR 0.0** (SR* 0.454 per trade from 357 registered trials)
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
- trades 80, win 19%, **expectancy +0.026R net** (before commission+swap +0.031R; commission 0.000R; swap -0.005R, unknown-swap trades 4), total +2.1R, PF 1.03
- trade t 0.1, day-clustered t 0.09, daily Sharpe 0.18, Sortino 0.4, lag-1 autocorr 0.046, effective days 60.2 of 66
- PSR(>0) 0.54, **DSR 0.0** (SR* 0.454 per trade from 357 registered trials)
- block bootstrap: total R 90% CI [-32.5, 38.6], P(total<=0) 0.478, max DD median 20.8R / p95 41.0R
- drawdown: max 25.41R, peak->trough 47.8 d, recovery not recovered d, longest under water 60.3 d; months positive 1/4
- by symbol: EURUSD n15 -0.11R (-1.6); GBPUSD n11 -0.17R (-1.8); USDCAD n6 -0.26R (-1.6); USDCHF n2 -1.01R (-2.0); USDJPY n21 +0.13R (+2.7); XAUUSD n25 +0.26R (+6.5)
- by session: Asia 00-07 n10 +0.02R (+0.2); London 07-12 n15 +0.89R (+13.4); London/NY 12-16 n34 +0.28R (+9.6); NY 16-21 n13 -1.01R (-13.1); rollover 21-24 n8 -1.00R (-8.0)
- by trend regime: range n37 -0.01R (-0.2); trend n43 +0.05R (+2.3)
- by vol regime: high vol n32 -0.27R (-8.5); low vol n48 +0.22R (+10.7)
- by month frozen walk forward: 2026-08 n12 +0.93R (+11.1); 2026-09 n34 -0.16R (-5.4); 2026-10 n32 -0.05R (-1.6); 2026-11 n2 -1.01R (-2.0)
- by side: long n35 -0.08R (-2.8); short n45 +0.11R (+4.9)
- by exit: stop n65 -1.01R (-65.5); target n14 +4.59R (+64.3); time n1 +3.36R (+3.4)

### enigma
- trades 24, win 29%, **expectancy +0.352R net** (before commission+swap +0.368R; commission 0.000R; swap -0.016R, unknown-swap trades 1), total +8.5R, PF 1.51
- trade t 0.66, day-clustered t 0.63, daily Sharpe 1.23, Sortino 3.8, lag-1 autocorr -0.086, effective days 79.5 of 67
- PSR(>0) 0.781, **DSR 0.034** (SR* 0.454 per trade from 357 registered trials)
- block bootstrap: total R 90% CI [-10.2, 30.6], P(total<=0) 0.257, max DD median 7.5R / p95 15.0R
- drawdown: max 8.08R, peak->trough 39.6 d, recovery not recovered d, longest under water 53.4 d; months positive 2/3
- by symbol: EURUSD n6 +0.06R (+0.3); GBPUSD n4 +2.55R (+10.2); USDCAD n5 +1.39R (+6.9); USDCHF n5 -1.00R (-5.0); USDJPY n2 -1.01R (-2.0); XAUUSD n2 -1.00R (-2.0)
- by session: Asia 00-07 n6 -0.46R (-2.8); London 07-12 n6 +1.35R (+8.1); London/NY 12-16 n7 +0.09R (+0.6); NY 16-21 n3 -0.13R (-0.4); rollover 21-24 n2 +1.45R (+2.9)
- by trend regime: range n13 +0.92R (+12.0); trend n11 -0.32R (-3.6)
- by vol regime: high vol n8 -0.15R (-1.2); low vol n16 +0.60R (+9.6)
- by month frozen walk forward: 2026-08 n9 +0.63R (+5.7); 2026-09 n8 +0.37R (+3.0); 2026-10 n7 -0.03R (-0.2)
- by side: long n11 -0.51R (-5.7); short n13 +1.09R (+14.1)
- by exit: stop n16 -1.01R (-16.1); target n6 +3.99R (+24.0); time n2 +0.32R (+0.6)

### h1_fvg
- trades 41, win 17%, **expectancy -0.464R net** (before commission+swap -0.457R; commission 0.000R; swap -0.007R, unknown-swap trades 3), total -19.0R, PF 0.44
- trade t -2.14, day-clustered t -2.14, daily Sharpe -4.13, Sortino -5.6, lag-1 autocorr -0.025, effective days 71.4 of 68
- PSR(>0) 0.084, **DSR 0.001** (SR* 0.454 per trade from 357 registered trials)
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
- PSR(>0) 0.859, **DSR 0.538** (SR* 0.454 per trade from 357 registered trials)
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
- PSR(>0) 0.003, **DSR 0.0** (SR* 0.454 per trade from 357 registered trials)
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
- PSR(>0) 0.015, **DSR 0.014** (SR* 0.454 per trade from 357 registered trials)
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
- PSR(>0) nan, **DSR nan** (SR* 0.454 per trade from 357 registered trials)
- block bootstrap: total R 90% CI [0.0, 2.6], P(total<=0) 0.358, max DD median 0.0R / p95 0.0R
- drawdown: max 0.22R, peak->trough 0.5 d, recovery not recovered d, longest under water 0.5 d; months positive 1/1
- by symbol: USDJPY n1 -0.22R (-0.2); XAUUSD n1 +1.09R (+1.1)
- by session: Asia 00-07 n1 -0.22R (-0.2); London 07-12 n1 +1.09R (+1.1)
- by trend regime: range n2 +0.44R (+0.9)
- by vol regime: low vol n2 +0.44R (+0.9)
- by month frozen walk forward: 2026-10 n2 +0.44R (+0.9)
- by side: short n2 +0.44R (+0.9)
- by exit: friday_close n1 -0.22R (-0.2); target n1 +1.09R (+1.1)

## Portfolio detail
### (a) all 8, independent
- trades 1320, win 30%, **expectancy -0.084R net** (before commission+swap -0.081R; commission 0.000R; swap -0.003R, unknown-swap trades 44), total -110.8R, PF 0.88
- trade t -2.0, day-clustered t -1.54, daily Sharpe -2.79, Sortino -3.64, lag-1 autocorr -0.06, effective days 86.8 of 77
- PSR(>0) 0.029, **DSR 0.0** (SR* 0.454 per trade from 357 registered trials)
- block bootstrap: total R 90% CI [-217.7, -6.9], P(total<=0) 0.96, max DD median 134.5R / p95 230.3R
- drawdown: max 145.69R, peak->trough 41.0 d, recovery not recovered d, longest under water 52.5 d; months positive 0/4
- by symbol: EURUSD n182 -0.17R (-30.7); GBPUSD n140 +0.05R (+6.5); USDCAD n85 +0.06R (+5.4); USDCHF n76 -0.17R (-12.5); USDJPY n354 -0.12R (-44.1); XAUUSD n483 -0.07R (-35.4)
- by session: Asia 00-07 n272 -0.21R (-57.9); London 07-12 n267 +0.06R (+17.0); London/NY 12-16 n481 -0.06R (-26.5); NY 16-21 n209 -0.11R (-23.2); rollover 21-24 n91 -0.22R (-20.1)
- by trend regime: range n681 -0.04R (-29.4); trend n639 -0.13R (-81.4)
- by vol regime: high vol n486 -0.12R (-56.3); low vol n834 -0.07R (-54.5)
- by month frozen walk forward: 2026-08 n314 -0.04R (-12.4); 2026-09 n454 -0.05R (-23.9); 2026-10 n522 -0.11R (-59.4); 2026-11 n30 -0.51R (-15.2)
- by side: long n638 -0.14R (-88.4); short n682 -0.03R (-22.4)
- by exit: friday_close n55 +0.19R (+10.4); stop n902 -1.01R (-908.2); target n352 +2.21R (+778.2); time n11 +0.80R (+8.8)

### (b) live set, independent
- trades 201, win 29%, **expectancy +0.211R net** (before commission+swap +0.223R; commission 0.000R; swap -0.011R, unknown-swap trades 9), total +42.5R, PF 1.3
- trade t 1.31, day-clustered t 1.08, daily Sharpe 2.08, Sortino 4.5, lag-1 autocorr -0.017, effective days 70.3 of 68
- PSR(>0) 0.928, **DSR 0.0** (SR* 0.454 per trade from 357 registered trials)
- block bootstrap: total R 90% CI [-13.3, 97.6], P(total<=0) 0.106, max DD median 22.8R / p95 45.2R
- drawdown: max 25.71R, peak->trough 11.3 d, recovery not recovered d, longest under water 52.5 d; months positive 3/4
- by symbol: EURUSD n41 +0.32R (+12.9); GBPUSD n39 +0.78R (+30.2); USDCAD n27 +0.19R (+5.1); USDCHF n18 -0.28R (-5.0); USDJPY n33 -0.10R (-3.4); XAUUSD n43 +0.06R (+2.7)
- by session: Asia 00-07 n21 -0.36R (-7.5); London 07-12 n42 +1.10R (+46.0); London/NY 12-16 n89 +0.18R (+16.2); NY 16-21 n32 -0.21R (-6.6); rollover 21-24 n17 -0.33R (-5.6)
- by trend regime: range n100 +0.36R (+35.6); trend n101 +0.07R (+6.9)
- by vol regime: high vol n69 -0.10R (-7.1); low vol n132 +0.38R (+49.6)
- by month frozen walk forward: 2026-08 n41 +0.60R (+24.6); 2026-09 n75 +0.25R (+18.9); 2026-10 n81 +0.03R (+2.0); 2026-11 n4 -0.78R (-3.1)
- by side: long n88 -0.04R (-3.8); short n113 +0.41R (+46.3)
- by exit: friday_close n14 +0.42R (+5.9); stop n136 -1.01R (-137.8); target n44 +3.80R (+167.4); time n7 +1.00R (+7.0)

### (c) live set, one trade per pair
- trades 165, win 31%, **expectancy +0.264R net** (before commission+swap +0.274R; commission 0.000R; swap -0.01R, unknown-swap trades 8), total +43.6R, PF 1.39
- trade t 1.48, day-clustered t 1.42, daily Sharpe 2.74, Sortino 6.13, lag-1 autocorr -0.016, effective days 70.3 of 68
- PSR(>0) 0.956, **DSR 0.0** (SR* 0.454 per trade from 357 registered trials)
- block bootstrap: total R 90% CI [0.3, 86.1], P(total<=0) 0.049, max DD median 16.0R / p95 30.8R
- drawdown: max 17.56R, peak->trough 11.3 d, recovery 7.6 d, longest under water 18.9 d; months positive 3/4
- by symbol: EURUSD n34 +0.51R (+17.3); GBPUSD n31 +0.62R (+19.1); USDCAD n22 +0.07R (+1.4); USDCHF n15 -0.14R (-2.0); USDJPY n27 +0.10R (+2.6); XAUUSD n36 +0.14R (+5.1)
- by session: Asia 00-07 n15 -0.10R (-1.5); London 07-12 n39 +0.97R (+38.0); London/NY 12-16 n70 +0.21R (+15.0); NY 16-21 n27 -0.20R (-5.5); rollover 21-24 n14 -0.18R (-2.5)
- by trend regime: range n84 +0.36R (+29.9); trend n81 +0.17R (+13.7)
- by vol regime: high vol n54 +0.02R (+1.0); low vol n111 +0.38R (+42.6)
- by month frozen walk forward: 2026-08 n35 +0.44R (+15.5); 2026-09 n60 +0.31R (+18.5); 2026-10 n66 +0.19R (+12.7); 2026-11 n4 -0.78R (-3.1)
- by side: long n75 -0.02R (-1.8); short n90 +0.50R (+45.4)
- by exit: friday_close n14 +0.42R (+5.9); stop n107 -1.01R (-108.5); target n38 +3.75R (+142.5); time n6 +0.60R (+3.6)
