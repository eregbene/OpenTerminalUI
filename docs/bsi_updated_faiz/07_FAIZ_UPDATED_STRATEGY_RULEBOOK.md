# Faiz Updated Strategy Rulebook

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

## Extraction Status

This document is being built only from `F:\new faiz` transcripts and visual contact sheets.

## Order Flow Trading Strategy

- Strategy ID: `bsi_v3_order_flow`
- Sources: `1. Order Flow Trading Strategy.mp4`, `20. ORDERFLOW 101.mp4`
- Evidence status: transcripts complete; visual contact sheets inspected/generated.
- Mentor definition: Order Flow uses the learned SMC components together: MSB/MSS, liquidity, order block, premium/discount, and imbalance.
- Entry sequence observed so far: identify MSS, draw Fibonacci across the displacement/dealing range, prefer the most extreme imbalance or most extreme order block, and use liquidity such as trendline liquidity as part of the setup.
- ORDERFLOW 101 refinement: use H4 or daily market structure first. If HTF is bullish, only longs; if bearish, only shorts.
- Market condition filter: must be a clear trending market, not consolidation, because consolidation produces liquidity sweeps that can stop out otherwise correct directional ideas.
- HTF POI: wait for price to return to an HTF order block after break of structure.
- OB definition for this strategy: last bearish candle or consecutive bearish candles before move up for bullish OB; last bullish candle or consecutive bullish candles before move down for bearish OB. Mark the candle body.
- Execution: drop to M15 and look for MSS or confirmation entry during killzone.
- PD filter: OB above/below the 0.5 level in the correct premium/discount side can qualify with confirmation.
- Extra quality: mentor likes some consolidation before price returns into the POI.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Bias-Driven Simple Model

- Strategy ID: `bsi_v3_bias_mss_ob`
- Source: `1. Finding Monthly, Weekly, & Daily Bias.mp4`
- Evidence status: transcript complete; visual contact sheet pending detailed inspection.
- Sequence: external liquidity purge on higher timeframe, MSS on confirmation timeframe, entry from resulting order block or related lower-timeframe entry model, partials along the way, main target at unmitigated internal FVG.
- Evidence: `SPOKEN_EXPLICIT`
- Production status: supporting model unless later videos teach it as standalone.

## SMT Divergence

- Strategy ID: `bsi_v3_smt_divergence`
- Source: `1. SMT Divergence.mp4`
- Evidence status: transcript complete; visual contact sheet inspected.
- Eligible instruments explicitly taught: BTC/ETH, NAS100/S&P500/US30, EURUSD/GBPUSD/AUDUSD with DXY.
- Core setup: correlated instruments diverge at a high/low; one sweeps liquidity while the other fails to confirm with a matching high/low.
- Direction logic: a failed higher high on the correlated pair while the traded pair sweeps a high signals bearish order-flow shift; a failed lower low while the traded pair sweeps a low signals bullish order-flow shift.
- Execution: trade the instrument that swept liquidity, then drop from M15 to M1 in the taught example, wait for MSS, and enter from the extreme FVG or order block.
- Stop: mentor gives example stop above the first candle of the FVG or wider above the local high "to be safe."
- Target: target the relevant low/high; if ABC is present, ABC target logic can be used.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: candidate independent V3 strategy plus confluence filter.

## ABC Trading Strategy

