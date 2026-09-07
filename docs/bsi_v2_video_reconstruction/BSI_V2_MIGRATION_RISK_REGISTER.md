# BSI V2 Migration Risk Register

## Change Risk Matrix

| Change | Risk | Blast Radius | Rollback |
|---|---|---|---|
| Add `BSI_BASELINE_V2_AUDIOVISUAL` version isolation | CRITICAL | HISTORICAL_DATA | Disable V2 dispatch; leave V1 constants |
| Add BSI-only primitive wrappers | HIGH | BSI_ONLY | Revert V2 imports; V1 untouched |
| Add trendline liquidity to BSI Order Flow | HIGH | BSI_ONLY / MARKET_STRUCTURE_SHARED if raw detector changed | Use wrapper around `pivot_trendlines.py`; feature flag off |
| New York original-level resolver | CRITICAL | BSI_ONLY / EXECUTION_SHARED if geometry emitted wrong | Disable `bsi_new_york` V2 subtype |
| Mentor OB wrapper | HIGH | BSI_ONLY | Fall back to V1 only; keep generic OB untouched |
| Remove BSI OTE dependency | HIGH | BSI_ONLY | Re-enable V1 path |
| Lifecycle/opportunity identity | CRITICAL | BSI_ONLY / HISTORICAL_DATA | Disable V2 persistence and dispatch |
| Entry freshness quote recheck | CRITICAL | EXECUTION_SHARED if wired globally | Keep BSI-only execution gate; bypass by disabling V2 |
| OB Liquidity residual-liquidity clearance | HIGH | BSI_ONLY | Disable subtype or revert wrapper |
| OB Liquidity heavy-reaction reroute | HIGH | BSI_ONLY | Disable subtype |
| Under/Over partial management wiring | MEDIUM | EXECUTION_SHARED | Keep metadata-only until separately enabled |
| 9:30 partial/BE management wiring | HIGH | EXECUTION_SHARED | Disable BSI management policy |
| 9:30 instrument gating | MEDIUM | BSI_ONLY | Config to evidence-only mode |
| ABCD optional structure/1:3 target | MEDIUM | BSI_ONLY | Keep fixed 1:2 default |
| Add V2 persisted evidence fields | HIGH | HISTORICAL_DATA | Additive migration rollback; ignore fields |
| Historical V2 replay/backfill | CRITICAL | HISTORICAL_DATA | Separate run id/version; delete V2 rows only |
| DEMO activation | CRITICAL | BROKER_SHARED | Keep disabled until checklist passes |

## File-By-File Plan

