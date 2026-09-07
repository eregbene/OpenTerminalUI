# BSI V2 All Strategies Video Rulebook

Video-first reconstruction for all nine BSI strategies. No implementation changes are included here.

## 1. Order Flow

Base full-confluence model.

- Requires liquidity before structure.
- Supports MSS reversal and MSB continuation.
- Liquidity may be trendline or horizontal swing/equal-level liquidity.
- MSS uses Fib on the break-causing leg and requires the correct premium/discount half.
- Entry uses unmitigated OB/FVG created by the structure break.
- Mentor OB definition: first candle that creates the FVG/imbalance.
- If FVG is very short, use FVG; otherwise use OB.
- Entry is the most extreme valid array.
- Stop goes beyond the array, with optional wider structural stop.
- Target is next opposing liquidity/structure, not fixed RR.

## 2. New York

Bare swept-level retest.

- Sweep significant swing high/low during New York.
- Entry is retest of the original swept level.
- No OB/FVG.
- No MSS.
- No Fib or premium/discount.
- Small fakeout preferred.
- If price never retests, no trade.
- Stop tight beyond fakeout.
- Target fixed 1:2.

## 3. ABC

Three-leg continuation structure.

- A = impulse.
- B = significant pullback that must not exceed A start.
- C = continuation that takes B level.
- Price must reclaim A level and break C-leg structure.
- Entry from OB/FVG created by the structure break.
- Target B-leg high/low.
- No premium/discount shown.
- Fractal/reusable if a new valid ABC forms.

## 4. Asian

Asian session range sweep.

- Mark Asian box high/low.
- Sweep one side during post-Asian/lunch-gap context.
- Wait for MSS/CHoCH.
- Enter from OB/FVG after reversal.
- Target opposite side of the same Asian box.
- No daily bias.
- No premium/discount.
- One thesis per box demonstrated; multiple entries are alternatives, not sequential rules.

## 5. Under/Over

Close-based support/resistance fakeout reclaim.

- Need clear support/resistance with at least three touches.
- Origin candle functions as order-block-quality anchor.
- Break must be candle close; wicks do not count.
- Reclaim must close back through level.
- Entry at reclaimed level.
- No MSS/CHoCH required.
- No premium/discount.
- Stop beyond fakeout/level.
- Partials at intermediate levels; full close when opposite level is taken.
- Skip huge fakeouts.

## 6. 9:30

New York open index sweep.

- Instrument focus: indices, especially NAS100/S&P500.
- Hard window 09:30-11:59 New York.
- Identify liquidity on 15m or similar HTF.
- Execute on 1m, or 2m if needed.
- Daily/HTF bias required; only trade with bias.
- Sweep liquidity, then MSS with displacement.
- Reject clumsy MSS.
- Prefer extreme FVG.
- Strong displacement can substitute for clean MSS.
- Target-setting band: 3R-5R.
- Partials and structure-break breakeven are part of management.

## 7. Reactionary

Order Flow plus second-array confirmation.

- Array 1 forms from standard Order Flow MSS/MSB.
- Price enters/mitigates array 1.
- Price pushes away and creates array 2.
- Array 2 does not need fresh structure break.
- Entry is array 2, never array 1.
- Stop beyond array 2 or wider structural extreme.
- Natural opposing target.
- 1:2-1:5 is mentor discipline, but examples can exceed it.

## 8. ABCD

ABC reversal plus New York-style D-leg.

- Complete ABC first.
- D leg moves opposite C.
- D leg breaks the B-leg endpoint.
- Price reclaims and retests that endpoint.
- Entry is level retest, not OB/FVG.
- Stop beyond D fakeout extreme.
- Default target fixed 1:2.
- Optional structure/1:3 target flexibility is spoken.
- Optional tiny FVG/OB at D is confluence only.

## 9. OB Liquidity

Order Flow plus same-array fakeout/reclaim.

- Origin OB must be local extremum.
- Internal structure break is sufficient.
- Same origin OB is used for fakeout, reclaim, and entry.
- Fakeout must close beyond OB edge; wicks do not count.
- Reclaim must close back through same level.
- Entry on retest of same origin OB.
- Heavy clean reaction means valid OB, not liquidity OB.
- Older residual wick liquidity must clear first.
- Stop beyond origin OB.
- Target next imbalance/liquidity.

## Critical Distinctions

- New York is bare retest; ABC uses OB/FVG.
- Reactionary uses a second array; OB Liquidity reuses the same array.
- Under/Over is close-based level reclaim; New York sweep is wick-based.
- 9:30 has daily bias and indices focus; Asian explicitly does not require daily bias.
- Order Flow uses premium/discount; most sub-strategies do not show it in their own videos.
