# Order Flow — course notes and strategy

Sources, and nothing else:
- **Faiz SMC Trading Course** (`G:/Faiz SMC Trading Course`): *1. SMC Basics* videos 1–9, *2. Trading Strategies* videos 1–5 (Order Flow) and 29 (Order Block Liquidity).
- **JeaFx — "This entry model will change how you trade… (10x results)"** (`D:/Downloads`), module 4.

Every video was transcribed (audio → text) and checked frame by frame (scene-change keyframes, 287 frames for Faiz + 63 for JeaFx). Where the mentor's words differ from ICT / LuxAlgo, the difference is called out; it is not silently "corrected".

Code: `bensim/orderflow_strategy.py` (`python -m bensim.backtest --strategy orderflow ...`).

---

## Module 1 — SMC basics (Faiz videos 1–9)

### 1.1 Market structure (video 2)
- **Bullish** = higher highs + higher lows. **Bearish** = lower lows + lower highs. **Ranging** = no direction, small range.
- Structure is **fractal**: an M5 uptrend can be a M1 downtrend inside its pullback. So a pullback on the higher timeframe can be traded as a trend on the lower one.

### 1.2 Market Structure Break (MSB) vs Market Structure Shift (MSS) (videos 3–4)
| Term | Meaning | ICT / LuxAlgo name |
|---|---|---|
| **MSB** | trend continues: new HH breaks the previous HH (or new LL breaks the previous LL) | BOS |
| **MSS** | trend changes: after a new HH, price breaks **the low that made that HH** (mirror for bearish) | CHoCH / MSS |

He also counts a **wick** through the level as a break "sometimes" (video 9) — the code requires a **close** (LuxAlgo rule), which is stricter.

### 1.3 Liquidity (videos 5–6)
- Resistance = **buy-side liquidity**: shorts put stops above it, breakout traders buy above it. Support = **sell-side liquidity**.
- The fake-out: price breaks the level, triggers those stops / breakout orders, then reverses hard ("liquidity purge"). He credits ICT.
- Liquidity is anywhere stops cluster: equal highs/lows, trend lines, old highs/lows, obvious S/R. **"Liquidity is everything."** "If there's no liquidity you're agreeing to lose" (Order Flow video 2).

### 1.4 Fair Value Gap / imbalance (video 7)
- 3-candle pattern. **Sell FVG**: gap between candle-1 **low** and candle-3 **high**. **Buy FVG**: gap between candle-1 **high** and candle-3 **low**.
- Price tends to come back and fill it. **Never trade an FVG on its own** — only with liquidity + structure.

### 1.5 Premium / discount (video 8)
- Fib from the start of the impulse to its end. **Below 50% = discount (buy there). Above 50% = premium (sell there).**
- **His fib settings (read off the settings dialog in the video, not said aloud):** levels **0, 0.33, 0.5, 0.705, 0.79, 1, −0.618**. 0.705–0.79 is the ICT *Optimal Trade Entry* band; −0.618 is the extension target.

### 1.6 Order blocks — **his definition differs from ICT** (video 9)
- **Faiz:** "the **first candle of the fair value gap**" (candle 1 of the 3-candle pattern), whatever its colour. "They always tell you to pick the very last candle… that is absolutely wrong."
- **ICT / LuxAlgo:** the **last opposite-colour candle** before the displacement (LuxAlgo: the extreme candle between the broken swing and the break).
- Only use OB/FVG in **discount** for buys / **premium** for sells.
- Entry choice: **small FVG → enter at the FVG; big FVG → enter at the OB.** Put the order a little inside the zone for spread.
- He warns plain OB trading loses — "they are helpful but in a different way" → that different way is Module 3.

---

## Module 2 — Order Flow strategy (Faiz Trading Strategies 1–5)

"Everything we learned so far used together." His own words: "**my least favourite strategy**… it's a very old SMC strategy, available on YouTube for free."

