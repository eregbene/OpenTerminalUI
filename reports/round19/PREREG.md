# Round 19 pre-registration: Norges Bank FX-transaction announcement effect on EURNOK

Written 2026-11-07 BEFORE any EURNOK price was looked at for this test. Rules below are fixed; no changes after results.

## Source and sign convention
- BSIC Bocconi (Oct 2026): "A positive amount means Norges Bank buys foreign currency and sells kroner", which "in principle weakens the krone". Finding: a NOK 1bn/day increase in the announced daily FX purchase weakens NOK vs EUR by ~0.41% on the announcement day (daily close-to-close, 14:15 CET fixing); not significant next day, partial reversal.
- Announcement data: Norges Bank data API, dataset ANN_FX_SPU (`https://data.norges-bank.no/api/data/ANN_FX_SPU?format=csv&locale=en`), saved to `data/bars/nok/norges_ANN_FX_SPU.csv`. Columns: TIME_PERIOD = announcement date, OBS_VALUE = NOK million per day, TRANSACTION_TYPE B = Norges Bank buys foreign currency (sells NOK), S = sells foreign currency (buys NOK). Checked against the 31 Aug 2022 press release ("will purchase foreign exchange ... NOK 3500 million per day", type B in the API, NOK weakened per Reuters).
- Release time: 10:00 Oslo time (press releases show "Published ... 10:00", 2016, 2017, 2022; BSIC verified Jan 2026). Assumed 10:00 for every event.

## Definitions
- Signed amount A = +OBS_VALUE if B, -OBS_VALUE if S (positive = NOK-negative flow).
- Surprise S_t = A_t - A_prev, where A_prev is the most recent earlier announcement (including intramonth revisions 2020-03-18, 2020-11-23, 2021-11-18). Positive S = more NOK selling = NOK weaker expected.
- Events: regular month-end announcements only (TRANSACTION_MONTH after the announcement month), announcement date 2015-01-01 .. 2026-10-31. Intramonth revisions are not traded (unknown release time) but count as A_prev.
- Signal: S >= +300 (NOK m/day) -> BUY EURNOK; S <= -300 -> SELL EURNOK; otherwise no trade.

## Trades
- Prices: histdata.com EURNOK M1 ASCII (bid OHLC, timestamps fixed EST = UTC-5, no DST). No ask, so fixed cost.
- Entry: open of the M1 bar starting at 10:05 Europe/Oslo local time on the announcement day.
- H1 exit: open of the M1 bar at 16:00 Europe/Oslo the same day. ("16:00 CET" read as 16:00 Oslo local time, CET or CEST.)
- H2 exit: open of the M1 bar at 16:00 Europe/Oslo on the next trading day that has data (skipping weekends/holidays).
- Missing bar: use the first bar within the following 10 minutes; if none, the event is dropped and reported.
- Gross bp = dir * (exit/entry - 1) * 1e4.
- Costs: 4.0 bp round trip fixed spread + 0.3 bp slippage = 4.3 bp per trade.
- H2 swap per calendar night held: dir * (r_EUR - r_NOK) [% p.a.] * 100 / 365 bp - 0.5 bp markup. r_EUR from `data/macro/policy/policy_rates_daily.csv`, r_NOK from BIS `data/macro/policy/cbpol.zip` (REF_AREA NO, daily), value on entry date (ffill).

## Tests
- One observation per announcement-event trade. t = mean/(sd/sqrt(n)), one-sided p (H: mean > 0) from Student t with n-1 df.
- Benjamini-Hochberg at q = 0.05 over the two p-values (H1, H2).
- Periods by announcement date: 2015-2018, 2019-2022, 2023-2026.
- PASS (per hypothesis): mean net > 0 in all three periods AND full-sample t >= 2 AND BH-significant.
- Expected power: ~12 announcements/yr, but only months with |S| >= 300m trade (likely ~3-5/yr, maybe 30-50 total). Low power; a FAIL does not prove there is no effect, a PASS on this n would still need forward confirmation.

## Money
- $100k account, 1 lot = EUR 100,000, fixed $11.6 per bp per lot. Report $ per year, worst calendar month, max drawdown (on cumulative closed-trade $) at 1, 5, 7 lots.

## Diagnostics (reported, not part of PASS)
- Signed move 09:55 -> 10:05 Oslo (the first 5 minutes we do not capture), and gross day returns, to show where any reaction happens.
- Regression of day-0 (09:55 -> 16:00) EURNOK log return on S (bn) over all regular announcements, to compare with BSIC's 0.41%/bn.
