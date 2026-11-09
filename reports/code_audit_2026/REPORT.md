# Bensim source code and software correctness audit (2026-11-03)

**Read-only.** No source, configuration, data, database record or engine state was changed, and no order was placed.

**What was run:**
- the existing offline/DB-read tests (**26/26 pass**) and module self-checks (all pass);
- a hand-verified calculation script `reports/code_audit_2026/math_checks.py` (18/18 correct, 1 limitation);
- static inventory/dependency extraction (`inventory.csv`, `deps.json`);
- read-only DB queries on `live_signals` / `live_books` for the entry-gap measurement.

**Findings:** [FINDINGS.md](FINDINGS.md) (F01-F29). **Tests:** [TEST_GAP_ANALYSIS.md](TEST_GAP_ANALYSIS.md).

## 1. Executive summary: the eight questions
1. **Is the software mathematically correct? Yes, for everything checked.**
   - Pip sizes, tick-value sizing (EURUSD, USDJPY, GBPJPY, USDCAD, XAUUSD), lot flooring, USD conversion, commission-in-R, swap-in-R, R multiples, guard currency legs and the statistics engine all match independent hand calculations.
   - **Exception:** the backtest can't price cross pairs (F14, KeyError).
2. **Does each strategy implement its documented rules? Mostly.**
   - **One systematic deviation:** every strategy documented with a *limit* entry (FVG CE, OB, fib 0.618) actually sends a **market order at the close of the bar that touched the level** (F06). It shifts R:R (fib618 median 3.04 → 2.78 live).
   - The fib level is frozen at the breakout bar and the target is ATR-based. Both are implemented as coded, and both are design properties.
3. **Does the backtest simulate trading correctly? Mechanically yes:**
   - completed-bar windows, causal indicators (self-check), stop-first on conflicts, gap fills at the open, bid/ask sides, commission and swap, one trade per strategy per symbol.
   - **It doesn't simulate the live portfolio** (F20): no pair-slot, priority, max-open or guard.
   - **Before 2026-11-03 it didn't simulate the live Friday close or swap** (F01).
   - **Its spread model misses the Sunday reopen and the winter rollover** (F12).
4. **Does demo execution reproduce the backtest? On shared code, yes:**
   - identical signals, stops, targets and lots on 38/38 shared trades (reconciliation);
   - a ~0.10R/trade execution shortfall from the ~8 s order timing (F13);
   - books with their own live logic (parallax two legs, cta trail, sflow targets) differ from their backtests (F02, F16, F17).
5. **Are risk controls consistently enforced? No.**
   - flow bypasses max-open, currency, per-trade-risk and worst-case-day checks with fixed lots (F04);
   - sflow / parallax / cta / manager have no minimum stop or lot cap (F03);
   - the active guard is the permissive DEMO config (F05);
   - manual positions are invisible to the guard (F10);
   - the daily-loss base shifts after downtime (F11).
6. **Could defects explain the poor performance? Only partly, and not the main part.**
   - The findings move results by roughly 0.1-0.3R per trade:
     - weekend holds in old backtests (F01, inflating old results);
     - execution timing and spread (F12/F13, ≈ −0.1R/trade live);
     - market-instead-of-limit entries (F06, slightly worse entries);
     - tiny sflow stops (F03).
   - **None turns a negative strategy positive.** Pullback, h1_fvg and orderflow are negative before costs, and the long-run results don't depend on these defects.
   - The finding that no strategy has an edge still stands. Software defects make the *historical* numbers somewhat **too optimistic**, not too pessimistic.
7. **Fix before further research:**
   - F01: always use the live-rule profile;
   - F12 + F13: cost/spread realism or a shortfall sensitivity;
   - F14: if FXCM crosses are used;
   - F20: a portfolio layer, if portfolio claims are made;
   - F06: decide market vs limit and document it;
   - F15: insert-only ingestion;
   - F08 / F25: reproducibility.
8. **Engineering improvements unrelated to profitability:**
   - F03, F04, F05, F10, F11 (risk control);
   - F07, F09, F16, F21, F28, F29 (execution robustness);
   - F22, F23, F24, F26, F27 (consistency, docs, config).

