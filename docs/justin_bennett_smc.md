# Justin Bennett — SMC (Daily Price Action)

Sources, and nothing else:
- 14 videos in `D:/Downloads/SMC new` (Justin Bennett, YouTube) — audio transcribed with faster-whisper (small, int8) into `D:/Downloads/SMC new/_transcripts/`, keyframes checked.
- Article: [SMC Trading Strategy — dailypriceaction.com](https://dailypriceaction.com/blog/smc-trading-strategy/).

Status: all 14 videos transcribed, read and noted (base = "SMC Trading Strategy Explained Step by Step (Full Entry Model)"); strategy in `bensim/bennett_strategy.py`.

---

## 1. The base entry model (Full Entry Model video + article)

Example in the video: EURUSD, 1H → 15M → 5M, short. Every step below is his; quotes are his words.

| # | Step | His rule |
|---|---|---|
| 1 | **Higher-timeframe context** | "Every good entry model starts with market structure." The larger trend (4H in the article; the 1H history in the video) decides which side you want. A move against it "could just be more relief before a lower high". |
| 2 | **External vs internal structure (1H)** | An **external** high/low extends beyond the internal ones and **sweeps internal highs/lows**. It is only **confirmed by a break of structure**: "an external low is not valid until a BOS confirms it". Everything between the external high and low is **internal** — "this is what I really don't care about". |
| 3 | **BOS / CHoCH need a candle CLOSE** | "Confirmed BOS here on the hourly close above. Again, **wicks do not count**." CHoCH = the external low (in an up-leg) closed below → "change of character". |
| 4 | **Fib on the displacement** | From the **start of the displacement** that made the CHoCH (the high) to the **new external low**. His fib levels (read off the settings dialog): **0, 0.5, 0.62, 0.79, 1**. |
| 5 | **Premium / discount** | 50% line. After a bearish CHoCH: "everything below the 50% line **I cannot short**". "About 7 or 8 times out of 10 you get the market pulling back to rebalance." |
| 6 | **OTE = 62–79%** of the move | "You will be amazed how often the market comes back into this area when you get a confirmed BOS or CHoCH." |
| 7 | **Areas of interest = FVGs inside the OTE** | A **1H FVG** in the OTE, then a **15M FVG** that lines up ("I never stop at one FVG"). "That gives me an area of interest, **not an entry**." |
| 8 | **Targets drawn BEFORE the entry** | **T1** = the obvious prior low on the 1H. **T2** = **beyond the equal lows** — liquidity still resting there ("markets seek liquidity"; the second low was ~0.5 pip above the first = untaken liquidity). |
| 9 | **Trigger zone** | Price trades **into the OTE** (it does **not** need to tag the FVGs) → "enough for me to start actively hunting for a short." |
| 10 | **Entry confirmation (5M)** | The pullback is an internal uptrend on 5M. Wait for a **5M CHoCH**: a 5M **close** below the 5M external low (an external low that swept internal lows and was confirmed by a 5M BOS). A **lower high before the CHoCH = "CHoCH plus"**, stronger. |
| 11 | **Entry price (the one subjective step)** | If the 5M CHoCH leg left a **15M FVG**, wait for price to come back into it (tighter stop). If it left **none** ("nothing for the market to come back and rebalance"), **enter on the 5M CHoCH confirmation** — otherwise you miss the trade. |
| 12 | **Stop = invalidation** | Above the **5M lower high** (the CHoCH-plus high): "if this high doesn't hold, the market is moving higher… my stop always goes at an invalidation level, not a random number." |
| 13 | **Minimum 3R** to T1/T2 | His example was 2.86R — accepted ("not going to stress over 0.14R"); **2.3–2.5R he would hesitate on**. |
| 14 | **Management** | **50% off at T1**, rest to T2. (Example: T1 in ~1 hour, T2 ~24 hours later.) |
| 15 | **Why entering in discount is OK here** | The OTE pullback "served one purpose — to rebalance the move" (and take the liquidity of unfilled orders); the 5M CHoCH confirms the bears are back. |

**His confluence count** (end of the video): 1H CHoCH (1) + OTE (2) + 1H FVG (3) + 15M FVG (4) + 5M internal CHoCH (5) + ≥3R (6). "The more factors in your favour, the better the trade."

### Differences from what we already have
- **vs Faiz Order Flow:** both use a structure break + FVG zone in premium/discount; Bennett adds a **lower-timeframe CHoCH trigger** inside the zone instead of a blind limit order, puts the stop at the **5M lower high** (not beyond the leg origin), and plans **two liquidity targets** with a partial.
- **vs JeaFx:** same "confirmation" idea, but Bennett confirms on **5M inside a 1H OTE**, JeaFx confirms on the same timeframe and enters at the last candle before the impulse.
- **vs the live bensim strategy:** live uses fib 0.618 + 2-of-3 (killzone / POI / sweep) on M15 with a market entry; Bennett waits for **price in 0.62–0.79** and then a **5M CHoCH**.

---

## 2. Refinements from the other videos

### 2.1 "BoS and CHoCH Trading Strategy — Wait For This Entry Signal"
- **BOS = trend continuation, CHoCH = trend reversal** ("the character is bulls vs bears"). Both need a **close** beyond the level on the timeframe you are on (daily → daily close); a wick never counts.
- **Which low the CHoCH must break — the protected low:** "take the **last BOS**, track back to the **last low** before it" = the lowest point between the previous high and the BOS high. Only a close below **that** low is a CHoCH. Inner lows are not.
- **Long from discount, short from premium (50%)** — "eliminates 70–80% of the FOMO". Context is "the biggest factor": the same LTF CHoCH somewhere random on the chart "is not going to work".
- **Fractal top-down confirmation:** HTF shift → price in HTF premium → drop one timeframe and wait for **that** timeframe's CHoCH → draw a new fib on the LTF leg → enter in LTF premium. Can be repeated (D → 1H → 5M) for tighter entries.
- **He normally uses two timeframes:** **D → 1H** or **4H → 15M**. Beginners: start with **D or 4H** as the baseline and **15M/1H** for entries — "the lower the timeframe, the more noise".
- **Also works with the trend:** after an HTF **BOS** (not only a CHoCH), wait for the pullback into HTF premium, then the LTF CHoCH.
- **Entry choice after the LTF CHoCH:** (a) enter **on the CHoCH close** (when there are no inefficiencies left above to rebalance); (b) wait for price to come back up into the **inefficiency / LTF premium** — better RR but you may miss it; (c) his compromise: **half size on the close, half at the inefficiency**. Price often only reaches the **50%** after aggressive selling.
- **Stop above the LTF swing high**; "rules are rules" — a trade can sit underwater as long as the invalidation high holds.

### 2.2 "Fair Value Gap Secrets I Wish I Knew as a Beginner" — the 4 FVG rules
- **Definition:** 3-candle pattern; a gap away from **fair value** (consolidation = equilibrium). It only exists once the **3rd candle has closed** — "a lot of traders look at the candle while it's forming".
- **Why price returns:** market makers need liquidity to fill large orders; orders left unfilled in the fast move sit there → "markets are liquidity-seeking machines". Imbalance (supply vs demand) is the cause, inefficiency the effect.
- **Rule 1 — with the trend** (a CHoCH starts a new trend). "Rowing downstream."
- **Rule 2 — it must be in the leg that triggered a BOS or CHoCH.** FVGs formed without causing a break are **internal FVGs — "that's not what we want"**. (The break doesn't have to come immediately on the FVG, but within that leg.)
- **Rule 3 — in discount for buys / premium for sells**, measured on the **current leg: external low → the new high** (only once a pullback shows that high is the high). "Even a perfect FVG up here I cannot trade because we're still in premium." Lower is better ("typically 50% or lower").
- **Rule 4 — one-time use.** Once tested/mitigated it is no longer valid (the resting orders have been filled; traders get "induced").
- His 1H fib here: **0, 0.5, 1 only**, discount/premium shaded.
- **Area of interest = the overlap of discount + 1H / 30M / 15M FVGs**; on a 1H setup the window should be **~10–15 pips** — 20–30 pips is "too wide" (a LTF CHoCH inside a wide window gets stopped).
- **Entry = a lower-timeframe CHoCH inside the area** — never blind. **15M is less noisy than 5M**: in his example the 5M CHoCH long lost, the 15M never triggered.
- **Red flag:** at the area, price behaves differently from the previous legs (slow chop, lower highs instead of an explosive reaction) → stop looking for buys, especially once it breaks below the area.
- **FVGs as targets** too — open FVGs in the trade's direction are places price is likely to reach. Two 3R+ wins and one 1R loss in his example leg.

### 2.3 "How I Filter Out Bad Trades (9 Simple Rules)" — his checklist
"My timeframe of choice for market structure is the **1H**, because I like dropping to a **5M** for confirmation." He answers most filters before the setup arrives.
| # | Filter | Rule | Codeable? |
|---|---|---|---|
| 1 | **Higher-timeframe structure** | 1H trend via BOS/CHoCH (closes). After a bearish CHoCH: "shorts only from this candle on". Chop (e.g. holidays) = no trading. | yes |
| 2 | **Area / location** | Short high: premium, ideally **OTE 62–79%**, where it lines up with **imbalances (FVGs) left on the way down** — "always look to the left: where is the liquidity?" | yes |
| 3 | **LTF entry trigger** | 5M uptrend inside the OTE → a 5M BOS makes a **protected low** → a 5M **close** below it = CHoCH = trigger. It also gives a place to "hide the stop". | yes |
| 4 | **Risk quality ≥ 3R** | "If I'm risking 20 pips my reward has to be 60." He takes **2.75–2.8R**, not **2.5–2.6R**. | yes |
| 5 | **Path to target** | The target must be the **logical** one (the recent low, an imbalance on the way) with nothing in the way. **Never stretch the target to an old low just to reach 3R** ("form fitting"). | yes (nearest pool only) |
| 6 | **High-impact news** | **No trade if FOMC / NFP / rate decisions are within the next few hours**; ≥ ~20–24 h away is fine (take partial + protect before it). | yes (news calendar in DB) |
| 7 | **Time-in-trade expectation** | Measure how long the recent 1H legs took from external high to low (his: 24–35 h, up to ~2 days) — don't cut a trade for not moving in a few hours. | yes (max hold ≥ leg duration) |
| 8 | Emotional state | Sick, tired, angry, revenge → don't trade that day. | no (human) |
| 9 | Clarity check | Explain the trade in one sentence: "downtrend, CHoCH, back into OTE mitigating imbalances, LTF trigger, ≥3R". | — |

Extra details: **protected low = draw a box from the last BOS back to the swing and take its lowest point**; enter **partial size on the 5M CHoCH close**, **add** if price returns to the imbalance above (about **50% of the time it doesn't**); if there are no imbalances above → full size on the close. **Stop 0.5–1 pip beyond the high** (buffer for wicks).

### 2.4 "How to Use Trend Lines With SMC (The Right Way)"
- Trend lines **supplement, never replace** market structure (BOS/CHoCH stays the foundation).
- **Valid trend line:** the market **respects** it — **no candle bodies cut through it (wicks are fine)**; **2 touches minimum, 3–4+ better** ("the more touches, the bigger the liquidity cascade when it breaks").
- **Use 1 — channels (HTF):** two parallel trend lines give structure: near the **top of the channel → look for a LTF CHoCH short**; the **bottom of the channel = take profit** for shorts / bounce zone.
- **Use 2 — trend-line break = early entry** inside a counter-trend relief move: a **close** through the line (he uses **4H**) → enter on the retest, **before** the 1H CHoCH. More risk because structure hasn't confirmed; best when the break is followed by the CHoCH.
- **Check the timeframe**: a line can be clean on 8H while a 4H candle closes through it.
- **DXY cross-check**: the dollar index is the inverse of EURUSD; if the EURUSD line is messy, confirm the break on **DXY** (4H close through its line + retest).
- **Inducement note**: a "BOS then CHoCH" that leaves **open liquidity behind it (an unmitigated imbalance)** is **inducement, not a real CHoCH** (detail in the inducement video).
- Big engulfing news candles make everything after them "internal" → no clean LTF CHoCH → stand aside or use the trend line.

### 2.5 "Inducement in SMC Explained"
- **Inducement = a LTF BOS/CHoCH against the higher-timeframe structure.** The market "engineers liquidity": buyers are induced to buy each small break, their stops stack under the lows, then price accelerates through those lows.
- **How to avoid it — "zoom out and look left":**
  1. The **last confirmed 1H shift decides the bias** (he uses **1H for directional bias**). A 15M internal shift "is not going to trump what you see externally over weeks".
  2. **Unmitigated higher-timeframe FVGs (4H / D / W) are magnets.** On HTF he marks the **extreme** of the FVG (the candle-1 high for a bullish gap) and expects price to **fully** mitigate it, not just tap it. Until it is fully mitigated, don't trade internal shifts against it.
  3. **Displacement + an unmitigated imbalance + lower highs into equal lows** = the lows are going to break.
- **Turning it into a trade (his live Discord short):** with the trend, price **sweeps internal highs** (the "external high" of the pullback), then **closes below the lows ("acceptance below")** → short = a **liquidity-sweep reversal with the trend**. Target = full mitigation of the HTF imbalance; half off at T1.

### 2.6 "Liquidity Sweep Reversals In 3 Simple Steps (No CHoCH Needed)" — his favourite reversal
Use when there is **no CHoCH nearby** (the protected low is far away). Catches the turn much earlier — "a 100-pip difference" vs waiting for the CHoCH on his GBPUSD example. Timeframe: **1H** (bias + pattern); 15M for intraday.
1. **Established low** (bullish example; mirror for tops) in an established trend — buyers buy it and put their **stops below it**.
2. **Liquidity sweep** — price **wicks below that low but does NOT close below it**. Size doesn't matter (**1.5 pips to 57 pips** both qualified); a close below "changes things".
3. **Displacement confirmation** — a **close above the high that triggered the sweep** (the last high before the down-leg into the sweep), with a strong push out of the range. Even a **2-pip close** counts if there was displacement and a higher low.
- **Entry:** on/after that close — **pullbacks are usually shallow** ("large buyers won't let it come back to the lows"), so don't wait for a deep retrace.
- **Context:** best after an established trend (end of a down-trend) **or with the HTF trend** — a LTF down-move sweeping a low **inside the HTF OTE + FVG area of interest** (his EURUSD example).

### 2.7 "Most SMC Traders FAIL Because They Skip This" — internal vs external
- **External range:** after a CHoCH/BOS, the low that started the breaking leg = **external low**; once an **exaggerated pullback** starts, the high = **external high**. Everything between them is **internal** (IBOS / iCHoCH).
- Internal/external is **relative to the timeframe** (1H internal = 15M/5M structure; weekly internal = 4H).
- **Trading the external range** (CHoCH/BOS → buy pullbacks into the imbalance): **more reliable but few setups** (~2 in a few weeks per chart).
- **Trading internal structure** gives more setups, but **only with the external trend** — internal counter-trend trades "can get you in trouble". Labels must be exact, not gut feel.
- His framing: **4H/1H = external context**, **15M internal BOS/CHoCH** for the trades.

### 2.8 "SMC High Time Frame Directional Bias Simplified" — weekly + daily
- "Less is more." Many stop-outs come from ignoring the HTF. Map **weekly + daily** once; it changes only every few days (daily) or months/years (weekly).
- **Find the last TRUE weekly BOS — look far left** (can be years back). External high = the highest high **boxed between that BOS's low and the BOS bar**; external low = the low of the pullback after it. Everything inside = **internal** (EURUSD's multi-month "uptrend" was internal to a weekly downtrend, and price sat in the **weekly OTE 62–79%**).
- **Daily:** each daily BOS has an external low (the lowest point of its range); as long as **no daily close below** those lows, the daily trend stands. A close below an **internal** low = internal CHoCH; a close below the **last external low** = **external CHoCH** = the real shift.
- **Why it matters on 1H/15M:** a daily external CHoCH **inside weekly OTE** opens far targets (weekly FVG / channel bottom, "hundreds of pips"); **don't buy** LTF setups against that.
- **Markets love symmetry / mean revert** (channels, repeated "three taps").
- Even after an HTF shift: **don't chase** — wait for the pullback into premium of the displacement leg, then the LTF trigger.

### 2.9 "SMC Market Structure: The Top-Down Strategy Most Traders Get Wrong" — weekly → daily → 1H → 5M
- **A market is bullish AND bearish at once** — always attach a timeframe ("bearish" without a timeframe means nothing).
- **How much of a close counts (new, concrete):** on the **1H**, a close beyond the level of **0.2 pips is NOT enough** — "at least a couple of pips, ideally **3–5 pips minimum**". On the **5M, ~1 pip is enough**. On the daily his CHoCH closed **55–60 pips** below → valid even though the next day closed back above ("acceptance below"; it is not a "sweep").
- **External low after a BOS = the lowest point between the previous high and the BOS bar** (the box method again).
- **"Bearish is not sell."** The CHoCH/BOS candle is **not** the trade: wait for the relief into **premium** (mitigating the imbalance the displacement left), then the LTF CHoCH.
- **OTE/premium only from external high → external low** — "if you do this internally it won't matter a whole lot".
- **His full alignment example (EURUSD short):** weekly BOS down + price in **weekly OTE** + top of the weekly channel → **daily bearish CHoCH from inside weekly OTE** → daily relief into daily premium/OTE → 1H internal BOS, then **1H iCHoCH** (relief) → **5M iCHoCH** = exact entry.
- Mostly he uses **1H + 5M**, after a quick weekly/daily mark-up (TradingView: set each level visible only on its own timeframe).

### 2.10 "Weekly SMC Outlook DXY, EURUSD, GBPUSD, AUDUSD, XAUUSD (Jan 12–16 2026)" — how he applies it live
- **DXY as the referee for marginal breaks:** EURUSD closed only **~1 pip** above a high (normally not enough) — he still counted it as a **BOS because DXY (the inverse) showed full acceptance below its matching low** on the 1H. So: when a EURUSD/GBPUSD break is marginal, **check the DXY**.
- **A valid external high/low needs two things: (1) a BOS that confirms it and (2) it swept internal liquidity** first. "This is textbook."
- **Distribution channel = early warning before a CHoCH:** in a trend, price distributes on both sides of a midline inside a channel; **acceptance (a close) outside the channel's far side** signals a reversal is coming — the 1H CHoCH came later.
- **HTF FVGs often fill completely** (4H/daily): mark the extreme of the gap (the candle-1 high/low) — his 4H example bounced exactly off both extremes.
- **Ideal sequence (GBPUSD/AUDUSD plans):** wait for the **1H close below the external low** (e.g. "we have to get a close below 1.3400") → then a **retrace up into OTE where a 4H imbalance lines up** → LTF CHoCH → short; targets = the daily FVGs/displacement candles below. "I'm not going to chase", "not going to buy high".
- **Gold (with the trend):** after a 1H BOS, wait for the pullback into the **OTE + imbalance**, and **want the internal lows below swept** before a LTF buy trigger.
- **Context notes:** holiday / start-of-year price action is choppy → less interest; **AUD and NZD are risk-sensitive** (follow stocks); monthly key levels (e.g. 50% of a monthly wick, single prints) act as long-term magnets.

### 2.11 "Weekly SMC Outlook (Jan 19–23 2026)"
- **DXY is his barometer** — "if you can nail this chart you can navigate the rest of the FX market". He analyses DXY first, then EURUSD/GBPUSD as its inverse.
- **A CHoCH needs only acceptance, not a monster candle**: "all you need is this" — a clean close beyond the protected level (external high/low that was confirmed by a BOS **and** swept internal liquidity).
- **The recurring reversal pattern = sweep + acceptance** (= the liquidity-sweep reversal, 2.6): low swept → close above the high; top: high swept → close below. Seen on 4H, 1H and 15M (his live DXY bottoms). **"Context is key — if you go hunting for the pattern, you're not looking at context first"**: it must line up with an HTF imbalance / key pivot and the bias.
- **Liquidity, not levels:** "it's not testing a level… it's always about liquidity" — the retrace goes into the **4H imbalance**, the reversal sweeps **equal highs** (15M EQH were his short trigger area), and price later taps the **new imbalance** the acceptance candle left; then a 5M shift = entry, stop above the highs.
- **Distribution channel acceptance can veto an OTE trade**: if price closes below the channel, he would **not** buy the OTE below — that acceptance often means at least a short-term reversal.
- **Bias stays until the protected high/low breaks**: "bearish as long as we're below this external high". Gold: bullish → buy pullbacks only, **at minimum below 50% of the external range**, then a LTF CHoCH, target the highs. AUD back in chop → skip.
- 15M **order block + FVG** stacked in the retrace = his short zone for EURUSD.

### 2.12 "Why Your SMC Trades Fail (And How To Fix It)" — when NOT to trade
- **The BOS/CHoCH candle is never the trade** — it only tells you who is in control. From the break up to the **imbalance it left behind is a no-trade zone**; the chop in between is "exactly where traders get chopped up".
- **4H FVGs usually get fully mitigated** (price runs to the far extreme of the gap) → wait for that, then the **15M BOS → 15M CHoCH** → short into the 15M FVG of that CHoCH leg, stop above the high; his example paid **7.5R** (T1) / 5R+.
- **After a 15M loss: stand aside and require the 1H** to confirm before trying again — "a 1H candle has ~4x the volume of a 15M candle; volume = votes = conviction". His loss was on an **NFP** day.
- "Discipline" = **rules + a checklist** you follow; "chasing discipline alone is chasing a ghost". Losses are fine with ≥3R winners (one 7R winner covers ~7 losses).

### 2.13 "Why Your SMC Trading Is Inconsistent (And How To Fix It)" — apply the concepts in order
1. **4H external structure** — BOS + external high that swept internal liquidity → "as long as we're below it the market is bearish, no guessing".
2. **1H**: after the BOS, don't short the low — wait for **premium (above 50%)** of external high → low; everything above 50% is "fair game".
3. **15M**: walk the internal structure up (each IBOS defines a protected low) → the **close below the protected low = the trigger**. A **2.3-pip close was enough** on 15M ("it's all you need") — **because the confluence is stacked**; a random 15M CHoCH "and you are going to lose".
4. Entry (he waited a little for the imbalance), stop above the highs, far target; descending **channel** = entries at the top, profit near the bottom.
- Only liquid markets (majors, gold, indices, BTC/ETH).

---

## 3. From notes to code — `bensim/bennett_strategy.py` (`--strategy bennett`)

| His rule | Code |
|---|---|
| 1H directional bias = last 1H BOS/CHoCH, **close-through only** | `h1_leg()`: LuxAlgo-style swings on 1H (`h1_len`), break needs a close ≥ `break_atr` × ATR(14) beyond the level (≈ his 3–5 pips; 0.2 pip doesn't count) |
| Fib from the displacement start to the new external low; OTE 62–79%; never short in discount | `ote_band()` with `ote=(0.62, 0.79)` |
| Area of interest = 1H / 15M FVG of the breaking leg inside the OTE, unmitigated (4 FVG rules) | `need_fvg=True`, plain 3-candle gaps formed in that leg, not mitigated, overlapping the OTE |
| Price must trade into the OTE; taking the leg high invalidates | checked on M15 since the external low |
| No trade on the break; trigger = LTF CHoCH after the OTE touch (15M "less noise", 5M in the base video) | `ltf="M15"` (or `"M5"`), CHoCH in the trade direction on the candle that just closed, after the OTE touch |
| Enter on the CHoCH close when there's nothing to rebalance | market entry on the next M5 (the engine's standard) |
| Stop at invalidation = above the LTF lower high + buffer | `stop_buffer_atr` beyond the LTF swing |
| T1 = prior 1H low, T2 = equal lows / liquidity; ≥ ~3R (2.75 OK); no stretching | first of (leg low, next unswept pool) that pays `min_rr=2.75` |
| High-impact news (FOMC, NFP) in the next hours → no trade | backtester `--news-block <minutes>` |
| Time in trade 1–2 days | `max_hold_bars=200` M15 (50 h) |

**Also coded (options):**
- `trigger="lsr"` — the **liquidity-sweep reversal** (2.6) as the LTF trigger inside the area: swing high wicked above without a close above → close below the low the sweep leg started from; stop above the sweep high.
- `dxy="agree"` — the **DXY barometer** (2.10/2.11): the synthetic DXY's last 1H break must point the dollar's way.
  **DXY data:** ICMarkets MT5 has **no DXY symbol** (USDX/DXY/USDIndex/.cash all absent) but has **USDSEK**, so `bensim/dxy.py` builds it with the official ICE formula (`50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × GBPUSD^-0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036`) from our own M5 bars and stores it as symbol `DXY` (M5, source `synthetic-ice`). **Validated against Yahoo DX-Y.NYB** over 6,019 hourly bars (Oct 2025–Oct 2026): mean difference −0.001, std 0.013, hourly-change correlation 0.97. Live use would need it recomputed from live MT5 bars (not wired).

**Not coded yet:** weekly/daily external-structure bias (2.8), distribution channels / trend lines (2.4, 2.10), half-size entry + add, 50% partial at T1, "after a 15M loss wait for 1H", emotional state / clarity (human).
BoS & CHoCH entry signal · FVG secrets · 9 filter rules · trend lines with SMC · inducement · liquidity-sweep reversals (no CHoCH) · most traders skip this · HTF directional bias · top-down market structure · weekly outlook DXY/EURUSD/GBPUSD/AUDUSD/XAUUSD ×2 · why trades fail · why inconsistent.

---

## 4. Backtest — last 3 months (2026-07-12 → 2026-10-12, 7 symbols, ICMarkets costs)

| # | Variant | Trades | Win % | Avg R | t | PF | Total R | Max DD |
|---|---|---|---|---|---|---|---|---|
| 1 | 15M trigger | 33 | 15 | −0.37 | −1.2 | 0.59 | −12.3 | 3.3% |
| 2 | 15M + news block 2h | 33 | 15 | −0.37 | −1.2 | 0.59 | −12.3 | 3.3% |
| 3 | 5M trigger | 74 | 24 | +0.27 | 0.9 | 1.33 | +20.0 | 4.3% |
| 4 | 5M + news | 71 | 24 | +0.46 | 1.2 | 1.56 | +32.4 | 4.0% |
| 5 | 5M + news + LSR trigger | 55 | 26 | +0.42 | 1.1 | 1.53 | +23.0 | 2.5% |
| 6 | **5M + news + DXY agree** | 51 | 29 | **+0.63** | 1.6 | 1.84 | +32.3 | 3.1% |
| 7 | 5M + news + LSR + DXY | 41 | 22 | +0.19 | 0.5 | 1.23 | +7.8 | 2.7% |

- His full rules (5M confirmation, no news, DXY barometer) score best; 15M trigger loses.
- T1 (the leg's prior low) carries the result: 48 trades +0.67R; T2 trades ≈ 0R — supports "don't stretch".
- **Not significant** (best t = 1.6), top-5 winners > total, and variant 4 by month: Jul +24.2R, Aug +6.6R, Sep +6.8R, Oct −5.2R (decaying). **Not traded.** Next: variant 6 unchanged on 2026-04 → 2026-07 (out of sample).

### 4.1 Out-of-sample (2026-04-12 → 2026-07-12, unchanged parameters) — **FAILED**
| Variant | Trades | Win % | Avg R | t | PF | Total R | Max DD |
|---|---|---|---|---|---|---|---|
| 5M + news + DXY (best in-sample) | 57 | 14 | **−0.42** | −1.9 | 0.55 | −23.9 | 6.8% |
| 5M + news | 60 | 15 | −0.38 | −1.8 | 0.58 | −22.9 | 6.5% |
Every month negative (Apr −3.7R, May −13.0R, Jun −3.6R, Jul −3.6R). The in-sample +0.63R was period luck: **Bennett's model as coded has no edge after costs.** Not traded.

### 4.2 Trade diagrams (his chart style) — `python -m bensim.bennett_chart`
`docs/charts/bennett/`: 3 winners (EURUSD sell 13 Jul +6.49R, EURUSD buy 18 Aug +4.85R, XAUUSD sell 11 Sep +5.35R) and 2 losers (EURUSD sell 2 Sep −1.11R: the 5M CHoCH came only after price had already fallen back below the 50% line and the OTE held as support; USDJPY buy 16 Sep −1.08R: stopped by one wick, then price ran straight to T1).
