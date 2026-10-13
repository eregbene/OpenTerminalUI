# Phantom Trading -- course notes (Phantom 1.0 "Old" + Phantom 2.0 core lessons only)

Source: `D:\trading\Phantom Trading 2.0 Refined` -- Phantom 1.0 (12 videos) and the 11 core lessons of Phantom 2.0
(no live meetups / team content / backtesting recordings). Transcripts: `D:\trading\phantom1_work\audio\_transcripts`
(GPU: Const-me Whisper on the RX 580, small.en; CPU: faster-whisper small), frames every 30 s in `...\frames`.
Every rule below cites the lesson and timestamp; nothing gets coded without a source.

## 1. Market structure (1.0: "Multi Timeframe Market Structure", "Market Structure Walk Through")
- **Three mapping methods**; his preference is **method 1: highs/lows on candle BODIES, break = BODY CLOSE beyond**
  (wicks differ between brokers, bodies do not). Method 2: wick mapping + body-close break; method 3: wick + wick.
  Pick one, never mix. [MTF 01:00-04:30]
- A wick through a level without a close = a **liquidity grab** (stops above the prior high), not a break.
  [MTF 01:40; Walk 03:13, 05:52]
- Only **prominent swings** count -- a pause inside a move is not a swing. Check on the **line chart (closes)**: it
  "really shows that clear delineation". [Walk 04:03-04:44, 06:44-08:05]
- **One impulsive leg = one swing**: dips inside a strong leg that break nothing are not new lows. [Walk 11:07-11:36]
- **Higher timeframe leads**: daily HH/HL -> 1H LL/LH is the daily pullback; sells there are allowed but managed
  "more aggressively" because the daily higher low is coming. [MTF 00:10-01:00, 09:30-11:00]
- **What happens after the break**: a true break runs away from the level; an immediate return = false / no momentum.
  [MTF 10:00]
- Structure is only the **framework / direction**; entries come from **order blocks, inefficiency and Wyckoff**. [MTF 05:30]

## 2. Order blocks (1.0: "Order Blocks", 40 min)
- **Definition**: the last opposite candle before a **large impulsive move (or series) that BREAKS STRUCTURE**.
  Bullish OB = down candle before the up move that breaks a high; bearish = up candle before the down move. [00:27-00:50]
- **Why it works**: unfilled institutional orders + rebalancing -> price returns to the block. [00:50-01:14]
- **Entry / stop**: limit at the near edge (bullish: OB top, bearish: OB bottom), stop just beyond the far edge;
  **put the stop beyond nearby liquidity wicks**, not at the block itself, or it gets spiked. [01:14-01:54, 15:45-15:59, 26:49-27:25]
- **Quality markers**: the break is **impulsive / with momentum**, often leaves an **imbalance** (gap) [03:08-03:16, 12:48-13:02];
  an OB candle that is **too large** is skipped (use it only as a lower-TF point of interest). [10:55-11:06]
- **Refinement ("price doesn't know time")**: if the candle after the OB candle does **not engulf it / shows no momentum**,
  move the OB forward to the candle right before the **first strong full-bodied engulfing candle** -- where momentum
  actually starts. Smaller stop, less drawdown. [04:09-05:39, 18:28-19:35, 20:30-20:57]
- **Multi-timeframe entry**: 1H OB = point of interest; when price taps it, wait for a **15M break of structure**, which
  leaves a **15M OB**; enter on the retest of the 15M OB -> ~half the stop, ~2x the reward (example 26 -> 12 pips,
  RR 2.8 -> 5.9). [13:08-16:35, 25:01-26:46]
- **Targets**: previous highs / **equal highs** (resting stops = liquidity price seeks). [09:45-10:25, 28:05-28:19]
- **Trend**: with the **daily** trend; counter-trend OBs are "dangerous", lower confidence. [11:44-12:19, 20:17-20:27, 21:45-21:54]
- **Two OBs stacked**: split the risk (half on each) or wait for the second test / lower-TF confirmation. [22:45-23:30]
- A tap-and-reject at an OB is also a place to **take profit / secure the stop** on an open trade. [28:46-29:00]

