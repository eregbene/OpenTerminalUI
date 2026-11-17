# ICT folder videos (2026-11-09) — notes, rules, liquidity

Source: `C:\Users\Intel\Downloads\ICT` → copies, audio, transcripts and 15-s frames in `D:\trading\ict_work`
(transcripts: CPU faster-whisper small.en; frames checked by hand as contact sheets in `sheets/`).

| # | Video | Length | What it is |
|---|---|---|---|
| 1 | Jesse Rogers (Casper Trading) — *Learn ICT Concepts in 9 Minutes* | 9 min | a complete, codeable ICT model (NQ futures) |
| 2 | IQCapital — *Trading World Champion… Robbins Cup orderflow* (Chris Creamer) | 58 min | futures (MNQ) orderflow: environment → location → confirmation; needs volume profile, delta, footprint, GEX |
| 3 | Craig Percoco — *Give Me 25 Minutes…* | 25 min | process/mindset, no setup |
| 4 | Chart Fanatics — *The SIMPLE 10 Million ICT Blueprint* (8-figure crypto/FX trader, co-founder of Breakout) | 58 min | the clearest mechanical ICT framework: HTF range + order block + LTF breaker |

---

## 1. Jesse Rogers — the model (every rule he states, with timestamps)

**Liquidity (00:45–01:34)**
- Price always moves toward liquidity = where the most stop losses are; large participants take retail stops to fill.
- Two types: **external = swing highs/lows (ERL)**, **internal = fair value gaps (IRL)**.
- FVG = big candle leaving a gap between the wick of the candle before and the wick of the candle after.
- **Price moves like a magnet IRL → ERL → IRL**: after reaching a swing point, a NEW FVG gets traded back into, then
  price goes to the next swing point.

**Market structure (01:50–03:21)**
- Swing high/low = 3-candle fractal (lowest low with higher lows on both sides; mirror for highs).
- **Manipulation vs displacement:** a wick through a low without an energetic close beyond it = manipulation → likely
  reversal. A real break needs **displacement: an FVG and a close beyond the level**.
- **MSS (market structure shift):** from lower highs/lows to a TRUE new higher high = a close beyond the swing high
  **with an FVG**. Use it together with liquidity or a key-level tap.
- **Target trick (liquidity):** when a low is manipulated (no displacement) and a reversal forms, target **the high that
  failed** to push price lower ("its job was to make a lower low").

**Market maker model (03:23–05:08)** — HTF FVG → LTF MMM
- HTF: price trades into an HTF FVG and then to liquidity. LTF: **two or more consolidations** on the way into the HTF
  FVG (early traders get stopped under them = liquidity).
- At the HTF FVG: **manipulation then displacement**, leaving a new LTF FVG → entry; **target = HTF swing point** (IRL→ERL).
- Once active: every new dip under a low = likely manipulation (late entries / trail stops).

**Power of three (05:09–05:53)** — accumulation → stop run → reversal. Bullish: wait for the range lows to be run,
then an entry pattern back into the range.

**Entry rules (05:57–08:47)**
- **No key level tapped = no trade.** Key level = HTF FVG or HTF swing point (ERL); two key levels together = better.
- Liquidity taken WITHOUT displacement (no FVG) → expect a return to the high that failed to make a new low.
- **Entry model 1 — inverted FVG (iFVG):** at the key level, a bearish FVG on the way down gets a candle CLOSE through
  it → enter on that close; **stop at the lowest low made**; target the HTF level; **min 2:1 RR**.
- **Entry model 2 — CISD (change in state of delivery):** the up-close candles finally engulf the run of down-close
  candles (close above the open of the last down-close series) → enter.
- **Entry model 3 — MSS + FVG:** the high is taken with an FVG through it (market structure shift) → enter.
- All: enter when the pattern confirms, **stop at the lowest low**, **target the HTF high**.

**Manual frame re-check (2026-11-09, sheets/jesse_0*.jpg) — on-screen text the transcript missed / I summarised wrong:**
- Entry model 3 on screen = **"MSS + FVG Return: structural break, retrace into imbalance"** → the entry is a
  RETRACE INTO the new FVG after the break (limit at the gap), not at the breaking close.
- CISD on screen = **"liquidity sweep → engulf → entry"** → a sweep is required before the engulf.
- iFVG on screen = "opposing FVG inverted"; his example position: stop under the lowest low, target the prior swing high.
- Market structure slide: "MSS after liquidity or Key Level Tap"; "Manipulation vs Displacement (weak sweep vs energetic break)".
- MMM diagram: HTF FVG → LTF steps down in 2 boxed consolidations → wick under the last one into the HTF FVG → LTF
  FVG forms on the way up → entry there.
