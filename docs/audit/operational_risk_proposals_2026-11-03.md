# Operational risk proposals: NOT implemented (each needs separate approval)

Numbers come from the live account's own records, 2026-10-13 → 2026-11-03 12:00 UTC (`reports/reconciliation_2026-11-03/positions.csv`).
- Initial balance in `live_state` = **$97,677.19**; equity 2026-11-03 13:30 = $98,772.83.
- Hindsight P&L of the affected trades is shown for information only. With these sample sizes it is **not** evidence that any change improves returns. The case for each change is risk control.

## 1. Parallax and CTA: suspend new entries
- **Code / config today:**
  - `books.py` L46-47: `_on = env("BENSIM_BOOKS", ",".join(MAGICS))` → all five books on by default. `.env` doesn't set `BENSIM_BOOKS`.
  - parallax: `EXPIRE, MAX_HOLD = 5, 30` D1 bars (parallax.py L41).
  - cta: hold `120 * DAY` plus trail (books.py L179).
  - Every Friday 20:00 UTC `live.close_all` closes them (live.py L142-145, L198-199).
- **Why:** the backtested designs need multi-day/multi-week holds. Live can't hold past a Friday, so the live trades aren't the strategy that was tested.
- **Proposed change:** `BENSIM_BOOKS=imbalance,enigma,sflow` in `.env`, then an engine restart. `books._exits` still manages any open position of every book magic (L188-215).
- **Consequence:**
  - Live history: parallax **0 orders ever**; cta 1 (XAUUSD short, closed manually, +$739.97).
  - No pending or open parallax/cta order exists now (`live_books`: cta SENT 1, closed).
  - So **no immediate effect**; future D1 setups simply aren't taken.
- **Alternative:** exempt them from the Friday close. Not recommended: that brings back the weekend-gap risk the rule was added for (EURUSD 2026-10-16 −$423 vs −$243 planned).

## 2. Risk guard policy
- **Code / config today:** `guard.py` L22-33. `ACTIVE = FTMO if BENSIM_GUARD == "ftmo" else DEMO`; `.env` doesn't set it, so **DEMO** is active.

| Limit | DEMO (active) | FTMO cfg |
|---|---|---|
| worst-case daily risk stop | 10 % | 4 % |
| daily flatten | 10 % | 4.5 % |
| half risk from drawdown | never (100 %) | 2.5 % |
| no new trades from drawdown | never | 4 % |
| max trades per currency side | 99 | 2 |
| max open | 6 | 4 |

- **Consequence, replayed on the actual sequence of 76 live bensim positions:**
  - FTMO would have **blocked 8**: 6 by currency side 2, 2 by max open 4.
  - By book: sflow 5, fib618 1, enigma 1, imbalance 1.
  - Their combined result was −$600 (hindsight only).
  - The flow book calls only `guard.flatten_now` / `risk_scale` (flow_live.py L200), so max-open and currency-side never apply to flow orders (flow positions do count toward the other books' limits). Applying them to flow too would be a separate change; it would also have blocked 1 flow trade (−$385).
  - Max concurrent positions observed: 5.
  - Drawdown rules: equity is above the initial balance (−1.1 % drawdown), so **no risk reduction would apply today**.
  - In money terms: the daily worst-case cap falls from $9,768 to $3,907, and the flatten from $9,768 to $4,395.
- **Options:**
  - (a) Keep DEMO.
  - (b) **FTMO in full.**
  - (c) Middle: FTMO loss limits + max_per_side 2, but max_open 6.

  **Recommendation: (b) or (c).** The DEMO setting has no drawdown brake and effectively no currency-concentration cap, while USD exposure reached 4R in one direction.

## 3. Flow EURUSD morning short: sizing
- **Code / config today:**
  - `flow_live.py` L42: `AM_LOTS = 2.5` (env `BENSIM_FLOW_AM_LOTS`); L48 disaster stop 68 bp; `_send` L157-165.
  - L41: ML `ML_LOTS = 0.80`, 145 bp stop.
  - **Fixed lots, not risk-scaled, and no guard risk_scale applied.** The flow book passes the guard's checks, but its lots don't scale.
- **Numbers (live, 11 AM trades):**
  - net +$810, mean +$74, worst −$385, best +$455.
  - Disaster-stop risk = 1.12 × 0.0068 × 100,000 = **$761 per lot → 2.5 lots = $1,903 = 1.95 % of the initial balance**.
  - Typical swing: median |P&L| $113/lot → $282 (0.29 %).
- **Options:**
  - (a) **Risk-based: 0.25 % at the disaster stop = 0.32 lots.** That's ×0.128, so the realized P&L would have been ≈ +$104 instead of +$810. Consistent with the books.
  - (b) 0.5 % at the disaster stop = 0.64 lots (×0.256).
  - (c) Keep 2.5 lots and accept up to 1.95 % on one trade.
- **ML** (14 trades, 0.8 lots): disaster risk ≈ $1,299 (1.33 %) per position, up to 3 at once ≈ 4 %. Same options apply (0.25 % ≈ 0.15 lots).
- **Recommendation: (a).** The flow edge is unproven live (25 trades), and its worst case is 4–8× any other book's.

## 4. sflow: minimum stop distance and sizing safeguards
- **Code today:**
  - `books.sig_sflow` L142-152: stop = protected swing ∓ 0.1 ATR(H1).
  - `books._send` L217-243: lots = risk / loss-per-lot with **no minimum stop and no lot cap**. Only R:R ≥ 1 and the price-inside-plan checks.
  - By contrast, all `plan()` strategies require **stop ≥ 8 × round-trip cost** (`strategy.py` L273; `cost_pips` EURUSD 0.9, AUDUSD 1.0, USDJPY 1.0, USDCAD 1.1, USDCHF 1.1, GBPUSD 1.2, XAUUSD 3.0).

**Live sflow trades:**

| Pair | Stop (pip, fill → SL) | 8 × cost minimum | Lots | Net USD |
|---|---|---|---|---|
| AUDUSD | 7.50 | 8.0 | 3.25 | −260 |
| USDCAD | 7.19 | 8.8 | 4.75 | −248 |
| USDJPY | 14.06 | 8.0 | 2.73 | +239 |
| AUDUSD | 5.76 | 8.0 | 4.24 | −246 |
| EURUSD | 47.47 | 7.2 | 0.51 | +91 |
| USDJPY | 28.28 | 8.0 | 1.35 | +48 |
| USDCAD | 3.59 | 8.8 | 9.49 | −252 |
| AUDUSD | **1.26** | 8.0 | **19.39** | **−310 (planned −244: −1.27R)** |

- **Proposed:**
  - (i) the same rule as the `plan()` strategies: skip if the stop < 8 × `cost_pips`;
  - (ii) a lot cap per order, e.g. 5 lots (≈ 5× leverage on equity).
- **Consequence:**
  - (i) would have skipped **5 of 8** (−$1,315 combined, hindsight); the 3 kept made +$378.
  - (ii) would have cut 2 orders (9.49 and 19.39 lots).
  - The 1.26-pip trade shows the mechanism: a stop within ~1.5 spreads loses more than planned (slippage). The rule protects the risk budget whatever the strategy's edge.
- **Recommendation: (i) + (ii).**

## Not proposed here
Changes to fib618 / Bennett / imbalance / enigma rules, the Friday close, or existing positions.