- Strategy ID: `bsi_v3_abc`
- Sources: `10. ABC Trading Strategy.mp4`, `19. ABC 101.mp4`, `10. Things To Avoid In ABC Strategy.mp4`
- Evidence status: transcripts complete for base, ABC 101, and avoidance lesson; visual contact sheets generated.
- Core sequence: identify A leg as the main trend leg; wait for B leg as a significant pullback or small trend in the opposite direction; C leg moves back through B-side liquidity; C must create internal structure before the sweep; then price must reclaim the A/B reference level and break the structure created inside C.
- B-leg invalidation: the B leg must not exceed or break the A-leg origin level. If B crosses the A origin, the setup is invalid.
- Structure rule: structure below/after the wrong side of the B leg does not count; the qualifying structure must be inside the B-to-C area before the sweep.
- Entry: identify the order block or FVG after the valid MSS/reclaim and enter from that level.
- Stop: below/above the entry structure shown around the OB/FVG.
- Target: high/low of the B leg.
- Evidence: `SPOKEN_EXPLICIT`, visual sheet pending detailed replay.
- Uncertainty: early transcript wording says C takes out A-leg low/high, but the detailed example repeatedly describes C breaking the B-leg level. Treat the detailed chronology as controlling until visual replay confirms.
- Avoidance rule: do not treat a strong continuation breakout as C. C should be a fakeout to a reasonable degree; the mentor gives roughly 30-35 pips on GBPUSD as acceptable context and says larger continuation becomes risky.
- Quality filter: prime ABC setups show consolidation/rejection/liquidity buildup near the A-leg high/low before the C-leg sweep. Without that buildup, mentor estimates lower odds and says the move may simply continue.
- Avoidance source: `10. Things To Avoid In ABC Strategy.mp4` at `00:00:00-00:05:23`.
- ABC 101 refinement: use high-timeframe market structure/trend first. H4 or daily trend controls direction; if bullish, only look for longs, if bearish, only shorts.
- PD requirement: draw fib on the relevant HTF move and look for ABC setups in the correct premium/discount side beyond the `0.5` level.
- POI requirement: C leg should go into a POI such as OB, FVG, or supply/demand. The whole ABC may form inside the POI; C touching the POI is not the only valid variant.
- ABC POI Combination refinement: if an HTF POI exists, wait for ABC near that POI with the C leg entering the POI, then follow normal ABC rules. Mentor says this can improve win rate by about 5-10%.
- ABC POI entry: after the ABC rules complete near the POI, prefer the extreme FVG for entry.
- Timeframes: H4 is preferred for structure; daily is also valid. H1 can be used but is not recommended. ABC can technically be on any timeframe except mentor does not recommend M1. Execution commonly drops to M15, with M5 refinement if M15 structure is unclear or POI needs refinement.
- Killzone: entry must occur in London `03:00-06:00` or New York `08:30-11:15` New York time.
- ABC 101 source: `19. ABC 101.mp4` at `00:00:00-00:13:24`.

## 9:30AM Trading Strategy Updated

- Strategy ID: `bsi_v3_0930`
- Source: `13. 930AM Trading Strategy (Updated).mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Instrument scope: works best with indices such as NAS100 and S&P500. Forex is allowed by the mentor but not recommended for this strategy.
- Timezone/session: New York timezone. Entries only from `09:30` through `11:59` NY time.
- Liquidity: identify liquidity on M15. Liquidity can be equal highs/lows, major swing high/low, daily high/low, previous day high/low, or weekly high/low. Liquidity may be identified around `09:25`, but trade entry is after `09:30`.
- Execution: after 09:30, drop to M1, wait for liquidity sweep, then wait for MSS.
- Entry: first FVG or extreme FVG after MSS. Mentor prefers the extreme FVG because missed trades are acceptable when the reward profile is better.
- Stop: beyond the sweep/structure implied by the FVG entry.
- Target: FVG target or fixed minimum `3R` to maximum `5R`; mentor does not chase larger R because he does not want long holds.
- Evidence: `SPOKEN_AND_VISUAL`

## MMXM Second Distribution Entry

- Strategy ID: `bsi_v3_mmxm_second_distribution`
- Source: `13. MMXM 2ND DISTRIBUTION ENTRY.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Context: daily external liquidity is taken; daily key level provides the higher-timeframe event, then the actual model is identified on H1.
- Draw: internal liquidity/FVG from the consolidation low/high becomes the draw-on-liquidity target.
- Sequence: external liquidity sweep -> MSS -> pullback -> lower-low/lower-high/lower-low continuation -> continuation purge where price takes a lower high -> another BOS/MSS -> pullback -> second/final BOS/fractal BOS.
- Entry: use FVG or OB from the final fractal BOS leg; if no FVG appears in the immediate leg, look back to the previous leg for an FVG.
- Target: internal liquidity/FVG target from the original MMXM range/consolidation.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## ABCD 101 / Updated ABCD

