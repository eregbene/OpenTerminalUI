# Bensim Historical Intelligence — Persistence, Supervision & Coverage Audit

**Date:** 2026-08-15
**Scope:** Focused follow-up to `docs/HISTORICAL_INTELLIGENCE_AUDIT.md`, on: EURUSD backtest persistence/reproducibility, scratch utility results, worker/watchdog deployment, MT5↔ForexSB overlap coverage, threshold/config duplication, `classify_directional_edge` test coverage, and current EURUSD/GBPUSD pipeline state.
**Method:** Code read (this branch's working tree) **plus** live inspection of the running `openterminalui-postgres-1` and `openterminalui-backend-1` containers (`docker exec`, read-only queries/`cat`/`tail`). Numbers below marked "confirmed live" came directly from the running system, not inference.
**Constraint honored:** No code was changed. No ESS logic, thresholds, `top_k`, similarity weighting, live-decision behavior, risk, or execution was touched — this is inspection only.

---

## 1. What already exists

### 1.1 The EURUSD backtest — it ran, and its output currently exists, in the backend container
- `/app/eurusd_backtest_records.json` (5,114,806 bytes) and `/app/eurusd_backtest_progress.json` (`{"evaluated": 4542, "total": 4542}`) are present on `openterminalui-backend-1` right now, both last written **2026-08-14 22:00 UTC**.
- `/app/eurusd_backtest_run.log` (5,698 bytes) contains the full printed summary. **Confirmed live, exact figures**, superseding "must have come from an untracked console run" in the prior audit:
  - `Evaluated 4542 candidates.`
  - ESS threshold sweep: `{20: n=3978, 30: n=3709, 50: n=3210, 75: n=2189, 100: n=0, 150: n=0}` — **zero candidates clear ESS≥100**, confirming the gate is not merely "close" but exactly `n=0` at that bar.
  - `VERDICT BREAKDOWN`: `HIST_INTEL_INSUFFICIENT: count=4542` — literally 100% of resolved candidates, exact figure.
  - `Candidates with raw_neighbor_count >= 100: 2736/4542` = **60.2%** — matches "~60%" precisely.
  - `Effective sample size distribution: min=0.0 median=74.5 max=90.7` — **max=90.7 confirmed exactly**; median (74.5) and min (0.0) are new data points not previously stated.
  - Part 9 (MT5-only vs combined): `combined_mean_ess=61.27`, `mt5_only_mean_ess=55.92` — ForexSB-sourced history is measurably raising average ESS (+5.35), just not enough to cross 100 anywhere in this sample. `combined_more_accurate: false` (on the approximated scoring formula the analysis script itself flags as an approximation, not the real `_historical_score`) — worth noting but not over-reading, since the script's own docstring caveats this isn't a re-run of the real formula.
  - At the ESS≥75 cut (2,189 of 4,542 candidates), calibration is already tight: predicted p(1R)=0.4103 vs actual=0.4089 (MAE=0.0014) — i.e., the statistics are honest well below the 100-sample bar; this is evidence the gate is conservative, not evidence the underlying estimates are unreliable.
- `run_eurusd_backtest.py:40` confirms this is a **stride-15 subsample** of the full resolved EURUSD corpus, as previously inferred — 4,542 is not the full candidate universe.

### 1.2 Watchdog + worker supervision — is actually running, informally
- `/app/watchdog_status.json` (updated 2026-08-15 08:00:40 UTC, i.e. current/live) and `/app/watchdog.log` show the watchdog is **presently running inside the backend container**, tracking 5 workers with real PIDs:

| worker | status (confirmed live) | restarts |
|---|---|---|
| `eurusd_backtest` | COMPLETED | 0 |
| `eurusd_bulk_replay_forexsb` | RUNNING (pid 1471) | 0 |
| `eurusd_adaptive_backfill` | RUNNING (pid 49787) | **4** |
| `gbpusd_bulk_replay_forexsb` | RUNNING (pid 68361) | 0 |
| `forexsb_multi_symbol_backfill` | RUNNING (pid 61785) | **2** |

- This corrects the prior audit's framing: it is not merely "undeployed code" — it is **actively running as a manually-launched, unmanaged foreground/background process inside the container**, with no docker-compose/systemd/supervisor entry, so it does not survive a container recreation and nobody outside the container is alerted if it stops. "Not deployed as a managed service" and "not currently running" are different claims — only the first is true.
- The two workers with `complete_check: lambda: False` (`eurusd_adaptive_backfill`, confirmed by 4 real restarts; `gbpusd_bulk_replay_forexsb`) are behaving exactly as flagged as a risk: they get killed (reason not visible from these logs) and are unconditionally relaunched, forever, with no way to ever reach a "done" state on their own.

### 1.3 MT5 ↔ ForexSB overlap validation — has now actually run, for 3 of 10 symbols, and passed
`historical_provider_reconciliations` (confirmed live, 9 rows total):

| symbol | timeframe | providers | overlapping_bars | median_rel_diff | status | when |
|---|---|---|---|---|---|---|
| EURUSD | M15 | MT5↔FOREXSB | 4,125 | 0.052% | **CONSISTENT** | 2026-08-14 13:37 |
| GBPJPY | M15 | MT5↔FOREXSB | 4,125 | 0.056% | **CONSISTENT** | 2026-08-14 13:38 |
| XAUUSD | M15 | MT5↔FOREXSB | 3,629 | 0.288% | **CONSISTENT** | 2026-08-14 13:39 |
| EURUSD/XAUUSD | M5/M15/H1 | MT5↔YAHOO | — | 0.05–1.5% | mostly CONSISTENT, 1 DIVERGENT (XAUUSD M5 vs Yahoo) | 2026-08-13 |

This is genuinely good news the prior audit couldn't see: the 3-symbol ForexSB overlap check didn't just exist as a script, it ran, and all three passed CONSISTENT with small (<0.3%) median deviation. The other 7 symbols (GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY) still have **zero** MT5↔FOREXSB reconciliation rows.

### 1.4 Walk-forward / directional-edge results — actually computed and persisted, current
`historical_walk_forward_results` has **64 rows**, most recently computed **2026-08-14 01:05 UTC**. Sample of what's there (confirmed live): `mean_reversion` → STRONG/POSITIVE_EDGE (train n=1522, OOS n=1015, OOS expectancy +0.82R); `mtfai1` → FAILED_OOS/NEGATIVE_EDGE (n=41,387 total, by far the largest corpus, OOS expectancy −0.085R); `breakout`, `ema_trend`, `session_breakout`, `liquidity_sweep_reversal` → FAILED_OOS/NEGATIVE_EDGE; `support_resistance_bounce`, `smc_continuation` → FAILED_OOS/UNSTABLE. This table is live and being used — it is not stale scaffolding.

### 1.5 Trust-gating state — computed live from real parity data, and it has moved since the determinism-fix commit
`historical_replay_parity_checks`, aggregated per `trust_gating.py`'s own rule (ACTIVE requires n≥20 and exact_match≥50% OR exact+direction≥70%; confirmed live):

| strategy_id | n | exact% | exact+dir% | vs. ACTIVE bar |
|---|---|---|---|---|
| momentum | 81 | 65.4 | 75.3 | **clears** (ACTIVE) |
| mtfai1 | 81 | 64.2 | 79.0 | **clears** (ACTIVE) |
| mean_reversion | 141 | 37.6 | 37.6 | **does not clear** |
| session_breakout | 81 | 43.2 | 66.7 | does not clear (66.7 < 70) |
| vwap_reversion | 118 | 25.4 | 27.1 | does not clear |
| trend_pullback | 26 | 23.1 | 23.1 | does not clear |
| smc_continuation | 75 | 5.3 | 36.0 | does not clear |
| support_resistance_bounce | 81 | 22.2 | 22.2 | does not clear |
| breakout | 75 | 8.0 | 28.0 | does not clear |
| ema_trend | 75 | 10.7 | 16.0 | does not clear |
| liquidity_sweep_reversal | 13 | 30.8 | 30.8 | below min-n(20), no trust at all |
| **unknown** | 75 | 0.0 | 0.0 | data-quality flag, see §2.5 |

**Notable, current-state finding:** commit `10f678e`'s message reported `mean_reversion` reaching 51.7% exact-match over n=60 (ACTIVE) after the replay-determinism fix. The live table now shows n=141 (more parity checks accumulated since) with exact-match **down to 37.6%** — below the ACTIVE bar. This isn't necessarily a regression bug; trust_gating is a live-recomputed signal by design and a wider, more recent sample can legitimately shift the estimate. But it means **`mean_reversion`'s trust state should not be assumed ACTIVE today** based on the commit message alone — the current computation, from current data, says it isn't. `vwap_reversion` (also mentioned as fixed by the same commit) shows a similarly modest 25.4%/27.1% today. This is exactly the kind of drift that should be checked against runtime state rather than a point-in-time commit message before relying on it.

### 1.6 EURUSD / GBPUSD fingerprint & adaptive-state pipelines — real current numbers

**Fingerprints** (`historical_pattern_fingerprints`, confirmed live):

| symbol | provider | count | span |
|---|---|---|---|
| EURUSD | FOREXSB | 57,303 | 2018-08-07 → **2020-06-07** |
| EURUSD | MT5 | 63,829 | 2024-08-08 → 2026-08-13 (live, ongoing) |
| GBPUSD | FOREXSB | 2,152 | 2018-08-07 → **2018-09-03** |
| GBPUSD | MT5 | 63,999 | 2024-08-08 → 2026-08-13 (live, ongoing) |

**Adaptive states** (`historical_adaptive_states`, joined to fingerprints for provider, confirmed live):

| symbol | provider | count | span |
|---|---|---|---|
| EURUSD | FOREXSB | 42,123 | 2018-08-07 → **2018-12-10** |
| EURUSD | MT5 | 98,987 | 2024-08-08 → 2026-08-13 |
| GBPUSD | FOREXSB | **0** | — |
| GBPUSD | MT5 | 48,415 | 2024-08-08 → 2026-08-13 |

Interpretation:
- EURUSD's ForexSB **fingerprint** walk has reached 2020-06-07 — roughly 40% of the way (by calendar span) from the 2005/2018 ForexSB start toward the 2022-08-04 MT5 boundary.
- EURUSD's ForexSB **adaptive-state** backfill (a separate, downstream pass over the fingerprints — see `adaptive_backfill.py`) lags well behind the fingerprint walk: only to 2018-12-10, i.e. roughly 4 months of the ~2-year ForexSB window has been converted into adaptive states so far, against ~22 months of fingerprints already generated. This is a real, currently-open gap between the two stages, not a modeling issue — `eurusd_adaptive_backfill`'s 4 restarts (§1.2) are directly relevant here as a likely contributor to the lag.
- GBPUSD is dramatically earlier-stage: only 2,152 ForexSB fingerprints spanning **27 days**, and **zero** ForexSB adaptive states at all. `gbpusd_bulk_replay_forexsb` (§1.2) was only just started (0 restarts, empty log file at inspection time) — GBPUSD's deep-history corpus building has effectively just begun, in contrast to EURUSD which is well underway.
- `forexsb_multi_symbol_backfill`'s log shows repeated passes over the remaining 8 symbols (USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, XAUUSD) with **no `"ALL SYMBOLS COMPLETE"` string found anywhere in the log** (`grep -c` returned 0). The claim that all 10 symbols' ForexSB *candle* backfill (the raw-bar stage, distinct from fingerprint/adaptive-state generation above) has completed cannot be confirmed by the one signal the code itself defines for "complete" — the worker is still actively cycling through the symbol list as of this inspection.

---

## 2. What is incomplete

1. **GBPUSD's historical corpus is far behind EURUSD's** at every stage (raw ForexSB coverage: 27 days vs ~22 months of fingerprints; adaptive states: 0 vs 42,123). Any expectation that GBPUSD will reach ESS≥100 on a similar timeline to EURUSD is not supported by current pipeline state — it's meaningfully earlier.
2. **EURUSD's adaptive-state stage lags its own fingerprint stage** by roughly 18 months of calendar coverage within the ForexSB window (fingerprints to 2020-06, adaptive states only to 2018-12). Adaptive-state generation, not fingerprint generation, is the current bottleneck for EURUSD specifically — consistent with `eurusd_adaptive_backfill` being the most-restarted worker (4 restarts).
3. **7 of 10 symbols have no MT5↔ForexSB overlap validation on record** (only EURUSD, GBPJPY, XAUUSD do, and all three passed CONSISTENT).
4. **`classify_directional_edge` remains completely untested** — confirmed by a targeted repo-wide grep of `backend/tests/` for the function name: zero matches. It is an active, currently-populated live gate (64 rows in `historical_walk_forward_results` all have a `directional_edge` value) with no regression coverage.
5. **The "unknown" `strategy_id`** in `historical_replay_parity_checks` (n=75, 0%/0% match) suggests some parity-check-producing code path either fails to resolve a real strategy id or intentionally tags a fallback case as `"unknown"` — worth a quick source check (not performed here, out of this audit's stated scope) since it currently contributes 75 samples of pure 0%-match noise into whatever aggregate view might group by strategy loosely.
6. **The all-10-symbols ForexSB candle-backfill "complete" signal has never fired**, per the log evidence in §1.6 — treat as still in progress unless confirmed by another channel.

## 3. What is ephemeral / not durable

1. **The entire EURUSD backtest result set is container-local and un-backed-up.** `/app/eurusd_backtest_records.json` (5.1MB) and its sibling progress/log files exist only inside `openterminalui-backend-1`'s writable layer. Confirmed: `docker-compose.yml` mounts only `/data` and `/backups` as volumes — `/app` is not mounted. **If this container is removed, rebuilt, or its writable layer is pruned, this 4,542-candidate result set — including the exact figures in §1.1 — is permanently lost**, with no way to regenerate it without re-running the full backtest end-to-end (which itself depends on the point-in-time corpus state at re-run time, which will have changed).
2. **All worker/watchdog logs are equally container-local**: `watchdog_status.json`, `watchdog.log`, `forexsb_backfill.log`, `eurusd_backtest_run.log`, `adaptive_backfill_roundrobin.log`, etc. — none are volume-mounted. Restart counts, timing history, and the only extant evidence of "which workers are running and how many times they've crashed" all disappear on container recreation.
3. **`run_eurusd_redis_verification.py` exists in the container but produced no discoverable output file** — same conclusion as the prior audit: it may have been run interactively (stdout only, never captured) or not run at all; no artifact survives either way to distinguish the two.
4. **The five scratch scripts are present in `/app`** (`scratch_adaptive_inventory.py`, `scratch_fragmentation_ab_test.py`, `scratch_fresh_parity_batch.py`, `scratch_policy_comparison.py`, `scratch_regime_determinism_repro.py`) but **no adjacent output file/log matches their names** in the `/app` listing — consistent with "run interactively, output never redirected/captured," which for two of them (`scratch_adaptive_inventory.py`, `scratch_policy_comparison.py`) means the same open-investigation status as the prior audit, still unresolved from any durable record's perspective. (`scratch_regime_determinism_repro.py` and `scratch_fresh_parity_batch.py`'s conclusions are independently corroborated by the git commit message and by the live parity-check data in §1.5, so those two are less at risk even without a captured log — but still have no artifact of their own.)
5. **Walk-forward and provider-reconciliation results ARE durable** (Postgres tables, confirmed above) — flagging this positively since it's the exception: unlike the backtest JSON and worker logs, `historical_walk_forward_results` and `historical_provider_reconciliations` survive a container restart because they live in the `postgres` container's data volume, not the backend container's writable layer.

## 4. What needs to be fixed or preserved next

Ordered by risk of silent, permanent data loss first, then by investigative/coverage gaps. None of these require touching ESS logic, thresholds, `top_k`, similarity weighting, live-decision behavior, risk, or execution.

1. **Copy `/app/eurusd_backtest_records.json`, `/app/eurusd_backtest_run.log`, and `/app/eurusd_backtest_progress.json` out of the container to durable storage now**, before any container rebuild/redeploy can silently destroy the only copy of the 4,542-candidate result set. This is the single highest-risk item found in this audit — a `docker compose up --build` or volume prune away from losing the concrete evidence behind every number in §1.1.
2. **Decide where worker/watchdog logs should live** — either mount `/app`'s log-producing paths to the existing `/data` volume (already mounted, per `docker-compose.yml`) or redirect these specific scripts' output there, so restart history and progress aren't lost on every container recreation.
3. **Re-run and capture output for the two genuinely-open scratch investigations** (`scratch_adaptive_inventory.py`, `scratch_policy_comparison.py`), redirecting stdout to a file this time, and either fold the findings into a committed doc or discard the scripts once answered.
4. **Extend MT5↔ForexSB overlap validation to the remaining 7 symbols** using the existing, already-proven-working `run_forexsb_overlap_validation.py` mechanism — no new code, just wider invocation.
5. **Investigate why `eurusd_adaptive_backfill` (4 restarts) and `forexsb_multi_symbol_backfill` (2 restarts) keep dying**, since this is directly slowing the EURUSD adaptive-state stage (§2.2) and the full 10-symbol candle backfill (§2.6). The watchdog masks the symptom (keeps relaunching) but doesn't diagnose the cause — worth checking OOM, unhandled exceptions, or resource contention with the actively-running backend service in the same container.
6. **Add test coverage for `classify_directional_edge`** (§2.4) — it's a live, populated gate today with zero regression protection.
7. **Re-verify `mean_reversion` and `vwap_reversion` trust state against current data before relying on either being ACTIVE** — the commit-message snapshot is now stale relative to the live `historical_replay_parity_checks` table (§1.5); whatever currently consumes trust state should be treated as reading a moving signal, not a one-time-earned badge.
8. **Resolve the `"unknown"` strategy_id in parity checks** (§2.5) — likely a small source-level lookup gap, low effort to locate.
9. **Fix the two duplicated/dead threshold constants** flagged in the prior audit and re-confirmed here by direct read: `similarity.py:89` (`_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL = 100`, declared, never referenced anywhere) and `similarity_oos.py:132,142` (hardcodes literal `100` twice instead of importing `entry_intelligence._MIN_SAMPLE_FOR_LIVE_INFLUENCE`, confirmed live at `entry_intelligence.py:49`). Low-risk cleanup that removes a future drift hazard — not a threshold change, since all values are already numerically identical today.

### Explicitly out of scope / not recommended
Same as the prior audit: no change to the ESS formula, the 0.6 similarity floor, the 6-hour dedup window, the ESS≥100 live-influence gate, the trust-gating percentage bars, or any walk-forward purge/embargo window. Everything found here is a persistence, supervision, or coverage gap — the statistical core is confirmed still working as designed, and the honest calibration at ESS≥75 (§1.1) is evidence the conservatism is appropriately tuned, not miscalibrated.

---

## 5. Files/modules actually involved

**Persistence/backtest:**
- `run_eurusd_backtest.py` (repo root, untracked) — produces `/app/eurusd_backtest_records.json`, `/app/eurusd_backtest_progress.json`
- `analyze_eurusd_backtest_offline.py` (repo root, untracked) — reads the above, produces the printed summary captured in `/app/eurusd_backtest_run.log`
- `backend/historical_intelligence/similarity.py`, `backend/historical_intelligence/entry_intelligence.py` — the real scoring/gating logic `run_eurusd_backtest.py` calls directly

**Scratch utilities (all repo-root, untracked):**
- `scratch_adaptive_inventory.py`, `scratch_policy_comparison.py` (open, no captured output)
- `scratch_regime_determinism_repro.py`, `scratch_fresh_parity_batch.py` (resolved, corroborated by commit `10f678e` and live parity data)
- `scratch_fragmentation_ab_test.py` (resolved, corroborated by `adaptive_similarity.py:42-51`)
- `scratch_reresim_historical_analog.py`, `scratch_replay_historical_analog_backfill.py` (adaptive_management-side, separate `historical_analog_v1` shadow policy, not the entry-side system)

**Worker supervision:**
- `watchdog_historical_workers.py` (repo root, untracked) — currently running live in `openterminalui-backend-1`, unmanaged by docker-compose/systemd
- `run_forexsb_backfill.py`, `run_forexsb_intelligence_pipeline.py`, `scratch_run_adaptive_backfill_roundrobin.py` — the actual worker command bodies the watchdog supervises
- `backend/historical_intelligence/adaptive_backfill.py` (modified on this branch) — the EURUSD/GBPUSD adaptive-state backfill logic behind `eurusd_adaptive_backfill`

**Overlap validation:**
- `run_forexsb_overlap_validation.py` (repo root, untracked)
- `backend/historical_intelligence/ingestion.py::reconcile_providers` (modified on this branch), `backend/historical_intelligence/quality.py::compare_provider_overlap`
- DB: `historical_provider_reconciliations`

**Threshold/config duplication:**
- `backend/historical_intelligence/entry_intelligence.py:49` (`_MIN_SAMPLE_FOR_LIVE_INFLUENCE = 100`, the real gate)
- `backend/historical_intelligence/similarity.py:89` (orphaned duplicate constant)
- `backend/historical_intelligence/similarity_oos.py:132,142` (re-hardcoded literal)
- `backend/historical_intelligence/adaptive_similarity.py` (separate, intentionally different `min_effective_sample=20` for adaptive/exit side — not a duplication bug)

**`classify_directional_edge` coverage:**
- `backend/historical_intelligence/walk_forward.py` (definition, migration `0064_walk_forward_directional_edge.py`)
- `backend/tests/test_historical_intelligence_walk_forward.py` (confirmed: no reference to `classify_directional_edge` anywhere in `backend/tests/`)

**EURUSD/GBPUSD pipelines:**
- `backend/historical_intelligence/fingerprint.py`, `bulk_replay.py`, `adaptive_backfill.py`, `providers/forexsb_provider.py`, `providers/mt5_provider.py`
- DB: `historical_pattern_fingerprints`, `historical_adaptive_states`, `historical_setup_outcomes`
- Trust state: `backend/historical_intelligence/trust_gating.py`, DB: `historical_replay_parity_checks`