## 2. Source inventory
- **128 Python files, 28,204 lines:**
  - `bensim/` 101 files;
  - `optsim/` options research;
  - `portfolio/`;
  - `bensim_github_lab/` (external research copies).
- **66 files are untracked by git**, including **`bensim/cta_momentum.py`, which the live engine imports** (F25).
- Full list with line counts, git status, first docstring line and internal imports: `inventory.csv`; import graph: `deps.json`.
- **Runtime processes:**
  - `bensim.mt5_bridge` (MT5 HTTP bridge, :8773);
  - `bensim.live` (engine);
  - `bensim.tradeguard live` (25K account guard);
  - started by `bensim/autostart.ps1` (Windows task every 5 min, admin) → `start_live.ps1` (watchdog loop) and `start_bridge.ps1`.
- **Third-party:** numpy, pandas, psycopg, MetaTrader5 (host only), psutil (optional), scikit-learn / lightgbm (flow models, via `macro_yields` / `edge_miner`), pytest.
- **Configuration:** repo `.env` (read by `config._dotenv`, last occurrence wins; env vars override); `BENSIM_*` switches (books, flow, guard, risk, symbols, Friday hour, live-from); `logs/tradeguard.json`.

## 3. Architecture and dependency map
**The live engine (`live.py`) pulls in 29 modules**, including research modules it doesn't obviously need:
- `phantom` (via `sflow`);
- `edge_miner`, `edge_wf`, `macro_yields` (via `flow_live`);
- `binance_history`, `crypto_momentum` (via `crypto_paper`);
- `fxcm_history`.

A failure at import time in any of them stops trading.

```
autostart.ps1 → start_live.ps1 (watchdog) → python -m bensim.live
                └→ start_bridge.ps1 → python -m bensim.mt5_bridge (MetaTrader5 → E:\MT5\Bensim-100k)
live.cycle (every M5 + 8 s)
  bridge.account/positions/tick ─► guard.flatten_now ─► Friday close (close_all: all bensim magics)
  ─► shadow / trade_watch (observe) ─► time exits
  ─► fib618: bars(sym) → windows → strategy.plan → live_signals insert (PK) → decide → order_for → lots_for → guard.check → bridge.order
  ─► Bennett: bars + h1_bars → bennett_windows → bennett_strategy.plan → live_signals_bennett → decide_bn → guard.check → order
  ─► flow_live.step (ML / AM; own checks; fixed lots) → bridge.order
  ─► books.step: imbalance/enigma via plan(); sflow/parallax/cta via own signals → pick (one per pair) → _send → guard.check → order
  ─► crypto_paper.step (Binance REST, paper files)
manager.py (CLI) → guard.check → bridge.order (magic 770109)
research: backtest.run_symbol/_simulate ← the same plan() functions; timemachine; validate/registry; audit_p2*; reconcile
```