- Strategy ID: `bsi_v3_abcd`
- Source: `16. ABCD 101.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Direction filter: determine overall market structure/trend by looking back roughly 20-30 days. If market has been going up, look for longs; if going down, look for shorts.
- Timeframes: identify market structure and ABC pattern on H4; execute MSS on M15.
- Core sequence: identify A-B-C, then wait for D leg. In a bullish trend, D usually takes out B-leg low; in a bearish trend, D takes out B-leg high. Then drop to M15 and wait for MSS with displacement/FVG before the shift.
- Timing: entries should be during killzone hours; mentor refers back to ABC timing rules.
- Entry: M15 MSS displacement with FVG, preferably around OB/FVG confluence.
- Optional A+ confluence: draw fib from start of A leg to end of C leg; D reaction around `0.705`, `0.75`, or `0.79` improves quality. OB/FVG around that zone is stronger.
- Targets/management: target FVG, supply/demand, or OB/FVG area; take partials along the way and trailing is risk-style dependent.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Reactionary Order Block

- Strategy ID: `bsi_v3_reactionary_block`
- Source: `17. Reactionary Block Trading Strategy.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Relationship: mentor says this is Order Flow with more confirmation, not a separate lower-timeframe confirmation model.
- Setup: after MSS/MSB, mark the original FVG + OB in discount/premium.
- Confirmation: when price mitigates that original OB/FVG, do not enter immediately. Wait for an impulsive reaction on the same timeframe that creates another FVG + OB. The reaction does not need to break structure.
- Entry: enter from the new reactionary OB/FVG.
- Stop: either below/above the new impulsive low/high, or beyond the original zone for safety.
- Target: next high/low; mentor discourages chasing huge R and says 2R-5R is enough.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Holy Grail

- Strategy ID: `bsi_v3_holy_grail`
- Source: `17. The Holy Grail.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Timeframes: daily -> H1 -> M5.
- Bias/context: determine external and internal liquidity. Daily external liquidity purge creates the narrative; unmitigated daily FVG in discount/premium is the draw.
- H1 confirmation: after daily liquidity is taken, drop to H1 and wait for MSS aligned with the daily narrative. Mark the H1 FVG/OB/PD array caused by that shift.
- M5 execution: when price trades into the H1 FVG/OB, drop to M5. The move into the H1 PD array must contain a market-maker model / consolidation that becomes target context.
- Entry sequence: after M5 MSS, every FVG, OB, and mitigation block inside the move can be used for entry or continuation while the target remains open.
- Exit: once the model target/consolidation liquidity is taken, exit.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Juggernaut Model

- Strategy ID: `bsi_v3_juggernaut`
- Source: `21. The Juggernaut Model.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Type: no-daily-bias scalping model.
- Timeframes: mentor says it works on all timeframes including 30 seconds, M1, and M5.
- Setup: look for a liquidity sweep or MSS leg. The sweep/MSS leg must contain exactly one FVG. If there are multiple FVGs on the current timeframe, move to 2m/3m/4m/higher until there is one FVG in the leg.
- Trigger: wait for the FVG to become IFVG through a candle body close beyond it.
- Entry: enter on the body close or wait for retest of the IFVG.
- Target: closest liquidity high/low is TP1 or full TP. After TP1, options are BE and hold for HTF draw, take 30-40% partial and move BE, or close full.
- Minimum RR: trade must have at least 1R to closest liquidity; adjust stop to achieve at least 1R only if still logically protected by structure.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Spectre Model