## 3. Market inefficiency (1.0: "Market Inefficiency", 14 min)
- **Definition (= a 3-candle FVG)**: efficient if candle 1's high meets candle 3's low (up move; mirror for down);
  a gap between them = **inefficient** price, one side never got filled. [00:07-01:42]
- Price **gravitates back to rebalance** the gap before / while continuing -- consolidations are often this. [01:42, 07:04-07:30]
- **Not an entry on its own** ("we don't just trade inefficiencies for the sake of it" -- strong momentum blasts through
  them); **order blocks are the entry**, inefficiencies are **targets / points of interest / market understanding**.
  [07:46-07:51, 09:37-09:53, 13:06-13:16]
- **OB filter**: if price returns to an OB with **strong opposite momentum** and has **already rebalanced the
  inefficiency** in front of it, the OB is likely to **fail** (nothing left to fund the move). [04:33-05:38]
- The best OB entries often **fill the inefficiency first, then tap the OB** ("cleared out the imbalance, then tapped
  the order block"). [10:09-10:29]
- Inefficiency also measures volatility; higher-TF gaps can take a very long time to fill. [12:05-12:15, 13:47-13:58]

## 4. Entry types (1.0: "Entry Types", 22 min) -- GBPUSD 4H -> 1H -> 15M walk-through
- **Top-down**: 4H trend + 4H order blocks (points of interest, POI) -> refine on 1H -> refine on 15M. [03:25-07:26]
- **Risk entry**: buy/sell **limit on the lower-TF (15M) refined OB inside the higher-TF POI**, stop beyond it.
  Best when: **with the trend**, price returns to the POI **correctively (slowly)**, the 15M OB sits inside the
  higher-TF POI. Con: little confirmation, more stop-outs. [00:15-01:46, 10:47-11:22]
- **Confirmation entry**: when price **taps the POI**, drop to the lower TF and **wait for a break of structure in the
  trade's direction**, then enter (limit) on the **newly created order block** -- he uses the **most recent** one,
  stop beyond it **+1 pip for spread**. Best when: price returns to the POI **impulsively / with strong opposite
  momentum** ("catching a falling knife"), counter-trend, several POIs, or a large gap between the HTF POI and the
  LTF refinement. Con: misses trades that tap and run without a retest. [01:46-03:19, 07:26-08:26, 19:22-19:42]
- **Approach momentum is a filter**: impulsive approach into the POI = low probability (the confirmation rule
  filtered out the failed upper block); **corrective** approach = higher probability. [08:42-09:08, 21:51-22:13]
- **Invalidation**: price breaking through the POI (beyond the OB) kills it. [10:12-10:16]
- **Structure method used here: type 2** (wick mapping, body-close breaks); a wick below a low = liquidity purge,
  not a lower low. [12:47-13:09]
- **Management**: stop trailed below each new 15M higher low; a lower-TF break of structure against the trade at an
  opposing POI = take profits / exit; **target = the opposing higher-TF order block / POI**. [15:42-18:42]
- **Reversal at the target**: the opposing 4H POI (built after an equal-highs sweep) gave the next trade -- a
  confirmation short after a 15M break down, entry at the most recent OB. [18:42-20:32]

## 5. Putting it all together -- GBPJPY buy (1.0, 23 min): the full process on one trade
1. **Weekly**: uptrend, price reacting off a (refined) weekly OB -> "retest of the weekly highs is highly likely". [00:07-01:07]
2. **Daily**: the weekly pullback shows as daily lower lows; daily now turning up (HH/HL) with a **daily OB** where
   the higher low is expected. [01:07-02:02]
3. **4H**: still down -- that is the daily pullback; it will turn up once the higher low forms. [02:06-02:43]
4. **1H**: the **first timeframe to break structure UP** -> its OB = the point of interest (18 pips). [02:47-03:37]
5. **15M**: same break, refined OB inside it (~9 pips). [04:02-04:58]
6. **5M confirmation**: after price taps the 1H/15M OBs, a **5M break of structure up** -> 5M OB (~5 pips) -> buy limit
   just above it, stop just below. [06:02-10:24]
7. **Targets in steps**: 1H high -> 4H high -> daily high -> **weekly high** (= 81.8R from a 5-pip stop). [05:22-05:43, 10:30-11:03]
8. **Management**: after each **body-close** break of a 15M/1H/4H high, trail the stop below the **prominent** low that
   made it (wick breaks don't count -- "waiting for breaks with candles is much more effective"); a multi-day
   pullback with a lot of open profit -> tighten under the nearest low. Locked 14% -> 28% -> 44% -> 61R, target hit at
   81.78R after ~17 days. [11:30-20:16]
9. A pullback on the 1H/4H is judged against the **higher timeframes**: daily/weekly still up = pullback, not
   reversal; it typically refills the **inefficiency** left by the impulse. [15:07-17:40]
10. A later 1H OB inside the move = **scale-in** opportunity, same logic. [20:33-22:16]

## 6. Phantom 2.0 -- Zone creation & mapping framework (theory, 19 min)
- Idea: institutions can't fill their size at once -> price **returns to where they entered** (supply/demand zones). [04:05-06:12]
- **Two zone types** [06:18]:
  - **Range-created**: a sideways range (**4-6 candles, never > 8** -- otherwise go up a timeframe), zone = range
    **highest high to lowest low**, valid only after a **rapid expansion that breaks out of the range**. [06:28-07:39, 13:33-15:33]
  - **Pivot-created**: a sharp move one way followed by a rapid expansion the other way; zone = the **pivot candle(s)**
    (the last opposite candle(s) before the expansion -- one or several) = the order block idea. [11:42-12:28, 15:41-16:47]
- **Refinement**: a range zone can be refined to the pivot candle(s) inside it (last opposite candle(s) before the
  breakout). [16:47-18:29]
- **Validity = how price leaves**: a **rapid, significant expansion** = high volume / institutional footprint = valid;
  a slow **trickle** out, or a breakout that **immediately retraces**, = low volume = no zone. [08:39-09:56]
- **Never trade the breakout itself** -- mark the zone and **wait for price to return** to it. [08:29-08:39, 11:11-11:42, 12:28-12:43]
- Ranges also form from **low volume** (NY close -> Asia); the expansion test separates them. [07:39-08:19]
- **Application (2.0 lesson 2, USDJPY 15M)**: three valid drawings of one zone -- full range (e.g. 12 pips), the pivot
  candles (11.5), or the **last opposite candle where momentum began** (4) -- refined for RR, wider when waiting for
  a higher-TF return + lower-TF entry. [06:30-07:51] **Include the adjacent candle's wick** if it extends the zone, since
  stops go beyond the zone ("it only adds ~1.5 pips"). [09:23-10:20] The **sharper the expansion, the more likely a large
  order** -- not all moves are equal. [15:30-15:51] Many raw zones fail; the filtering rules come in later lessons. [12:05-12:26]

## 7. Phantom 2.0 -- Continuation & reversal models (theory 12 min + application 15 min)
- **Continuation zone**: the zone forms **in the direction of the move before it** -- up move, range/pivot, up expansion
  (rally-base-rally) = continuation demand; down-base-down = continuation supply. [Theory 00:26-02:33]
- **Reversal zone**: the move before it was the **other way** -- down move, range/pivot, up expansion (drop-base-rally) =
  reversal demand; up-base-down = reversal supply. [Theory 02:42-04:32]
- Same three drawing options as before (whole range / pivot candles / single last candle); one candle on a higher TF
  is a range or pivot on a lower TF -- the models are fractal. [Theory 04:47-11:12; App 04:20-04:45]
- **Testable claim: reversal models are the higher-probability ones**; continuation zones work but are often
  "liquidity building", and **each further continuation zone in the same leg is less likely to hold** ("the further
  up an up move ... the less likely"). Build ideas around the reversal, then the *first* continuation. [App 13:11-14:37]
- Almost every zone gets *some* reaction; the job is a probability model: does the zone hold given the zones around
  it and the higher-TF trend? A huge zone with a small reaction -> think continuation inside it instead. [App 07:21-09:07]

## 8. Phantom 2.0 -- Return-to-zone considerations (application, 14 min; theory being re-transcribed)
- **How price comes back matters most**: a **corrective / rounded return** into the zone = higher probability; a
  **V-shaped (impulsive) return** = the zone usually fails or only gives a small reaction ("I've had more losses on
  aggressive V returns than on corrective returns"). [00:35-02:54, 11:33-11:49]
- We judge zones by **follow-through**, not by the first reaction (almost every zone gets one). [01:55-02:40]
- The **departure** must be aggressive (expansive, displacement); a corrective departure = no institutional footprint.
  [06:55-07:41, 09:02-09:17]
- Combination seen on the chart: **continuation zone + V return -> failed (three times in a row)**; **reversal zone +
  corrective return -> respected**. [12:14-12:56]
- Ranges in the **Asian session** = low volume -> weaker zones. [05:38-06:22]

## 9. Phantom 2.0 -- Fractal supply/demand refinements (theory, 11 min)
- **Inside bar** (candle inside the previous one, then broken) = a **range on the lower timeframe** -> a zone (continuation
  if in the trend direction). Usable without dropping to the lower TF. [00:58-04:12]
- Small pivots made of wicks/bodies on a higher TF = continuation pivots on the lower TF. [04:20-05:49]
- **Large-wick zone candle**: refine the zone to **the wick only** (inside it the lower TF shows the real zone); the
  whole candle is still valid, just wider. [05:49-09:12]
- Combine with the return type (rounded / corrective / V). [10:57-11:10]

## 10. Phantom 2.0 -- Market structure basics (theory 15 min + application 12 min, EURUSD)
- Uptrend = HH/HL; a **complex pullback** (substructure) sits between the swing low and swing high; a higher low is only
  **confirmed when the high breaks**; the trend holds until the protected low is broken. [Theory 00:42-02:51, 06:52-07:33]
- **The higher low = the origin of the move that broke structure** (lowest point before the breaking leg). [Theory 02:51-03:28]
- **Breaks need a close**: a wick through the lower high without a close leaves the downtrend intact. [App 04:05-04:23]
- **Structure + supply/demand**: uptrend = **demand respected, supply failing**; downtrend = supply respected, demand
  failing; both respected = a fight -> wait. [Theory 08:56-15:19; MTF App 26:34-26:53]
- Higher-TF trend sets the side: trade **demand zones with a bullish higher TF** (short-term counter trades allowed,
  but longevity is with the trend). [Theory 08:13-08:39]
- Again: **reversal zones held, continuation zones (four in a row) reacted but failed**. [App 07:34-08:07]
- Zones at the **extreme / origin** of a range hold better than minor ones mid-range. [App 09:42-09:53; MTF App 11:26-11:36]

## 11. Phantom 2.0 -- Multi-timeframe market structure (theory 10 min + application 30 min, EURUSD)
- His timeframes: **daily (narrative), 4H + 15M (structure), 1M (execution)**; weekly/monthly for context; pick a few
  and stick to them. [Theory 05:54-06:49; App 24:37-25:05]
- A higher-TF pullback = lower-TF **lower lows/lower highs**; the lower-TF lower low **is** the higher-TF higher low;
  trends **change on the lower TF first** (4H/15M change of character before the daily). [Theory 01:22-02:16; App 11:58-12:42, 25:27-25:38]
- **Don't sell just because the 4H turns bearish while the daily is bullish** -- in that case supply gets "totally ripped
  through". [App 08:01-09:01]
- **Each extra tap weakens a zone** ("the more taps ... the weaker"); a zone tapped twice gets absorbed -> free fall. [App 13:21-13:36, 20:32-21:00]
- **Equal lows are liquidity, not valid structure**. [App 07:31-07:48]
- Not in the lessons we have (referenced as later videos): supply/demand **flips**, **liquidity** details, the 2.0
  entry model -> we use the 1.0 entry rules (section 4) for execution.

## 8b. Return-to-zone considerations -- theory (2.0 lesson 5, 11 min; re-transcribed with the medium model)
- Three ways price returns: **rounded** (slow curve back in -- "very high probability"), **corrective** (stair-steps
  absorbing opposing mini-zones on the way -- high probability), **V-reversal** (immediate sharp return -- "very risky",
  "statistically don't play out"; stay out or wait for a newly created zone with a better return).
- Exception that is **even better**: a rounded/corrective return whose **last leg into the zone is impulsive** (large
  candles -- a "sucker's rally" that lures breakout traders) -> often the most aggressive move away.
- How the zone was created matters, but **how price returns matters even more**.

## 12. Mechanical rulebook (what gets coded) -- every line traced to a section above
1. **Bias**: daily structure (wick mapping, body-close breaks = his type 2 [4]) -> trade only its direction [1, 10, 11].
2. **Zones (points of interest) on the 4H** [4, 11]: **pivot/OB zones** = last opposite candle(s) before an impulsive
   move that **breaks structure**, refined to the candle before the first full-bodied engulfing candle [2, 6]; **range
   zones** = 4-6 (max 8) candle range left by a rapid expansion [6]; include an extending neighbour wick [6].
3. **Zone quality features (recorded, then tested)**: reversal vs continuation [7]; departure strength / imbalance left
   [2, 3, 6]; return type rounded / corrective / V, impulsive last leg [8, 8b]; number of taps [11]; extreme vs
   mid-range location [10]; zone width; Asian-session range [8]; inefficiency already filled in front of it [3].
4. **Entry** [4]: *confirmation* -- price taps the 4H zone -> 15M break of structure in the trade direction -> limit at
   the newest 15M OB, stop beyond it + liquidity wick + 1 pip; *risk* (variant) -- limit on the zone itself.
5. **Invalidation** [4]: price closes through the zone before the entry.
6. **Exits (all recorded)**: fixed R; the previous 4H swing high/low (liquidity) [2, 4]; the opposing 4H zone [4];
   structure trailing under each new 1H prominent low after a body-close break [5].
7. **Not coded (no rules in our lessons)**: supply/demand flips, liquidity sweeps beyond equal levels as triggers,
   Wyckoff schematics.

## 13. Results -- 3-year setup database (Oct 2023 -> Oct 2026, EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD)
`python -m bensim.phantom` (engine) / `python -m bensim.phantom_report <csv>` (rule report). 4,516 zones (4H + 1H),
~1,050 confirmation trades (4H zone -> 15M break, 1H zone -> 5M break, limit at the newest order block; median stop
~4-5 pips) and ~4,500 risk-entry trades (limit on the zone). All R net of costs; discovery = created < 2025-07-01.

| Discovery (2,545 zones) | conf 2R | conf 3R | conf trail | risk 3R | risk trail |
|---|---|---|---|---|---|
| all zones | -0.20 (t -3.4) | -0.23 (t -3.2) | -0.16 | -0.13 (t -3.8) | -0.15 |
His filters (kept vs dropped) did **not** create an edge: reversal zones, slow / non-V / corrective returns, impulsive
last leg, daily-trend agreement (4H-trend agreement even hurt), imbalance, zone size, Asian session -- every kept group
is still negative, and most differences are inside the noise.
**One-time confirm** of the best rule-based slice (his Entry Types method: 4H zones + 15M confirmation): discovery
64 trades +0.28R (2R) -> **confirm Jul 2025 - Oct 2026: 59 trades -0.15R (2R) / -0.22R (3R) / -0.47R (trail)**.
Verdict: as mechanical rules, Phantom 1.0 + 2.0-core has **no edge after costs** on these pairs. Tiny 5M stops reach
3R ~23% of the time but rarely survive to the 1H trailing stop; costs take ~0.2R per trade. Not coded (no rules in the
lessons used): supply/demand flips, liquidity lessons, Wyckoff.
