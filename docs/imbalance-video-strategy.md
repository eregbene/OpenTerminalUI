# Imbalance Video Strategy ("market efficiency" / imbalanced zones)

Built 2026-10-08 from `D:\Downloads\imbalance.mp4` (12:42, video only) and `D:\Downloads\Imbalance.mp3` (its audio).

**Sources:**
- The audio was transcribed with faster-whisper `small.en`; the transcript is in `data/research/artifacts/bensim/imb_zone/video_transcript.txt`.
- Each frame was extracted at 1 per second (762 frames). Unchanged frames were dropped, leaving 175, and 130 of those show charts. All were reviewed. Contact sheets are in `.../imb_zone/video_frames/`.

**Code:** `bensim_research/imb_zone.py` and `bensim_research/test_imb_zone.py`. Research only; not connected to live trading.

## 1. What the video teaches

| Concept | Video (time) | Mechanical rule used |
|---|---|---|
| Imbalance | "an open price range … a large candle or a collection of large candles with no wicks pulling back through them" (0:26–1:19). Identified like an FVG, but "the way it's traded is totally different" (0:48–1:04) | A chain of consecutive same-direction 3-candle gaps counts as one imbalance |
| Where price reacts | "not trade into this area, but fill this area entirely and trade to the demand zone that's below it" (1:25–1:43) | The **zone is the base candle the run started from**, not the gap: demand = [low, high] of that candle; supply mirrored |
| Efficient vs inefficient | A market that pulls back to retest every area is efficient. A push away that leaves an untested zone is inefficient, and price should come back to it (1:58–3:38) | Only zones not yet touched count |
| Already retested = gone | "we can actually get rid of this because this has already been retested" (10:43) | One trade per zone, on its first retest only |
| Trade the extreme | "the extreme imbalance, which is the furthest away from price, is going to be the higher probability" (3:48–4:08, 12:03) | Skip a zone if an older untouched same-direction zone lies beyond it |
| Direction | "I used the market structure and the trend structure to determine which way" (8:58); "multiple breaks of structure driving us into this upside move, so when we return to it we can look for a buying opportunity" (5:41) | The run must break the last confirmed swing (BOS) **before** price first returns to the zone |
| Entry and stop | "buy from this area with stops below the zone" (7:11) | Limit order at the zone's near edge; stop beyond the far edge + 0.1 ATR |
| Target | "for targets, we can use imbalances too … the next imbalance … leads us into the supply zone" (7:22–7:40, 9:10, 11:21) | Nearest untouched opposite zone; no target means no trade; skip if under 1R |
| Refinement | A newer break forms a newer imbalance → "confirmed" entry with the same target (8:03–8:48) | Covered: every armed zone is its own setup |
| Timeframes | "daily … 30 minute, 5 minute, 1 minute — fractal" (6:17–6:30); examples on D1, M30, H2 | Tested on M30, H2 and D1 (the video's timeframes) |
| Extra confluence | "providing we have additional confluences" (4:20, 5:39) | **Not specified in the video**, so not modelled |

## 2. Check against the video's own trades (regression tests)

| Video example | Video levels | Code | Result in our data |
|---|---|---|---|
| EURUSD M30, 19 Apr 2024 01:30 UTC | entry 1.06193, stop 1.06047 | entry 1.06189, stop 1.06053 | target hit, +2.7R (the code targets the nearest supply zone, 1.0656; he used 1.0710) |
| AUDUSD D1, buy after the extreme imbalance fills (Apr 2024) | zone ≈ 0.6355–0.638 | entry 0.63909 on 16 Apr 2024 | target hit, +2.3R |
| AUDUSD D1 upper zone (≈0.655–0.66) | "doesn't respect the first area, because there's deeper imbalance below" | correctly skipped as not the extreme | — |
| EURNZD H2 | — | not in our data | — |

`python -m pytest bensim_research/test_imb_zone.py` checks both reproduced trades.

**Two things found while matching the video:**
1. **EURUSD is missing from the M15 data for Oct 2022 → Aug 2024.** The strategy fills those gaps with FOREXSB M30 data. The earlier Bensim and FVG audit EURUSD numbers were based on the gappy data.
2. **A stricter "current trend must agree" filter blocked the video's own AUDUSD buy.** It is kept only as a diagnostic.

## 3. Backtest

**Setup:**
- 7 instruments (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, XAUUSD), 2018-08 → 2026-08.
- Base-scenario costs.
- DEV = before 2024; HOLD = from 2024.
- Every rule above was fixed before this run, so this is one honest test.

| TF | trades | win % | avg R:R | gross R/trade | cost R/trade | **net R/trade** | PF | t | DEV net | HOLD net |
|---|---|---|---|---|---|---|---|---|---|---|
| M30 | 9,188 | 22.2 | 5.6 | +0.039 | 0.178 | **−0.139** | 0.85 | −4.9 | −0.138 | −0.141 |
| H2 | 2,057 | 20.6 | 5.2 | −0.050 | 0.085 | **−0.135** | 0.85 | −2.8 | −0.125 | −0.155 |
| D1 | 187 | 23.0 | 3.6 | −0.213 | 0.018 | **−0.231** | 0.71 | −2.0 | −0.406 | +0.202 (n=54) |

**Diagnostics** (net R per trade, same order M30 / H2 / D1):
- Without the "trade the extreme" rule: −0.157 / −0.100 / −0.157. The extreme rule doesn't help.
- With a current-trend filter added: −0.167 / −0.058 / −0.248.

**By instrument (H2):** USDCAD +0.11, EURUSD −0.00; every other instrument is negative.
**By direction:** both sides are negative on every timeframe.

## 4. What the data shows

- **The method behaves as taught:** it wins about 21–23% of the time at about 5:1 reward-to-risk. The break-even win rate at that ratio is about 15–17% before costs.
- **Before costs, M30 is slightly positive (+0.04R) and H2/D1 are negative.**
- **The zone is a single candle, so stops are very tight** (a few pips on M30). Spread then costs 0.18R per trade on M30 and 0.08R on H2, which turns every timeframe negative.
- **D1 is too small a sample to judge** (187 trades in 8 years). It was negative before 2024 and positive after (54 trades): no consistent evidence.
- **The video's examples are real, and the code reproduces them,** but over 8 years and 7 markets the rules as taught do not make money after costs.

**Not tested, because the video doesn't define them:**
- The "additional confluences" he mentions.
- His discretion in choosing a further target (EURUSD 1.0710 instead of the nearest zone).
- The 5-minute and 1-minute timeframes (we don't have enough M5 history; M1 isn't stored).

## 5. Recommendation

Don't deploy. If you want to continue with this video's method, the next steps are:
1. Write down the confluences you personally use with it. That's the missing part of the rules.
2. Test that as one pre-registered rule, only on data after 2026-08-14 or on forward demo.

On costs: a wider stop (the whole base area rather than one candle) would cut the spread share. But that changes his method, so it would need to be declared as a new hypothesis, not tuned on this data.
