# Night research: morning report (2026-11-07)

Rules: tests fixed before running, real costs, discovery 2012–18 then confirmation, corrected for multiple testing, and **no forced results**. Details: `NIGHT_LOG.md`. Nothing on DEMO was changed.

## Two genuine findings, both extending Flow ML (the one proven model)

### 1. The ATR-expansion filter: the strongest result
Flow ML trades taken while short-term volatility is expanding (ATR(6) / ATR(48) > 1.2 at the decision) earn far more.

| Flow ML, live 5 pairs (5-seed walk-forward 2016–26, no late hours) | trades | bp/trade | daily Sharpe | 2021–26 Sharpe | max DD | years + |
|---|---|---|---|---|---|---|
| as now | 6,138 | +1.44 | 0.72 | 0.21 | −1,977 bp | 8/11 |
| **ATR expanding only** | 2,028 | **+4.32** | **1.05** | **0.89** | **−773 bp** | **11/11** |

Why I believe it:
- **Out of sample:** the rule came from the round-4 audit of the live 5 pairs. On **20 pairs it never saw** it holds in all 3 independent runs: expanding +1.8 to +2.1 bp vs the rest +0.6 to +0.8 bp, one-sided p 0.012–0.058.
- **Not a knife-edge:** it improves smoothly from a threshold of 1.0 up to 1.4, in both groups and both periods.
- **Independent backtest:** the 2-year advisor backtest showed the same thing (+3.2 vs −0.1 bp).
- **Specific to Flow ML:** tested on Flow AM it is mixed (worse in 2012–18, better after), so it belongs to the Flow ML signal, not to every strategy.

Caveats:
- For the live 5 pairs it is partly in-sample.
- The real DEMO sample (23 trades) is too small to judge, and points both ways.

**Effect:** roughly the same total profit from a third of the trades, with less than half the drawdown and less time at risk.

### 2. Flow ML on the AUD pairs (AUDJPY, AUDUSD, EURAUD, AUDNZD, AUDCAD, AUDCHF)
The live model, trained on all 25 pairs, works on the AUD pairs about as well as on the live 5, even without AUD rate data.

| | run 1 | run 2 | run 3 (5 seeds) |
|---|---|---|---|
| bp per trade | +1.79 | +1.24 | +1.47 |
| 2021–26 | +0.94 | +1.20 | +1.68 |
| years positive | 10/11 | 11/11 | 10/11 |

- **Combined, live 5 + AUD:** positive every year 2016–26 in one run. The 2021–26 daily Sharpe rose from 0.55 to 0.88.
- **Built:** an AUD shadow book (`bensim_phase20_dev/intel/flow_shadow.py`, inside the advisor, read-only). From Sunday's open it scores these pairs every hour with the live model and records outcomes, never trading.

### 3. Weaker: new 2-year yields for GBP / CHF / NZD
- **Data:** BoE, SNB and RBNZ yields downloaded into a new file (the live file is untouched).
- **Effect on the 14 pairs that gained yields:** 2021–26 improves from about 0 to +0.6 bp at 6 h, and **+1.5 bp after swap at 12 h holds** (9 of 11 years).
- **Status:** promising but not significant, and it was the best of 36 versions.

## What failed tonight (and one bug I caught)
- **Hour-of-day scan, 2,400 tests: 0 pass.** The first run showed 47 fake "winners" at the daily rollover. They came from my own price-construction bug (bar opens built from the close spread). I found it, fixed it with exact quotes and documented it.
- **Also failed:**
  - dollar-factor momentum;
  - carry timing (carry itself stays weak, about +2.6%/yr, with crash risk);
  - intraday momentum into the London fix;
  - S&P big-day → yen;
  - volatility-managed sizing;
  - Flow AM timing variants (the live 09 → 12 is already best);
  - per-pair thresholds;
  - longer holds for the live 5.

## Recommendation (each needs your explicit approval)
1. **ATR filter for Flow ML.**
   - *Safest:* nothing to build. The advisor already rates "ATR expanding" for every Flow ML signal, so forward evidence accumulates now.
   - *Faster:* apply it live. Flow ML then only enters when ATR6/48 > 1.2. It only removes trades, so it adds no risk. It is a small engine change plus a restart.
2. **AUD pairs:** let the shadow book collect about 6–8 weeks of forward signals, then decide whether to trade them.
3. **GBP / CHF / NZD 12 h:** research only for now.
4. **Still open from yesterday:** sflow, **1** keep it with the improved rules at small risk, or **2** switch it off.
