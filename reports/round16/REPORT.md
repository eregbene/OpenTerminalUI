# Round 16 result (2026-11-07, tester agent): intraday-only carry -- FAIL
Long top-3 / short bottom-3 G8 by BIS policy rate, held 08:00-16:45 New York only (no swap). FXCM M5 exact quotes 2012-26.
| claim | days | net bp/day | t | DISC / VAL / LOCK | gross | verdict |
|---|---|---|---|---|---|---|
| H1 every day | 3,695 | -1.95 | -4.6 | -3.5 / -1.1 / -0.7 | +0.05 | FAIL |
| H2 US release days (NFP, CPI, retail sales) | 495 | -1.58 | -1.1 | -3.1 / +0.7 / -1.7 | +0.53 | FAIL |
US-hours gross carry return is ~0; carry is earned overnight via the interest itself (overnight price+carry +0.75 bp, net -1.24).
$ at 1/5/7 lots per year: H1 -4,871 / -24,356 / -34,098. ISM and GDP dates dropped (no reachable archive). Files: r16_*.