- Strategy ID: `bsi_v3_spectre`
- Source: `25. The Spectre Model.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Primitive: inverse order block / order-block liquidity.
- Setup: after MSS/BOS, price forms an OB that many traders would enter from directly, but instead price sweeps or closes through the OB.
- Trigger: wait for reclaim back through the OB with body close, similar to IFVG reclaim logic.
- Entry: enter on retest after reclaim.
- Target: nearby liquidity or FVG target around the opposing side of the move.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Asian Session Strategy V2.0

- Strategy ID: `bsi_v3_asian_v2`
- Source: `23. ASIAN SESSION STRATEGY V2.0.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Instrument/session scope: use FX and Asian session range.
- Bias requirement: no daily bias or HTF market structure requirement for V2.0.
- HTF setup: on M15, mark POIs below Asian low and above Asian high after/around the Asian range.
- OB definition for this strategy: use the first candle of the FVG sequence, not the classic Order Flow last-opposite-candle OB.
- Timing: after Asian session ends, wait for price to reach the POI. Unlike Asian V1, V2.0 can also trigger during early London session.
- Execution: when price reaches M15 POI, drop to M1, wait for MSS with displacement, then enter using FVG, breaker block, BPR, or other taught entry array.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model; supersedes/refines old Asian V1 when trading this updated method.

## Silver Bullet With Bias

- Strategy ID: `bsi_v3_silver_bullet_with_bias`
- Source: `24. Silver Bullet With Bias.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Timeframes: use M15 context for M1 entries, or H1 context for M5 entries.
- Windows: ICT Silver Bullet timings named as `03:00-04:00` and `10:00-11:00` New York time.
- Bias/draw: use external-to-internal liquidity logic. A purge of external liquidity plus intact internal FVG draw establishes the target.
- Trigger: MSS can happen before the exact Silver Bullet window, but entry must be after the window opens, from an FVG that forms after BOS/displacement.
- Target: mentor suggests not targeting the full internal liquidity in the first simple scenario; use 1:2. Later continuation/second-distribution entries can target better continuation.
- Enhancement: one-minute second distribution entry can be used inside Silver Bullet timing after purge/double BOS to increase win rate.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## ICT Silver Bullet

- Strategy ID: `bsi_v3_ict_silver_bullet`
- Source: `30. ICT Silver Bullet.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Precondition: before `10:00` New York time, identify liquidity that has already been swept.
- Draw: identify opposing liquidity that has not been swept yet.
- Trigger: after `10:00`, use M1 and wait for internal market structure shift with displacement.
- Entry: FVG created by the M1 displacement.
- Stop/target: ICT baseline is 5 handles stop and 5 handles TP on E-mini S&P, effectively 1:1. Faiz says his backtesting prefers 1:2 and moving stop to breakeven at 1:1.
- Invalidation/caution: if price takes the significant target-side low/high before returning to entry, be cautious/skip because liquidity may already be taken.
- Structure scope: use internal market structure on M1, not the broader external structure.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Standard Deviations / Daily Power Of Three