- Journal shown (01 May 2025): 4 trades, 3 wins, net $41,940, commissions $40.50 — one day, not a track record.
- ictbp only used Chart Fanatics' breaker entry; **Jesse's three entry models (iFVG / CISD / MSS+FVG retrace) at an
  HTF FVG were NOT tested yet.**

**Timeframe alignment (08:47, frame)** key level → entry TF: Weekly→4H, Daily→1H, 4H→15m, 1H→5m, 15m→1m.

**Liquidity notes (all):** stops cluster at swing highs/lows; FVG = internal liquidity; IRL→ERL magnet; a sweep without
displacement = manipulation (reversal), with displacement = real break; the failed high/low is the target.

---

## 2. IQCapital / Chris Creamer — orderflow (futures)

He trades **MNQ 5-minute**, mostly the **first 90 minutes of the New York open** (sometimes Asia). 1.5–2R, win rate
60–65%, profit factor ~1.8 (his numbers).

1. **Environment (before the open):** H1/H4 structure — value-up / value-down / sideways (higher highs + higher lows =
   value up). **GEX** (gamma exposure, naive, from QQQ/NDX options): positive gamma = dampened volatility (failed
   breakouts), negative = amplified. Note the call wall, put wall, gamma flip.
2. **Location:** don't trade inside value. Trend-following only: in value-up, buy only in **discount** = below the value
   area low, AND inside **Fib 70.5 / 78.8 / 88.6 %** of the last swing low→high (the zone must be OUTSIDE the value
   area); an internal swing (sweep) should form before price enters discount. **Below 88.6% = no trade.**
   Inefficient (fast, low-volume node) moves get revisited.
3. **Confirmation (5m):** aggressive sellers (negative delta, POC/volume at the lower wick) in discount with no result
   → candle closes bullish (absorption) → next candle pulls back, sellers try again and **fail higher** → dominance flips
   → **enter long**. **Stop beyond the failed sellers' low.**
4. **Management / target:** buyers must reclaim the value area; if they can't → break-even or cut. Target the **swing
   high** (orders cluster at swing points / round numbers), sometimes the POC; trail behind buyer aggression. Usually
   1.5–2R.
- Filters: participation **≥ 20,000 contracts per 5-min MNQ candle**; trade ~90 min only; **stop after 2 losses in a row**;
  no trading before a big news event.

**Liquidity notes:** trapped participants (wrong side, forced to exit) fuel the move; swing highs/lows and round numbers
= where orders cluster = targets; value-area edges are where participation is forced.

**Testability on our data:** delta, footprint (bid×ask), volume profile and GEX are NOT in our FX/MT5 bars. Only a
**candle proxy** is possible (structure + fib 70.5–88.6 + "two failed pushes" on M5 + swing-high target, NY 90 min).

---

