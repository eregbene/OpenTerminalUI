# Research-environment remediation plan (F01, F06, F12, F13, F15, F14, F20, T16)

**Proposal, 2026-11-03.** No code changed. No E1–E4, demo, paper or order activity.
- Findings: `reports/code_audit_2026/FINDINGS.md`.
- Implementation, if approved, happens in the isolated copy `D:\Devops\trading\bensim_phase3\` with a separate test DB (the Phase 3 rules), and is delivered as a diff.

## Ground rule that shapes every correction
The live demo engine imports research code:
- `books.py` uses `backtest.Exec()` and `backtest._spread()` for its own live fill and trail decisions (L51, L212, L290);
- `live.py` uses `backtest._win`, `resample`;
- the strategies' `plan()` and `order_for` are shared.

**So no research correction may change an existing default, function signature or output.** Every change is a new, opt-in code path: a new profile, function, field or option. A regression test proves the defaults are byte-identical: re-run the saved P2/tm_check trades with defaults and compare.

## Tracks
- **Research track (this plan):** F15 → F01 → F12 → F13 → F06 → F20 → T16 → (F14 only if crosses are approved).
- **Engineering track (separate, not in this plan):** risk controls F03/F04/F05/F10/F11/F02/F17, execution robustness F07/F09/F16/F21/F28, Phase 3 logging F08/F25/F29. None of those is changed here.

**One interaction:** future parity checks (T16) are much stronger with Phase 3 heartbeats and bar fingerprints. For the past period, T16 uses the MT5 journal and `live_events` instead.

---

## F15: historical data can be silently overwritten (do first: foundation)
1. **Existing:** `history.store` (history.py L55-70) does `insert … on conflict do update` and overwrites OHLC/volume/source of existing bars. `backfill.py` is insert-only. `registry.data_fingerprint` = row count, first/last ts and `sum(close)` per symbol.
2. **Problem:** a re-download can change bars under past results with no trace. The fingerprint is too weak to detect single-bar changes reliably.
3. **Correction:**
   - (a) `history.store` becomes insert-only by default.
   - (b) A changed bar is written to a new append-only table `candle_revisions` (symbol, tf, ts, old OHLCV, new OHLCV, detected_at, source, reason) **instead of** updating.
   - (c) An explicit `--apply-revisions` flag applies them, after a report.
   - (d) `registry.data_fingerprint` becomes a sha256 over the ordered (ts, o, h, l, c) rows per symbol and month, stored with every registered run.
   - (e) A new table `candle_spread` (symbol, tf, ts, spread_points, source) for F12. It's additive and never touches `candles`.
4. **Files:** `bensim/history.py` (store), `bensim/registry.py` (fingerprint), `bensim/db.py` (additive schema). `backfill.py` is already insert-only (unchanged).
5. **Dependencies:** none. F01/F12/F13/F20 depend on it (fingerprints in every run).
6. **Tests:**
   - storing identical bars twice → 0 changes;
   - a changed bar without the flag → 0 updates, 1 revision row;
   - with the flag → updated, revision kept;
   - a fingerprint changes when one close changes by 1 point, and is stable otherwise.
7. **Verification:** run against a copy of the candles in the test DB; existing rows unchanged (checksum before and after).
8. **Strategy rules changed:** no.

## F01: one consistent live-rule research profile
1. **Existing:**
   - `backtest.Exec()` defaults are the legacy model: weekends held, no swap, raw-spread guess + $7/lot.
   - `exec_profile("observed_account")` (P0) adds the Friday 20:00 close, swap per lot-night from deals, observed spread and $0 commission.
   - Research scripts choose their Exec ad hoc.
   - Symbol sets are hard-coded per script (P2: 6 for all).
2. **Problem:** nothing forces a research run to use live rules or records which rules it used. Live symbol sets differ per book (`live.LIVE_SYMBOLS`, `live.BN_SYMBOLS`, `books.SYMBOLS`).
3. **Correction:** a versioned research profile `research_profile("live-v1")` returning a frozen object with:
   - **rules:** Friday close 20:00 UTC and no entries Friday ≥ 20:00 for **every** book (as `live.close_all`); time exit as now (600 M5 bars), with the live wall-clock difference documented (F22, not changed here); the per-book live symbol set, snapshotted from the live modules into the profile and stored, not read at runtime from env;
   - **costs:** the F12 spread model + F13 entry model + observed commission and swap;
   - **a profile hash.**

   Research entry points (`backtest.main`, new runners) **require** `--profile` (legacy only by an explicit `--profile legacy`). `registry.append` refuses research runs without a profile name + hash + data fingerprint. `Exec()` defaults and `exec_profile()` stay untouched (live books depend on them).
4. **Files:** `bensim/backtest.py` (new `research_profile`, `main --profile`), `bensim/registry.py` (required fields). New runners use it. Old scripts stay as they are (historical, marked legacy).
5. **Dependencies:** F15 (fingerprint), F12/F13 (cost part; until they're done the profile uses the P0 observed costs and is versioned "live-v0").
6. **Tests:**
   - the profile hash is deterministic;
   - `Exec()` defaults produce trades identical to the saved legacy CSVs (regression);
   - with the profile: no hold past Friday 20:00, no Friday ≥ 20:00 entries (extends `test_backtest_p0`), the symbol set per book equals the live constants at snapshot time;
   - the registry rejects a run without a profile.
7. **Verification:** re-run P2 v1's observed profile through `live-v0` → identical trades. A `live-v1` run on the same window → differences explained by F12/F13 only.
8. **Strategy rules changed:** no. It makes simulation match the existing live rules.

## F06: documented limit entries vs actual market entries
1. **Existing:**
   - `strategy.order_for` (L353-364), shared by fib618, imbalance, fvg/enigma/H1 FVG, orderflow, pullback, gf and Bennett, sends a **market** order at the bid/ask after the M5 bar that touched the level closes.
   - The backtest does the same at the bar close.
   - The docs describe **limits** (fvg_strategy docstring step 5 "limit at CE"; orderflow "limit near OB"; TradingView fib "limit @0.618").
2. **Problem:**
   - The designed entry price and R:R aren't what is traded: fib618 live R:R median 3.04 → 2.78; 15 % of orders' risk differs > 25 % from the zone risk.
   - Research describes one strategy and simulates another.
   - The trade records don't store the designed level, so the gap is invisible in backtests.
3. **Correction:**
   - **(A, recommended, no rule change):** declare market-at-touch-close the official entry rule. Correct every docstring and research document. Add the designed level (`plan["entry"]`) to every backtest trade record as `zone_entry`, plus `zone_gap_r`, so the distortion is measured in all future research.
   - **(B, optional, a different strategy):** a research-only entry model `entry_model="limit_at_level"` in `_simulate` (fill at the level when the bar's bid/ask crosses it; stop-first if the same bar crosses the stop; the order expires as the plan expires). It can't be traded live without broker pending orders (a bridge change, engineering track). So results from (B) would need their own pre-registration and wouldn't describe the live strategy.
4. **Files:**
   - (A): docstrings in `fvg_strategy.py`, `orderflow_strategy.py`, `strategy.py`, `docs/tradingview/*` notes; `backtest._simulate` adds `zone_entry` and `zone_gap_r` columns (additive output; `save()` ignores extra columns).
   - (B): `backtest._simulate` new option (default off).
5. **Dependencies:** F13 (the realistic market fill is what (A) declares as the rule).
6. **Tests:**
   - (A): `zone_entry` equals `plan["entry"]` for every trade; trades otherwise identical to before (regression).
   - (B): hand-built bars for a limit fill, a no-fill, a limit+stop same bar, and expiry.
7. **Verification:** the zone gap distribution in a backtest replay of Oct 13 → Nov 3 matches the live measurement (fib618 median ≈ +0.02R).
8. **Strategy rules changed:** (A) no; it documents the implemented rule. (B) yes; a new variant, pre-registration required.

**Decision needed: A only, or A + B.**

## F12: spread model (hour, rollover by DST, Sunday reopen)
1. **Existing:**
   - `backtest._spread` (L79-84) = constant `raw_spread_pips[sym]` × `rollover_mult` (4) inside a **fixed UTC** window 20:55-22:05.
   - The observed profile replaces the constant with a 5-sample mean from one London/NY session.
2. **Problem:**
   - The broker's rollover is at **server midnight** = 21:00 UTC (US summer) / 22:00 UTC (US winter), so in winter the widening after 22:05 isn't modelled.
   - The **Sunday reopen** isn't modelled (USDCHF 2026-11-01 22:05: 33.8 pips vs the model).
   - No hour-of-day variation (Asia, pre-news).
   - The calibration rests on 5 samples.
3. **Correction:** a new function `spread_model(sym, t, profile)`, used only by research profiles. Legacy `_spread` is untouched (live books use it). Three parts:
   - (a) A per-symbol, per-hour-of-week median spread table.
   - (b) A rollover window anchored to server midnight via `mt5_time.server_offset_hours` (window and multiplier calibrated).
   - (c) A weekly-reopen component for the first N minutes after the Sunday open (calibrated).

   **Calibration data** (choose one or both; each is a separate data-collection approval):
   - **(i) Recommended:** MT5 per-bar `spread` (points) via the **existing** read-only bridge `/rates`. It already returns the field (mt5_bridge `_rows` passes all fields) and `history.py` drops it today. Stored in the new `candle_spread` table, so no bridge change. Caveat: MT5's bar `spread` is a single value per bar (typically the bar's opening/minimum spread), so it understates spikes. Hence (ii).
   - **(ii)** A light read-only `/tick` sampler (e.g. 1/min × 7 symbols for 2–4 weeks) to calibrate the bar-spread → real-spread ratio, especially at rollover and reopen. Runs outside the trading cycle.
4. **Files:** `bensim/backtest.py` (new `spread_model`, used when the profile says so), `bensim/history.py` or a new `bensim/spread_history.py` (collect spread into `candle_spread`), `bensim/mt5_time.py` (helper for server-midnight UTC times; additive).
5. **Dependencies:** F15 (new table, fingerprinting), F01 (the profile selects the model).
6. **Tests:**
   - the rollover window lands at 21:00 UTC in summer and 22:00 UTC in winter on the DST change dates;
   - a Sunday-open bar gets the reopen spread;
   - the hour profile is looked up correctly;
   - legacy `_spread` output is unchanged for a grid of (symbol, t);
   - the calibration is reproducible from stored data.
7. **Verification:** model spread vs observed (bars + samples) by hour, with error statistics. Replay the reconciliation trades: the USDCHF reopen trade's entry is now within the modelled range.
8. **Strategy rules changed:** no (cost realism only).

## F13: execution delay and realistic entry prices
1. **Existing:**
   - The backtest enters at the trigger bar's close (bid, + modelled spread for buys) + 0.1 pip slippage; the stop exit also gets 0.1 pip.
   - Live enters ~8 s after the bar close at the live quote (`live.main` sleeps to boundary + 8 s; `decide` → `bridge.tick` → `order`).
2. **Problem:** a measured **−0.10R/trade** shortfall on 27 shared trades. The median entry is +0.2 pip worse, driven by movement in the 8 s, not broker slippage (fill vs quote ≈ 0). Stop-exit slippage is larger than 0.1 pip in fast markets.
3. **Correction:** a research-only entry/exit execution model in the profile:
   - (a) entry = bar close + a **delay drift** term + spread, where the delay drift is drawn from an empirical per-symbol distribution of (quote at decision − bar close) in pips, signed by direction;
   - (b) stop-exit slippage from an empirical distribution (fill − SL) per symbol;
   - (c) deterministic modes for sensitivity: `none` (= today), `median`, `p75`.

   **Calibration:** live decisions since 2026-10-13 have the quote at decision (`detail.price`) and the stored bar close. The deals have the SL fill price. That's 38 + 30 SL fills today: small, so (c) is the primary use, and (a)/(b) are refreshed as Phase 3 logging adds records.
4. **Files:** `bensim/backtest.py` (`_simulate` new optional model fields; default none → identical); new `bensim/exec_calibration.py` (reads live records + deals → calibration JSON, read-only).
5. **Dependencies:** F01 (profile), F12 (spread at decision time), Phase 3 (more calibration data later; not required to start).
6. **Tests:**
   - model none → trades identical (regression);
   - a fixed +X pip delay shifts every entry by X in the adverse direction and R accordingly (hand case);
   - stop slippage applies only to stop exits;
   - the calibration script is deterministic on a fixture.
7. **Verification:** re-simulate the 38 shared trades with `median`. The backtest-minus-live R gap should shrink from −2.69R (same-exit trades) towards 0, with the residual reported.
8. **Strategy rules changed:** no (execution realism).

## F14: cross-pair support (only if the FXCM research uses crosses)
1. **Existing:** `backtest.CURRENCIES` holds majors + XAU + crypto. `_usd_per_unit_move` raises KeyError for crosses (verified `math_checks.py`). The guard handles crosses by fallback. `load_m5` reads only the DB or Binance files (no FXCM).
2. **Problem:** the backtest can't price GBPJPY/EURJPY/… P&L in USD and can't read FXCM bars.
3. **Correction:**
   - (a) `usd_per_unit_move(sym, price, t, conv)`: for a cross XXXYYY use the USD value of 1 YYY at time t from the conversion pair's bars (YYYUSD → price; USDYYY → 1/price), time-aligned. The legacy function stays for majors (unchanged).
   - (b) Per-symbol contract/pip/cost tables for crosses. Cross costs on IC are unknown, so they're estimated from FXCM relative spreads × the IC/FXCM ratio on majors, and labelled *estimated*.
   - (c) An FXCM bar source for research: `load_bars(sym, source="fxcm")` with a **hole map**; trades whose window touches a hole are flagged and excluded (no filling).
4. **Files:** `bensim/backtest.py` (new functions; legacy untouched), new `bensim/fxcm_source.py` (loader + holes), cost tables in the profile.
5. **Dependencies:** F01, F12 (cost model per symbol), F15 (fingerprints for FXCM files: sha256 per file).
6. **Tests:** hand-checked USD P&L for EURJPY, GBPJPY, EURGBP, AUDNZD at known rates; majors unchanged; the hole map excludes a synthetic trade spanning a missing week.
7. **Verification:** majors on FXCM vs IC data over an overlapping month → trade sets compared, differences reported (not required to match).
8. **Strategy rules changed:** no.

**Decision needed: is F14 required?** E2 on the 5 FX majors (FXCM has no XAUUSD) doesn't need it. It's mainly needed to give Bennett/enigma enough trades.

## F20: portfolio-level simulation
1. **Existing:**
   - `backtest.run_symbol` simulates one strategy on one symbol. Its own `open_until` locks the next signal until its simulated trade exits.
   - No pair-slot, priority, max-open, currency-side, worst-case-day or Friday-close-all across strategies.
   - P2 v2's `one_per_pair` is a post-hoc approximation.
2. **Problem:**
   - Live accepts trades through `live.cycle`'s fixed order (fib618 → Bennett → flow → books with `books.pick` by confirmation) and `guard.check` against all open bensim positions.
   - Live rejects backtest trades (6 in 3 weeks) and takes trades the per-strategy backtest doesn't (path dependence, the GBPUSD 10-14 case).
   - Portfolio results can't be simulated.
3. **Correction:** a new `bensim/portfolio_sim.py`, event-driven over the M5 timeline:
   - (a) A signal stream per strategy and symbol, produced exactly as live does: `plan()` called after every M5 close with live windows (`live.windows` / `bennett_windows`). That means TRIGGERED signals keep coming while a trade is open, because the plan's internal busy logic is the same as live. It's implemented as a new `run_symbol(..., emit_signals=True)` mode (default off) or a dedicated generator.
   - (b) An acceptance layer replicating `live.cycle` order, `books.pick` (conf via the same `books.conf` trend function), and `guard.check` / `legs` / `flatten_now` / `risk_scale` with a selectable guard cfg (DEMO as live today, FTMO for sensitivity). Pure functions are reused; nothing in guard/books is changed.
   - (c) Accepted trades simulated with `_simulate` under the research profile. Occupancy is updated at exit; Friday close-all at 20:00.
   - (d) Flow and manager trades can't be regenerated historically. For historical windows they're excluded, and documented as such. For reconciliation windows their **actual** positions (from deals) are injected as occupancy.
4. **Files:** new `bensim/portfolio_sim.py`; `bensim/backtest.py` (optional `emit_signals` mode; default unchanged). Reuses `guard`, `books.pick` / `conf`, `live.windows` unchanged.
5. **Dependencies:** F01 (profile), F12/F13 (costs). T16 validates it.
6. **Tests:** hand-built streams for pair-slot blocking, fib618 priority over a same-bar book signal, max-open 6, currency side, worst-case-day refusal, Friday close-all, an exit freeing the slot on the next bar, and Bennett vs book on the same pair.
7. **Verification:** T16 portfolio parity on 2026-10-20 → 11-03 (all four shared-code books live, with real flow/manager occupancy).
8. **Strategy rules changed:** no (reproduces live portfolio behaviour).

## T16: two-way backtest/demo parity
1. **Existing:** `test_live.py` checks **one way** only: every backtest trade is triggered by the live path (live ⊇ backtest), for one fib618 week and Bennett Aug–Oct.
2. **Problem:** extra live triggers, portfolio effects and execution differences aren't tested.
3. **Correction:** three parity levels, all offline:
   - **(L1) Signal parity, two-way:** for fib618, Bennett, imbalance, enigma, the set of TRIGGERED signals from the live path (`plan` every M5 on stored bars) **equals** the set from `run_symbol(emit_signals=True)`. Any asymmetric difference fails.
   - **(L2) Portfolio decision parity:** `portfolio_sim` over 2026-10-20 → 11-03 with live symbol sets, DEMO guard and injected flow/manager occupancy reproduces the live decisions (`live_signals.decision`, `live_books.status`) **exactly**. The only allowed exceptions are an explicit list: engine-down windows from `live_events` / MT5 journal (e.g. 2026-10-18 18:26-05:08 UTC) and the documented XAUUSD 10-26 case (UNKNOWN).
   - **(L3) Execution parity:** with the F13 model, the modelled entry/exit vs deal prices are within calibrated tolerances. The aggregate R gap is reported.
4. **Files:** new `bensim/test_parity.py`. Fixtures: a **read-only export** of the needed candles and live tables (`live_signals*`, `live_books`, deals JSON) into the separate test DB, so the test can't touch the live DB.
5. **Dependencies:** F20 (L2), F13 (L3), F01 (profile).
6. **Tests:** the parity tests themselves, plus a negative control: deliberately perturbing one bar must break L1, which proves the test can detect a difference.
7. **Verification:** L1/L2 pass with an empty or explained exception list; L3 tolerances documented.
8. **Strategy rules changed:** no.

---

## Sequencing and the "research environment v1" freeze
| Step | Item | Approval-gated data actions |
|---|---|---|
| 1 | F15 (insert-only, revisions, strong fingerprints, additive tables) | none |
| 2 | F01 profile framework (`live-v0` = P0 observed costs) | none |
| 3 | F12 spread model | **collect MT5 bar spreads via the existing `/rates`** (read-only, into a new table) and optionally the `/tick` sampler |
| 4 | F13 execution model + calibration | reads live records + deals (read-only) |
| 5 | F06 (A) docs + `zone_entry` fields; (B) only if approved | none |
| 6 | F20 portfolio simulator | none |
| 7 | T16 parity tests L1–L3 | read-only fixture export to the test DB |
| 8 | F14 crosses + FXCM source, **only if required** | none (files already on disk) |
| 9 | **Freeze "research environment v1"**: code hash, profile `live-v1` hash, data fingerprints, all tests green, recorded in the registry | — |

**E1–E4 and R1/R2/P3 would only run after step 9**, each re-pre-registered against `live-v1`.

## Decisions needed
1. F06: option **A only** (recommended) or **A + B**.
2. F12 calibration data: (i) MT5 bar spreads via `/rates` (recommended); (ii) also a `/tick` sampler for 2–4 weeks.
3. F14: include crosses (needed only for a 25-pair FXCM study) or defer.
4. F20: guard cfg for research runs, DEMO (as live today) as primary with FTMO as a sensitivity (recommended)?
5. Confirm implementation in the isolated Phase 3 copy with the separate test DB, delivered as a diff with no deployment.
