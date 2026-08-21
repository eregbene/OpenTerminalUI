"""Strategy Robustness Report (QuantConnect/LEAN gap-analysis roadmap Phase 1, items 2-7).

Connects Bensim's EXISTING statistical machinery -- robustness/scorecard.py's PSR/DSR/bootstrap
implementation and historical_intelligence/walk_forward.py's TRAIN/OOS split -- to REAL MT5 Forex
strategy R-sequences, instead of rebuilding either. Neither dependency is modified by this module;
both are only called (read-only) exactly as their existing public API already allows (segment_
matrix.py already calls walk_forward.run_walk_forward the same way).

This module is ANALYSIS/VALIDATION INFRASTRUCTURE ONLY -- nothing here writes to, or is read by,
any live trading decision path (strategy activation, confidence thresholds, risk sizing, or the
Adaptive Trade Manager). It only reads historical_pattern_fingerprints/historical_setup_outcomes
and reports what it finds.

R-multiples are NOT compounding percentage returns (an R of -1.0 means "lost 1x the risk unit on
this trade", not "lost 100% of the account"), so this module deliberately does NOT feed raw R
sequences into scorecard.py's own bootstrap `cagr`/`max_drawdown` sub-fields (those compute
np.cumprod(1 + values), which silently corrupts to zero at any single -1R loss) or into
model_lab's equity-curve-shaped run_monte_carlo(). Two separate, explicit views are produced
instead:

  - STRATEGY-SEQUENCE robustness (Sharpe/PSR/DSR via scorecard.compute_robustness on the raw R
    sequence -- valid without any equity-fraction assumption, since those formulas depend only on
    the sequence's own mean/std/skew/kurtosis) plus an R-space block-bootstrap Monte Carlo
    (additive cumulative-R paths, drawdown measured in R units) -- reuses scorecard's own
    block-bootstrap resampling primitive, never a fresh reimplementation of it.
  - ACCOUNT-LEVEL view (optional, clearly labeled): R multiplied by Bensim's own REAL, currently-
    configured live risk-per-trade setting (MT5_RISK_PERCENT_PER_TRADE, brokers/mt5/config.py) to
    express drawdown/terminal-wealth in equity-percentage terms. This is Bensim's actual live
    sizing rule, not a fabricated assumption -- but it is still explicitly a DIFFERENT question
    ("what would this look like at Bensim's current position size") from strategy-sequence
    robustness ("is the R sequence itself statistically an edge"), and the two are never blended
    into one number.
"""
from __future__ import annotations

import math
import statistics as pystats
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

from backend.brokers.mt5.config import mt5_config
from backend.historical_intelligence import walk_forward
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.robustness.scorecard import _moving_block_resample, compute_robustness
from backend.shared.db import SessionLocal

_MIN_ROBUSTNESS_SAMPLE = 30  # below this, PSR/DSR/bootstrap are not meaningfully estimable
_DEFAULT_BLOCK_SIZE = 10
_MAX_BLOCK_SIZE = 60
_MC_PATHS = 2000
_MC_SEED = 42  # fixed for reproducibility (Part 10's explicit requirement)
_DRAWDOWN_PERCENTILES = (50, 75, 90, 95, 99)


@dataclass(frozen=True)
class _Row:
    entry_time: datetime
    canonical_symbol: str
    gross_r: float
    net_r: float | None
    spread_provenance: str
    commission_provenance: str


def fetch_strategy_rows(anchor_strategy: str, *, canonical_symbol: str | None = None, strategy_version: str | None = None) -> list[_Row]:
    """RESOLVED, non-UNTRUSTED outcomes only -- same exclusion rule statistics.py::pattern_
    statistics already applies, for the same reason (an UNTRUSTED outcome touched a TP/SL level
    but the underlying future candles could not be trusted, so it must not feed any statistic)."""
    with SessionLocal() as db:
        query = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(
                HistoricalPatternFingerprintORM.anchor_strategy == anchor_strategy,
                HistoricalSetupOutcomeORM.resolution_status == "RESOLVED",
                HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED",
                HistoricalSetupOutcomeORM.outcome_r.isnot(None),
            )
        )
        if canonical_symbol:
            query = query.filter(HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol.upper())
        if strategy_version:
            query = query.filter(HistoricalPatternFingerprintORM.strategy_version == strategy_version)
        rows = query.order_by(HistoricalPatternFingerprintORM.entry_time.asc()).all()

    out: list[_Row] = []
    for fp, oc in rows:
        entry_time = fp.entry_time if fp.entry_time.tzinfo else fp.entry_time.replace(tzinfo=timezone.utc)
        out.append(_Row(
            entry_time=entry_time, canonical_symbol=fp.canonical_symbol, gross_r=float(oc.outcome_r),
            net_r=float(oc.net_outcome_r) if oc.net_outcome_r is not None else None,
            spread_provenance=oc.spread_cost_provenance or "UNKNOWN",
            commission_provenance=oc.commission_cost_provenance or "UNKNOWN",
        ))
    return out