- Strategy ID: `bsi_v3_standard_deviation_po3`
- Source: `29. Standard Deviations.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Relationship: can be used with Order Flow, key levels, Silver Bullet zones, and daily Power of Three.
- Bias/order-flow: if order flow is bearish, prefer manipulation into buy-side liquidity then expansion down; inverse for bullish.
- Key-level variant: after price trades into HTF OB/FVG/key level and gives MSS, measure the last high/low before MSS with Fibonacci projections.
- Projection target: focus on negative `2` and `2.5`; price often reverses there. If significant liquidity remains beyond, price may take that liquidity before reversing. Negative `4` is possible but risky.
- Silver Bullet zone: negative `1` to `1.5` can provide a continuation entry area if price does not exceed the `-1.5` zone and a PD array is present.
- PO3 variant: mark daily candle open, drop to M5, wait for manipulation away from open and MSS, then measure manipulation using last high/low before MSS. After MSS, measure expansion and target significant liquidity around/beyond negative `2`.
- Entry: FVG, OB, or breaker block after MSS. Mentor usually likes OB entries.
- Management: partial at intermediate FVG/structure, move breakeven, final target at liquidity near projection level.
- Instruments: examples include USD/CAD and AUD/USD; mentor says pick three to four pairs and use it every day.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model plus target/projection framework.

## 4 Hour Order Block Trading Strategy

- Strategy ID: `bsi_v3_4h_order_block`
- Source: `34. 4 Hour OB Trading Strategy.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Relationship: mentor describes this as a unique Order Flow variant, but the lesson teaches it as its own rule-based strategy.
- Timeframes: only H4 and M15 are used. H4 defines order flow and POIs; M15 provides MSS and entry.
- H4 trend: if current price action shows about three higher highs/higher lows, trend is bullish; inverse for bearish. A reversal needs MSS and preferably a second BOS for this model.
- H4 POI marking: mark every relevant H4 order block near price action. For consecutive opposite candles, refine the HTF zone to the body of the last candle in that leg.
- OB validity: in trend, an H4 bearish candle followed by a bullish candle whose body closes above the bearish candle body can be valid demand; wicks do not matter for the body-close rule. The OB remains valid until its wick/low is violated.
- Entry condition: wait for price to return into the H4 OB, then drop to M15 and wait for MSS during killzone hours. Entry must also occur during killzone hours.
- M15 OB quality: the M15 entry OB should take out some form of liquidity before MSS. If it does not take liquidity, mentor calls it lower probability.
- Big block alignment: a stronger entry can occur when the first candle of the leg and the first candle of the OB are aligned/similar in size.
- Target/management: target fixed `1:2`. Move SL to breakeven when price reaches `1:1`, then hold for `1:2`.
- Instrument scope: mentor says this works with every forex pair but recommends sticking to main forex pairs. He says he has not tried it with indices and does not recommend indices for this model.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Quarterly Theory

- Strategy ID: `bsi_v3_quarterly_theory`
- Sources: `31. Quarterly Theory part 2.mp4`, `32. Quarterly Theory Example 1.mp4`, `33. Quarterly Theory Example 2.mp4`
- Evidence status: transcripts complete for part 2 and examples; visual contact sheets generated.
- Relationship: mentor says it can be standalone or used with other strategies. Treat as a timing/confluence engine unless paired with explicit liquidity/MSS/PD array rules.
- Core cycle: each period has quarters, and each quarter has accumulation, manipulation, distribution/expansion, and continuation quadrants.
- Prime condition: look for liquidity sweep or mitigation into a PD array during the manipulation quadrant of the manipulation quarter. The sweep is preferred, but moving into an FVG/PD array without a fresh sweep is acceptable.
- Direction filter: trade with the higher-timeframe trend or higher-timeframe draw.
- Execution: after manipulation into liquidity/PD array, wait for MSS and entry. Mentor suggests staying on M15 for the intraday model, though M2/M1 can be used for lower-timeframe confirmation.
- Usage: improves timing by separating manipulation entries from expansion entries. Examples combine H4 PD array/OB context with M15 entry timing.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: V3 confluence/timing module; standalone only when liquidity + MSS + PD array are all present.

## AR50 / Asian Range 50 Percent

