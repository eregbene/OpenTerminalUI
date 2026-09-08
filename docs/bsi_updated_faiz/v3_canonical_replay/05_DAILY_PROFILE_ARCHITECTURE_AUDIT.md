# Daily Profile Architecture Audit

Detector validity and profile decisions are stored separately. Profile may rank or block only with point-in-time evidence.

| Mode                                                | Trades | WR    | Net R     | PF     | Max DD R |
| --------------------------------------------------- | ------ | ----- | --------- | ------ | -------- |
| A_MENTOR_VALIDITY_ONLY                              | 7453   | 70.23 | 3185.9123 | 2.5506 | 24.4487  |
| B_PROFILE_RANKING_NO_HARD_BLOCK                     | 7453   | 70.23 | 3185.9123 | 2.5506 | 24.4487  |
| C_CURRENT_PROFILE_POLICY_FULL_PERIOD_LEAKY_RESEARCH | 7122   | 70.74 | 3126.2218 | 2.6274 | 11.5     |
| D_RELIABILITY_GATED_POINT_IN_TIME                   | 7387   | 70.42 | 3193.6193 | 2.5805 | 15.2641  |
