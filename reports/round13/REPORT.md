# Round 13 result (2026-11-07): post-fix reversal, calendar-month seasonality, cross mean reversion -- ALL FAIL
r13_fx.py, r13_results.json, trade CSVs. FXCM 2012-26 exact quotes; BIS monthly 2000-26 for H2.
| claim | n | net | t | periods | verdict |
|---|---|---|---|---|---|
| H1a fade 15:30-15:55 move, 16:05->17:00 London | 92,656 | -0.97 bp | -11.4 | all < 0 | FAIL (no reversal: corr 0.009) |
| H1b same, month-end only | 4,312 | -1.12 bp | -3.0 | -2.6 / -0.1 / +0.02 | FAIL |
| H1c fade through the fix 15:55->16:30 | 92,659 | -0.72 bp | -9.3 | all < 0 | FAIL |
| H2 same-month seasonality G8 long3/short3 | 321 months | -0.26 %/month | -2.7 | negative since 2009 | FAIL (reverses; flipped still < 0 after costs) |
| H3 H4 z>2 fade AUDNZD/EURGBP/EURCHF | 1,260 | -0.2 bp | -0.1 | -0.2 / +1.1 / -1.4 | FAIL (EURGBP alone +3.9, post-hoc) |
The Evans reversal does not exist at 5-minute resolution on FXCM data after 2012 (it is a tick/minute effect, gone in costs).