## 4. Strategy-by-strategy audit
| Strategy | Indicators / timeframes | Entry as coded | Stop / target as coded | Expiry / lock | Session / Friday | Doc vs code |
|---|---|---|---|---|---|---|
| fib618 (`strategy.plan`, LIVE params) | Donchian 20 on M15 (shift 1), ATR14 Wilder (`atr` L93), ATR/SMA50 ≥ 1.1, H1 `trend` (pivots confirmed at j+k, causal) | first M5 touch of leg1 − 0.618·leg (leg end frozen at the signal bar), **market at bar close** | origin ∓ 0.1 ATR; next-bar open ± 4 ATR; skip if stop < 8 × cost or R:R > 20 | 48 M15 bars; internal simulated trade marks the setup busy (F18) | killzone is one of the 2-of-3 confirmations; Friday by the engine | "limit @0.618" (TV) vs market (F06) |
| imbalance (default `Params`) | same signal | base candle of the latest 3-gap chain, market at touch close | zone far edge ∓ 0.1 ATR; 4 ATR | same | none | consistent |
| enigma / H1 FVG (`fvg_strategy.plan`) | LuxAlgo FVG on H4 / H1 (UTC-aligned buckets), M15 MSS + displacement FVG | first M5 touch of the CE, market at close | leg extreme ∓ 0.1 ATR; nearest liquidity ≥ 1.5R else 2R | 48 bars, simulated busy | none | "limit at CE" vs market (F06) |
| Bennett (`bennett_strategy.plan`, LIVE) | H1 leg, H4 bias (UTC buckets), OTE 62-79 %, M5 CHoCH, 30-min agreement, liquidity targets ≥ 2.75R, clear path | market at the trigger close | tight structure stop + buffer, ≥ 8 × cost (L369, L381) | expire 48 h; plan's own hold | killzones 07-11 / 13-17, `no_sunday` (weekday formula verified) | consistent; very low frequency |
| pullback | EMA20/50 M15, band ± 1 ATR, H1 trend | first M5 after the M15 close | 1.2 / 2.5 ATR | — | none | consistent |
| orderflow | internal CHoCH, OB, fake-out | market at the touch close | fake extreme; liquidity ≥ 2R else 3R | 32 bars | none | "limit near OB" vs market (F06) |
| gf | D1 / 12H / 4H structure (UTC buckets, Sunday stub F24) | first M5 after the 4H close | beyond the 4H pullback; 12H extreme | 480 M15 | none | consistent |
| sflow (live book) | H1 close-only BOS, zone | fill when the M5 ask/bid touches the near edge, market | protected swing ∓ 0.1 ATR; impulse extreme at fill, R:R ≥ 1 | 5-day wait, 10-day hold | Friday close (engine) | **no minimum stop** (F03) |
| parallax (live book) | NY-close D1, BB + Stoch + pattern | two limits 23.6 / 38.2 % filled at market on touch | 78.6 %; −100 % extension | 5-day expiry, 30-day hold | Friday close truncates (F02) | live: two full-size legs, second blocked (F17) |
| cta (live book) | NY-close D1, SMA200 + fractals | buy-stop 1 pip beyond, market on trigger | midpoint; engine trail | 14-day expiry, 120-day hold | Friday close truncates (F02) | trail engine-managed (F16) |
| flow ML / AM | H1 features over 25 pairs + yield gaps (5-seed model) / clock | market at the hour / 09:00 UTC | disaster stop 145 / 68 bp; no TP; 6 h / 3 h | per pair | Friday close (engine) | fixed lots, guard bypass (F04) |

**Duplicate prevention verified** (H): `live_signals` PK (symbol, signal_time, dir); `live_books` PK (book, symbol, signal_time, dir, leg); `live_flow` unique; inserts happen before the order, so two engines can't double-send one signal.

## 5. Mathematical calculation audit (`math_checks.py`, all hand-verified)

| Calculation | Result |
|---|---|
| Lots (risk / (ticks × tick value), floored to the step) | EURUSD 10-pip $500 → 5.00; USDJPY 20-pip $500 → 3.92 ($127.39/lot); GBPJPY 30-pip $250 → 1.30; USDCAD 15-pip $244 → 2.27; XAUUSD $16.7 $500 → 0.29; the float-floor guard holds |
| USD per unit move | 1 for USD-quote, 1/price for USD-base; **crosses KeyError (F14)** |
| Commission R | 0.07R EURUSD 10-pip; 0.05495R USDJPY 20-pip; 0.007R XAUUSD $10 |
| Swap R | EURUSD 2 nights × −$8.033 / 15-pip risk = −0.1071R |
| Pips | EURUSD 1e-4, JPY 0.01, XAUUSD 0.1 (= $0.10) |
| Guard legs | XAUUSD sell = (XAU −1, USD +1); GBPJPY buy = (GBP +1, JPY −1) |

- **Live R** (reconciliation) uses the planned risk at the quote. **Backtest R** uses the risk at the bar-close entry. The definitions are documented and consistent with sizing.
- The drawdown / Sharpe / PSR / DSR / bootstrap / PBO formulas pass `validate._selftest`.
- **Not verified here:** the broker's own `trade_tick_value` for crosses (taken from MT5 at runtime).

## 6. Backtesting engine audit
- **Ordering and indexing: verified.**
  - Candles are ordered by ts.
  - `resample` uses UTC buckets.
  - `plan()` gets windows that end at completed bars (`searchsorted(t + sec, now, "right")`).
  - The trigger bar is included via `k+1`.
  - Exits start at k+1.
  - `now = t[k] + 300`.
