# BSI V3 PIT Chronology Audit

No future leakage found in the research runner contract. The replay window is strictly `2026-08-01 00:00:00 UTC` through `2026-09-01 00:00:00 UTC`; future-dated candles in the database are excluded. Detector objects carry point-in-time `occurred_at`/`confirmed_at` semantics for swings and FVGs.