**MSS version (videos 1–3)** — reversal:
1. MSS (CHoCH) happens.
2. Fib from the top to the bottom of the breaking leg (sells) — find the **most extreme** FVG / OB in premium (sells) or discount (buys).
3. **Liquidity must build** before price returns: equal highs/lows, a trend line, a small range. No liquidity → skip.
4. Entry at the extreme FVG/OB; stop just beyond the OB; target the next liquidity pool (he shows equal highs as the TP).

**MSB version (videos 4–5)** — continuation:
1. Each new HH (buys) / LL (sells) = MSB.
2. Find the open (unfilled) FVG of that leg — "everything else is filled". Entry at the FVG if it is small, else at the OB (first candle).
3. Stop: beyond the OB, or **"on the safe side" beyond the leg's low wick** (a lower timeframe can hide another OB in that wick).
4. Target the leg's high (buys) / low (sells). His examples: 3.73R, 5.72R.

Code: `--set mode='"flow"' --set breaks='"any"'` (videos 4–5 = BOS too). Two details taken from the basics:
- **FVG = any 3-candle gap** (video 7) — `flow` uses plain gaps, not LuxAlgo's size-filtered FVGs (which would drop the "very short" imbalances he prefers to enter from).
- **The entry itself must be in discount/premium** (video 9: the imbalance above 50% was "risky", the OB below it "the perfect place") — so the check is on the entry price, and if the FVG entry is above 50% the first-candle OB below it can still qualify.

---

## Module 3 — Order Block Liquidity (Faiz Trading Strategy 29) ★ the main strategy

His upgrade of Order Flow: the first OB after a shift is often **liquidity**, not support. Wait for it to be faked out and reclaimed, then trade the retest.

**Sell setup (mirror for buys):**
1. **Shift**: MSS / CHoCH down. "It can break the internal structure as well" — internal structure is enough. Don't look too far back.
2. **Mark the OB**: the **last bullish candle** of the final push up before the shift. It must be **the highest candle** — if a later (bearish) candle has a wick above it, **the setup is invalid**.
3. **Invalid if the OB works as a real OB**: if price reacts heavily from it and breaks the shift low, there's no fake-out trade.
4. **Fake-out**: price goes back up and a candle **body closes above the OB** ("wicks do not count"). It shouldn't go far — "a big fake-out is not to my liking but it still works".
5. **Liquidity first**: if there are older **wicks** just above the OB, those are liquidity — wait until the fake-out clears them.
6. **Reclaim**: a body closes back below the OB → **sell on the retest** of the OB.
7. **Stop**: above the fake-out high (safer), or above the OB (better RR, stopped more often).
8. **Target**: 1:2 minimum, or the next imbalance / liquidity level (his examples: 3R, 6.2R; one held to 40R; one went to break-even).
9. He trades it on **M5**; backtest it **at least a month** before going live.

Code (defaults = this module): `python -m bensim.backtest --strategy orderflow --start <3 months ago>`.
Knobs: `max_fake_atr` (fake-out size), `liq_lookback` (wicks to clear), `stop_at` (`"fake"`/`"box"`), `min_rr`, `rr_target`, `setup_bars`.

### 3.0 Order Flow (videos 1–5) vs OB-Liquidity (video 29) — not the same
He says it himself: "essentially this is like order flow, but it has a little twist… the order flow strategy is a very old SMC strategy, available on YouTube for free."
| | Order Flow (1–5) | OB-Liquidity (29) |
|---|---|---|
| Which zone | the most extreme **FVG / first-candle OB** of the breaking leg, in discount/premium | the **last opposite candle at the extreme** of the push before the shift (must be the extreme candle) |
| What you expect at the zone | it **holds** — enter on the first return | it **fails first** — it is liquidity: body closes through, then back |
| Entry | first touch of the zone | retest after the fake-out + reclaim |
| Stop | beyond the leg origin / OB | beyond the fake-out (or the OB) |
| Invalid when | no liquidity built before the return | the OB holds as a real OB (new low/high before any fake-out), fake-out too big, or old wicks not cleared |
So 29 trades exactly the case where a 1–5 entry would have been stopped out.