- **Re-plan schedule:** M15 boundaries plus waiting-zone touches. Bennett asks for every M5 (`waiting.append((1, inf, None))`). **Equivalent to live's every-M5 call** for all strategies (verified in code).
- **Fills: verified.**
  - Entry = bid close (sell) / bid + spread (buy) + 0.1 pip slippage.
  - Exits long on bid, short on ask.
  - Gap through the stop = the open (worse).
  - Target exits at the target (no positive slippage).
  - Both touched = stop.
- **Costs:** spread constant per symbol plus a fixed UTC rollover window (F12); commission per lot; swap only with the P0 profile (F01).
- **Lifecycle:** one trade per strategy and symbol (`open_until`); time exit by bar count; Friday close only with the profile; **no portfolio interactions** (F20); no floating equity in `summarize` (closed trades, fixed fraction; comment mismatch F23).
- **Lookahead / leakage:** none found in the shared code.
  - Donchian `shift(1)`;
  - pivots registered at confirmation;
  - `confirm()` indexes by signal bar;
  - `indicators._selfcheck` causality passes.
- **Holes:** not detected inside the backtest. A strategy sees a hole as a price jump, with no false-signal guard; `data_audit` reports holes.
- **Survivorship:** n/a for FX; **symbol selection bias exists** (live.py L42).

## 7. Demo execution audit
| Question | Answer (code) |
|---|---|
| Completed-candle confirmation | `bridge.rates(start=1)` (excludes the forming bar); stale check: the newest bar must be the one just closed, else skip (live.py L218-223) |
| Order construction | market `TRADE_ACTION_DEAL`, buy at ask / sell at bid, SL+TP attached, deviation 20 points, filling FOK/IOC/RETURN by symbol, **demo-only guard in the bridge** |
| Lot sizing | broker tick value / size, floor to the step, `volume_min` check; **`volume_max` not checked** (F21) |
| Broker response | ok only on retcode DONE; partial = "rejected" (F09); no retry; timeout leaves `decision` NULL (F09) |
| Duplicate orders | prevented per signal (PKs), also across two engines |
| Missed signals | possible: engine/terminal outage (2026-10-18), long cycles (F07), or data differing from the DB at the 48-bar edge (XAUUSD 10-26) |
| Stale data | refused per symbol |
| Outdated positions | positions fetched every cycle; books use one snapshot per step |
| Registers a placed order? | yes via the `detail.result`; a DB failure after `bridge.order` loses the record (manager F29; others update after the order) |
| Assumes success incorrectly? | no; it may record a partial as a rejection (F09) |
| Trades with incomplete account info? | `bridge.account` failure raises; after 3 failures the engine exits (watchdog restarts) |
| Manual closes / broker SL-TP | only visible in MT5 deals (Phase 3 deals sync) |
| Restart | signal PKs make restarts idempotent; `initial` / `day_start` from `live_state` (F11) |
| Concurrency | single engine; manager CLI race possible (F28) |

**Compared with the backtest:**
- same `plan()` and windows;
- different fill timing (+8 s), rollover/reopen spreads;
- portfolio rules exist only live;
- book-specific live logic differs for parallax/cta/sflow.

## 8. Risk management audit
- **The enforcement matrix** is in the second-stage review §4. Gaps: F03, F04, F05, F10, F11.
- **Applied uniformly to every bensim magic:** daily flatten, Friday close, kill switch.
- **Per-trade risk:** 0.5 % × initial (fib618, ×0.5 on 2/3 confirmations), 0.25 % (Bennett, books, manager). **Flow is fixed lots.**
- **Minimum stop distance:** only inside the `plan()` strategies.
- **No lot cap.**
- **The currency cap is effectively off** (DEMO 99).

