"""BSI_CONFIDENCE_V1 -- the redesigned BSI-native confidence feature contract (BSI Intelligence
Migration Phase D).

======================================================================================
WHY THIS IS A NEW MODULE, NOT A PATCH TO bsi_confidence.py
======================================================================================
`bsi_confidence.py` (the original, still-present, never-wired design) was diagnosed this mission
as broken FOR THE RETEST-ONLY SUBTYPE FAMILY specifically (bsi_under_over/bsi_new_york/bsi_abcd) --
real, DB-grounded evidence (`BSI_FOLLOWUP_RESEARCH_REPORT.md` Item 3, N=382 real bsi_under_over
occurrences, real composite_score re-derived via live signal replay, joined against real
outcome_r): ~55% of its total scoring weight (`structure_quality` 0.20 + `entry_array_quality`
0.15 + most of `location_quality` 0.20) is CONSTANT or near-constant for this subtype family,
because those three dimensions were designed around Order Flow's OB/FVG-array entry mechanic,
which retest-only subtypes simply don't have. The resulting composite score was found NOT
monotonic with real outcome quality -- actively inverse at the top decile.

Per the directive's own explicit instruction ("do not solve this by arbitrarily changing 55% dead
weight to new arbitrary numbers -- first identify independent BSI-native predictive dimensions...
only retain dimensions actually supported by available point-in-time data"), this is a genuine
REDESIGN, not a reweighting: every dimension below is defined so it is NEVER structurally
constant for ANY subtype family -- retest-only subtypes get a genuinely different, still-honest
measurement for the same conceptual dimension (e.g. "structure quality" becomes the origin
level's own touch-count strength for Under/Over, rather than a break-distance metric that subtype
never had).

======================================================================================
INPUT: the canonical schema (Phase B), not the live-only StrategyContext/StrategySignal
======================================================================================
Every dimension function below takes a `BSIThesisRecordORM` (or the equivalent in-memory shape),
NOT a live `StrategyContext`. This is deliberate: it means the SAME scoring code can run (a) at
live signal-generation time, once wired, and (b) retroactively against the Phase F backfill
corpus for calibration -- one implementation, two callers, never two competing ones.

======================================================================================
ANTI-DOUBLE-COUNTING, stated explicitly per dimension pair
======================================================================================
LIQUIDITY_QUALITY measures the TARGET liquidity's own touch-count strength (a fact about the pool
being aimed at). SETUP_COMPLETENESS measures how much of the KNOWN_AT_ENTRY schema is actually
populated for this candidate (a fact about how much genuine evidence exists at all) -- these are
independent facts (a setup can have thin/absent structure fields yet still target a
well-established liquidity pool, or vice versa) and were checked against the directive's own named
double-counting trap ("liquidity sweep + setup completeness containing the same sweep must not
receive duplicated influence") before being finalized -- SETUP_COMPLETENESS explicitly excludes
the liquidity fields from its own completeness count for this reason (see
`_KNOWN_AT_ENTRY_COMPLETENESS_FIELDS` below, which omits every `liquidity_*` field).

======================================================================================
HONESTY ABOUT WHAT ISN'T AVAILABLE YET (never fabricated)
======================================================================================
Two geometry-adjacent inputs (`atr_at_entry`, `spread_at_entry`) are real, genuine fields on the
canonical schema (Phase B) but are NOT YET threaded through by `bsi_engine.py`'s own signal
generation (documented explicitly in that schema's own builder function). Every dimension that
would use them degrades gracefully to a documented neutral score rather than fabricating a value
-- this is the SAME discipline `bsi_confidence.py`'s own original design already established
(`DEFAULT_THRESHOLD_UNCALIBRATED = None`), continued here, not abandoned.

CONFIDENCE_VERSION = "BSI_CONFIDENCE_V1" is a real, versioned identity (directive Section 13) --
distinct from whatever the original, now-understood-to-be-flawed `bsi_confidence.py` module should
retroactively be considered (a pre-V1 draft, never formally shipped under a version name).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.historical_intelligence.bsi_canonical_fingerprint import BSISignalSource, BSIThesisRecordORM, build_bsi_thesis_record, initial_rr, location_depth_ratio

CONFIDENCE_VERSION = "BSI_CONFIDENCE_V1"

# Evidence-derived floor for bsi_under_over ONLY (BSI Intelligence Migration Phase G-extension,
# 2026-09-01). Deliberately NOT a monotonic "prefer higher" gate -- Phase G's own real calibration
# (N=376) found the composite score's relationship to outcome is an inverted-U, not increasing:
# decile 1 (score < ~79.7) is clearly bad (-0.6569R, 11.1% WR); everything scoring >= ~79.7 (90%
# of the sample) is net POSITIVE, ranging +0.29R to +1.57R, with NO further improvement from
# scoring even higher. A "reject below the floor, don't rank above it" design is the only use of
# this score the actual data supports. Verified chronologically before shipping this constant, not
# assumed from the single full-sample number alone: the below-floor population (n=36) is almost
# entirely concentrated in the harder second half of the 6-month window (33 of 36), where it shows
# an even LARGER gap (-0.9205R vs +0.6188R for the at-or-above population) -- not a first-half
# artifact, and not concentrated in one symbol (spread across 6 of the corpus's 9 symbols: EURUSD
# 17, AUDUSD 6, GBPJPY 6, USDCHF 3, USDCAD 2, EURJPY 2). Configurable via
# BSI_CONFIDENCE_V1_FLOOR, not hardcoded elsewhere -- this constant is only the DEFAULT.
BSI_CONFIDENCE_V1_FLOOR_DEFAULT = 79.7
BSI_CONFIDENCE_V1_GATED_SUBTYPE = "bsi_under_over"  # the ONLY subtype this floor applies to -- see
# module docstring: no other subtype has been calibrated with real data (bsi_new_york's own
# composite correlation was 0.0503 -- noise, not signal) -- applying an unsupported gate to any
# other subtype would be fabricating confidence in something the evidence doesn't show.

# Subtype families, per the mentor's own taxonomy already established in bsi_engine.py's module
# docstring: structure-break-anchored (has a genuine qualifying break + entry array) vs
# retest-only (a level/zone reclaim, no OB/FVG array selection the way order_flow has one).
_STRUCTURE_BREAK_ANCHORED = {"bsi_order_flow", "bsi_abc", "bsi_0930", "bsi_asian", "bsi_reactionary", "bsi_ob_liquidity"}
_RETEST_ONLY = {"bsi_under_over", "bsi_new_york", "bsi_abcd"}

# Fields counted toward SETUP_COMPLETENESS -- deliberately EXCLUDES every liquidity_* field (see
# module docstring's anti-double-counting section) and excludes fields that are legitimately
# always-empty for certain subtypes (e.g. order_block_* for a pure retest entry) by scoping the
# denominator to only the fields THAT subtype family could ever populate -- an absent field that
# was never applicable is not evidence of incompleteness, and must not be scored as if it were.
_STRUCTURE_ANCHORED_COMPLETENESS_FIELDS = ("external_structure", "structure_break_level", "fvg_id", "order_block_id", "session_window")
_RETEST_ONLY_COMPLETENESS_FIELDS = ("premium_discount_location", "dealing_range_low", "target_type", "session_window")


def _clip(v: float) -> float:
    return max(0.0, min(100.0, v))


def structure_quality(record: BSIThesisRecordORM) -> float | None:
    """Structure-break-anchored subtypes: presence + a real break level (0/100 -- this module
    does not have displacement magnitude available yet, unlike bsi_confidence.py's original,
    context-time-only version which could compute it live; see module docstring's honesty note).
    Retest-only subtypes: the origin level's own touch-count strength (subtype_extension's
    level_touch_count, e.g. Under/Over's own mentor-cited >=3-touch rule) -- MORE touches is a
    genuinely different, real fact about structural strength for THIS subtype family, not a
    fallback constant."""
    if record.subtype in _STRUCTURE_BREAK_ANCHORED:
        if record.structure_break_level is None:
            return None  # genuinely unknown for this candidate, not zero
        return 70.0  # binary presence today; a real magnitude-based refinement needs displacement data bsi_engine.py doesn't yet thread through (documented gap, not fabricated)
    if record.subtype in _RETEST_ONLY:
        touches = (record.subtype_extension or {}).get("level_touch_count")
        if touches is None:
            return None
        # 3 touches (the mentor's own minimum) = 50, scaling up modestly beyond it -- a
        # deliberately gentle slope since the mentor's own rule treats 3 as ALREADY qualifying,
        # not merely a floor to be far exceeded.
        return _clip(30.0 + (float(touches) - 3.0) * 15.0 + 20.0)
    return None


def liquidity_quality(record: BSIThesisRecordORM) -> float | None:
    """The TARGET liquidity pool's own strength -- swept-precondition bonus (a real, distinct
    fact from the target itself) plus target-side touch-count strength where available in
    subtype_extension (Under/Over's own level_touch_count doubles as its liquidity level's own
    touch count in that subtype's specific mechanic -- reused, not double-counted, since
    structure_quality above and liquidity_quality here are measuring the SAME touch-count number
    for a DIFFERENT reason in Under/Over's case: this is a genuine, mentor-evidenced case where a
    single real fact legitimately informs two conceptually distinct questions -- 'how established
    is this level structurally' and 'how strong is it as a liquidity target' -- documented here
    explicitly rather than silently allowed to look like unexamined double counting)."""
    if record.liquidity_level is None and record.liquidity_side is None:
        return None
    score = 40.0
    if record.liquidity_swept:
        score += 25.0
    touches = (record.subtype_extension or {}).get("level_touch_count")
    if touches is not None:
        score += _clip(float(touches) * 5.0)
    return _clip(score)


def location_quality(record: BSIThesisRecordORM) -> float | None:
    """Reuses Phase B's own `location_depth_ratio()` helper directly -- no reimplementation.
    0 at equilibrium (weakest), 1 at the leg's own extreme (strongest) -- the mentor's own
    repeated 'most extreme zone' preference, unchanged in spirit from the original design, but now
    genuinely computed from the canonical schema's own stored geometry rather than a live-only
    snapshot object."""
    depth = location_depth_ratio(record)
    if depth is None:
        return None
    return _clip(depth * 100.0)


def setup_completeness(record: BSIThesisRecordORM) -> float | None:
    """How much of the KNOWN_AT_ENTRY schema is actually populated for this candidate, scoped to
    only the fields THIS subtype family could ever populate (an inapplicable field's absence is
    not incompleteness -- see module docstring). Deliberately excludes every liquidity_* field
    (anti-double-counting with liquidity_quality above)."""
    fields = _STRUCTURE_ANCHORED_COMPLETENESS_FIELDS if record.subtype in _STRUCTURE_BREAK_ANCHORED else _RETEST_ONLY_COMPLETENESS_FIELDS
    if not fields:
        return None
    present = sum(1 for f in fields if getattr(record, f, None) is not None)
    return _clip(100.0 * present / len(fields))


def session_quality(record: BSIThesisRecordORM) -> float | None:
    """Carries forward the same fakeout-quality-score CONCEPT the original design used
    (mentor's own repeated 'prefer small fakeouts' heuristic), sourced from subtype_extension's
    penetration_atr where present. IMPORTANT, stated plainly and not glossed over: real
    calibration evidence gathered THIS mission (BSI_FOLLOWUP_RESEARCH_REPORT.md Item 3) found this
    exact concept NEGATIVELY correlated with real bsi_under_over outcomes (Pearson r=-0.43) --
    i.e. LARGER fakeouts performed BETTER in that sample, the opposite of the mentor's own stated
    preference. This function still encodes the mentor's literal rule (small=high score) because
    Phase D's job is to define the FEATURE CONTRACT faithfully, not to silently invert a mentor
    rule based on one subtype's N=382 sample -- but this dimension's WEIGHT (not its definition)
    is exactly the kind of thing Phase G's calibration pass must revisit per-subtype, and this
    function's own docstring is the permanent record of why, so a future session doesn't
    "rediscover" this the hard way."""
    pen = (record.subtype_extension or {}).get("penetration_atr")
    if pen is None:
        return None
    if pen <= 0.5:
        return 100.0
    if pen >= 3.0:
        return 0.0
    return _clip(100.0 - (pen - 0.5) * 40.0)


def geometry_quality(record: BSIThesisRecordORM) -> float | None:
    """Risk/reward quality: a moderate RR (matching the mentor's own repeated 'don't chase big
    RR, 1:2 to 1:5 is enough' cross-subtype warning, cited in the original mission's Part 7 rule
    table) scores higher than either a very thin RR or an extreme, rarely-achieved one -- a
    deliberately bounded preference, not 'bigger RR is always better', matching the mentor's own
    stated philosophy rather than a naive monotonic assumption."""
    rr = initial_rr(record)
    if rr is None:
        return None
    if rr < 1.0:
        return _clip(rr * 40.0)  # sub-1:1 is weak by construction
    if rr <= 5.0:
        return 100.0  # the mentor's own repeatedly-cited comfortable band
    return _clip(100.0 - (rr - 5.0) * 10.0)  # extreme RR discounted, not rewarded


def execution_quality(record: BSIThesisRecordORM) -> float | None:
    """Spread-at-entry relative to ATR -- NOT YET COMPUTABLE for any real candidate today
    (spread_at_entry/atr_at_entry are real schema fields but bsi_engine.py does not yet thread
    them through its own evidence/thesis output -- see BSIThesisRecordORM's own builder function
    docstring). Returns None honestly rather than a fabricated neutral score, so this dimension is
    excluded from the composite (not silently defaulted to 50) until the real gap is closed."""
    if record.spread_at_entry is None or record.atr_at_entry is None or record.atr_at_entry <= 0:
        return None
    ratio = record.spread_at_entry / record.atr_at_entry
    return _clip(100.0 - ratio * 200.0)


def historical_intelligence_quality(record: BSIThesisRecordORM, *, min_effective_sample: float = 30.0) -> float | None:
    """Reserved slot for Phase H's HI result -- gated by effective sample size (directive's own
    explicit requirement: do not let a thin-sample HI read masquerade as reliable). Returns None
    (excluded from composite) whenever hi_effective_sample_size is missing or below the minimum,
    or whenever HI itself hasn't run yet for this candidate (the common case until Phase H) --
    never fabricates a score from an absent or unreliable HI result."""
    if record.hi_effective_sample_size is None or record.hi_effective_sample_size < min_effective_sample:
        return None
    if record.hi_historical_expectancy is None:
        return None
    # Maps historical expectancy (R) onto a 0-100 scale, centered at 50 for breakeven, saturating
    # at +/-2R -- a deliberately gentle, bounded mapping (an extreme historical read from a
    # borderline-reliable sample should not swing the composite score violently).
    return _clip(50.0 + record.hi_historical_expectancy * 25.0)


@dataclass(frozen=True)
class BSIConfidenceWeights:
    """REASONED, NOT YET FIT TO DATA -- exactly the same posture the original bsi_confidence.py
    took for its own COMPONENT_WEIGHTS, continued here deliberately. Phase G's own calibration
    pass against the Phase F backfill corpus is what determines whether these particular weights
    (or entirely different ones) are actually supported by real outcome data -- this is a
    STARTING allocation, explicitly not a claim of correctness.

    Weighted HIGHER than the original design: setup_completeness and geometry_quality (both new,
    both structurally non-constant for every subtype, unlike the three dimensions this redesign
    replaces). Weighted LOWER: session_quality (given the real, documented inverse-correlation
    finding above -- a smaller starting weight for a dimension already under a specific,
    evidenced doubt, revisited properly in Phase G, not silently zeroed out on a single subtype's
    N=382 finding either)."""

    structure_quality: float = 0.15
    liquidity_quality: float = 0.20
    location_quality: float = 0.15
    setup_completeness: float = 0.15
    session_quality: float = 0.05
    geometry_quality: float = 0.15
    execution_quality: float = 0.05
    historical_intelligence_quality: float = 0.10


DEFAULT_WEIGHTS = BSIConfidenceWeights()
DEFAULT_THRESHOLD_UNCALIBRATED: float | None = None  # unchanged discipline: never silently default to 75 or any other number


def bsi_confidence_v1_score(record: BSIThesisRecordORM, *, weights: BSIConfidenceWeights = DEFAULT_WEIGHTS) -> dict[str, Any]:
    """Computes every available dimension, RE-NORMALIZES weights over only the dimensions that
    actually returned a value (never silently treats a missing dimension as 0, which would punish
    a candidate for a data gap rather than a real quality signal), and returns the full
    breakdown -- never itself decides eligibility, exactly like the original design's own
    `bsi_confidence_score()`."""
    raw = {
        "structure_quality": structure_quality(record),
        "liquidity_quality": liquidity_quality(record),
        "location_quality": location_quality(record),
        "setup_completeness": setup_completeness(record),
        "session_quality": session_quality(record),
        "geometry_quality": geometry_quality(record),
        "execution_quality": execution_quality(record),
        "historical_intelligence_quality": historical_intelligence_quality(record),
    }
    available = {k: v for k, v in raw.items() if v is not None}
    weight_map = {
        "structure_quality": weights.structure_quality, "liquidity_quality": weights.liquidity_quality,
        "location_quality": weights.location_quality, "setup_completeness": weights.setup_completeness,
        "session_quality": weights.session_quality, "geometry_quality": weights.geometry_quality,
        "execution_quality": weights.execution_quality, "historical_intelligence_quality": weights.historical_intelligence_quality,
    }
    total_weight = sum(weight_map[k] for k in available) or 1.0
    composite = sum(available[k] * weight_map[k] for k in available) / total_weight
    return {
        "confidence_version": CONFIDENCE_VERSION,
        "components": raw,
        "components_available": list(available.keys()),
        "components_missing": [k for k in raw if k not in available],
        "weights": {k: weight_map[k] for k in weight_map},
        "composite_score": round(composite, 2) if available else None,
        "threshold": DEFAULT_THRESHOLD_UNCALIBRATED,
        "threshold_calibrated": False,
    }


def score_live_signal(*, subtype: str, canonical_symbol: str, direction: str, generated_at, entry: float, stop_loss: float,
                       thesis: dict | None, evidence: dict | None) -> dict[str, Any]:
    """Live-signal-generation-time entry point (BSI Intelligence Migration Phase G-extension) --
    scores a candidate the SAME way bsi_phase_g_calibration.py already validated against the real
    backfill corpus, reusing the EXACT SAME construction path (`BSISignalSource` ->
    `build_bsi_thesis_record()` from Phase B) rather than a second, parallel implementation. The
    resulting `BSIThesisRecordORM` instance is built purely in memory -- never added to a DB
    session, never persisted -- this function's only job is scoring, not recording (recording
    already happens through the existing fingerprint/thesis-record persistence path when a signal
    is actually taken; this just needs the same shape to call `bsi_confidence_v1_score()`
    correctly).

    A synthetic `fingerprint_id` is used (this candidate doesn't have a real one yet at
    signal-generation time) -- fine, since `build_bsi_thesis_record()` only uses it to derive
    `record_id`, which is discarded here."""
    source = BSISignalSource(
        fingerprint_id="LIVE_SCORING_TEMP", bsi_version=(thesis or {}).get("bsi_version", "BSI_BASELINE_V1"),
        subtype=subtype, canonical_symbol=canonical_symbol, direction=direction,
        candidate_time=generated_at, entry_time=generated_at, execution_timeframe="M15",
        thesis=thesis or {}, evidence=evidence or {}, entry=entry, initial_stop=stop_loss,
    )
    record = build_bsi_thesis_record(source)
    return bsi_confidence_v1_score(record)


def bsi_under_over_floor_verdict(composite_score: float | None, *, floor: float = BSI_CONFIDENCE_V1_FLOOR_DEFAULT) -> tuple[bool, str]:
    """Returns (passes_floor, reason). A None composite_score (no dimensions were computable at
    all) FAILS OPEN -- passes the floor rather than rejecting -- since a missing score reflects a
    data gap, not evidence of a bad setup; rejecting on absent information would be fabricating a
    signal the data doesn't contain, exactly what this whole redesign was built to avoid."""
    if composite_score is None:
        return True, "COMPOSITE_SCORE_UNAVAILABLE_FAIL_OPEN"
    if composite_score < floor:
        return False, f"BSI_CONFIDENCE_V1_BELOW_FLOOR_{composite_score}_LT_{floor}"
    return True, "OK"
