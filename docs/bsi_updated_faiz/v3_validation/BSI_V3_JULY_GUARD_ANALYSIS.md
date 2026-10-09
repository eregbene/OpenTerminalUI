# BSI V3 July Guard Analysis

| Mode                    | Trades | Win % | Net R     | PF     | Max DD R | Max LS |
| ----------------------- | ------ | ----- | --------- | ------ | -------- | ------ |
| Jan-Jun trained forward | 2895   | 51.47 | -288.5851 | 0.787  | 310.4154 | 12     |
| July hindsight buckets  | 504    | 62.3  | 70.304    | 1.3932 | 6.0      | 5      |
| Best no-lookahead guard | 1023   | 53.96 | -42.8446  | 0.9053 | 65.0216  | 23     |

## Month Kill Switches

- `-10.0R`: stopped at `2026-07-01T00:20:00+00:00`, kept net `-10.0R`, skipped net `-294.5851R`
- `-25.0R`: stopped at `2026-07-01T00:45:00+00:00`, kept net `-25.0R`, skipped net `-279.5851R`
- `-50.0R`: stopped at `2026-07-02T05:00:00+00:00`, kept net `-50.4824R`, skipped net `-254.1027R`

Conclusion: July needs circuit-breaker/profile-regeneration logic, not a higher RR-only rule.