### 3.1 Every liquidity rule Faiz gives, and where it lives in the code
| # | Rule (his words / video) | In the code |
|---|---|---|
| L1 | Resistance = buy-side liquidity, support = sell-side; stops + breakout orders sit beyond them (Basics 5) | pools = `indicators._pools`: equal highs/lows, previous-day H/L, Asian H/L, swing extreme |
| L2 | Price fakes through those levels, triggers the stops, then reverses — "liquidity purge" (Basics 6) | the whole of Module 3 (fake-out → reclaim) |
| L3 | Liquidity is anywhere stops cluster: equal highs/lows, trend lines, old highs/lows, "all retail patterns" (Basics 5, OF 1) | EQH/EQL + swings coded; **trend lines not coded** (no objective definition) |
| L4 | **"Make sure you identify liquidity before taking these entries. If there's no liquidity you're agreeing to lose"** (OF 2) | `need_liquidity=True` (flow and confirmation): an internal swing (stop pool) must form between the entry and the break extreme, after the break, before the touch |
| L5 | Liquidity is the target ("take profit at another liquidity pool", relative equal highs) (OF 2) | target = nearest **unswept** opposite pool at ≥ `min_rr` (levels already traded through are dropped, as LuxAlgo removes swept levels); pools within 0.1 × ATR(200) form one zone (LuxAlgo buyside/sellside margin), target = its near edge, zone size logged as `target_pool_levels` |
| L6 | The first OB after a shift can itself be liquidity — wait for the fake-out (OB-Liquidity 29) | `liquidity_ob` mode |
| L7 | **Wicks just beyond the OB are liquidity — "wait till these wicks are cleared out"; a fake-out that leaves them is not a real fake-out** (29) | `liq_lookback`: older wicks inside the fake-out range must be exceeded before the reclaim counts |
| L8 | A fake-out must close with the **body** beyond the OB — "wicks do not count" (29) | coded (close, not wick) |
| L9 | Fake-out should be small — "a big fake-out is not to my liking" (29) | `max_fake_atr` (default 1.5 × ATR) |

---

## Module 4 — JeaFx "Confirmation entry" (This entry model will change how you trade…)

Full 21:26 transcribed (Whisper) + 63 keyframes checked. His promise: more winners, fewer losses, bigger RR.

### 4.1 How he takes a trade (buy — sells are "exactly the same, flipped")
1. **Trend first**: a downtrend printing lower lows and lower highs.
2. **Confirmation = break of structure with a candle CLOSE** above the **final lower high**, making a higher high. "Not just wicks — wicks show rejections." If price only wicks above, it's still a downtrend/consolidation → no trade. If price hasn't made a new low and then a higher high, "this for now can just be seen as a consolidation".
3. **Demand zone = the last candle before the impulse**: the small / indecision / doji / pin-bar / wicky consolidation candle at the low, just before the large rally. "For every confirmation entry I use demand zones." It must be **fresh** ("this candle is still not yet retested"). He refines it on a lower timeframe (30-minute) when the higher-timeframe candle is big.
4. **Entry = buy limit**, placed **as soon as the higher high prints**, **on or a little above** the demand ("even if it's just 0.2 pips… because of broker spreads").
5. **Stop = just under the swing low of the confirmation** ("if the market comes back to this low, the trade is invalidated — take a small controlled loss"). On continuations: under the notable swing low, or more aggressively under the demand candle.
6. **Target**: wherever you want — the next **supply zone**, or the **swing high**. On **reversals**, aim higher (previous swing highs / "notable highs") because you're early in a new trend.

