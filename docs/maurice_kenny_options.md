# Maurice Kenny — "How to Day Trade Like the Top 10" — rules as taught

Source: `D:\trading\Maurice Kenny - How to Day Trade Like the Top 10` (90 lessons, transcripts + 1 frame / 15 s in
`D:\trading\mkenny_work`). Engine: `optsim/` (separate from the FX `bensim` and crypto engines).

## What he trades
- **SPY only.** "I don't trade Tesla … Apple … bouncing chart to chart". One static screen, no scanners. (Ch 5 L1)
- **Options, bought (calls up / puts down), 0DTE, at the money.** Frame check Ch 12 L1: `SPY 201120P357.5` bought
  on Fri 20 Nov 2020 10:41 CST with SPY at 357.41, 75 contracts at ~$0.86. Avoid too-cheap OTM: "the dang thing don't
  move" (Ch 18). High-volume ATM so fills are instant (Ch 5 L3).
- Size: he puts ~$6-14k per trade (60-100 contracts at $0.6-1.6); students start at 1 contract and scale up weekly.
- Risk: pick a "magic number" per trade — his: ~$700 max loss on a $10k trade, aims for 2-3x that (Ch 15 L1).
- Four screens: SPY 1-min, SPY 5-min (VWAP only indicator), option 1-min, option 5-min. Multi-day: 5-day 5-min.

## Zones (5-minute chart)
- Supply zone: the level where buyers and sellers "agreed" it won't go higher and price was pushed away hard. Draw a
  line at the top of the turning candle(s) (resistance) and a box 10 cents below it (red). Demand: bottom + 10 cents
  (green). Tiny candles: cover the wick. 10 cents = his measured average of how far candles stop short of the line.
- **Major** zones = the top and bottom of the day's range (2-6 in a 4-hour window). **Minor** zones = the swings in
  between; they are magnets = targets. **#1 tip: trade only the major zones** (Ch 8 L1.5, Ch 14 L3.5, Ch 21).
- **Multi-day zones**: prior days' highs/lows (5-day 5-min chart) — "nothing tells you where it stops" intraday;
  previous-day levels did (Ch 17 L1). Draw 1-3 candidates where the move could cap.
- Broken zone flips: supply broken becomes demand and vice versa (hard trades, Ch 10, Ch 14).
- Zones weaken each touch ("usually once is enough … the more it bounces the lower the probability") (Ch 16 L2).
- Zones too tight (major demand moved, new one right below) → no trade, it ping-pongs (Ch 17 L9). Draw a zone only
  if you'd trade off it.
- VWAP acts as a zone/wall. Price above VWAP = up day, below = down day (Ch 17 L4, L8).

## Liquidity (all he said — user rule: keep every liquidity note)
- A fast push just through a zone and straight back = hedge funds "buying out all your stop losses … using it as
  liquidity" then reversing ("intentional … barely went above it") (Ch 8 L1).
- Long wicks = "just getting liquidity", not real buyers; thick bodies = real participation (Ch 4 L3, Ch 13 L1).
- Dips toward break-even after a good entry = funds taking retail stops at break-even before pushing on (Ch 11 L1).
- Physical stops are visible (level 3) and get hunted → he uses **mental stops** at the zone edges (Ch 15 L3-4).
- A failed confirmation candle with a big wick into the zone = "get some of those stop losses … panic you out" — wait
  for the pattern to re-form (Ch 11 L4). On Dec 9 price "finally went in the zone to get as much liquidity as
  possible, and then it started revving down" (Ch 14 L2).

## Timing
- Open the platform **1 hour after the open** (draw zones then); set an alarm ~20 cents before the zone; walk away;
  come back only when it rings (no FOMO). (Checklist, Ch 11 L3, Ch 14 L3.5)
- Busiest windows: first 2 hours and the last hour (power hour) (Ch 21). Power hour: minor zones get ignored, only
  majors hold; in power hour only, entering on a fast confirmation candle (3rd candle) is allowed (Ch 14 L3).
- No news trading; both of his shown losses came from trading news / skipping candles (Ch 15 L5).
- He does not hold overnight.

## Entry — 4 candles on the 1-minute chart (Ch 11 L2, L4; Ch 12-14)
1. **Zone candle**: the last candle of the move into the zone (touching it; 1-2 cents short still counts).
2. **Reversal candle**: first candle of the opposite colour (two small side-by-side ones count as one).
3. **Confirmation candle**: same colour, forming beyond the reversal candle, and **closed outside the zone** (a wick
   back into the zone invalidates it).