def cost_realism_summary(rows: list[_Row]) -> dict[str, Any]:
    """Gross vs net expectancy/profit-factor comparison plus cost provenance distribution --
    Part 1/2 of the roadmap ("recompute strategy statistics using NET results... comparison
    reporting"). Net figures are computed ONLY from rows whose net_r is actually known (spread
    provenance was not UNKNOWN) -- never imputed for the rest."""
    n = len(rows)
    if n == 0:
        return {"trades": 0, "gross_expectancy_r": None, "net_expectancy_r": None, "cost_drag_r": None,
                "gross_profit_factor": None, "net_profit_factor": None, "net_sample_size": 0,
                "spread_provenance_pct": {}, "commission_provenance_pct": {}}

    gross = [r.gross_r for r in rows]
    net_rows = [r for r in rows if r.net_r is not None]
    net = [r.net_r for r in net_rows]

    def _pf(values: list[float]) -> float | None:
        wins = sum(v for v in values if v > 0)
        losses = abs(sum(v for v in values if v < 0))
        return round(wins / losses, 4) if losses > 0 else None

    def _provenance_pct(field: str) -> dict[str, float]:
        counts: dict[str, int] = {}
        for r in rows:
            key = getattr(r, field)
            counts[key] = counts.get(key, 0) + 1
        return {k: round(100.0 * v / n, 2) for k, v in counts.items()}

    gross_exp = pystats.fmean(gross)
    net_exp = pystats.fmean(net) if net else None

    return {
        "trades": n,
        "gross_expectancy_r": round(gross_exp, 4),
        "net_expectancy_r": round(net_exp, 4) if net_exp is not None else None,
        "cost_drag_r": round(gross_exp - net_exp, 4) if net_exp is not None else None,
        "gross_profit_factor": _pf(gross),
        "net_profit_factor": _pf(net) if net else None,
        "net_sample_size": len(net_rows),
        "spread_provenance_pct": _provenance_pct("spread_provenance"),
        "commission_provenance_pct": _provenance_pct("commission_provenance"),
    }


def _trades_per_year(entry_times: list[datetime]) -> float:
    if len(entry_times) < 2:
        return 252.0  # scorecard.py's own default when annualization can't be estimated
    span_days = max((entry_times[-1] - entry_times[0]).total_seconds() / 86400.0, 1.0)
    span_years = max(span_days / 365.25, 1.0 / 365.25)
    return max(len(entry_times) / span_years, 1.0)


def _lag1_autocorrelation(values: list[float]) -> float:
    if len(values) < 10:
        return 0.0
    arr = np.asarray(values, dtype=float)
    a, b = arr[:-1], arr[1:]
    if np.std(a) <= 0 or np.std(b) <= 0:
        return 0.0
    return float(np.clip(np.corrcoef(a, b)[0, 1], -0.99, 0.99))


def effective_sample_size(values: list[float]) -> float:
    """Standard AR(1) effective-sample-size adjustment: ESS = N * (1 - rho) / (1 + rho). A
    well-established approximation for serially-correlated series (not a Bensim invention) --
    used here because Forex strategy losses can cluster by regime/session (Part 5's explicit
    concern), so the naive trade count overstates independent evidence when rho > 0."""
    n = len(values)
    if n == 0:
        return 0.0
    rho = _lag1_autocorrelation(values)
    ess = n * (1.0 - rho) / (1.0 + rho) if (1.0 + rho) > 1e-9 else float(n)
    return round(max(1.0, min(float(n), ess)), 1)


