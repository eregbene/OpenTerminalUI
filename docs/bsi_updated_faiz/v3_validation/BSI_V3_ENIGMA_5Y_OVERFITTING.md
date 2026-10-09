# V3 enigma 5-year: overfitting check

Trials: 20 (entry model x symbol). Weeks: 257.

- Best trial: **limit_mid:EURUSD**, 375 trades, +0.0637R/trade, t=1.16
- PSR (best > 0, ignoring the search): 0.879
- Deflated Sharpe: 0.326 (bar = expected best-of-20 by luck: SR 0.0833 vs observed 0.0601; survives 95%: False)
- Benjamini-Hochberg @5% FDR: 0 of 20 trials significant
- PBO (CSCV, 16 splits): 0.635 over 12870 splits (>0.5 = picking the best backtest is worse than random)

| trial | n | mean R | t | p |
|---|---|---|---|---|
| limit_mid:EURUSD | 375 | +0.0637 | 1.16 | 0.122 |
| limit_mid:USDJPY | 545 | +0.0449 | 0.97 | 0.166 |
| market_confirm:EURUSD | 132 | +0.0378 | 0.44 | 0.330 |
| limit_mid:XAUUSD | 637 | +0.0360 | 0.84 | 0.200 |
| market_confirm:GBPJPY | 198 | +0.0296 | 0.42 | 0.338 |
| market_confirm:XAUUSD | 238 | +0.0278 | 0.39 | 0.350 |
| limit_mid:EURJPY | 556 | +0.0218 | 0.48 | 0.314 |
| limit_mid:GBPJPY | 529 | +0.0121 | 0.26 | 0.397 |
| market_confirm:USDJPY | 260 | +0.0064 | 0.09 | 0.463 |
| limit_mid:AUDUSD | 621 | -0.0226 | -0.54 | 0.704 |
| market_confirm:EURJPY | 221 | -0.0307 | -0.44 | 0.669 |
| market_confirm:NZDUSD | 261 | -0.0351 | -0.55 | 0.710 |
| limit_mid:GBPUSD | 580 | -0.0448 | -1.02 | 0.847 |
| limit_mid:USDCHF | 523 | -0.0461 | -1.02 | 0.846 |
| market_confirm:USDCHF | 220 | -0.0463 | -0.66 | 0.747 |
| market_confirm:USDCAD | 219 | -0.0504 | -0.69 | 0.756 |
| market_confirm:GBPUSD | 239 | -0.0483 | -0.73 | 0.767 |
| limit_mid:NZDUSD | 627 | -0.0706 | -1.70 | 0.955 |
| limit_mid:USDCAD | 558 | -0.0778 | -1.75 | 0.960 |
| market_confirm:AUDUSD | 253 | -0.0893 | -1.37 | 0.915 |
