"""Phase 2 (Forex/MT5 roadmap): Monte Carlo / sequence-risk lab per account.

Answers, from REAL evidence, whether this account's configured per-trade risk
(config.risk_percent_per_trade, default 0.25%) is sustainable: what is the realistic range of
drawdown, losing-streak length, and FTMO daily/max-loss breach probability over a coming sequence
of trades. Never used to justify RAISING risk -- callers only ever read this for a downgrade
signal (see classify_sustainability's docstring).

R-multiple source, in priority order -- never fabricated:
  1. REAL closed trades for this exact account (AdaptivePositionStateORM.closed_detected_at is
     not null, non-contaminated). realized_r = compute_trade_costs(deals).net_pnl /
     original_risk_money -- the SAME computation service.py's own auto-replay path already uses
     for this account's real trade history (_auto_replay_recently_closed).
  2. If fewer than MIN_REAL_TRADES exist for this account (real DEMO trading has only been
     running a few days -- see walk_forward.py's own finding that most strategies are still
     INSUFFICIENT_SAMPLE), falls back to the pooled, trusted (non-UNTRUSTED) historical replay
     outcome corpus (HistoricalSetupOutcomeORM.outcome_r) as an EXPLICITLY labeled proxy -- never
     silently blended with real per-account trades. The result always reports which source was
     used (`r_source`).
  3. If neither has enough samples, returns INSUFFICIENT_SAMPLE -- never fabricates a synthetic
     R-multiple distribution just to produce a number.

Per-trade simulation: risk_money = equity * risk_percent_per_trade / 100 (COMPOUNDING -- matches
the real, live sizing formula in autonomous.py's base_risk_budget / execution.py's
equity_risk_cap, both scaled off account.equity, not a fixed initial balance). Bootstrap-resamples
the real R-multiple pool (i.i.d., not a parametric/normal assumption) for `trades_per_path`
trades, `num_paths` independent paths.

FTMO limits reused from prop_risk.ftmo_2step_limits (5% daily / 10% max -- this codebase's
existing FTMO_2_STEP default; see backend/brokers/mt5/prop_risk.py's PROFILES table), evaluated by
grouping the simulated trade sequence into `trades_per_day` chunks -- a real, explicit modeling
simplification (true intraday tick-level equity isn't reconstructible from R-multiples alone),
documented here rather than hidden. Max-loss is measured from the account's initial balance
(STATIC drawdown type, matching FTMO_2_STEP's profile), not a trailing peak.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np

from backend.brokers.mt5.prop_risk import ftmo_2step_limits
from backend.shared.db import SessionLocal

MIN_REAL_TRADES = 30
MIN_PROXY_TRADES = 30

SOURCE_REAL_ACCOUNT_TRADES = "REAL_ACCOUNT_TRADES"
SOURCE_PROXY_HISTORICAL_CORPUS = "PROXY_HISTORICAL_CORPUS"
SOURCE_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"

SUSTAINABLE = "SUSTAINABLE"
MARGINAL = "MARGINAL"
UNSUSTAINABLE = "UNSUSTAINABLE"
UNKNOWN = "UNKNOWN"


def real_closed_trade_r_multiples(account_id: str) -> list[float]:
    """Real, per-account R-multiples for every closed, non-contaminated position -- computed
    identically to service.py's own _auto_replay_recently_closed path (same compute_trade_costs
    call, same original_risk_money normalization), just read-only here."""
    from backend.adaptive_management.orm import AdaptivePositionStateORM, AdaptiveTradeEventORM
    from backend.brokers.mt5.trading_costs import compute_trade_costs

    r_values: list[float] = []
    with SessionLocal() as db:
        positions = (
            db.query(AdaptivePositionStateORM)
            .filter(
                AdaptivePositionStateORM.account_id == account_id,
                AdaptivePositionStateORM.closed_detected_at.isnot(None),
                AdaptivePositionStateORM.contaminated.is_(False),
            )
            .all()
        )
        for row in positions:
            if not row.original_risk_money or row.original_risk_money <= 0:
                continue
            deals = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.position_id == row.position_id, AdaptiveTradeEventORM.event_type == "DEAL").all()
            if not deals:
                continue
            net_pnl = compute_trade_costs([{"profit": d.realized_pnl, "commission": d.commission, "swap": d.swap, "fee": d.fee, "volume": d.volume} for d in deals]).net_pnl
            r_values.append(net_pnl / row.original_risk_money)
    return r_values


def proxy_historical_r_multiples(*, limit: int = 5000) -> list[float]:
    """Pooled, trusted (non-UNTRUSTED data_quality) historical replay outcome corpus -- an
    explicitly-labeled PROXY for when an account's own real trade history is too short. Never
    filtered per-account (the corpus isn't account-scoped -- it's market/strategy evidence)."""
    from backend.historical_intelligence.orm import HistoricalSetupOutcomeORM

    with SessionLocal() as db:
        rows = (
            db.query(HistoricalSetupOutcomeORM.outcome_r)
            .filter(HistoricalSetupOutcomeORM.resolution_status == "RESOLVED", HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED", HistoricalSetupOutcomeORM.outcome_r.isnot(None))
            .limit(limit)
            .all()
        )
    return [float(row[0]) for row in rows if row[0] is not None]


def proxy_historical_r_multiples_excluding_negative_combinations(*, limit: int = 5000) -> list[float]:
    """Phase G (edge-quality investigation): the SAME pooled proxy corpus as
    proxy_historical_r_multiples(), minus every row belonging to a (anchor_strategy,
    canonical_symbol) combination segment_matrix/walk_forward has persisted as FAILED_OOS
    (see historical_intelligence.walk_forward.latest_edge_stability_for_symbol) -- i.e. what the
    Monte Carlo sequence-risk picture looks like if Phase D's combination-level gate had already
    been preventing those specific, evidenced-negative setups from ever being traded. Read-only;
    never mutates the walk-forward results it reads."""
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
    from backend.historical_intelligence.walk_forward import EDGE_FAILED_OOS, latest_edge_stability_for_symbol

    with SessionLocal() as db:
        pairs = db.query(HistoricalPatternFingerprintORM.anchor_strategy, HistoricalPatternFingerprintORM.canonical_symbol).distinct().all()
        negative_pairs = {(strategy, symbol) for strategy, symbol in pairs if latest_edge_stability_for_symbol(anchor_strategy=strategy, canonical_symbol=symbol)["edge_stability"] == EDGE_FAILED_OOS}

        query = (
            db.query(HistoricalSetupOutcomeORM.outcome_r, HistoricalPatternFingerprintORM.anchor_strategy, HistoricalPatternFingerprintORM.canonical_symbol)
            .join(HistoricalPatternFingerprintORM, HistoricalPatternFingerprintORM.fingerprint_id == HistoricalSetupOutcomeORM.fingerprint_id)
            .filter(HistoricalSetupOutcomeORM.resolution_status == "RESOLVED", HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED", HistoricalSetupOutcomeORM.outcome_r.isnot(None))
            .limit(limit)
            .all()
        )
    return [float(r) for r, strategy, symbol in query if r is not None and (strategy, symbol) not in negative_pairs]


def r_multiple_pool(account_id: str) -> tuple[list[float], str]:
    real = real_closed_trade_r_multiples(account_id)
    if len(real) >= MIN_REAL_TRADES:
        return real, SOURCE_REAL_ACCOUNT_TRADES
    proxy = proxy_historical_r_multiples()
    if len(proxy) >= MIN_PROXY_TRADES:
        return proxy, SOURCE_PROXY_HISTORICAL_CORPUS
    return [], SOURCE_INSUFFICIENT_SAMPLE


@dataclass
class SequenceRiskResult:
    account_id: str
    r_source: str
    sample_size: int
    num_paths: int
    trades_per_path: int
    trades_per_day: int
    risk_percent_per_trade: float
    initial_balance: float
    daily_loss_limit: float
    max_loss_limit: float
    max_drawdown_percentiles: dict[str, float]
    losing_streak_percentiles: dict[str, float]
    probability_daily_loss_breach: float
    probability_max_loss_breach: float
    probability_of_ruin: float
    sustainability: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "r_source": self.r_source,
            "sample_size": self.sample_size,
            "num_paths": self.num_paths,
            "trades_per_path": self.trades_per_path,
            "trades_per_day": self.trades_per_day,
            "risk_percent_per_trade": self.risk_percent_per_trade,
            "initial_balance": self.initial_balance,
            "daily_loss_limit": self.daily_loss_limit,
            "max_loss_limit": self.max_loss_limit,
            "max_drawdown_percentiles": self.max_drawdown_percentiles,
            "losing_streak_percentiles": self.losing_streak_percentiles,
            "probability_daily_loss_breach": self.probability_daily_loss_breach,
            "probability_max_loss_breach": self.probability_max_loss_breach,
            "probability_of_ruin": self.probability_of_ruin,
            "sustainability": self.sustainability,
        }


def classify_sustainability(*, probability_of_ruin: float, probability_daily_loss_breach: float) -> str:
    """Conservative, evidence-only classification -- this function has exactly one legitimate use:
    flagging that risk should be REDUCED. It is never consulted anywhere as a reason to raise
    risk_percent_per_trade; nothing in this module writes to config."""
    if probability_of_ruin >= 0.20 or probability_daily_loss_breach >= 0.50:
        return UNSUSTAINABLE
    if probability_of_ruin >= 0.05 or probability_daily_loss_breach >= 0.20:
        return MARGINAL
    return SUSTAINABLE


def simulate(
    *,
    account_id: str,
    r_multiples: list[float],
    r_source: str,
    initial_balance: float,
    risk_percent_per_trade: float = 0.25,
    trades_per_path: int = 200,
    trades_per_day: int = 3,
    num_paths: int = 5000,
    daily_loss_percent: float = 5.0,
    max_loss_percent: float = 10.0,
    seed: int | None = 42,
) -> SequenceRiskResult:
    limits = ftmo_2step_limits(Decimal(str(initial_balance)), daily_loss_percent=Decimal(str(daily_loss_percent)), max_loss_percent=Decimal(str(max_loss_percent)))
    daily_loss_limit = float(limits["daily_loss_limit"])
    max_loss_limit = float(limits["max_loss_limit"])

    if r_source == SOURCE_INSUFFICIENT_SAMPLE or not r_multiples:
        return SequenceRiskResult(
            account_id=account_id, r_source=SOURCE_INSUFFICIENT_SAMPLE, sample_size=0, num_paths=0, trades_per_path=trades_per_path,
            trades_per_day=trades_per_day, risk_percent_per_trade=risk_percent_per_trade, initial_balance=initial_balance,
            daily_loss_limit=daily_loss_limit, max_loss_limit=max_loss_limit, max_drawdown_percentiles={}, losing_streak_percentiles={},
            probability_daily_loss_breach=0.0, probability_max_loss_breach=0.0, probability_of_ruin=0.0, sustainability=UNKNOWN,
        )

    rng = np.random.default_rng(seed)
    pool = np.array(r_multiples, dtype=float)
    max_dds: list[float] = []
    streaks: list[int] = []
    daily_breaches = 0
    max_breaches = 0

    for _ in range(num_paths):
        sampled = rng.choice(pool, size=trades_per_path, replace=True)
        equity = initial_balance
        peak_equity = initial_balance
        min_equity = initial_balance
        day_start_equity = initial_balance
        current_streak = 0
        worst_streak = 0
        path_daily_breach = False
        path_max_breach = False
        for i, r in enumerate(sampled):
            risk_money = equity * risk_percent_per_trade / 100.0
            equity += risk_money * float(r)
            peak_equity = max(peak_equity, equity)
            min_equity = min(min_equity, equity)
            if r < 0:
                current_streak += 1
                worst_streak = max(worst_streak, current_streak)
            else:
                current_streak = 0
            if (day_start_equity - equity) >= daily_loss_limit:
                path_daily_breach = True
            if (initial_balance - equity) >= max_loss_limit:
                path_max_breach = True
            if (i + 1) % trades_per_day == 0:
                day_start_equity = equity
        max_dds.append((peak_equity - min_equity) / peak_equity if peak_equity > 0 else 0.0)
        streaks.append(worst_streak)
        if path_daily_breach:
            daily_breaches += 1
        if path_max_breach:
            max_breaches += 1

    max_dd_arr = np.array(max_dds, dtype=float)
    streak_arr = np.array(streaks, dtype=float)
    probability_daily_loss_breach = daily_breaches / num_paths
    probability_max_loss_breach = max_breaches / num_paths
    sustainability = classify_sustainability(probability_of_ruin=probability_max_loss_breach, probability_daily_loss_breach=probability_daily_loss_breach)

    return SequenceRiskResult(
        account_id=account_id, r_source=r_source, sample_size=len(r_multiples), num_paths=num_paths, trades_per_path=trades_per_path,
        trades_per_day=trades_per_day, risk_percent_per_trade=risk_percent_per_trade, initial_balance=initial_balance,
        daily_loss_limit=daily_loss_limit, max_loss_limit=max_loss_limit,
        max_drawdown_percentiles={f"p{p}": round(float(np.percentile(max_dd_arr, p)), 6) for p in (5, 25, 50, 75, 95, 99)},
        losing_streak_percentiles={f"p{p}": round(float(np.percentile(streak_arr, p)), 2) for p in (5, 25, 50, 75, 95, 99)},
        probability_daily_loss_breach=round(probability_daily_loss_breach, 6),
        probability_max_loss_breach=round(probability_max_loss_breach, 6),
        probability_of_ruin=round(probability_max_loss_breach, 6),
        sustainability=sustainability,
    )


async def run_for_account(account_id: str, *, trades_per_path: int = 200, num_paths: int = 5000, seed: int | None = 42) -> dict[str, Any]:
    """End-to-end: resolves this account's real config (risk_percent_per_trade, expected initial
    balance) and real R-multiple pool, then simulates. Never raises -- an unresolvable account
    (unknown profile) returns INSUFFICIENT_SAMPLE rather than propagating."""
    from backend.brokers.mt5.account_registry import profile_by_id
    from backend.brokers.mt5.multi_account import adapter_for_account

    profile = profile_by_id(account_id)
    initial_balance = float(profile.expected_initial_balance) if profile else 10000.0
    try:
        config = adapter_for_account(account_id).config
        risk_percent = float(config.risk_percent_per_trade)
    except Exception:
        risk_percent = 0.25

    r_multiples, r_source = r_multiple_pool(account_id)
    result = simulate(
        account_id=account_id, r_multiples=r_multiples, r_source=r_source, initial_balance=initial_balance,
        risk_percent_per_trade=risk_percent, trades_per_path=trades_per_path, num_paths=num_paths, seed=seed,
    )
    return result.as_dict()
