"""BSI (Bensim Structural Intelligence) -- architecture map, not a code duplication layer.

Mission directive Section 23 asks for a clean target tree:

    backend/structural_intelligence/{engine,structure,liquidity,imbalance,order_blocks,
        dealing_range,sessions,thesis,models,historical,scoring,validation}

Section 5 of the SAME directive is equally explicit the other way: "Reuse infrastructure where
semantics genuinely match... Do NOT rewrite working infrastructure merely to make BSI look
independent." Bensim's market_structure/, historical_intelligence/, and mt5_strategies/ packages
already contain correct, tested, production-serving implementations of structure/liquidity/
imbalance/order-block/dealing-range/session primitives (structure/liquidity/FVG detection are
byte-for-byte reused, unmodified, by every BSI setup evaluator -- see bsi_engine.py's own module
docstring, points 1-4). Physically relocating those modules into a new package tree the same
week they are being extended would (a) touch every existing strategy family's imports
simultaneously as an unrelated side effect of this work, (b) violate Section 5's own instruction,
and (c) buy no behavioral benefit -- Section 23 itself allows "an equivalent architecture that
fits the existing repository cleanly" as the alternative.

This package is that equivalent: a thin, no-runtime-behavior INDEX documenting exactly where each
Section-23 conceptual layer actually lives today, so a reader (or a future migration) can find the
real implementation without guessing. Nothing here is imported by any evaluator, adapter, or
route -- importing this package has zero effect on any running code path.

CONCEPTUAL LAYER  -> ACTUAL LOCATION (reused, not duplicated)
---------------------------------------------------------------------------------------------
engine              -> backend/mt5_strategies/families/bsi_engine.py
                        (evaluate_bsi + the 9 setup-subtype evaluators; orchestrates every layer
                        below into one signal per cycle)
structure           -> backend/market_structure/structure.py (swings, BOS/CHoCH/MSS) -- REUSED
                        UNMODIFIED. BSI's own MSS/CHoCH-synonym semantics (Section 4A) live as a
                        composition on top, in bsi_engine.py's `_MENTOR_MSS_KINDS`, not a fork of
                        structure.py itself.
liquidity           -> backend/market_structure/liquidity.py (sweeps, EQH/EQL, buy/sell-side) --
                        REUSED UNMODIFIED. BSI's own >=3-touch Under/Over filter
                        (bsi_engine.py::_mentor_equal_levels) filters the engine's own output; it
                        does not reimplement equal-level detection.
imbalance           -> backend/market_structure/imbalance.py (3-candle FVG detection) -- REUSED
                        UNMODIFIED (row 7: EXACT match against the course's own definition).
order_blocks        -> bsi_engine.py::_mentor_order_block_for_fvg (Section 4B: BSI's own
                        course-faithful "first candle of the FVG's own 3-candle sequence"
                        definition). Deliberately NOT market_structure/zones.py::
                        detect_order_blocks() -- that function implements the legacy
                        break-anchored backward scan the course explicitly rejects (row 9); it is
                        untouched and unused by BSI, still serving whatever legacy strategy relies
                        on it.
dealing_range       -> bsi_engine.py::_leg_bounds (Section 4C: the specific structure-breaking
                        leg, not market_structure/dealing_range.py's freshest-high/freshest-low
                        pairing, which the mission directive itself flags as not representing that
                        leg for BSI's purposes). market_structure/dealing_range.py is untouched.
sessions            -> backend/market_structure/sessions.py (session boxes: Asian/London/NY) --
                        REUSED for the Asian/New York session-box lookups. BSI's own literal
                        9:30-11:59 America/New_York clock window (a genuine gap in sessions.py,
                        no prior 9:30-specific concept existed) is new, narrowly-scoped code:
                        bsi_engine.py::_in_930_window / _before_930.
thesis              -> backend/mt5_strategies/families/bsi_engine.py::_bsi_thesis_metadata
                        (built at signal time, persisted in StrategySignal.metadata["bsi_thesis"])
                        + backend/adaptive_management/bsi_thesis.py (thesis_still_intact /
                        recommended_management_action -- the not-yet-wired reference design for
                        thesis-aware management, Section 16).
models              -> backend/mt5_strategies/models.py (STRATEGY_FAMILIES["bsi"] registration,
                        DISABLED by default) + backend/market_structure/models.py (the shared
                        StructureBreak/SwingPoint/ImbalanceZone/LiquidityLevel dataclasses BSI's
                        engine consumes, unmodified).
historical          -> backend/historical_intelligence/{replay,fingerprint,outcomes,
                        entry_intelligence,similarity,statistics}.py -- REUSED UNMODIFIED. BSI
                        gets setup-level isolation for free: build_fingerprint's own
                        peer_group_hash hard-matches on `anchor_strategy`, and every BSI backfill
                        run persists pseudo_strategy_id="bsi__<subtype>" (run_bsi_backfill.py), so
                        BSI's 9 subtypes each occupy their own peer-group space, never blended
                        with each other or with any legacy strategy's fingerprints (Section
                        17.8's own requirement, satisfied structurally rather than by new code).
scoring             -> backend/mt5_strategies/families/bsi_confidence.py (the 6-dimension,
                        explicitly-uncalibrated quality model -- Section 14; calibration is a
                        BSI_OPTIMIZED_Vx decision, gated on the real backtest evidence in
                        BSI_OVERNIGHT_REPORT.md, never silently defaulted to 75).
validation          -> backend/tests/test_bsi_engine.py, test_bsi_confidence_and_thesis.py
                        (unit/golden tests) + run_bsi_backfill.py / run_bsi_report.py (the real
                        chronological historical validation driver and its metric reader).

BSI_BASELINE_V1 (Section 3): the version string `bsi_engine.BSI_VERSION` stamped into every
`bsi_thesis["bsi_version"]` field. Any future statistically-motivated change to a rule above must
ship as a NEW value (e.g. BSI_OPTIMIZED_V1) with its own hypothesis/validation/OOS/decision
record -- never a silent edit of what BASELINE_V1 already means historically.
"""
from __future__ import annotations
