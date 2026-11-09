# Round 18: fade no-news FX jumps (H1), follow news jumps (H2): FAIL / FAIL

Rules: `PREREG.md` (written before any code or results; nothing was changed afterwards). Code: `r18_jumps.py`. Output: `r18_results.json`, `r18_trades.csv` (4x sigma trades; `entry_tc`/`entry_utc` = jump bar CLOSE time, UTC).
Data: FXCM M5, 7 USD majors, 2012-01 .. 2026-10. Exits 60 min later at the real bid/ask, +0.3 bp slippage. 15 trades were skipped because of data holes (no bar within 90 min).

## Verdict (4x sigma, the pre-registered rule)

| Hyp | n | gross bp | net bp | DISC net | VAL net | LOCK net | week-cl. t | BH q.05 | Result |
|---|---|---|---|---|---|---|---|---|---|
| H1 fade no-news jump | 24,986 | +0.01 | -0.69 | -0.88 | -0.41 | -0.66 | -3.83 | no | **FAIL** |
| H2 follow news jump | 3,965 | -0.26 | -1.12 | +0.59 | -2.54 | -2.32 | -1.39 | no | **FAIL** |

- H1: before costs a no-news jump neither reverses nor continues over 60 min (gross +0.01 bp). The equity-ETF reversal result does not carry over to G10 FX M5. Net loss = the spread.
- H2: news jumps continued in 2012-17 (gross +1.56 bp), then **reversed** in 2018-21 and 2022-26 (gross -1.9 / -1.5 bp). That fits the general "edges decayed after ~2017-2021" finding in earlier rounds.

## By pair (4x, net bp / week-clustered t)
| Pair | H1 n | H1 net | H1 t | H2 n | H2 net | H2 t |
|---|---|---|---|---|---|---|
| EURUSD | 4037 | -0.72 | -2.99 | 639 | -0.39 | -0.32 |
| GBPUSD | 3980 | -0.65 | -2.34 | 548 | -1.05 | -0.91 |
| USDJPY | 3669 | -1.63 | -5.59 | 665 | -0.81 | -0.74 |
| USDCHF | 4020 | -0.71 | -2.11 | 471 | -2.79 | -2.22 |
| USDCAD | 3476 | -0.33 | -1.28 | 559 | -0.31 | -0.30 |
| AUDUSD | 3109 | -0.12 | -0.31 | 615 | -1.37 | -1.13 |
| NZDUSD | 2695 | -0.53 | -1.24 | 468 | -1.56 | -0.97 |

No pair is positive net for either hypothesis. Gross H1 is positive on AUD/NZD/CAD (+0.3..+0.6 bp), well below the round-trip cost.

## Sigma variants (reported only, not tests)
| Variant | n | gross | net | t | DISC / VAL / LOCK net |
|---|---|---|---|---|---|
| H1 3x | 60,992 | +0.20 | -0.50 | -5.26 | -0.59 / -0.43 / -0.40 |
| H1 6x | 6,758 | -0.29 | -1.04 | -2.19 | -1.59 / +0.12 / -1.16 |
| H2 3x | 5,566 | -1.04 | -1.90 | -2.96 | -1.03 / -2.11 / -2.96 |
| H2 6x | 2,551 | +1.48 | +0.65 | +0.63 | +3.60 / -1.27 / -1.68 |

H2 6x looks positive overall only because of 2012-17; negative in both later periods. Not a lead.

## Dollars ($100k account, fixed lots on every trade, 2012-01..2026-10)
| | 1 lot $/yr | worst month | max DD | 5 lots $/yr | 5 lots max DD | 7 lots $/yr | 7 lots max DD |
|---|---|---|---|---|---|---|---|
| H1 | -12,017 | -11,826 | -184,597 | -60,086 | -922,983 | -84,121 | -1,292,177 |
| H2 | -2,734 | -6,586 | -54,494 | -13,672 | -272,470 | -19,141 | -381,457 |

Every size blows up a $100k account (H1 even at 1 lot). Trades on different pairs can overlap, so 7 lots per trade can mean more than 7 lots open at once.

## Sanity check (random 4x trades printed by the script)
- H1 USDCAD 2019-01-02 08:05 close: sigma 2.41 bp, jump +10.9 bp, sell at bid 1.35916, buy back at ask 1.36331, gross -30.1, net -30.7 bp.
- H1 AUDUSD 2026-08-12 09:25: sigma 1.54, jump +6.8, sell at bid 0.70700, exit ask 0.70679, net +2.67 bp.
- H2 EURUSD 2021-07-28 18:05 (FOMC 14:00 NY = 18:00 UTC): jump -14.3, sell at bid 1.17951, exit ask 1.18362, net -35.0 bp.
- H2 GBPUSD 2015-02-05 13:20 (BoE day): jump +17.6, buy at ask 1.52773, exit bid 1.52914, net +8.9 bp.
- US event times check: 2012-01-12 08:30 NY = 13:30 UTC; 2012-03-09 (before US DST) = 13:30 UTC. Self-check asserts pass for the [-5, +30] min window and the clustered t.

## Caveats
- "No-news" is contaminated: other countries' data releases (UK/EZ/AU/CA/JP data) and unscheduled news are not in the calendar. Some H1 trades are really news jumps.
- Central-bank days flag the whole UTC day as news, so H2 also includes jumps on those days that have nothing to do with the decision.
- The FOMC time is fixed at 14:00 NY. In 2012 the statement on press-conference days came at 12:30 NY, so those 2012 jumps were labelled no-news.
- FXCM M5 spreads are bar-close spreads; spreads right after news can be wider than this inside the bar. A wider real spread would only make H2 worse.
- The dollar numbers are fixed-lot sums with no compounding and no margin limit.

## Bottom line
Both pre-registered hypotheses FAIL. No-news jumps in G10 FX have no 60-minute reversal before costs. News-jump continuation existed in 2012-17 and has turned into a reversal since 2018. Nothing here to trade.