### 4.2 Two contexts (same entry model, different context)
| | Reversal | Continuation |
|---|---|---|
| Where | the end of a trend — "straight shift from downtrend into uptrend", no significant zone behind it | the higher-timeframe trend already exists; the lower-timeframe **pullback** (its own LL/LH) shifts back up **from a higher-timeframe demand zone** |
| Why | you get in at the very start of a new trend → bigger targets | high-timeframe and low-timeframe agree (top-down) → higher probability |
| Target | further — previous / notable swing highs | the swing high |

"Confirmation entries are **best used in context**": higher-timeframe BOS showing the direction + the lower-timeframe confirmation forming **inside a higher-timeframe demand zone**. The frames show the large higher-timeframe zone (pink box) with the small confirmation demand inside it, and one crossed-out example where the BOS came from no zone.

### 4.3 Liquidity, strength and side notes (everything he noted)
- **Liquidity sweep before the reversal**: in his reversal example "the market swept through one of [the significant swing lows] and then made this run away… we had a **small sweep of liquidity**, but nothing really significant". → For him the sweep is **supporting context, not a requirement**; a significant sweep or a demand zone would make it a stronger, context trade.
- **Why demand works**: it's where significant, fresh buying created the shift, a "discounted / fair price"; when price returns, the same or new buyers buy again, "momentum slows down, the market taps demand, the buy limit is triggered".
- **Impulse vs correction = you're on the right side**: the impulse was "fast, clear, strong, no pullbacks"; the correction was "slow… buyers kept attempting to go higher". Strong impulse + slow correction = trade with it.
- **Closures across several structure points** strengthen the confirmation ("broken through a couple of structure points… closures above some of these significant higher levels").
- Supply for sells = the last candle before the bearish impulse, a "premium price"; sell limit on or just below it, stop above the swing high.
- "Go out there and **backtest** your confirmation entries."

### 4.4 Faiz vs JeaFx — the same skeleton, different details
| | Faiz Order Flow (videos 1–5) | Faiz OB-Liquidity (29) | JeaFx confirmation |
|---|---|---|---|
| Trigger | MSS or MSB | MSS (CHoCH), internal OK | BOS with a candle **close** (wicks = no) |
| Zone | most extreme FVG / **first candle of the FVG** in discount/premium | last opposite candle at the extreme (must be the extreme) | **last candle before the impulse** (fresh) |
| Entry | at the zone (FVG if small, else OB) | after a **fake-out through the OB and a reclaim**, on the retest | buy/sell limit just beyond the zone, right after the BOS |
| Stop | beyond the OB / safe side under the wick | beyond the fake-out (or the OB) | under the confirmation swing low |
| Liquidity | **required** — must build before entry; also the target | the OB *is* liquidity; old wicks must be cleared | a sweep before the shift = context, not required |
| Context | premium/discount | — | higher-timeframe trend + higher-timeframe zone |