def robustness_scorecard(r_values: list[float], entry_times: list[datetime], *, bootstrap_paths: int = 2000) -> dict[str, Any]:
    """Direct, unmodified reuse of robustness/scorecard.py::compute_robustness -- the R sequence
    IS the return series (trade-based Sharpe/PSR/DSR, standard practice; valid without any
    equity-fraction conversion since these formulas only depend on the series' own moments).
    `periods_per_year` is estimated from the strategy's own observed trade cadence so "annualized
    Sharpe" means something for a trade-indexed series rather than assuming daily bars.
    `bootstrap_paths` is overridable (default 2000) for callers that need a lighter, faster run
    (e.g. an ad-hoc report against a large corpus) without touching the statistical method."""
    if len(r_values) < _MIN_ROBUSTNESS_SAMPLE:
        result = compute_robustness(r_values, periods_per_year=int(round(_trades_per_year(entry_times))), bootstrap_paths=0, seed=_MC_SEED)
        result["insufficient_sample_note"] = f"n={len(r_values)} < {_MIN_ROBUSTNESS_SAMPLE}; PSR/DSR/bootstrap are not meaningfully estimable yet."
        return result
    ppy = int(round(_trades_per_year(entry_times)))
    rho = _lag1_autocorrelation(r_values)
    block_size = int(np.clip(round(1.0 / max(1e-6, 1.0 - abs(rho))), _DEFAULT_BLOCK_SIZE, _MAX_BLOCK_SIZE)) if rho > 0.15 else _DEFAULT_BLOCK_SIZE
    return compute_robustness(r_values, periods_per_year=ppy, bootstrap_paths=bootstrap_paths, block_size=block_size, seed=_MC_SEED)


def monte_carlo_r_space(r_values: list[float], *, risk_percent_per_trade: float | None = None, paths: int = _MC_PATHS, seed: int = _MC_SEED) -> dict[str, Any]:
    """R-space block-bootstrap (additive cumulative-R paths, NOT compounding) -- reuses scorecard.
    _moving_block_resample's exact resampling algorithm (Part 3/4: "reuse the existing block-
    bootstrap implementation rather than building another one") but constructs the equity/drawdown
    metrics itself in R-units, since scorecard's own cagr/max_drawdown sub-fields assume
    compounding percentage returns and would silently corrupt on any single -1R loss.

    `risk_percent_per_trade`: when given (Bensim's own real, currently-configured live risk-per-
    trade setting), ALSO reports an account-level equity-percentage view alongside the R-space
    view -- clearly separated (Part 4's explicit requirement: distinguish strategy-sequence
    robustness from account-level probability of ruin, never blend them into one number)."""
    n = len(r_values)
    if n < _MIN_ROBUSTNESS_SAMPLE:
        return {"paths": 0, "note": f"n={n} < {_MIN_ROBUSTNESS_SAMPLE}; Monte Carlo skipped as statistically unreliable."}

    values = np.asarray(r_values, dtype=float)
    rho = _lag1_autocorrelation(r_values)
    block_size = int(np.clip(round(1.0 / max(1e-6, 1.0 - abs(rho))), _DEFAULT_BLOCK_SIZE, _MAX_BLOCK_SIZE)) if rho > 0.15 else _DEFAULT_BLOCK_SIZE
    rng = np.random.default_rng(seed)

    terminal_r = np.empty(paths, dtype=float)
    max_dd_r = np.empty(paths, dtype=float)
    longest_losing_run = np.empty(paths, dtype=int)
    for i in range(paths):
        sample = _moving_block_resample(values, block_size=block_size, rng=rng)
        cum = np.cumsum(sample)
        peak = np.maximum.accumulate(np.concatenate(([0.0], cum)))[1:]
        dd = cum - peak
        terminal_r[i] = cum[-1]
        max_dd_r[i] = float(-np.min(dd))
        longest_losing_run[i] = _longest_negative_run(sample)

    result: dict[str, Any] = {
        "paths": paths,
        "method": "block_bootstrap_r_space",
        "block_size": block_size,
        "lag1_autocorrelation": round(rho, 4),
        "terminal_cumulative_r": {f"p{p}": round(float(np.percentile(terminal_r, p)), 3) for p in (5, 25, 50, 75, 95)},
        "probability_terminal_negative": round(float(np.mean(terminal_r < 0.0)), 4),
        "drawdown_r_percentiles": {f"p{p}": round(float(np.percentile(max_dd_r, p)), 3) for p in _DRAWDOWN_PERCENTILES},
        "longest_losing_run_percentiles": {f"p{p}": int(np.percentile(longest_losing_run, p)) for p in (50, 90, 95)},
    }

    if risk_percent_per_trade and risk_percent_per_trade > 0:
        pct = risk_percent_per_trade / 100.0
        # Compounding equity drawdown from a sequence of fixed-fractional-risk R losses:
        # 1 - exp(-R_drawdown * risk_pct) (each R-unit of drawdown compounds the SAME fixed
        # fraction of current equity, matching Bensim's actual fixed-percent-of-equity sizing
        # rule, not a fabricated conversion).
        equity_dd_pct = 1.0 - np.exp(-max_dd_r * pct)
        result["account_level_view"] = {
            "source": "MT5_RISK_PERCENT_PER_TRADE (Bensim's live-configured risk-per-trade)",
            "risk_percent_per_trade": risk_percent_per_trade,
            "probability_of_breaching_10pct_drawdown": round(float(np.mean(equity_dd_pct >= 0.10)), 4),
            "probability_of_breaching_20pct_drawdown": round(float(np.mean(equity_dd_pct >= 0.20)), 4),
            "drawdown_pct_p95": round(float(np.percentile(equity_dd_pct, 95)) * 100.0, 2),
            "note": "Approximates equity drawdown as 1-exp(-R_drawdown * risk_pct); assumes fixed fractional risk per trade, not compounding position sizing. This is a SEPARATE, account-sizing-dependent view -- not a property of the strategy's R-sequence itself.",
        }
    return result