- Strategy ID: `bsi_v3_ar50`
- Source: `34. AR50 Trading Model.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Relationship: Asian range 50% model with added draw-on-liquidity bias logic.
- Bias/draw: use daily candle close logic to define draw-on-liquidity. Once a candle closes above the relevant opposing candle/high, the next daily high can become draw-on-liquidity for bullish AR50; inverse for bearish.
- Entry area: trade pullback to 50% of the Asian range in the direction of the draw.
- Target: daily draw-on-liquidity high/low identified by the bias logic.
- Bias uncertainty: if neither side is broken/confirmed, bias is unclear and AR50 should wait.
- Scope: examples include forex and NAS100; transcript says the bias method gives direction about 80-90% of the time, but this must be replay-tested before automation.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model pending example replay for exact range-session boundaries and stop placement.

## Monday Range Model

- Strategy ID: `bsi_v3_monday_range`
- Source: `37. Utilizing Monday Range.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Instrument scope: mentor explicitly says it works on gold, NAS100, EURUSD, and similar markets.
- Setup: after Monday daily candle closes, mark Monday candle high and Monday candle low.
- Trigger day: on Tuesday, wait for price to take out either Monday high or Monday low.
- Execution: after the sweep, drop to M15 and wait for MSS.
- Entry: enter from the resulting FVG or order block. In examples, mentor prefers OB when available, but may use FVG when the range is large and earlier entry is needed.
- Target: if Monday low is swept first, target Monday high or 50% of Monday range; if Monday high is swept first, target Monday low or 50% of range.
- Asian-session interaction: Tuesday may take Asian high/low first before giving the MSS toward the Monday opposite side; this can be treated as additional liquidity context.
- Stop: outside the entry structure/OB/FVG from the M15 reversal.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Weaver Model

- Strategy ID: `bsi_v3_weaver`
- Source: `38. The Weaver Model.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Instrument scope: mentor says the model works on all pairs and assets, with setups available most days when conditions present.
- Setup: mark previous-day high and previous-day low.
- Trigger: on the next day, wait for price to take out either previous-day high or previous-day low.
- Draw-on-liquidity: after the sweep, drop to H1 and identify an H1 FVG inside the recent dealing range on the correct side of equilibrium. That H1 FVG becomes the draw.
- Dealing range rule: choose the high/low of the dealing range from a swing that swept liquidity and pushed away; do not use a swing that did not sweep liquidity.
- Execution: drop to M15, wait for MSS, then enter from FVG, breaker block, or related entry array.
- Confluence: SMT divergence may be used to increase win rate.
- Target: the H1 FVG/draw-on-liquidity. If the draw is too close, mentor may accept safer returns such as around `1.5R`; if room exists, prefer `1:2`.
- Stop: outside the M15 entry structure. Mentor discusses not forcing stop reduction just to make `1:2`; only use the safest logical target when RR is constrained.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## IFVG / Power Of Three Model

- Strategy ID: `bsi_v3_ifvg_po3`
- Source: `39. The IFVG Model.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Instrument scope: mentor says it can be traded on NAS100, gold, and EURUSD, and says he primarily trades NAS100 because it has the highest win rate.
- Timeframe: primarily M1. If the manipulation leg has multiple FVGs, M2-M5 can be checked, but M5 is the highest allowed and must still show clear Power of Three. Mentor prefers staying on M1.
- PO3 sequence: accumulation creates liquidity, manipulation sweeps/drives price, then distribution returns in the intended direction.
- Setup: inside the manipulation leg, there must be exactly one FVG. Multiple FVGs invalidate the M1 version unless a higher allowed timeframe reduces it to one FVG and still shows PO3.
- IFVG trigger: a bullish FVG becomes inverse when a candle body closes below it; a bearish FVG becomes inverse when a candle body closes above it.
- Entry: enter on the body close through the FVG or wait for retest if price closes too far away and the immediate stop would be too large.
- Stop: beyond the manipulation leg extreme/high/low, not an artificially compressed stop.
- Closest-liquidity rule: target the most recent/closest liquidity first. If price takes that closest liquidity before entry, the setup is invalid.
- Management: when closest liquidity is taken, move to breakeven immediately. At `1R`, take 50% partial and remain breakeven. Runner can target `2.5R`, `3R`, HTF draw-on-liquidity, or a standard-deviation target.
- Confluence: HTF key levels and SMT divergence increase win rate. Trade the pair/asset that swept liquidity in SMT context.
- Execution note: mentor warns not to rely on broker-side fixed SL/TP orders on very low timeframe if spread can falsely stop BE/TP; V3 automation must model spread/slippage before copying this behavior.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Yin Yang London Gold Model