### 4.5 In the code
`--set mode='"confirmation"'` (implemented in `confirmation_zone()`):
- confirmation = internal CHoCH (LuxAlgo, **close**-based, so wick-only breaks never count);
- demand = the extreme candle of the leg (ties → the **last** one); if that candle is itself the big impulse candle (body > 50% of its range, in the trade's direction), the candle before it;
- entry = its high + `entry_buffer_atr` (buys) / its low − buffer (sells); stop = the confirmation swing low − `stop_buffer_atr`;
- **continuation** when the swing (higher) structure already trends the trade's way → target = the broken swing high; **reversal** otherwise → target = next liquidity pool ≥ `min_rr`, else `rr_target` × risk;
- higher-timeframe context: `--set htf_poi=True` (shift must follow a tap of an open H1 FVG) and/or `--set confirm='("swing_trend",)'`;
- liquidity sweep: optional, via `--set confirm='("sweep",)'` (he treats it as context, so it's off by default);
- **not coded**: "fresh / not yet retested" is only enforced as "first touch after the confirmation"; the impulse-vs-correction strength read; the 30-minute refinement.

---

## Module 5 — Research check (what the internet says)

- **OB definition**: ICT sources agree on "last opposing candle before a displacement"; zone drawing (full range vs body vs 50% "mean threshold") is not standardised — so Faiz's first-candle OB is his variant, not the standard. [LuxAlgo: OB anatomy](https://www.luxalgo.com/library/concept/order-block-anatomy-and-refinement/), [LuxAlgo: bullish/bearish OB](https://www.luxalgo.com/library/concept/bullish-bearish-order-block/), [displacement](https://backtrex.com/en/blog/ict-displacement-candle-market-concept).
- **OTE 0.705 / 0.79**: 0.705 is ICT's Optimal Trade Entry; sources disagree whether 0.79 is the edge of the entry zone or the invalidation. [ChartingLens OTE](https://chartinglens.com/blog/ict-optimal-trade-entry-ote), [Backtrex OTE](https://backtrex.com/en/blog/ict-fibonacci-golden-pocket-ote-setup).
- **LuxAlgo SMC indicator** (what bensim's indicators copy): internal + swing structure, BOS/CHoCH, OBs filtered by ATR and tracked for mitigation, FVGs, EQH/EQL, premium/discount. [TradingView script](https://www.tradingview.com/script/CnB3fSph-Smart-Money-Concepts-LUX/), [LuxAlgo library](https://www.luxalgo.com/library/indicator/smart-money-concepts-smc/).
- **Video 29 = a breaker / failed-OB pattern**: an OB that price closes through and then retests from the other side. [LuxAlgo: breaker block](https://www.luxalgo.com/library/concept/breaker-block/), [TakeProfit: breaker blocks](https://takeprofitapp.com/en/learn/breaker-blocks).
- **Caveat**: none of these sources show independent evidence of an edge. All of this is a hypothesis until backtested with costs.

---

## Module 6 — Backtest results (last 3 months, 2026-07-12 → 2026-10-12, 7 symbols, IC Markets costs)

| # | Variant | Trades | Win % | Avg R | t | PF | Total R | Max DD |
|---|---|---|---|---|---|---|---|---|
| 1 | OB-liquidity (Faiz 29) | 10 | 0 | −1.02 | — | 0 | −10.2 | 2.6% |
| 2 | OB-liquidity + JeaFx context | 0 | | | | | | |
| 3 | Order Flow (Faiz 1–5) | 42 | 29 | 0.00 | 0.0 | 1.00 | 0.0 | 2.6% |
| 4 | JeaFx alone | 166 | 21 | −0.22 | −1.5 | 0.73 | −37.0 | 13.3% |
| 5 | JeaFx + HTF context | 13 | 0 | −1.06 | — | 0 | −13.8 | 3.4% |
| 6 | **JeaFx + Faiz liquidity** | 42 | 33 | **+0.32** | 0.88 | 1.46 | +13.5 | 2.5% |

Variant 6 split: reversals 28 trades +0.76R, continuations 14 trades −0.55R (post-hoc split).
Takeaways: Faiz's liquidity rule is the one ingredient that clearly helps; JeaFx's "context" filter hurt; no variant is statistically significant. **Not traded.** Next: variant 6 reversal-only, unchanged, on the previous 3 months (out-of-sample).
Bugs found and fixed during the run: OB invalidation too strict; plan loop stopped scanning after an old touched setup (same bug still in `fvg_strategy.py`); "target the high" fixed at the break instead of at the entry.

### 6.1 Idea tested: "trade INTO the zone, then take the confirmation trade" (A + B)
Trade A = from the moment the setup is known, trade towards the zone (TP = zone), then B = the normal setup trade.
Result (same 3 months): price reaches the zone 56–68% of the time, but first runs AGAINST A about as far as the zone is away (median 1.67 ATR vs 1.59 ATR distance for JeaFx+liquidity; 2.30 vs 2.28 for flow). A was negative in every stop variant (best: −0.03R with the stop beyond the impulse extreme), and A + B was always worse than B alone. **Rejected** — waiting at the zone is where the edge (if any) is. Script: scratchpad `pre_trade.py`, data `data/pre_trade_3m_*.csv`.
