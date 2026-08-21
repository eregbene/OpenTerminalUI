# Bensim Historical Analog Intelligence — Current-State Specification & Audit

**Date:** 2026-08-15
**Branch audited:** `feature/adaptive-trade-manager-v2` (working tree, including uncommitted changes)
**Nature of this document:** A specification of what exists today, derived from reading the actual implementation — not a design proposal. Nothing in this document should be read as a mandate to redesign, restart, or weaken any statistical gate. Where the repo could not confirm a runtime claim, that is stated explicitly rather than assumed.

---

## 0. One-paragraph summary

The Historical Analog Intelligence system is real, production-wired, and statistically conservative by design. A production-grade fingerprint → point-in-time replay → outcome-labeling → similarity/ESS → multi-gate pipeline exists end-to-end and is now synchronously called from the live MT5 candidate-ranking path (as of commit `4fba6f8`). Right now it is a fully-armed but self-silencing system: every gate is correctly wired, but the EURUSD corpus's evidence, even where raw neighbor counts look large, is not yet clearing the ESS ≥ 100 live-influence bar, so the system is currently annotating candidates without changing any trading decision. LIVE trading is independently and redundantly disabled through five-plus unrelated code paths that have no coupling to historical-intelligence status. The main gaps are operational/observability, not statistical: no committed record of the EURUSD backtest's actual output, no deployed process supervision for the backfill workers, and a few dead/duplicated constants that risk drifting out of sync with the real gate.

---

## 1. System map

```
ForexSB (deep history, pre-2022-08)  ─┐
MT5 (live + native corpus, 2022-08→) ─┼─→ mt5_canonical_candles (point-in-time safe)
Yahoo (XAUUSD proxy only)            ─┘         │
                                                 ▼
                                    replay.py (bars_as_of / replay_at)
                                                 │  runs REAL strategy evaluators
                                                 ▼
                                    fingerprint.py (build_fingerprint)
                                                 │
                                    bulk_replay.py (offline, chronological)
                                                 ▼
                              historical_pattern_fingerprints (+ hash)
                                                 │
                                    outcomes.py (forward-only labeling)
                                                 ▼
                                historical_setup_outcomes (RESOLVED/UNTRUSTED)
                                                 │
                        ┌────────────────────────┴────────────────────────┐
                        ▼                                                 ▼
          statistics.py (exact peer-group match)          similarity.py (weighted, ESS)
                        │                                                 │
                        └────────────────────────┬────────────────────────┘
                                                   ▼
                                    entry_intelligence.py
                          gates in series: mode → trust_gating → walk_forward
                          (directional edge) → reliability+ESS≥100
                                                   │
                                                   ▼
                          MT5AutonomousTradingService._apply_historical_intelligence
                          (autonomous.py, synchronous, ranking-time)
                                                   │
                                    ranking_adjustment (±10) / hard REJECT / no-op
                                                   │
                                                   ▼
                                   candidate ranking → (independent) live-trading gates
                                                        (MT5_LIVE_TRADING_ENABLED, etc.)

adaptive_intelligence.py / adaptive_similarity.py → Adaptive Trade Manager (service.py)
  — same statistical core, applied to open-position management — observation-only today
  (recommendation_applied hardcoded False)

Redis (cache.py / adaptive_cache.py) fronts Postgres for both paths, fail-open, TTL 15min/2min.
```

---

## 2. What exists and is production-correct

### 2.1 Canonical candles & point-in-time integrity
- Canonical storage is `mt5_canonical_candles` (`backend/brokers/mt5/orm.py:207-247`), shared with the live trading path, unique on `(provider, broker_symbol, timeframe, timestamp)`.
- Two real historical bugs were found and fixed via migration `0052_historical_intelligence_point_in_time_integrity.py`: (a) a `db.merge()`-on-refetch pattern was silently overwriting already-finalized bars (~364k rows observed); (b) MT5 bar timestamps are broker-local, not UTC (empirically +3h), now corrected via `timestamp_utc`/`broker_utc_offset_minutes`. Both fixes are append-only/additive (`mt5_candle_revisions`, `mt5_decision_snapshots`) — no destructive rewrite of history.
- Finalization policy (`quality.is_finalized`) prevents a bar from being silently changed once stable; later-observed differences become `post_finalization_anomaly` revisions, not overwrites.

