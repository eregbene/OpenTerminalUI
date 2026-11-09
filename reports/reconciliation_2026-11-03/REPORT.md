# Live vs backtest reconciliation: 2026-10-13 06:00 → 2026-11-03 12:00 UTC

**Read-only.** Sources:
- the live DB tables `live_signals`, `live_signals_bennett`, `live_books`, `live_events`, `manager_actions`;
- MT5 deals and open positions via the bridge's read-only `/deals` and `/positions`;
- a backtest replay with the **current live rules**: `live.PARAMS` on `live.LIVE_SYMBOLS`, `bennett_strategy.LIVE` on `live.BN_SYMBOLS`, `books.IMB` / `books.ENIGMA` on `books.SYMBOLS`, `exec_profile("observed_account")` with the Friday 20:00 UTC close.

Code: `bensim/reconcile.py`. Files: `positions.csv` (every live position), `signals.csv` (every signal, both sides). The end is the DB candle end; nothing after 12:00 UTC today is included.

## 1. Live account, all books (MT5 deals)
| Book (magic) | Positions | Net USD (profit+swap+commission) | Swap USD | Sum R (vs planned risk) |
|---|---|---|---|---|
| fib618 (770101) | 26 (1 open) | **−1,560.87** | −151.05 | −2.03 (24 with planned risk) |
| bennett (770102) | 2 | +166.14 | −67.74 | +0.69 |
| imbalance (770104) | 9 | −117.50 | −9.56 | −0.47 |
| enigma (770105) | 4 | +985.33 | −6.95 | +4.05 |
| sflow (770106) | 8 | **−937.95** | 0 | −3.84 |
| cta (770108) | 1 | +739.97 | 0 | +3.36 |
| flow (770103) | 25 | +1,318.72 | +4.12 | n/a (fixed lots, no planned risk) |
| manager (770109) | 1 | +130.92 | −3.48 | n/a |
| magic 999001 | 3 | −0.26 | 0 | TradeGuard test trades ("guard test", 0.01 lots), not Bensim |
| **total** | **79** | **+724.50** | | |

- **Commission: $0 on every deal** (observed).
- **Exits:** 30 stop-loss, 5 take-profit, 25 engine (time / Friday / flow time), **18 manual** (MT5 mobile or terminal), 1 open.

## 2. Signal-level match (the 4 books with shared backtest code)
| Category | fib618 | bennett | imbalance | enigma | Explanation |
|---|---|---|---|---|---|
| matched, both traded | 24 | 2 | 9 | 4 (a) | §3 |
| backtest trade, live **skipped** | 2 | 0 | 3 | 0 | §4 |
| live recorded a signal and **skipped** it; backtest no signal | 10 | 0 | 0 | 0 | the backtest's own simulated trade was still open on that pair (it doesn't re-signal while in a trade). Same outcome: no trade |
| live **sent**, backtest didn't | 1 | 0 | 0 | 0 | GBPUSD short, signal 2026-10-14 13:00. **Path dependence, verified:** `plan()` on the stored DB bars at the live decision time (2026-10-15 00:35) gives TRIGGERED with the identical stop 1.3272731 and target 1.3151976. The backtest was still in its own GBPUSD short (signal 15:00), which live had refused (CURRENCY_SIDE_FULL). The data and code are the same |
| backtest trade, **no live record** | 2 | 0 | 9 | 1 | 8 imbalance + 1 enigma fall **before the books went live (first `live_books` row 2026-10-20 10:00)**. fib618 GBPUSD 2026-10-18 21:05 (Sunday open), fib618 XAUUSD and imbalance XAUUSD 2026-10-26 03:15/03:25: **UNEXPLAINED** (see below) |

(a) Plus 1 enigma before go-live.

**UNEXPLAINED (3 trades):**
- `plan()` on the DB bars triggers at those times, and the live stale-data check would pass.
- Live has no signal row and no log line. The engine logs only signals and errors, and keeps only the *latest* heartbeat, so "engine not cycling" and "live feed differed" can't be told apart.
- For Sunday 2026-10-18: the engine logged a start on Monday 2026-10-19 06:37, and the Friday-close code was committed 2026-10-19 06:41 UTC (`a83a77a`). The engine was most likely stopped for that deploy over the weekend. That's plausible, not proven.
- Backtest R of the 3: +3.09, −0.98, −1.00.

