# BSI V2 Video Reaudit Report

Original course directory found: `F:\Faiz SMC Trading Course`.

Original videos found for all requested lessons:

- Asian Session Trading Strategy and Examples 1-3.
- New York Session Trading Strategy and Examples 1-4.
- ABC Trading Strategy and Examples.
- Under Over Pattern examples.
- 9:30AM Trading Strategy updated and Examples 1-3.
- Reactionary Block lesson and example.
- ABCD lesson and example.
- Order Block Liquidity lesson.
- Putting Everything Together.

Visual contact sheets were extracted from the original videos into:

`data/research/bsi_v2_video_frames/`

Deeper strategy contact sheets were extracted into:

`data/research/bsi_v2_video_frames_deep/`

Confirmed video rules:

| Strategy | Mentor rule confirmed |
|---|---|
| Asian | Asian range high/low, lunch-break fakeout, MSS, OB/FVG entry, target opposite Asian range edge, no Daily bias. |
| New York | One move plus significant pullback, sweep during NY, reclaim/retest original level, fixed 1:2 target. |
| ABC | A leg, B pullback, C takes A/B-side liquidity after creating internal structure, MSS, OB/FVG entry, target B leg extreme. |
| Under/Over | Support/resistance or OB origin with at least three touches, candle close fakeout, reclaim, retest, target imbalance/liquidity. |
| 9:30 | Index-focused strategy; NAS/US30 style scope, 1m execution concern. |
| Reactionary | First OB/FVG reacts, creates second impulse/FVG/OB on same timeframe, entry from second array. |
| ABCD | ABC followed by D purge/reclaim/retest, fixed 1:2 target in examples. |
| OB Liquidity | Last qualifying candle/OB, fakeout through same array, reclaim, retest; heavy clean reaction disqualifies. |

No rule was intentionally loosened to create trades. The main misunderstanding was architectural: tests encoded mentor examples as fixtures, but live/replay never produced those fixture fields.

## Changes Needed From Recheck

The video pass found several places where the first raw extractor was too broad:

- New York: sweep must be inside the New York session, then reclaim/retest the original swept level.
- Asian: the post-Asian lunch fakeout window must be time-safe and anchored to the Asian range end, not built with hour replacement.
- Under/Over: the sequence must be candle-close fakeout, then reclaim, then retest; wicks alone do not count.
- OB Liquidity: the same order block/array must fake out, reclaim, then retest; heavy clean reaction after first touch disqualifies.
- 9:30: remains index-focused and M1-sensitive; zero FX/gold setups in this August universe is expected.

After those corrections, the August DB rerun produced `1,967` valid setups across the 10-symbol universe.
