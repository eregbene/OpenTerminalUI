from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class ThresholdProfile:
    name: str
    consensus_threshold: float
    min_eligible_strategies: int
    min_directional_score: float
    max_conflicting_strategies: int
    min_confidence: float
    min_risk_reward: float
    max_holding_minutes: int

    def model_dump(self) -> dict[str, float | int | str]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class AITradingConfig:
    provider: str = os.getenv("AI_PROVIDER", "openai")
    model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    analysis_enabled: bool = _bool("AI_ANALYSIS_ENABLED", True)
    trading_mode: str = os.getenv("AI_TRADING_MODE", "SHADOW").upper()
    order_submission_enabled: bool = _bool("AI_ORDER_SUBMISSION_ENABLED", False)
    analysis_interval_seconds: int = _int("AI_ANALYSIS_INTERVAL_SECONDS", 900)
    request_timeout_seconds: float = _float("AI_PROVIDER_TIMEOUT_SECONDS", _float("AI_REQUEST_TIMEOUT_SECONDS", 30.0))
    max_retries: int = _int("AI_MAX_RETRIES", 0)
    min_confidence: float = _float("AI_MIN_CONFIDENCE", 0.70)
    min_risk_reward: float = _float("AI_MIN_RISK_REWARD", 1.5)
    max_risk_percent: float = _float("AI_MAX_RISK_PERCENT", 0.10)
    symbol_allowlist: tuple[str, ...] = tuple(
        item.strip().upper().replace("/", "")
        for item in os.getenv("AI_SYMBOL_ALLOWLIST", "EURUSD,GBPUSD,USDJPY").split(",")
        if item.strip()
    )
    timeframe_allowlist: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv("AI_TIMEFRAME_ALLOWLIST", "15m").split(",")
        if item.strip()
    )
    allowed_order_types: tuple[str, ...] = tuple(
        item.strip().upper()
        for item in os.getenv("AI_ALLOWED_ORDER_TYPES", "MARKET").split(",")
        if item.strip()
    )
    max_candles: int = _int("AI_MAX_CONTEXT_CANDLES", 60)
    max_staleness_seconds: int = _int("AI_MAX_DATA_STALENESS_SECONDS", 900)
    max_position_size_forex: int = _int("AI_MAX_POSITION_SIZE_FOREX", 1000)
    max_open_positions: int = _int("AI_MAX_OPEN_POSITIONS", 1)
    max_open_orders: int = _int("AI_MAX_OPEN_ORDERS", 3)
    max_trades_per_day: int = _int("AI_MAX_TRADES_PER_DAY", 3)
    max_trades_per_symbol_per_day: int = _int("AI_MAX_TRADES_PER_SYMBOL_PER_DAY", 1)
    max_daily_loss_usd: float = _float("AI_MAX_DAILY_LOSS_USD", 10.0)
    max_total_open_risk_usd: float = _float("AI_MAX_TOTAL_OPEN_RISK_USD", _float("AI_MAX_DAILY_LOSS_USD", 10.0))
    max_trade_loss_usd: float = _float("AI_MAX_TRADE_LOSS_USD", 3.0)
    paper_account_starting_balance: float = _float("PAPER_ACCOUNT_STARTING_BALANCE", 100000.0)
    paper_account_currency: str = os.getenv("PAPER_ACCOUNT_CURRENCY", "USD").upper()
    estimated_commission_usd: float = _float("AI_ESTIMATED_COMMISSION_USD", 0.0)
    estimated_slippage_pips: float = _float("AI_ESTIMATED_SLIPPAGE_PIPS", 0.0)
    estimated_spread_pips: float = _float("AI_ESTIMATED_SPREAD_PIPS", 0.0)
    cooldown_seconds: int = _int("AI_COOLDOWN_SECONDS", 3600)
    post_trade_cooldown_minutes: int = _int("AI_POST_TRADE_COOLDOWN_MINUTES", 15)
    max_consecutive_losses: int = _int("AI_MAX_CONSECUTIVE_LOSSES", 2)
    daily_profit_lock_usd: float = _float("AI_DAILY_PROFIT_LOCK_USD", 0.0)
    max_spread_bps: float = _float("AI_MAX_SPREAD_BPS", 8.0)
    max_output_tokens: int = _int("AI_MAX_OUTPUT_TOKENS", 400)
    max_provider_requests_per_hour: int = _int("AI_MAX_PROVIDER_REQUESTS_PER_HOUR", 3)
    max_provider_requests_per_day: int = _int("AI_MAX_PROVIDER_REQUESTS_PER_DAY", 24)
    daily_cost_limit_usd: float = _float("AI_DAILY_COST_LIMIT_USD", 0.25)
    monthly_cost_limit_usd: float = _float("AI_MONTHLY_COST_LIMIT_USD", 5.0)
    consensus_threshold: float = _float("AI_CONSENSUS_THRESHOLD", 0.62)
    min_eligible_strategies: int = _int("AI_MIN_ELIGIBLE_STRATEGIES", 2)
    min_directional_score: float = _float("AI_MIN_DIRECTIONAL_SCORE", _float("AI_CONSENSUS_THRESHOLD", 0.62))
    max_conflicting_strategies: int = _int("AI_MAX_CONFLICTING_STRATEGIES", 0)
    require_protective_orders: bool = _bool("AI_REQUIRE_PROTECTIVE_ORDERS", True)
    max_holding_minutes: int = _int("AI_MAX_HOLDING_MINUTES", 240)
    acceptance_validation_mode: bool = _bool("AI_ACCEPTANCE_VALIDATION_MODE", False)
    validation_consensus_threshold: float = _float("AI_VALIDATION_CONSENSUS_THRESHOLD", 0.50)
    validation_min_eligible_strategies: int = _int("AI_VALIDATION_MIN_ELIGIBLE_STRATEGIES", 1)
    validation_min_directional_score: float = _float("AI_VALIDATION_MIN_DIRECTIONAL_SCORE", 0.58)
    validation_max_conflicting_strategies: int = _int("AI_VALIDATION_MAX_CONFLICTING_STRATEGIES", 1)
    validation_min_confidence: float = _float("AI_VALIDATION_MIN_CONFIDENCE", 0.65)
    validation_min_risk_reward: float = _float("AI_VALIDATION_MIN_RISK_REWARD", 1.50)
    validation_max_holding_minutes: int = _int("AI_VALIDATION_MAX_HOLDING_MINUTES", 60)
    scheduler_enabled: bool = _bool("AI_SCHEDULER_ENABLED", False)
    multi_trade_validation_mode: bool = _bool("AI_MULTI_TRADE_VALIDATION_MODE", False)
    trading_day_timezone: str = os.getenv("AI_TRADING_DAY_TIMEZONE", "Europe/Bucharest")
    daily_acceptance_max_entries: int = _int("AI_DAILY_ACCEPTANCE_MAX_ENTRIES", _int("AI_AUTONOMOUS_ACCEPTANCE_MAX_ENTRIES", 1))
    daily_acceptance_entries_used: int = _int("AI_DAILY_ACCEPTANCE_ENTRIES_USED", 0)
    daily_acceptance_date: str = os.getenv("AI_DAILY_ACCEPTANCE_DATE", "")
    autonomous_acceptance_max_entries: int = _int("AI_AUTONOMOUS_ACCEPTANCE_MAX_ENTRIES", 1)
    autonomous_acceptance_entry_used: bool = _bool("AI_AUTONOMOUS_ACCEPTANCE_ENTRY_USED", False)
    autonomous_acceptance_complete: bool = _bool("AI_AUTONOMOUS_ACCEPTANCE_COMPLETE", False)
    prompt_version: str = "ai_trade_decision_v1"
    context_version: str = "ai_trade_context_v2_compact"
    schema_version: str = "ai_trade_decision_schema_v1"

    @property
    def api_key_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY", "").strip())

    @property
    def production_profile(self) -> ThresholdProfile:
        return ThresholdProfile(
            name="PRODUCTION_INSTITUTIONAL",
            consensus_threshold=self.consensus_threshold,
            min_eligible_strategies=self.min_eligible_strategies,
            min_directional_score=self.min_directional_score,
            max_conflicting_strategies=self.max_conflicting_strategies,
            min_confidence=self.min_confidence,
            min_risk_reward=self.min_risk_reward,
            max_holding_minutes=self.max_holding_minutes,
        )

    @property
    def validation_profile(self) -> ThresholdProfile:
        return ThresholdProfile(
            name="PAPER_ACCEPTANCE_VALIDATION",
            consensus_threshold=self.validation_consensus_threshold,
            min_eligible_strategies=self.validation_min_eligible_strategies,
            min_directional_score=self.validation_min_directional_score,
            max_conflicting_strategies=self.validation_max_conflicting_strategies,
            min_confidence=self.validation_min_confidence,
            min_risk_reward=self.validation_min_risk_reward,
            max_holding_minutes=self.validation_max_holding_minutes,
        )

    def threshold_profile(self) -> ThresholdProfile:
        return self.validation_profile if self.acceptance_validation_mode else self.production_profile


def ai_trading_config() -> AITradingConfig:
    return AITradingConfig()
