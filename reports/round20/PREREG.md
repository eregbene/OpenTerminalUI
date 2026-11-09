# Round 20 PREREG -- OeNB WP 258 (Boeck, Steshkova & Zoerner 2024) FOMC-shock carry strategy

Written 2026-11-07 BEFORE any test was run. No rule changes after results.

## Paper's rules (Section 5, eq. 5.1-5.2, Table 3)
- Universe: 15 currencies vs USD (DM: AUD CAD CHF EUR GBP JPY NOK NZD SEK; EM: CZK INR MXN PHP THB ZAR).
- Sort currencies each period by forward discount (= interest differential i* - i_US under CIP) into terciles.
  P3 = highest third, P1 = lowest third, P2 not traded, equal weights. HML = mean(P3) - mean(P1), each vs USD.
- Signal: Jarocinski-Karadi (2020) high-frequency FOMC surprises (window -10/+20 min): ff = 1st principal
  component of FF1, FF4(3-month FF), ED2, ED3, ED4 surprises; SP500 = S&P 500 surprise.
- Rule (eq. 5.2): if ff > 0 AND SP500 < 0 (monetary-policy shock) -> buy USD, sell ALL currencies equally;
  otherwise -> HML carry.
- Entry: closing prices on the FOMC announcement day. Hold until the day prior to the next scheduled FOMC meeting.
- Instruments: weekly rolling forwards (announcement strategies), 1-month forwards (end-of-month). No transaction
  costs mentioned. Sample Jan 2004 - Jun 2019, in-sample only (no out-of-sample split in the paper).
- Reported (all 15 / DM / EM): Panel A HML end-of-month mean 2.70/2.38/2.47 %pa, SR 0.394/0.250/0.269;
  Panel B HML from announcement day 2.16/1.23/1.76, SR 0.317/0.140/0.252;
  Panel C Anncmt,Shock 4.01/2.70/3.26, SR 0.945/0.514/0.510, skew +0.18/+0.13/+0.64.

## Our faithful-as-possible version (data limits stated)
- Universe: the 7 G10 currencies we have vs USD: EUR GBP JPY CHF AUD NZD CAD (USD pairs only:
  EURUSD GBPUSD AUDUSD NZDUSD USDJPY USDCHF USDCAD). NOK, SEK and all EM currencies unavailable.
  Closest paper benchmark is the DM column (SR 0.514).
- Forward discount proxy: central-bank policy rate differential (data/macro/policy/policy_rates_daily.csv,
  forward-filled) as of the entry day. No forward points available; CIP/policy-rate proxy.
- Terciles with 7 currencies: P1 = 2 lowest-rate, P3 = 2 highest-rate, P2 = middle 3 (untraded).
  Ties broken alphabetically by currency code (fixed in advance).
- Surprises: JK file shocks_fed_jk_t.csv from github.com/marekjarocinski/jkshocks_update_fed (columns pc1, SP500),
  downloaded 2026-11-07, runs to 2026-09-16. Shock state = pc1 > 0 AND SP500 < 0.
- Meetings: scheduled FOMC dates data/macro/fomc_dates.csv (2012-2026). Meeting with no JK row -> no trade.
- Timing: entry at NY 17:00 close of announcement day (H1 bar ending 17:00 America/New_York); exit at NY 17:00
  close of the day before the next scheduled FOMC date. Last period truncated at last bar (2026-10-09).
- Fills: bid = c, ask = c + spr at bar close; buy at ask, sell at bid, +0.3 bp slippage per fill.
- Swap: per calendar night held, (i_long - i_short)/365 - 0.5 bp markup per leg.
- Portfolio sizing: "1 lot" = $100k notional per side. HML: $50k long each P3 ccy vs USD, $50k short each P1
  ccy vs USD (all through USD pairs, as the paper builds it vs USD). Shock state: $100k/7 short each ccy vs USD.
- Claims (3, BH-corrected at q=0.05 on one-sided full-sample p):
  C1 Anncmt,Shock (eq. 5.2, main claim); C2 HML announcement-day (Panel B); C3 HML end-of-month
  (Panel A: entry NY close of last trading day of month, hold to next month's last trading day).
- Unit = one holding period (non-overlapping; each period is its own cluster). t = mean/(sd/sqrt(n)).
  Also report year-clustered t.
- Periods by entry date: DISC 2012-2017, VAL 2018-2021, LOCK 2022-2026.
- PASS = mean net > 0 in DISC, VAL and LOCK AND full-sample t >= 2 AND BH-adjusted significance.
- Dollars on $100k account at 1/5/7 lots (lot = $100k per side): ret x $100k x lots.
- Caveat: 2012-01..2019-06 overlaps the paper's in-sample window (different universe/instruments);
  2019-07..2026 is genuinely out-of-sample for the paper.