| File | Function/Class | Current Role | V2 Change | Why | Rule IDs | Tests | Risk |
|---|---|---|---|---|---|---|---|
| `backend/mt5_strategies/families/bsi_engine.py` | `BSI_VERSION`, dispatch, existing evaluators | V1 BSI engine | Freeze V1 or route V2 separately | V1 history isolation | BSI2-VER-001 | version regression | CRITICAL |
| `backend/mt5_strategies/families/bsi_v2_engine.py` | new evaluators | none | New V2 subtype evaluators | Avoid V1 contamination | all strategy rules | golden suite | CRITICAL |
| `backend/mt5_strategies/families/bsi_v2_primitives.py` | new dataclasses | none | BSI liquidity, mentor OB, dealing leg, lifecycle ids | Shared BSI semantics | BSI2-LIQ-*, BSI2-OB-*, BSI2-PD-* | unit tests | HIGH |
| `backend/mt5_strategies/families/bsi_v2_interpretation.py` | new wrappers | none | Convert raw snapshots into mentor objects | Shared-vs-strategy separation | shared rules | primitive tests | HIGH |
| `backend/mt5_strategies/families/bsi_v2_lifecycle.py` | new state model | none | THESIS/OPPORTUNITY/EXECUTABLE_ENTRY | Dedup/freshness | BSI2-LIFE-001, BSI2-FRESH-001 | scheduler tests | CRITICAL |
| `backend/market_structure/liquidity.py` | `detect_liquidity_levels`, `detect_equal_levels`, `detect_liquidity_sweeps` | shared horizontal liquidity | Reuse raw; no BSI semantic rewrite | Protect non-BSI users | BSI2-LIQ-001/002/005 | non-BSI regression | HIGH |
| `backend/market_structure/pivot_trendlines.py` | `detect_pivot_trendlines` | shared trendline detector | Reuse or wrap into BSI trendline liquidity | Needed for Order Flow | BSI2-LIQ-003 | trendline golden | HIGH |
| `backend/market_structure/zones.py` | `detect_order_blocks` | generic OB | Do not use directly in V2 mentor OB | Generic OB differs | BSI2-OB-002 | generic/mentor OB negative | HIGH |
| `backend/market_structure/imbalance.py` | `detect_fair_value_gaps` | raw FVG | Reuse as source for mentor OB | Mentor OB requires FVG | BSI2-FVG-001 | FVG anchoring | MEDIUM |
| `backend/market_structure/structure.py` | `detect_structure_breaks` | raw breaks | Reuse; interpret in BSI wrapper | Avoid global CHoCH mutation | BSI2-STRUCT-* | break regression | HIGH |
| `backend/mt5_strategies/families/_shared.py` | `_dynamic_stop`, `_opposing_structural_level`, `_ote_zone_for_direction` | shared strategy helpers | Use stop/target carefully; do not call OTE in BSI V2 | Prevent unsupported gates | BSI2-PD-002 | OTE negative | HIGH |
| `backend/historical_intelligence/bsi_canonical_fingerprint.py` | `BSIThesisRecordORM`, `build_bsi_thesis_record` | BSI thesis persistence | Add V2 evidence fields if missing | Persist rule IDs, opportunity IDs | BSI2-EVID-001 | round-trip | HIGH |
| `backend/alembic/versions/*` | new migration | DB schema | Additive V2 evidence/opportunity columns/tables | Historical isolation | BSI2-VER-001, BSI2-LIFE-001 | migration tests | HIGH |
| `backend/historical_intelligence/bsi_hi_fingerprint.py` | hard filters | BSI HI dimensions | Hard-filter by `bsi_version` | No V1/V2 mixing | BSI2-VER-001 | HI isolation | CRITICAL |
| `backend/historical_intelligence/bsi_replay.py` | replay policy registry | BSI baseline replay | Add V2 replay policy/version after tests | Counterfactual comparison | BSI2-VER-001 | replay tests | HIGH |
| `backend/adaptive_management/bsi_thesis.py` | `recommended_management_action` | reference BSI management | Wire only after signal fidelity | Under/Over and 9:30 management | BSI2-UO-004, BSI2-930-007 | management tests | HIGH |
| `backend/adaptive_management/service.py` | live manager | generic live management | Keep V2 BSI management gated | Avoid broker-side surprises | management rules | demo safety tests | CRITICAL |
| `backend/tests/test_bsi_engine.py` | V1 tests | current BSI regression | Keep V1 tests; add V2 tests separately | Prevent accidental V1 edits | all | existing + new | HIGH |
| `backend/tests/test_bsi_v2_engine.py` | new | none | V2 strategy unit/golden tests | Main verification | all | golden suite | CRITICAL |
| `backend/tests/test_bsi_v2_lifecycle.py` | new | none | lifecycle/dedup/freshness tests | Scheduler safety | BSI2-LIFE-001 | duplicate tests | CRITICAL |
| `backend/tests/test_bsi_v2_persistence.py` | new | none | version/evidence persistence tests | HI isolation | BSI2-EVID-001 | round-trip | HIGH |

## Critical Before DEMO

- V2 disabled-by-default and V1 unchanged.
- New York original-level resolver complete.
- Lifecycle/opportunity dedup complete.
- Entry freshness quote gate complete.
- Mentor OB/FVG anchoring complete.
- Order Flow trendline liquidity complete or Order Flow V2 disabled.
- OB Liquidity residual/heavy-reaction checks complete or subtype disabled.
- V2 HI/confidence/self-learning isolated by `bsi_version`.
- Full golden suite and V1 regression passing.