- Strategy ID: `bsi_v3_yin_yang`
- Source: `41. The Yin Yang Model.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Instrument/session scope: mentor teaches it as the most mechanical gold strategy for London. He says it can work on forex too but other methods are planned; V3 should initially tag this as gold-first.
- Bias requirement: no daily bias or top-down analysis required for the mechanical version.
- Time/session: London session starts at `03:00` New York time. Lesson also states London session as `03:00-07:00`.
- Timeframe: M15.
- Setup: identify the first FVG that forms during the London session. For a valid London FVG, the second candle of the three-candle FVG should be exactly at `03:00`; if the second candle is at `02:45`, mentor rejects it as pre-London.
- Trigger: wait for that first London FVG to invert by candle body close through it.
- Entry: enter on the body close if close is near the IFVG; if price closes far away and stop would be too large, wait for retest.
- Stop: beyond/above/below candle bodies, not necessarily beyond the wick. Examples repeatedly place SL above or below bodies.
- Target: fixed `1:2` preferred; optional liquidity runners exist, but mentor says he usually takes `1:2`.
- Confluence: liquidity sweep before London open, resting equal highs/lows, trendline liquidity, or HTF PD array improves win rate but is not mandatory for the most mechanical version.
- News filter: avoid high-impact news and wait about 15 minutes after news.
- Low-probability warning: if target-side liquidity has already been swept before entry, the setup becomes lower probability.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## 4 Hour Candle Ranges / CRD

- Strategy ID: `bsi_v3_4h_candle_ranges`
- Source: `42. 4 Hour Candle Ranges.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Concept: candle range theory / turtle soup. Every candle is a range, but only specific timed candles are high probability.
- Instrument scope: mentor explicitly names gold, EURUSD, NAS100, US30, and S&P500. He says do not trade anything else for this model and choose one pair/asset that presents the setup.
- Timing: two high-probability 4H candles per day: the `01:00` candle and the `05:00` candle.
- Range marking: after the 4H candle closes, mark its high and low.
- Trigger: on M5, wait for price to raid/take out either the candle high or candle low.
- Acceptance rule: after the raid and MSS, price should come back/close back into the candle range. Mentor rejects entries where price never returns into the range.
- Entry: after M5 MSS and acceptance back into range, enter from FVG, breaker block, or order block.
- Confluence: tapping an H4 PD array/FVG while raiding the candle high/low increases probability. Standard deviation projections around `-2`, `-2.5`, or `-4` can add target confluence.
- Stop: beyond the raid high/low or entry structure.
- Management: take 40-50% partial at `1R`, move to breakeven, then hold for opposite candle high/low.
- Expiry/roll rule: if the `01:00` candle range is not raided before the `05:00` candle closes, stop using the `01:00` candle and wait for the `05:00` candle range instead.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## SMT With Session Highs/Lows

- Strategy ID: `bsi_v3_smt_session_hl`
- Source: `43. Utilizing SMT With Session Highs & Lows.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Instrument groups: NAS100/US30/S&P500; EURUSD/GBPUSD/AUDUSD/NZDUSD and yen-cross groups are named as correlated sets.
- Setup: mark session highs and lows on correlated instruments, especially Asian and London session highs/lows.
- SMT logic: if one correlated instrument sweeps a session high/low and the other fails to sweep the equivalent level, SMT divergence is present.
- Selection rule: primarily trade the instrument that did not sweep liquidity because it is showing relative strength/weakness in the intended reversal direction. Mentor says both can be traded, but prefers the non-sweeper.
- Entry trigger: use change in state of delivery, defined as candle close beyond the relevant order block body or SMT candle. Old-style MSS + OB entry is also acceptable.
- Target: opposite session high/low. For Asian session context, if the low is taken, target Asian high; if high is taken, target Asian low.
- Management: BE after `1R`; in strong HTF/order-flow alignment, BE can be delayed until around `1.5R`. Partial at `1R-1.5R` is acceptable.
- Session scope: do not use New York PM session for this model per transcript.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model plus confluence upgrade for Asian/session strategies.

## 1 Hour Candle Ranges / CRD

- Strategy ID: `bsi_v3_1h_candle_ranges`
- Source: `44. 1 Hour Candle Ranges.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Concept: one-hour CRD/turtle-soup model.
- Timing: use the `08:00` one-hour candle. Wait for that candle to close, then mark its high and low.
- Timeframe: after marking the 8AM H1 candle range, drop directly to M1.
- Trigger: wait for price to raid/take out the 8AM H1 high or low.
- Entry: after raid and structure shift/entry array, enter from the taught zone/OB/FVG.
- News filter: watch 09:30 high-impact news. If major news is at 09:30, avoid the setup.
- Invalidation: if both the H1 high and H1 low / intended target side are taken before entry, the trade is invalid.
- Management: use fib from CRD low to CRD high; at 50% of the range, take one partial and move to breakeven.
- Probability enhancers: the 9AM candle sweeping 8AM high/low plus separate liquidity increases probability. Equal highs/lows or liquidity formed inside FVG can be target/draw context.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Enigma / Engineered Range Model