4. **Entry candle**: buy in its first seconds if it opens/forms beyond the confirmation candle.
- A candle of the wrong colour after the reversal cancels → wait for a new reversal + confirmation.
- Doji does not count as confirmation (his loss on Dec 21).
- **Go with the day** (market context): down day → puts at supply only; up day → calls at demand (Ch 8 L3, Ch 17 L8).
- **VWAP in the way** between entry and target → no trade (Ch 17 L6).
- Dustin special = zone + VWAP together: his highest-probability setup, 1-2 per week (Ch 17 L4.5).

## Exit
- **Stop (mental)**: close beyond the zone. Conservative = near edge (fast, thick candles); wiggle room = far edge
  (small candles). "Give the trade wiggle room" — don't panic on one opposite candle (Ch 15 L2, Ch 16 L2).
- **Targets**: TP1 = nearest minor zone or VWAP; if it stalls there (opposite candle) sell; if it runs through, TP2 =
  next zone (Ch 17 L7). Exit after **two candles against you** at friction (Ch 8 L1, Ch 12 L1, L3).
- If the chart gives you break-even twice and can't go your way → take break-even (Ch 17 L8).

## Mechanical translation used by `optsim` (choices the course leaves open — fixed BEFORE any test)
- Zone width = 0.30 x ATR(14) of 5-min bars (10 cents on 2020 SPY ≈ 0.3 of its 5-min range), so it scales to TSLA.
- Zone = 5-min swing high/low (2 bars each side) followed by a push away of >= 1 ATR within 3 bars.
- Major = the day's current extreme zones + previous day's high/low; minor = other zones (targets only).
- Trades from 10:30 ET (1h after open) to 15:50 ET; flat by 15:58 ET. Each zone traded once. Max 2 trades/day/ticker.
- Direction filter ("don't fight the day"): no trade against a trending day = VWAP moved > 0.5 x ATR(5m) over the
  last 60 min AND price on that side of VWAP. (Strict "calls only above VWAP" was dropped BEFORE the test: his own
  20 Nov 2020 put was above VWAP on a range day. Strict and no-filter are reported as variants.)
- Corrections after reading his trade frames (2026-10-21, before any backtest result):
  1. No "VWAP / next zone in the way" skip: he took 18 Dec, 3, 10, 16 Dec with VWAP right in the path. VWAP / nearest
     zone = TP1: sell there only if it stalls (opposite candle), else hold to TP2 = next zone (Ch 17 L7).
  2. Flipped zones traded (15 Dec, 9 Dec) — only zones that existed at 10:30 (his drawn majors) or prior-day levels.
  3. Power hour (from 15:00): entry on the confirmation candle, last entry 15:55 (7 Dec).

## Manual check of all 9 course trades (frames vs engine on US500/10 ≈ SPY)
| Day | Type | His trade (frame) | Engine | Match |
|---|---|---|---|---|
| 20 Nov | easy | put 11:41 ET @357.41, zone 357.54-.64 | put 11:42 @357.42 | yes |
| 16 Nov | easy | call 13:46 ET, zone 359.71-.81 | call 13:47 | yes |
| 18 Dec | easy | put 12:45 ET, zone 369.54-.64 | put 12:33 (other zone, stopped) | close |
| 3 Dec | medium | call 11:16 ET, zone 366.8-.9 | puts | no |
| 10 Dec | medium | call 13:02 ET, zone 366.34-.44 | call 13:01 (+0.86R) | yes |
| 16 Dec | medium | call 14:23 ET, zone 369.05-.15 | call 14:03 | close |
| 15 Dec | hard flip | call 12:35 ET, broken supply 367.48-.58 | put 12:27 | no |
| 9 Dec | hard flip | put 11:58 ET, broken demand 369.56-.66 | put 13:29 | close |
| 7 Dec | hard PH | call 15:52 ET, zone 367.89-.99 | none | no |
3 exact, 3 close, 3 missed — remaining gaps are zone placement (he draws by eye on SPY; we test on the US500 CFD).
Not tuned further: fitting 9 days would be curve-fitting his examples.
- Entry at the open of candle 4; stop = 1-min close beyond the far edge; exit at TP1 touch or 2 opposite closes.
- Option: ATM, nearest listed expiry (SPY/QQQ daily since 2022; single stocks Friday weeklies), Black-Scholes on the
  day's implied vol (DoltHub), real ATM spreads from DoltHub chain quotes, $0.65/contract/side.