def _longest_negative_run(values: np.ndarray) -> int:
    """Vectorized run-length scan (boundary-diff trick) -- called once per Monte Carlo path
    (thousands of times per report), so a pure-Python per-element loop here was measured as the
    dominant cost of the whole report run. Equivalent result to the naive loop, just without
    per-element Python-level overhead."""
    is_negative = np.asarray(values) < 0
    if not is_negative.any():
        return 0
    bounded = np.concatenate(([0], is_negative.astype(np.int8), [0]))
    diffs = np.diff(bounded)
    starts = np.flatnonzero(diffs == 1)
    ends = np.flatnonzero(diffs == -1)
    return int(np.max(ends - starts))


def drawdown_duration_stats(rows: list[_Row], *, use_net: bool = False) -> dict[str, Any]:
    """Computed on the REAL (non-bootstrapped) historical R sequence, in entry-chronological
    order -- Part 6's drawdown-duration analytics. Duration is reported in days (elapsed
    entry_time span) since that is what will eventually matter for a live gate; trade-count is
    also reported as a supporting figure."""
    values = [r.net_r if use_net and r.net_r is not None else r.gross_r for r in rows]
    times = [r.entry_time for r in rows]
    n = len(values)
    if n == 0:
        return {"max_drawdown_r": None, "max_drawdown_duration_days": None, "avg_drawdown_duration_days": None,
                "recovery_duration_days": None, "recovery_factor": None, "longest_losing_streak": 0}

    cum = 0.0
    peak = 0.0
    peak_time = times[0]
    max_dd = 0.0
    max_dd_duration = 0.0
    episodes: list[float] = []
    episode_start_time = None
    in_drawdown = False
    unrecovered_duration = 0.0

    for i in range(n):
        cum += values[i]
        if cum >= peak:
            if in_drawdown and episode_start_time is not None:
                episodes.append((times[i] - episode_start_time).total_seconds() / 86400.0)
            peak = cum
            peak_time = times[i]
            in_drawdown = False
            episode_start_time = None
        else:
            if not in_drawdown:
                in_drawdown = True
                episode_start_time = peak_time
            depth = peak - cum
            if depth > max_dd:
                max_dd = depth
                max_dd_duration = (times[i] - peak_time).total_seconds() / 86400.0

    if in_drawdown and episode_start_time is not None:
        unrecovered_duration = (times[-1] - episode_start_time).total_seconds() / 86400.0

    longest_streak = _longest_negative_run(np.asarray(values))
    recovery_factor = round(cum / max_dd, 4) if max_dd > 0 else None

    return {
        "max_drawdown_r": round(max_dd, 4),
        "max_drawdown_duration_days": round(max_dd_duration, 1),
        "avg_drawdown_duration_days": round(pystats.fmean(episodes), 1) if episodes else None,
        "recovery_duration_days": round(pystats.fmean(episodes), 1) if episodes else None,
        "currently_unrecovered_duration_days": round(unrecovered_duration, 1) if in_drawdown else 0.0,
        "recovery_factor": recovery_factor,
        "longest_losing_streak": longest_streak,
        "closed_drawdown_episode_count": len(episodes),
    }


