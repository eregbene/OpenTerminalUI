# Round 18 pre-registration: fade no-news FX jumps, follow news jumps

Written 2026-11-07, before any test code was written or any result was seen. No rule changes after results.

## Data
FXCM M5 bars `data/bars/fxcm/<PAIR>_M5.npz`, pairs EURUSD GBPUSD USDJPY USDCHF USDCAD AUDUSD NZDUSD, 2012-01 .. 2026-10.
`t` = bar open epoch s UTC; o/h/l/c = BID; `spr` = spread in price units. Quote at bar close time t+300: bid = c, ask = c + spr.

## Definitions
- Mid = bid + spr/2. M5 log return r (bp) of mid, close to close of consecutive bars (r_i = 1e4 * ln(mid_i / mid_{i-1})).
- Jump bar: |r| > 4 x sigma, sigma = rolling standard deviation of the previous 288 M5 returns (one day) of that pair, excluding the current bar.
- Skip bars 20:00-23:59 UTC (rollover/illiquid) and weekends (Sat/Sun UTC).
- NEWS jump: bar close within [-5, +30] minutes of a US release (NFP, CPI, Retail Sales; 08:30 New York local, DST-aware) or FOMC statement (14:00 New York local) for any USD pair (all 7 pairs are USD pairs), or on a decision day (00:00-23:59 UTC) of that pair's non-USD central bank (EUR, GBP, JPY, CHF, CAD, AUD, NZD).
- Everything else = NO-NEWS jump. Caveat: other countries' data releases (UK CPI, AU employment, CA jobs, German IFO, etc.) and unscheduled news are NOT in the calendar, so the "no-news" bucket is contaminated with some real news jumps.

## Hypotheses / trades
- H1: on a NO-NEWS jump, trade OPPOSITE the jump at the jump bar's close quote, exit 60 minutes later (close quote of the bar 12 bars later; if bars are missing, the first bar whose close time is >= entry + 60 min).
- H2: on a NEWS jump, trade IN the jump direction at the close quote, exit 60 minutes later (same exit rule).
- One open trade per pair at a time (skip jumps while a trade on that pair is open), per hypothesis.
- Exact quotes: buy at ask, sell at bid, plus 0.3 bp slippage per trade (round trip total). Net bp = signed mid move minus spreads paid via bid/ask minus 0.3 bp.

## Periods and PASS rule
DISC 2012-2017, VAL 2018-2021, LOCK 2022-2026 (by entry year).
PASS (per hypothesis, all required): mean net bp > 0 in all three periods; week-clustered t >= 2 (all periods pooled); Benjamini-Hochberg q = 0.05 across H1, H2 (one-sided p from the clustered t).
Also reported (no pass/fail): gross bp, by pair, and 4x/6x sigma variants. (4x is the primary rule; a 6x variant and, for symmetry, a 3x variant are reported only.)

## Dollars
$100,000 account, 1 lot = 100,000 base units. $ per bp per lot ~ EUR 11.6, GBP 13.3, AUD 6.5, NZD 5.8, USD-base (USDJPY USDCHF USDCAD) 10.
Report $ per year, worst month, max drawdown at 1, 5, 7 lots (all trades of a hypothesis, fixed lots).