## 3. Matched trades (38): execution comparison
- **Lots:** live = planned on **38/38**.
- **Order timing:** live open 8–13 s after the backtest's bar-close entry (engine cycles 8 s after each M5 close).
- **Entry price:** live minus backtest, in pips (positive = live worse). Median **−0.2** (live slightly better), mean −1.14, pulled by XAUUSD (pip = 0.1) and one USDCHF fill (−33.8 pip, flagged for manual check).
- **Exit type:** same in **26/38**. The other 12:
  - 9 **manual closes** (user/manager; `manager_actions` shows "USER manual close (MT5 mobile)");
  - 1 trade from **before the live Friday rule existed** (EURUSD long 2026-10-16: the backtest closes it Friday at +0.58R; live held it over the weekend and was stopped Monday at −1.74R, the case cited at live.py:198);
  - 1 still open;
  - 1 time exit vs manual.
- **R comparison:** backtest +8.72R, live +3.24R.

| Cause | Trades | Backtest R | Live R | Difference |
|---|---|---|---|---|
| same exit type (fill, slippage, swap, R definition) | 27 | −8.46 | −11.14 | **−2.69 (−0.10R / trade)** |
| manual close by user/manager | 9 | +16.64 | +15.74 | −0.90 |
| Friday rule not yet live (2026-10-16) | 1 | +0.58 | −1.74 | −2.32 |
| still open | 1 | −0.04 | +0.39 | +0.43 |

**Execution shortfall:** on trades with the same exit, live is **0.10R per trade worse** than the observed-cost backtest. Causes: stop-fill slippage (e.g. USDCAD 10-28 −1.278R vs −1.007R), swap (−$151 on fib618), and the R denominator (live = planned risk at the decision quote).

**This is the best current estimate of unmodelled costs: ≈ 0.1R/trade.**

## 4. Backtest trades live skipped (6), with the reason confirmed by live occupancy
| Book | Pair | Signal | Live decision | Slot held by | Backtest R |
|---|---|---|---|---|---|
| fib618 | GBPUSD | 10-14 15:00 | CURRENCY_SIDE_FULL (pre-DEMO guard) | — (2 open) | −1.00 |
| fib618 | USDCHF | 10-20 12:15 | SYMBOL_ALREADY_OPEN | bennett | −1.01 |
| imbalance | GBPUSD | 10-26 13:45 | SKIP_PAIR_OPEN | fib618 (same signal bar; fib618 has priority in the cycle) | −1.01 |
| imbalance | EURUSD | 10-27 09:15 | SKIP_PAIR_OPEN | flow (AM short) | +2.52 |
| imbalance | XAUUSD | 10-30 07:45 | SKIP_PAIR_OPEN | cta | −1.00 |
| fib618 | XAUUSD | 10-30 07:45 | SYMBOL_ALREADY_OPEN | cta | +3.04 |

- **Pair-slot conflicts and fib618 priority** cost the backtest's version of these 6 trades **+1.55R** in total. Fib618 priority took 1 imbalance slot.
- **The six-position limit never bound.** Max concurrent bensim positions was **5**.
- **The currency-side limit bound twice** (Oct 14, while a stricter config was in place; DEMO's 99 since Oct 19).

## 5. Books without shared backtest code (live only)
- sflow (8, −$938), cta (1, +$740), flow (25, +$1,319), manager (1, +$131). No backtest comparison is possible with the current code. Their occupancy is included in §4.
- **sflow:** tight stops (1.26–7.5 pip) went with large lots. The 1.26-pip AUDUSD trade lost **$310 vs $244 planned (−1.27R)**: slippage on a stop smaller than ~1.5 spreads.

## 6. What this establishes
- **The live engine executes the shared-code strategies as backtested:** same signals on the same data, identical lots, entry within ~0.2 pip at the median, stops and targets at the planned levels.
- **Differences come from:**
  1. manual closes;
  2. rules introduced mid-period (Friday close 10-19, books 10-20);
  3. pair-slot interaction;
  4. a ~0.10R/trade execution shortfall;
  5. 3 unexplained misses, likely engine downtime around deploys.
- **Missing for a full reconciliation:**
  - a per-cycle heartbeat history (to prove uptime);
  - a code-version stamp per signal;
  - shared backtest code for sflow/parallax/cta/flow;
  - per-trade quote/spread at the fill (the MT5 deal price only).


---
**ERRATA (2026-11-03, see docs/audit/CORRECTIONS_2026-11-03.md C8):** in §3, "entry diff … median −0.2 (live slightly better)" is sign-reversed: live entries were **0.2 pip worse** at the median. Full R bridge in `SECOND_STAGE_FORENSIC_REVIEW.md` §2.