- Strategy ID: `bsi_v3_enigma_range`
- Source: `45. The Enigma.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Concept: mentor says there is no literal Enigma, but this is the closest thing and is his preferred range-trading method when present.
- Bias requirement: can be traded regardless of bias/order flow when the setup is present.
- Timeframes: works on all timeframes, but mentor trades M15 and above. H1 and H4 are also good; D1 can be used for daily bias context.
- Instrument scope: transcript explicitly says it works with every crypto coin. Examples also discuss forex and NAS100, especially pre-Asian/session ranges.
- Range construction: after a directional push, identify the opposite range boundary from the swing that caused an MSS. A low becomes valid range low because it caused MSS; a high becomes valid range high because it caused MSS.
- Trigger: wait for price to take out either range low or range high.
- Re-entry requirement: after the sweep and MSS, price must close back inside/above/below the range boundary. MSS alone is not enough.
- Entry: enter from the OB, usually consecutive down-close candles for longs or consecutive up-close candles for shorts. 50% of the OB or retest of range boundary can also be used in examples.
- Stop: beyond the sweep extreme or protected structure; for tight ranges mentor allows the tighter protected stop, but if the safer stop is needed it should be used.
- Management: draw fib from range high to range low or inverse; partial and BE at `0.5`; `0.79` can be main target or second partial; final target is the opposite range side (`1.0`) when narrative supports it.
- Low probability / invalidation: if price reaches `0.5` before giving entry, setup becomes low probability. If price reaches `0.79` before entry, setup is invalid.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.

## Turtle Soups & Ranges Mastery

- Strategy ID: `bsi_v3_turtle_soups_ranges`
- Source: `40. Turtle Soups & Ranges Mastery.mp4`
- Evidence status: transcript complete; visual contact sheet generated.
- Instrument scope: mentor says it does not work really well with forex, but works well with indices and crypto. NAS100 and crypto are emphasized.
- Timeframe/session: usually M1, during London and New York sessions. Do not trade Asian session because volume is low.
- Range construction: identify a significant range high/low after a directional push and significant pullback. A+ range has liquidity buildup on both sides, near range high and range low.
- Direction preference: if overall M1 trend is bullish, prefer range low sweep first; inverse for bearish. This is a preference, not a hard requirement.
- Trigger: wait for sweep of range low/high, optional SMT divergence, MSS/displacement, and price closing back into the range. Do not enter until price comes back into the range.
- Entry: enter from FVG, breaker block, OB, or another good entry zone after re-entry into range.
- Target: opposite range side. Use Fib from range high to range low; take 30-40% or 50% partial at 0.5 range and move breakeven. Standard deviation targets such as negative 4 can also be used.
- Invalidation: if target/opposite side is reached before entry, setup is invalid and no more trades should be taken from that completed range.
- Confluence: SMT, PD array, and standard deviation improve win rate.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: independent V3 strategy/model.