def walk_forward_verdict(anchor_strategy: str, *, canonical_symbol: str | None = None) -> dict[str, Any]:
    """Read-only reuse of walk_forward.run_walk_forward -- the SAME function brokers/mt5/
    sequence_risk.py already consumes for a live gate. Called here exactly the way segment_
    matrix.py already calls it (no modification to walk_forward.py itself, so this cannot change
    that live gate's behavior)."""
    result = walk_forward.run_walk_forward(anchor_strategy=anchor_strategy, canonical_symbol=canonical_symbol)
    edge_stability = result.get("edge_stability")
    if edge_stability in (walk_forward.EDGE_STRONG, walk_forward.EDGE_ACCEPTABLE):
        verdict = "PASS"
    elif edge_stability in (walk_forward.EDGE_DEGRADED, walk_forward.EDGE_FAILED_OOS):
        verdict = "FAIL"
    else:
        verdict = "N/A"
    return {
        "verdict": verdict,
        "edge_stability": edge_stability,
        "directional_edge": result.get("directional_edge"),
        "train_n": result.get("train", {}).get("n"),
        "oos_n": result.get("oos", {}).get("n"),
        "train_expectancy_r_gross": result.get("train", {}).get("expectancy_r"),
        "oos_expectancy_r_gross": result.get("oos", {}).get("expectancy_r"),
        "degradation_pct": result.get("degradation_pct"),
        "note": "Gross-R only -- walk_forward.py does not split net_outcome_r (not modified here to avoid touching a function already wired into a live gate; see brokers/mt5/sequence_risk.py).",
    }


def build_strategy_robustness_report(anchor_strategy: str, *, canonical_symbol: str | None = None, strategy_version: str | None = None) -> dict[str, Any]:
    """The consolidated per-strategy report (Part 7). Combines: cost realism (gross vs net),
    PSR/DSR/Sharpe (scorecard.py, unmodified), R-space Monte Carlo, drawdown-duration analytics,
    and walk-forward PASS/FAIL (walk_forward.py, unmodified, read-only) -- every number here is
    computed from REAL Bensim historical outcomes, never hardcoded."""
    rows = fetch_strategy_rows(anchor_strategy, canonical_symbol=canonical_symbol, strategy_version=strategy_version)
    cost = cost_realism_summary(rows)
    gross_r = [r.gross_r for r in rows]
    entry_times = [r.entry_time for r in rows]

    scorecard = robustness_scorecard(gross_r, entry_times)
    risk_cfg = None
    try:
        risk_cfg = mt5_config().risk_percent_per_trade
    except Exception:
        risk_cfg = None
    monte_carlo = monte_carlo_r_space(gross_r, risk_percent_per_trade=risk_cfg)
    drawdown = drawdown_duration_stats(rows, use_net=False)
    wf = walk_forward_verdict(anchor_strategy, canonical_symbol=canonical_symbol)
    ess = effective_sample_size(gross_r)

    return {
        "strategy": anchor_strategy,
        "symbol": canonical_symbol,
        "strategy_version": strategy_version,
        "historical_evidence": {**cost, "effective_sample_size": ess},
        "statistical_robustness": {
            "sharpe_annualized": scorecard.get("annual_sharpe"),
            "psr": scorecard.get("psr"),
            "dsr": scorecard.get("dsr"),
            "min_track_record_length_trades": scorecard.get("min_track_record_length"),
            "bootstrap_sharpe_ci": (scorecard.get("bootstrap") or {}).get("sharpe"),
            "monte_carlo": monte_carlo,
            "verdict": str(scorecard.get("verdict", "insufficient")).upper(),
            "verdict_reasons": scorecard.get("verdict_reasons"),
        },
        "drawdown": drawdown,
        "walk_forward": wf,
        "directional_edge": wf.get("directional_edge"),
    }