## 9. Shared components
- **ATR:** `strategy.atr` (Wilder EWM, used by most), `indicators.atr_n` (200-bar), pandas rolling ATR in cta / books. Different definitions are used by design; no defect.
- **Swings / structure: four independent implementations.**
  - `strategy.trend` (k = 2 pivots);
  - `indicators.swings` / `structure` (LuxAlgo-style);
  - `bennett_smc.structure` (protected levels, closes);
  - `sflow.structure` (close-only BOS).

  They can disagree on "trend" for the same bars. That's consistent per strategy, but a source of confusion (LOW).
- **FVG:** `indicators.fvgs` (LuxAlgo auto threshold) and `bennett_smc.fvgs`: two definitions.
- **Aggregation:** `backtest.resample` and `strategy._agg` are identical UTC bucketing. The `parallax.ny_bars` broker day is used by parallax / cta. The daily definitions differ (F24).
- **Currency tables:** `CURRENCIES` is duplicated in `backtest.py` and `guard.py` (the guard handles crosses by fallback, the backtest doesn't).
- **Time:** `mt5_time` (US DST rule, verified against data).
- **DB access:** `db.connect` per call, autocommit for logs.

## 10. Data integrity
- **Candles:** OHLC validated at ingestion (`history.download`, `backfill`); 0 duplicates; UTC via the US-DST server rule (verified by the gold daily-break shift on Nov 1).
- **Holes:** reported in `data_audit`, not filled.
- **The `history.store` upsert overwrites** (F15).
- **Bid-only bars;** spread is modelled.
- **USDCAD only from 2024-02-06.**
- **Live bars come straight from MT5 each cycle;** the DB is research only. They can diverge, and there's no fingerprint (F08).
- **Calendar ends 2026-10-18** (F27).
- **A missing bar can create a false signal:** a hole becomes a single large bar after resampling, which can satisfy the ATR / Donchian conditions. There's no guard (MEDIUM, covered by test T8).

## 11. Testing coverage
See TEST_GAP_ANALYSIS.md.
- 26 tests plus 4 self-checks, all passing.
- **There are no tests for** the bridge, the live cycle, order construction, books/flow/manager execution, data ingestion, or most strategies' `plan()` on hand-built cases.
- **The key parity test is one-way:** live ⊇ backtest.

## 12. Verified correct (no finding)
- pip / tick / lot maths;
- bid/ask sides;
- stop-first;
- gap fills;
- completed-bar windows;
- pivot causality;
- `confirm()` indexing;
- the Bennett Sunday formula;
- the re-plan schedule equivalence;
- duplicate-signal PKs;
- demo-only bridge guard;
- stale-bar refusal;
- the Friday close covering every bensim magic;
- the swap / commission R formulas;
- the statistics formulas.

## 13. Confirmed defects / 14. Suspected defects / 15. Design weaknesses
- **Confirmed:** F01, F03-F05, F07-F17, F20-F23, F25-F27, F29.
- **Suspected:** F28.
- **Design:** F02 (in part), F18, F19, F24.

All have reproduction steps and tests in FINDINGS.md.

## 16. Prioritised remediation plan (proposal; nothing implemented)
1. **Research validity (before any E1-E4 / R1-R2 / P3):**
   - F01: mandatory live-rule profile;
   - F12 / F13: spread and shortfall realism;
   - F06: market vs limit decision + docs;
   - F14: if crosses;
   - F20: if portfolio claims;
   - F15: insert-only ingestion;
   - T16: two-way parity test.
2. **Live risk** (DEMO, still worth doing):
   - F04 flow through the guard with risk sizing;
   - F03 minimum stop + lot cap for books / manager;
   - F05 guard config decision;
   - F10 include manual positions;
   - F11 day-start base;
   - F02 / F17 D1 books policy.
3. **Observability / reproducibility:** Phase 3 (F07, F08, F25, F29); deals sync; heartbeat with cycle duration.
4. **Execution robustness:** F09 partial/timeout handling; F21 `volume_max`; F16 broker-side trail (needs a bridge endpoint); F28 manager lock; move crypto_paper out of the cycle.
5. **Hygiene:** F22-F24, F26, F27, and the tests T1-T20.

**Final note:** the software is substantially correct where it matters most for research validity: no lookahead, correct maths, and live reproduces the backtest's signals. Its defects make past results **optimistic** and leave risk controls uneven. **They don't explain the absence of an edge.**
