# Donchian + Imbalance Combination: Check (2026-10-09)

Code: `bensim_research/donchian.py` (`--start/--end` for a window, `--full-history` for 2018–2026).

**Combination A:** imbalance-zone trades (video rules, nearest-zone target) taken only when the latest Donchian signal in the prior 24h points the same way.

## The reported Aug–Sep 2026 result (+79R, +0.75R/trade) was not real

1. **Missing data.** The live ICMarketsSC-Demo M5 feed has large non-weekend holes:
   - 26 Aug → 2 Sep: USDJPY (162h) and USDCHF (153h)
   - Monday 14 Sep: missing for all pairs (about 70h holes)
   - 25 → 30 Sep: EURUSD/USDCAD (108h), GBPUSD/AUDUSD (61h)
   - 13 → 17 Aug: the feed changeover

   That's 7–14 of about 45 trading days per pair. Trades that ran through a hole were scored as if price had never touched the stop. The two USDCAD +17R/+16R "winners" (5-pip stops) crossed the 14 Sep hole.
2. **Tiny stops.** 4 of the 5 biggest winners had stops of 4–7 pips (under 8× the spread). A tiny stop that survives becomes +15R; normally it gets stopped by noise.
3. **The top 5 trades made 69R of the 79R.** The other 100 trades made +9.7R.

## Corrected numbers (trades crossing a data hole excluded)

**Aug–Sep 2026:**

| Strategy | Trades | Win % | Net R/trade | Total R |
|---|---|---|---|---|
| Donchian alone | 268 | 40.3 | −0.107 | −28.7 |
| Donchian → entry at its imbalance zone | 42 | 16.7 | 0.000 | 0.0 |
| Imbalance H1 (video rules) | 297 | 17.8 | −0.244 | −72.6 |
| **Imbalance H1 + Donchian direction** | 97 | 22.7 | **+0.155** | **+15.0** |

**2018–2026 history** (189 of 24,489 trades crossing a hole excluded, mostly XAUUSD):

| | All zones | Donchian agrees | Donchian doesn't agree |
|---|---|---|---|
| H1 | −0.093 (18,814) | **−0.068** (6,194) | −0.106 |
| H4 | −0.108 (5,486) | **−0.075** (1,405) | −0.119 |

- H1 + Donchian agrees was positive in only 2 of 9 years (2020 +0.01, 2023 +0.04).
- H4 + Donchian agrees was positive in 2025 (+0.13) and 2026 (+0.46, 88 trades), and strongly negative in 2021–2022 (−0.32).

## Conclusion

The Donchian direction filter is a small, real improvement of about +0.03R/trade, but the combination is negative over 8 years. The clean Aug–Sep result (+0.155R on 97 trades) sits inside its normal 2-month range (−0.5 to +0.5R). There is no hidden edge here.

## Things that need doing

1. **The live candle feed has multi-day holes (Aug–Sep 2026).** Any research, outcome labelling or fingerprinting that reads `mt5_canonical_candles` for ICMarketsSC-Demo is affected. Worth checking whether the candle collector was down or skipping symbols on those dates, and whether live strategy context saw the same gaps.
2. **XAUUSD history has 118 missing days** (141 holes > 2h, 46 over 24h). Gold results across all bensim research are less reliable than the FX pairs.
3. All research scripts now drop and count trades that cross a data hole (`data.holes()` / `data.overlaps_hole()`).

## Clean-data re-test (ForexSB/Dukascopy M5, 2024-02-12 → 2026-10-09, `bensim_research/clean_retest.py`)

Net R/trade with the repo broker cost model:

| Rule set | Trades | Net | t | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| Video rules, all zones M30/H1/H4 | 20,731 | −0.128 | −7.9 | −0.17 | −0.11 | −0.11 |
| CORE (no M30, stop > 8 spreads, no GBPUSD) | 5,247 | −0.007 | −0.2 | −0.01 | −0.04 | +0.05 |
| CORE + no 12–20 UTC + skip HTF-aligned | 1,526 | +0.067 | 1.2 | +0.17 | −0.07 | +0.17 |
| **CORE + Donchian direction** | 1,556 | **+0.053** | 0.9 | +0.08 | +0.02 | +0.07 |
| Donchian alone (M15) | 5,058 | −0.086 | −5.3 | −0.11 | −0.07 | −0.08 |

- The clean data confirms the earlier findings: break-even to slightly positive at best, never significant.
- CORE + Donchian direction is the only variant positive in all 3 years.
- Dukascopy's own spread (median EURUSD 0.3 pip, USDJPY 0.6, AUDUSD/USDCAD/USDCHF about 1.0–1.6, gold $0.56) gives almost the same net results as the repo cost model.

## Why the live candle feed has holes (root cause)

The live system has no dedicated candle recorder. Candles are stored only as a side effect of live cycles (`adapter.candles()` → `copy_rates_from_pos(count≈100)` → `persist_candles`). When MT5 is unreachable, nothing is stored, and after a restart only the last ~100 bars are fetched, so long outages stay permanent holes.

Scheduler cycles show the outages directly:
- `TRADING_DISABLED` with `BROKER_NOT_READY:MT5UnavailableError` on 9–17 Sep (100% on 13–14 Sep) and 22–23 Sep.
- No cycles at all on 18–21 Sep and 28 Sep – 2 Oct.
- **100% MT5 unavailable from 3 Oct to 9 Oct** (no MT5 terminal process on the host). Last accepted trades: 24–25 Sep.

Fix options (not implemented, need sign-off because they touch the live system):
1. Restart the 100K terminal + bridge (`E:\MT5\Bensim-100k`, `scripts/start-mt5-bridges.ps1`).
2. Backfill on reconnect: on startup or reconnect, fetch from the last stored candle to now (`copy_rates_range`) instead of a fixed last-100-bars window.
3. Alert when `BROKER_NOT_READY` lasts more than ~30 minutes, so an outage isn't silent for days.

## Combined strategies on the clean 2 years (`bensim_research/clean_combos.py`)

- **96 filter combinations** (TF × stop × GBPUSD × session × HTF × Donchian direction). The in-sample top looks great (H4 combos +0.4 to +0.64R/trade), but it's driven by 2026 and small samples. **Learn-on-2024 → test-2025/26:** the top 10 picked on 2024 averaged **+0.027R** on 2025–26, the same as the median combination (+0.026R). Choosing filters had no predictive value.
- **Combo B (Donchian breakout → limit entry at its own imbalance zone, stop under the zone ≥ 8 spreads, 4-ATR target):** clean 2024-02 → 2026-10: 1,205 trades, **+0.165R/trade, t 2.2**, positive in 2024/25/26, 61% of months positive, max DD 62R.
- **Out-of-sample check, same rules on 2018-09 → 2024-02:** 2,689 trades, **−0.010R/trade (t −0.2)**. Years: 2018 −0.21, 2019 −0.17, 2020 −0.05, 2021 +0.03, 2022 +0.12, 2023 −0.03. By symbol: USDJPY +0.16, AUDUSD +0.12, EURUSD +0.05, GBPUSD −0.09, USDCAD −0.15.

**Conclusion:** combo B's clean-window result does not hold over the earlier 5.5 years. It is break-even over 2018–2026 as a whole, and the recent strength is a period effect, not a proven edge. It's still the best candidate for a frozen forward paper test.
