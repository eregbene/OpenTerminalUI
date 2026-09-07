# BSI V2 All Strategies Golden Test Report

## Summary

All nine BSI V2 strategies have video-derived semantic golden coverage in the V2 research path.

V2-only command:

`python -m pytest backend/tests/test_bsi_v2_foundation.py backend/tests/test_bsi_v2_new_york.py backend/tests/test_bsi_v2_all_strategies.py -q`

Result:

- `37 passed`

## Strategy Coverage

| Strategy | Golden Coverage | Result |
|---|---|---|
| bsi_order_flow | trendline liquidity, mentor MSB/MSS, mentor OB/FVG, no OTE, MSB new opportunity | PASS |
| bsi_new_york | original-level retest, wick-tip negative, bare retest, no OB/FVG | PASS |
| bsi_abc | A/B/C geometry, B-leg invalidation, OB/FVG entry, B-leg target | PASS |
| bsi_asian | Asian session box target, no Daily bias, no PD | PASS |
| bsi_under_over | >=3 touches, close reclaim, wick-only negative, partial metadata | PASS |
| bsi_0930 | index scope, window evidence, displacement, FVG/OB entry, >=3R | PASS |
| bsi_reactionary | two-array sequence, array2 entry, ambiguity persisted | PASS |
| bsi_abcd | P2 D-leg retest, fixed 1:2, ambiguity persisted | PASS |
| bsi_ob_liquidity | same-array fakeout/reclaim, heavy-reaction negative, residual-liquidity negative | PASS |

## Global Negative Coverage

Covered:

- generic last-opposite OB does not define V2 mentor OB
- OTE/golden-pocket does not affect V2 PD
- CHoCH is not a separate V2 mentor methodology state
- NY wick-tip substitution rejected
- scheduler duplicate blocked
- stale entry rescue rejected
- unsupported universal Daily/HTF gate rejected for Asian
- unsupported universal PD gate rejected across no-PD strategies
- whole-trend dedup avoided for Order Flow MSB/new array
- no legacy strategy fallback in V2 dispatcher

## Caveat

Current golden tests are semantic synthetic fixtures. They prove V2 methodology reasons and evidence shape, not exact replay of original video OHLC because the source videos/precise candle data are not present as machine-readable fixtures in the repo.