## 3. Craig Percoco — process
Establish rules → test them → evaluate (win rate × R:R table), keep it simple (Occam's razor), the work loops.
No setup to code; it's how our research already works.

---

## 4. Chart Fanatics — "ICT Blueprint" (the framework, with timestamps)

**Timeframes (05:27–06:39):** at least two. HTF analysis → LTF execution: **Weekly → Daily (or H12)**,
**Daily/H12 → H1**, **H4 → M15**, scalpers H1 → M5. A third agreeing timeframe = higher probability (31:42).
Trade **trends only** (higher highs + higher lows, or the inverse); ranges/consolidation are not the setup (04:13).

**Structure (07:05–08:38):** swing = **3-candle fractal**. **Market structure break (MSB) = a candle CLOSE through the
swing high.** After an MSB, expect the trend to continue: the job is to find where the pullback ends (the higher low).

**POIs (11:16–13:19):** order blocks, breaker blocks, FVGs (ICT terms); often 2–3 overlap. He uses **OBs/breakers**.
- **Order block (19:59–21:30):** the **down move (all its down candles, or just the last one) right before the up move
  that broke structure**. "The selling that occurred before the buying that took out the high." Precision doesn't matter
  — it's an HTF zone you zoom into.

**Liquidity (13:36–16:29):**
- **External range liquidity (ERL)** = swing highs/lows OUTSIDE the current trading range.
- **Internal range liquidity (IRL)** = a POI (order block) INSIDE the range.
- **Draw on liquidity:** price ping-pongs ERL → IRL → ERL → IRL. After a swing low is run, the draw is the next swing
  high, and vice versa. Longs filled at the low are sold "way up there".
- Engineered liquidity (27:42): inside the POI, wait for an LTF swing low that early buyers trust (their stops sit under
  it) — **it must be taken out first**.

**Premium / discount (16:30–17:55):** 50% of the range. **Buy only in discount (lower half), sell only in premium.**
Fib 61.8/70.5/78.6 (OTE) optional — "discount is just the lower half".

**The framework (19:00–23:06):**
1. Uptrend: a high gets broken (MSB). The **trading range = swing low → the swing high formed after the MSB**.
2. Mark the **order block that caused the MSB** (it sits in discount, below 50%).
3. The next draw is that OB (IRL). (LTF shorts down into it are allowed, 22:26.)
4. In the OB, expect the **higher low**; target = **the old swing high (ERL)**. Repeat.

**Entry (24:09–31:16) — the LTF breaker:**
- Option A: just enter at the top of the HTF OB, stop under it, target the HTF high (wide stop).
- **His setup (LTF, e.g. H1 inside a daily OB):** an LTF swing low forms → **that swing low gets swept** (engineered
  liquidity taken) → **the high that produced the swing low gets broken (close)** → the failed bearish OB = **bullish
  breaker** → entry. **Stop below the sweep low** ("if this low gets taken out I'm wrong anyway"; under the breaker
  gets tagged too often). "Double confirmation" = two structure breaks = better. A CISD or an inverted FVG are earlier,
  cheaper-RR alternatives.
- **Minimum 2:1 R:R, else no trade**, even if everything else ticks (33:29). Breakeven math: 2R needs 33% wins.
- **Management (31:21):** ideally hold to the HTF high (ERL); often **at 2R take half or move the stop to break-even**.
- Several attempts in the same OB are fine, each needs a new sweep + break (34:42).
- Timing (35:16): matters for intraday (New York open move); less for HTF swing trades.

**Examples (45:00–58:00):** bearish 2-day OB after ERL was taken, stop above the swing high at the OB, target the new
low; monthly range with the meaningful swing low → OB retest → "stop below, target the external"; H1 breaker inside an
HTF OB after a sweep; "breaker plus the FVG".

**Liquidity summary (all four videos agree):** stops = swing highs/lows (ERL); POIs/FVGs = IRL; price goes IRL → ERL;
the swept low that early longs trusted is the fuel; a sweep without displacement reverses, a close with an FVG breaks.

---

## Manual frame re-check of the other 3 videos (2026-11-09)
- **Chart Fanatics:** whiteboards match the notes (MSB → OB in discount below 50%, 61.8/70.5 marks, H1 breaker "BB"
  inside the daily OB, "H4/M15", "2:1 = 33%", ERL/IRL). Extra on screen: pros/cons board "Trust the W/D/H1 process"
  with all three arrows up (3 aligned TFs), "Not 100%"; examples are crypto (BTC $93k short, SOL); one chart caption
  "...is clear, reclaim the y/o and the point of breakdown" (yearly open). Nothing that changes ictbp.
- **Creamer:** board = notes (environment staircase, GEX +/−, location box with 705/788/886, call wall / put wall,
  absorption footprint candles, "$2,000 drawdown / $3,000 target" prop example). 20k contracts/5-min confirmed in audio.
- **Percoco:** slides add risk rules, no setup: his example rules (buy at moving average, stop below candle, target 2R)
  → win-rate × R table; Replay → Paper → Live; "Goal risk per trade = $ goal per month ÷ your measured average R per
  month"; percent-based scaling; **size risk so your longest losing streak (last 30 trades) never trips a prop rule.**

## Combined strategy to test ("ictbp", frozen before the test)
HTF/LTF = H4/M15 (also D/H1). Long (short = mirror):
1. HTF trend: last HTF close broke a 3-candle swing high (MSB). Range = leg origin low → highest high since the MSB.
2. HTF order block = the run of down candles right before the MSB leg; it must lie in discount (top below 50%).
3. Price pulls back into the OB while the range low is intact.
4. LTF (M15) inside the OB: a swing low forms → it is swept → an LTF close above the high that made that swing low
   (breaker / MSS) → enter at that close.
5. Stop = below the sweep low; target = the HTF range high (ERL); skip if < 2R.
6. Creamer's rule: max 2 losses per day.
Variants (pre-registered): (a) Jesse's key level = HTF FVG instead of OB; (b) OTE depth ≥ 70.5% of the range;
(c) half off at 2R + stop to break-even.