### 2.2 Providers
- **MT5**: live/execution source and default source for live-decision replay parity. Oldest demo-server bars flagged (not filtered) as possibly synthetic.
- **ForexSB** (new, untracked): Dukascopy-derived static-file backfill source, explicitly never imported by the live M5 cycle or by live-decision replay. Derives H1/H4/D1 from native M30 gap-safely (no fabrication across gaps). `MT5_M15_START` per symbol defines the exact boundary where ForexSB backfill stops and native MT5 corpus begins (EURUSD: `2022-08-04 22:45 UTC`).
- **Yahoo**: XAUUSD-proxy only (GC=F ≠ spot), narrow use.
- Cross-provider reconciliation (`ingestion.reconcile_providers` → `quality.compare_provider_overlap`, classifying NO_OVERLAP/CONSISTENT/DIVERGENT/INCOMPATIBLE) exists and works, but **`run_forexsb_overlap_validation.py` only covers 3 of the 10 symbols** (EURUSD, GBPJPY, XAUUSD) over a 60-day window — the other 7 symbols have no recorded cross-provider reconciliation evidence in the repo.

### 2.3 Fingerprint → replay → outcome pipeline
- `fingerprint.py` builds fingerprints from the exact same feature extraction the live engine uses — no parallel/divergent computation. A hard-match `peer_group_hash` (coarse, 7 fields) plus a wider weighted-similarity feature set (~20+ fields) are both stored on every row.
- `replay.py` reconstructs point-in-time OHLC via a 3-tier hierarchy (SNAPSHOT → revision-tracked RECONSTRUCTED → legacy fallback) and runs the *actual* production strategy evaluators — not a simplified proxy.
- `bulk_replay.py` is the offline, chronological, idempotent driver (`HPF_sha256(...)` deterministic IDs), confirmed never invoked from the live trading cycle.
- `outcomes.py` labels outcomes strictly forward-only, tagged by a `data_quality` tier (HIGH/ACCEPTABLE/APPROXIMATE/UNTRUSTED) reflecting bar-source trustworthiness.
- **A real determinism bug was found and fixed** (commit `10f678e`): RECONSTRUCTED-tier replay had unstable candle tie-break ordering. Fixed with a secondary deterministic sort key, verified via 8x-repeated replay across 4 real evaluation IDs, with a regression test added. This is resolved, not open.

### 2.4 Similarity & Effective Sample Size (ESS)
- Two genuinely distinct sample-size concepts exist in the code, and both are correctly kept separate:
  - **Raw exact-match N** (`statistics.pattern_statistics`) — a plain count of resolved outcomes sharing an identical `peer_group_hash`. No correlation correction.
  - **ESS** (`similarity.similarity_statistics` / `adaptive_similarity.weighted_state_statistics`) — the real statistical safeguard: neighbors below `min_similarity=0.6` are dropped entirely; each surviving neighbor contributes only its own similarity score (≤1.0), not a full unit; `cluster_and_dedup` collapses same-symbol/direction/strategy neighbors falling in the same sequential 6-hour window down to a single representative before summing, explicitly to stop one sustained trend from being counted as dozens of independent confirmations. `effective_sample_size ≤ raw_neighbor_count` is asserted by a dedicated regression test.
