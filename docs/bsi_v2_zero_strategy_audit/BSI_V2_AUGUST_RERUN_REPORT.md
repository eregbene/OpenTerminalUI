# BSI V2 August Rerun Report

Window: August 1, 2026 00:00 UTC to September 1, 2026 00:00 UTC.

Universe: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, XAUUSD.

Result file: `data/research/bsi_v2_august_rerun_after_video_recheck_all.json`.

## Overall

- Contexts: `23,003`
- Subtype evaluations: `207,027`
- Valid setups: `1,967`
- TP: `576`
- SL: `960`
- SL same bar: `171`
- Open timeout: `260`
- Closed win rate: `33.74%`
- Net R: `-171.3719R`

## By Subtype

| Subtype | Setups | Net R |
|---|---:|---:|
| `bsi_order_flow` | 184 | 38.1364 |
| `bsi_new_york` | 412 | -88.5380 |
| `bsi_ob_liquidity` | 114 | 0.2749 |
| `bsi_reactionary` | 1,134 | -124.6590 |
| `bsi_abcd` | 78 | -10.4413 |
| `bsi_under_over` | 17 | 27.5613 |
| `bsi_abc` | 16 | -4.7062 |
| `bsi_asian` | 12 | -9.0000 |
| `bsi_0930` | 0 | 0 |

## By Symbol

| Symbol | Setups |
|---|---:|
| AUDUSD | 218 |
| EURJPY | 163 |
| EURUSD | 232 |
| GBPJPY | 190 |
| GBPUSD | 155 |
| NZDUSD | 172 |
| USDCAD | 181 |
| USDCHF | 255 |
| USDJPY | 202 |
| XAUUSD | 199 |

## Video-Recheck Tightening

The second pass against the original videos reduced candidates from `3,439` to `1,967`. This was not optimization; it restored stricter mentor sequencing:

- New York sweep must occur during the New York session window.
- Asian lunch fakeout window is now anchored to the actual Asian range end time.
- Under/Over requires ordered candle-close fakeout, reclaim, then retest.
- OB Liquidity requires same-array fakeout, reclaim, retest, and rejects heavy clean reaction.
- 9:30 remains index/M1 scoped, so FX/gold symbols correctly stay at zero.

## Order Flow Simulator Check

Order Flow count and R were unchanged after the extractor fixes: `184`, `+38.1364R`.

The simulator uses signal entry/SL/TP, checks future M15 bars only after the signal bar, and treats same-bar TP+SL as SL first. Remaining caveat: OHLC bars cannot know true tick order inside a candle, so same-bar results are conservative, not exact.