- **This construction is exactly why "raw N ≥ 100 in ~60% of candidates, yet max ESS = 90.7 and 100% still gate as insufficient" is expected, documented behavior, not a bug.** Reaching the ESS≥100 bar via similarity-weighted evidence requires *more* raw neighbors than an exact peer-group match would, by design (the code's own comment states this directly).
- The live-influence gate is **`entry_intelligence._MIN_SAMPLE_FOR_LIVE_INFLUENCE = 100`** (`entry_intelligence.py:49`), hardcoded, checked against whichever of exact `sample_size` or similarity-weighted `effective_sample_size` was selected. Confirmed by direct read: this constant, `_RELIABLE_LEVELS = {"USEFUL","STRONG"}`, and the `< _MIN_SAMPLE_FOR_LIVE_INFLUENCE` comparison at line 198 are exactly as described.
- `HIST_INTEL_INSUFFICIENT` is produced when `status == "UNAVAILABLE"` and reason is one of `{PATTERN_SAMPLE_INSUFFICIENT, NO_GOOD_HISTORICAL_ANALOG, HISTORICAL_INTELLIGENCE_MODE_NOT_DEMO_ACTIVE}` — confirmed directly in code.
- Gates run **in series**, all must pass independently: mode gate → `trust_gating` (replay-trustworthiness, independent of any single pattern's sample size) → `walk_forward` strategy-level directional-edge gate → `walk_forward` combination-level (strategy+symbol) directional-edge gate → reliability+ESS≥100 gate. This is deliberate defense-in-depth, not redundant/dead logic.
- The adaptive/exit side (`adaptive_similarity`) uses a genuinely different, separately-declared lower bar (`min_effective_sample=20`) for open-trade management recommendations — this is a distinct feature from the entry-influence gate, not an inconsistency.

### 2.5 Walk-forward / directional-edge validation
- `backend/historical_intelligence/walk_forward.py` validates the *honesty of the historical-analog engine itself*, not a trading strategy: chronological train/OOS split (default 60/40) with a 12-hour purge/embargo window on both sides of the boundary to prevent leakage, classifying `edge_stability` (STRONG/ACCEPTABLE/DEGRADED/FAILED_OOS/INSUFFICIENT_SAMPLE) and, separately, `directional_edge` (added by migration `0064`, so a reliably-negative pattern isn't conflated with a genuinely unstable one).
- This is a **separate system** from `backend/core/walk_forward.py`, which is a generic equity-curve Sharpe-ratio tool with no knowledge of fingerprints/ESS — the two share a filename only.
- Results persist to `historical_walk_forward_results` and are read (not recomputed) by the live gate.

### 2.6 Entry integration & Adaptive Trade Manager
- `evaluate_historical_intelligence` is called synchronously, per top-K candidate, from `MT5AutonomousTradingService._rank_candidates_by_confidence` (`autonomous.py:798`), with a 5s timeout. This wiring is real but recent — before commit `4fba6f8` this function had no live caller at all.
- Effect is bounded and asymmetric: a `SUPPORT`/`RANK_ADJUST` result can move ranking score by at most ±10 points; a hard `REJECT` (requires `historical_score ≤ -35` AND `STRONG` reliability AND immediate-failure-rate ≥ 0.6 — a real conjunction of strict conditions) adds a rejection reason that excludes the candidate. `HIST_INTEL_INSUFFICIENT`/`HIST_INTEL_NEUTRAL` are pure no-ops on score/eligibility, annotation only.
- The Adaptive Trade Manager's consumption (`adaptive_intelligence.py` → `service.py:1478-1496`) is explicitly observation-only by design: `recommendation_applied` is hardcoded `False`, runs strictly after the real decision is made, wrapped in its own try/except so it cannot affect the live cycle even on failure. The code's own docstring states the current sample coverage isn't yet enough for any peer group to clear its reliability bar on the adaptive/exit side either.
- A separate `historical_analog` policy family exists inside `adaptive_management/service.py`'s `simulate_policy()` — this is a shadow/counterfactual comparison policy (`would_mutate_broker: False`), never the active live policy (`ACTIVE_POLICY_ID = "conservative_demo_manager_v1"`).

### 2.7 Redis caching
- Two intentionally separate cache modules: `cache.py` (entry-side, async, reuses the shared Redis client) and `adaptive_cache.py` (adaptive-side, **sync** client, because it runs inside `asyncio.to_thread()` with no event loop — a deliberate, documented exception, not an inconsistency).
- Caches pattern/similarity statistics (TTL 15 min) and gate results — trust state, edge stability, combination-edge stability (TTL 2 min). Versioned cache keys mean logic/schema changes naturally produce new keys rather than serving stale data. Every Redis call fails open to Postgres on error.
- `run_eurusd_redis_verification.py` is well-formed and tests the right things (cold-miss populate, warm-hit speed+correctness, presence of pre-2022 ForexSB-era neighbors) but is **untracked, uncommitted, and has no captured execution output anywhere in the repo** — "Redis historical lookup is working" cannot be independently confirmed from the repository; it is asserted, not evidenced, in-repo.

### 2.8 LIVE trading disable — verified, multi-layered, uncoupled from historical intelligence
This was audited as the highest-priority safety question. Findings, directly corroborated by reading `config.py`:
- `MT5_LIVE_TRADING_ENABLED` (`backend/brokers/mt5/config.py:25,122`) defaults to `False` and is read from env. Critically it acts as an **inverted tripwire** — set to `True`, it *adds a blocker*, it does not open a path. It is independently re-checked in at least five places: `execution.py::safety_blockers`, `autonomous.py::_submit`, `adaptive_management/service.py::activate_demo`, `adaptive_management/service.py::_can_execute`, and both `portfolio_execution/service.py` and `economic_intelligence/service.py`.
- A second, independent env var `MT5_ACCOUNT_MODE` must equal `"DEMO"` (default), checked alongside the above in every layer.
- A broker-data-driven check (`assert_demo_account`) independently validates the real account identity and is covered by its own regression test.
- A fourth, account-registry-level lock requires both a *different* env var (`LIVE_TRADING_ENABLED`, generic, not MT5-specific) and a manually-set, persisted `approved=True` DB row for any account classified `PERSONAL_LIVE`.
- **Historical intelligence status has zero coupling to any of these gates** — explicitly documented in `modes.py` and `docker-compose.yml` comments. No code path was found where `HIST_INTEL_*` status influences live-trading enablement in either direction.
- No single "the kill switch" line exists; safety is enforced by five-plus independently-checked, redundantly-defaulted-off conditions. This is a stronger property than a single switch, and nothing in this audit found a gap in it.

---

## 3. What is currently incomplete or unconfirmed

These are gaps in evidence, deployment, or bookkeeping — **not** gaps in the statistical logic itself.

1. **No committed record of the EURUSD backtest run.** `run_eurusd_backtest.py` writes to `/app/eurusd_backtest_records.json` / `/app/eurusd_backtest_progress.json`, container-internal paths with no Docker volume mount and never committed to git. The user-stated numbers (4,542 candidates, 100% `HIST_INTEL_INSUFFICIENT`, ~60% raw N≥100, max ESS=90.7) are structurally exactly what `analyze_eurusd_backtest_offline.py` would compute and print — but no file in the repository contains them. They exist only as console output from a run that hasn't been preserved anywhere durable.
2. **4,542 is very likely a stride-15 subsample, not the full corpus.** `run_eurusd_backtest.py` subsamples every 15th chronological candidate (`SAMPLE_STRIDE=15`) against a corpus the script's own docstring describes as 60,000+ candidates, "to keep a single run practical." This should be confirmed/labeled explicitly wherever these stats are reported, so "4,542" isn't later mistaken for "the whole EURUSD corpus."
3. **No schema exists to store per-candidate backtest results.** `historical_walk_forward_results` (migrations 0060/0064) is aggregate-only (one row per strategy/symbol/regime/etc. train/OOS split) — it has no columns for per-fingerprint ESS/raw-N/status, and `run_eurusd_backtest.py` doesn't write to it anyway (it calls `similarity.py`/`entry_intelligence.py` directly, bypassing `walk_forward.py` except for importing the purge-window constant). Any future need to query this backtest's per-candidate results durably requires new schema (or, at minimum, committing the JSON output as an artifact).
4. **"All 10 symbols' ForexSB backfill complete" cannot be confirmed from the repository.** Completion is tracked only as a literal log string (`"ALL SYMBOLS COMPLETE"`) printed once the backfill loop over all 10 symbols finishes — this does not itself guarantee every symbol succeeded, since per-symbol failures are caught, logged, and skipped without blocking the "complete" message. There is no DB row or checkpoint flag recording "symbol X backfill verified complete." This is a claim about runtime state that only the running system (DB/logs) can confirm, not the code.
5. **Cross-provider overlap validation only covers 3 of 10 symbols** (EURUSD, GBPJPY, XAUUSD). The other 7 have no recorded ForexSB-vs-MT5 reconciliation evidence.
6. **`watchdog_historical_workers.py` is not deployed anywhere.** No docker-compose, systemd, cron, or supervisor reference exists; it's a manually-invoked script with hardcoded `/app`-container paths. Of its 5 tracked workers, only 2 have a real completion check — the other 3 use `lambda: False`, meaning if actually run unattended, the watchdog would restart already-finished workers indefinitely. A declared `STALE`/`STALE_AFTER_SECONDS` status is never actually triggered in the loop (dead code).
7. **Two scratch investigation scripts have no captured results**: `scratch_adaptive_inventory.py` (adaptive-state coverage inventory) and `scratch_policy_comparison.py` (live-vs-historical_analog policy A/B/C/D comparison) are both untracked and have never had output committed anywhere — from the repo's perspective these investigations are open, even though `adaptive_intelligence.py`'s docstring already asserts (without citing a specific number) that adaptive-side coverage is currently insufficient.
8. **Minor dead/duplicated constants** that risk drifting from the real gate if someone edits one and not the other:
   - `similarity.py:89` `_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL = 100` is declared, commented as authoritative, but never referenced anywhere — the actually-enforced gate is the separate `entry_intelligence._MIN_SAMPLE_FOR_LIVE_INFLUENCE`.
   - `similarity_oos.py:132,142` hardcodes the literal `100` twice instead of importing either constant.
9. **Test coverage gaps** (logic itself untested, not logic that's wrong):
   - `classify_directional_edge` (migration 0064, actively used as a live gate) has zero test coverage.
   - No test exercises `run_adaptive_backfill` end-to-end with real (non-stubbed) outcome/checkpoint logic combined with the new provider filter.
   - No integration test against the real ForexSB HTTP endpoint, or of any of the four operational scripts (`run_forexsb_backfill.py`, `run_forexsb_intelligence_pipeline.py`, `run_forexsb_overlap_validation.py`, `watchdog_historical_workers.py`).
   - `test_mt5_historical_intelligence_ranking_wiring.py` proves the caller-side contract at a mocked boundary only — it does not prove `entry_intelligence.py`'s internal scoring/gating end-to-end (that is presumably covered elsewhere, but was out of this audit's file scope to confirm).
10. **EURUSD fingerprint/adaptive-state backfill toward the 2022-08 boundary is, by the user's own account and by the code's chronological-advancement design, still in progress** — `run_forexsb_intelligence_pipeline.py` advances the ForexSB-era bulk-replay walk toward `MT5_M15_START["EURUSD"] = 2022-08-04 22:45 UTC` as ingestion coverage grows; it has not yet reached that boundary. This directly bears on §3.2 below (more history → more/better-distributed neighbors → real chance of ESS crossing 100) and is expected, not a fault.

---

## 4. What must happen next

In order of what actually unblocks the "100% HIST_INTEL_INSUFFICIENT" state versus what's pure housekeeping. **None of these require touching the ESS formula, the dedup/clustering logic, the 100-sample gate, or any other statistical safeguard — the gates are working exactly as designed and should stay as they are.**

1. **Let the in-progress corpus backfill finish** (EURUSD fingerprints/adaptive-states advancing toward 2022-08; presumably similar for other symbols). More history is the single most direct way raw neighbor counts — and therefore ESS after honest dedup — can grow. This is already running; the only action item is not to interrupt or redesign it.
2. **Preserve the EURUSD backtest's actual output.** Re-run (or recover, if still resumable from the checkpoint) `run_eurusd_backtest.py` + `analyze_eurusd_backtest_offline.py` and commit the resulting summary numbers (not necessarily the full per-candidate JSON) into the repo or another durable location, explicitly labeled as a stride-15 subsample with its actual corpus size, so "4,542 candidates" is traceable and reproducible rather than living only in a chat transcript. This is bookkeeping, not a statistical change.
3. **Complete or explicitly retire the two open scratch investigations** (`scratch_adaptive_inventory.py`, `scratch_policy_comparison.py`) — run them, capture output, and either fold the findings into a committed doc/log or delete the scripts once their question is answered. Right now they represent unresolved investigative threads with no record of outcome.
4. **Run (or schedule) `run_forexsb_overlap_validation.py` for the remaining 7 symbols** to get the same data-quality confidence for GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY that already exists for EURUSD/GBPJPY/XAUUSD. Pure validation, no logic change.
5. **Decide, deliberately, whether `watchdog_historical_workers.py` should be operationalized** (added to docker-compose/supervisor with real completion checks for all 5 workers) or intentionally remains a manual/ad-hoc tool. Currently it is neither reliably automated nor clearly documented as manual-only — that ambiguity is itself the gap, not the tool's logic.
6. **Small, safe hygiene items** (no behavior change): remove or wire up the orphaned `_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL` constant in `similarity.py`; have `similarity_oos.py` import `_MIN_SAMPLE_FOR_LIVE_INFLUENCE` instead of re-hardcoding `100`; remove the dead `STALE_AFTER_SECONDS`/never-triggered `STALE` status in the watchdog, or actually wire it in if the intent was for it to fire.
7. **Add the missing test coverage** called out in §3.9 — especially `classify_directional_edge`, since it's already an active live gate with zero tests today. This reduces regression risk without touching any threshold.
8. **If per-candidate backtest results need to be queryable long-term**, that requires new schema (a per-candidate results table or JSON-array column) — this is a genuine design decision to make deliberately, not a mechanical fix, and is called out here as a decision point rather than a prescribed solution.

### Explicitly out of scope / not recommended by this audit
- Lowering or reinterpreting the ESS ≥ 100 live-influence threshold, the 0.6 similarity floor, the 6-hour dedup clustering window, or any walk-forward purge/embargo window. All are working as designed; the current 100% `HIST_INTEL_INSUFFICIENT` outcome is the gate doing its job on a still-growing corpus, not evidence the gate is miscalibrated.
- Re-architecting the fingerprint schema, the replay pipeline, the trust-gating state machine, or the Redis caching design — all audited as correct and internally consistent.
- Restarting any completed backfill, replay, or reconciliation work — nothing found in this audit indicates prior work needs to be redone; gaps found are additive (more coverage, more validation, more bookkeeping), not corrective of the existing corpus.
- Touching any of the live-trading-disable gates — they are correct, redundant by design, and uncoupled from historical-intelligence status as intended.

---

## 5. Open questions for the human owner

- Should `run_eurusd_backtest.py`'s output be committed as a durable artifact going forward (e.g., under `data/research/`), or is an external tracking system (dashboard, ticket, log aggregator) the intended home for these numbers? The repo currently has neither.
- Is `watchdog_historical_workers.py` intended to become a real supervised process, or was it always meant as a manual convenience script? The current ambiguity should be resolved one way or the other.
- Is there a target ESS/coverage milestone (e.g., "re-check live-influence rate once EURUSD backfill reaches the 2022-08 boundary and N candidates have been re-backtested") that should be scheduled explicitly, so "still insufficient" doesn't silently persist past the point where it's worth re-measuring?
