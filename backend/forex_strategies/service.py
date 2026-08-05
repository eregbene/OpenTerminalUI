from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.forex_frameworks.models import FrameworkBias
from backend.forex_intelligence.instruments import FOREX_INSTRUMENTS
from backend.forex_strategies.ibkr_acceptance import FX5_EXECUTION_PROVIDER, LOCAL_SIMULATOR_PROVIDER, ibkr_acceptance_service
from backend.forex_strategies.models import (
    ActiveTrade,
    CandidateStatus,
    Direction,
    ExecutionMode,
    RiskDecisionRecord,
    StrategyEligibility,
    StrategyStatus,
    TradeCandidate,
    utcnow,
)
from backend.forex_strategies.registry import FIRST_ENABLED, strategy_registry
from backend.forex_strategies.store import ForexSignalStore
from backend.trading.deployments import approve_deployment
from backend.trading.models import (
    DeploymentStatus,
    InstrumentReference,
    MarketReference,
    OrderIntent,
    OrderSide,
    PaperOrderType,
    RiskPolicy,
    StrategyDeployment,
)
from backend.trading.services import TradingControlService


SAFE_DEFAULTS = {
    "execution_mode": ExecutionMode.MANUAL_CONFIRMATION.value,
    "risk_per_trade_percent": 0.25,
    "maximum_risk_per_trade_percent": 0.50,
    "maximum_open_trades": 3,
    "maximum_trades_per_symbol": 1,
    "maximum_trades_per_strategy": 2,
    "maximum_candidates_per_strategy_per_candle": 1,
    "completed_candles_only": True,
    "stale_data_blocking": True,
    "stop_required": True,
    "minimum_risk_reward": 1.5,
    "xauusd_execution_enabled": False,
    "enabled_execution_symbols": FIRST_ENABLED,
    "execution_providers": [LOCAL_SIMULATOR_PROVIDER, FX5_EXECUTION_PROVIDER, "DISABLED"],
}


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _id(prefix: str, payload: Any) -> str:
    return f"{prefix}_{_hash(payload)[:16]}"


class ForexSignalService:
    def __init__(self, store: ForexSignalStore | None = None, trading: TradingControlService | None = None) -> None:
        self.store = store or ForexSignalStore()
        self.trading = trading or TradingControlService()

    def summary(self) -> dict[str, Any]:
        candidates = self.store.candidates()
        trades = self.store.trades()
        counts: dict[str, int] = {}
        for candidate in candidates:
            counts[candidate.status.value] = counts.get(candidate.status.value, 0) + 1
        return {
            "active_candidate_count": sum(1 for c in candidates if c.status in {CandidateStatus.CREATED, CandidateStatus.AWAITING_CONFIRMATION, CandidateStatus.APPROVED}),
            "awaiting_confirmation_count": counts.get(CandidateStatus.AWAITING_CONFIRMATION.value, 0),
            "approved_count": counts.get(CandidateStatus.APPROVED.value, 0),
            "rejected_count": counts.get(CandidateStatus.REJECTED.value, 0) + counts.get(CandidateStatus.RISK_REJECTED.value, 0),
            "expired_count": counts.get(CandidateStatus.EXPIRED.value, 0),
            "active_paper_trades": len([trade for trade in trades if trade.status == "OPEN"]),
            "current_paper_pnl": sum(trade.unrealized_pnl for trade in trades),
            "emergency_disable_state": "ACCOUNT_SCOPED",
            "safe_defaults": SAFE_DEFAULTS,
        }

    def eligibility(self, context: dict[str, Any]) -> list[StrategyEligibility]:
        symbol = str(context.get("symbol", "")).upper()
        timeframe = str(context.get("timeframe", "1h"))
        regime = str(context.get("regime") or context.get("market_regime") or "trend")
        data_quality = float(context.get("data_quality", 1))
        stale = bool(context.get("stale", False))
        spread = float(context.get("spread", 0.00008))
        frameworks = set(context.get("frameworks", []))
        rows: list[StrategyEligibility] = []
        for strategy in strategy_registry.all():
            reasons: list[str] = []
            if not strategy.enabled:
                reasons.append("STRATEGY_DISABLED")
            if strategy.status not in {StrategyStatus.PAPER_APPROVED, StrategyStatus.PAPER_ACTIVE, StrategyStatus.PAPER_CANDIDATE}:
                reasons.append(f"STATUS_{strategy.status.value}")
            if symbol not in strategy.supported_symbols:
                reasons.append("UNSUPPORTED_SYMBOL")
            if timeframe not in strategy.supported_timeframes:
                reasons.append("UNSUPPORTED_TIMEFRAME")
            if regime in strategy.unsupported_regimes:
                reasons.append("UNSUPPORTED_REGIME")
            if strategy.supported_regimes and regime not in strategy.supported_regimes:
                reasons.append("REGIME_NOT_SUPPORTED")
            if data_quality < strategy.minimum_data_quality:
                reasons.append("DATA_QUALITY_BELOW_MINIMUM")
            if stale and SAFE_DEFAULTS["stale_data_blocking"]:
                reasons.append("STALE_DATA")
            if spread > float(context.get("max_spread", 0.00030)):
                reasons.append("SPREAD_TOO_WIDE")
            missing_frameworks = [item for item in strategy.framework_dependencies if item not in frameworks]
            if missing_frameworks:
                reasons.append("FRAMEWORK_DEPENDENCY_MISSING")
            if symbol == "XAUUSD" and not SAFE_DEFAULTS["xauusd_execution_enabled"]:
                reasons.append("XAUUSD_EXECUTION_DISABLED")
            score = 0 if reasons else self.rank_strategy(strategy.strategy_id, context)
            rows.append(StrategyEligibility(strategy_id=strategy.strategy_id, eligible=not reasons, reasons=reasons, score=score))
        return sorted(rows, key=lambda row: row.score, reverse=True)

    def rank_strategy(self, strategy_id: str, context: dict[str, Any]) -> float:
        validation = 0.62 if strategy_registry.require(strategy_id).validation_scorecard_id else 0.25
        agreement = float(context.get("framework_agreement", 0.5))
        data_quality = float(context.get("data_quality", 1))
        regime_bonus = 0.12 if context.get("regime") in strategy_registry.require(strategy_id).supported_regimes else 0
        execution_cost_penalty = min(float(context.get("spread", 0.00008)) * 100, 0.08)
        return round(max(0, validation * 0.35 + agreement * 0.30 + data_quality * 0.25 + regime_bonus - execution_cost_penalty), 4)

    def generate(self, context: dict[str, Any]) -> TradeCandidate:
        symbol = str(context.get("symbol", "EURUSD")).upper()
        timeframe = str(context.get("timeframe", "1h"))
        candle_timestamp = context.get("candle_timestamp") or utcnow()
        eligibilities = self.eligibility(context)
        selected = next((row for row in eligibilities if row.eligible), None)
        if selected is None:
            candidate = self._rejected_candidate(context, eligibilities)
            self.store.upsert_candidate(candidate)
            return candidate
        strategy = strategy_registry.require(selected.strategy_id)
        direction = self._direction(context)
        entry = float(context.get("price", 1.085))
        pip = float(FOREX_INSTRUMENTS[symbol].pip_size)
        stop_distance = max(float(context.get("stop_distance", pip * 20)), pip * 5)
        stop = entry - stop_distance if direction == Direction.BUY else entry + stop_distance
        target = entry + stop_distance * 2 if direction == Direction.BUY else entry - stop_distance * 2
        rr = abs(target - entry) / abs(entry - stop)
        idem = _hash([strategy.strategy_id, strategy.strategy_version, symbol, timeframe, candle_timestamp, direction.value])
        existing = self._active_by_idempotency(idem)
        if existing:
            return existing
        content = {
            "strategy_id": strategy.strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction.value,
            "entry": entry,
            "stop": stop,
            "target": target,
            "candle": candle_timestamp,
        }
        candidate = TradeCandidate(
            candidate_id=_id("fxcand", content),
            idempotency_key=idem,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.strategy_version,
            paper_deployment_id=strategy.paper_deployment_id,
            symbol=symbol,
            instrument_type="FOREX" if symbol != "XAUUSD" else "PRECIOUS_METAL_PROXY",
            timeframe=timeframe,
            direction=direction,
            signal_timestamp=utcnow(),
            candle_timestamp=candle_timestamp,
            entry_type="MARKET",
            candidate_entry=round(entry, FOREX_INSTRUMENTS[symbol].display_precision),
            entry_zone={"low": round(entry - pip * 2, FOREX_INSTRUMENTS[symbol].display_precision), "high": round(entry + pip * 2, FOREX_INSTRUMENTS[symbol].display_precision)},
            stop_price=round(stop, FOREX_INSTRUMENTS[symbol].display_precision),
            target_prices=[round(target, FOREX_INSTRUMENTS[symbol].display_precision)],
            risk_reward=round(rr, 2),
            confidence=round(min(0.85, selected.score), 4),
            framework_agreement=float(context.get("framework_agreement", 0.66)),
            regime=str(context.get("regime", "trend")),
            session=str(context.get("session", "london")),
            spread=float(context.get("spread", pip)),
            data_quality=float(context.get("data_quality", 1)),
            feature_vector_id=context.get("feature_vector_id"),
            framework_signal_ids=list(context.get("framework_signal_ids", [])),
            source_dataset_id=context.get("source_dataset_id"),
            invalidation="stop beyond deterministic structure invalidation",
            expiration=utcnow() + timedelta(hours=2),
            status=CandidateStatus.CREATED,
            content_hash=_hash(content),
            explanation=self._explanation(strategy.strategy_id, context, direction, entry, stop, [target], eligibilities),
            eligibility=eligibilities,
        )
        candidate.risk_decision = self.evaluate_risk(candidate)
        candidate.status = CandidateStatus.AWAITING_CONFIRMATION if candidate.risk_decision.approved else CandidateStatus.RISK_REJECTED
        self.store.upsert_candidate(candidate)
        return candidate

    def evaluate_risk(self, candidate: TradeCandidate, account_equity: float = 100000.0) -> RiskDecisionRecord:
        reasons: list[str] = []
        if candidate.symbol == "XAUUSD":
            reasons.append("BROKER_CONTRACT_UNAVAILABLE")
        if candidate.expiration <= utcnow():
            reasons.append("CANDIDATE_EXPIRED")
        if candidate.data_quality < 0.8:
            reasons.append("STALE_OR_LOW_QUALITY_DATA")
        if candidate.risk_reward < float(SAFE_DEFAULTS["minimum_risk_reward"]):
            reasons.append("RISK_REWARD_BELOW_MINIMUM")
        if candidate.stop_price <= 0 or candidate.stop_price == candidate.candidate_entry:
            reasons.append("INVALID_STOP")
        trades = [trade for trade in self.store.trades() if trade.status == "OPEN"]
        if len(trades) >= int(SAFE_DEFAULTS["maximum_open_trades"]):
            reasons.append("MAX_OPEN_TRADES")
        if sum(1 for trade in trades if trade.symbol == candidate.symbol) >= int(SAFE_DEFAULTS["maximum_trades_per_symbol"]):
            reasons.append("MAX_TRADES_PER_SYMBOL")
        usd_exposure = self._usd_exposure_after(candidate, trades)
        if abs(usd_exposure) > 300000:
            reasons.append("CORRELATED_USD_EXPOSURE_LIMIT")
        size = self._position_size(candidate, account_equity) if not reasons else 0
        payload = {"candidate_id": candidate.candidate_id, "reasons": reasons, "size": size, "usd_exposure": usd_exposure}
        return RiskDecisionRecord(
            decision_id=_id("fxrisk", payload),
            candidate_id=candidate.candidate_id,
            approved=not reasons,
            rejection_reasons=reasons,
            position_size=size,
            estimated_risk=round(account_equity * float(SAFE_DEFAULTS["risk_per_trade_percent"]) / 100, 2) if not reasons else 0,
            estimated_margin=round(candidate.candidate_entry * size * 0.033, 2) if not reasons else 0,
            exposure_before={"open_trade_count": len(trades)},
            exposure_after={"usd_directional_exposure": usd_exposure},
            data_used={"risk_per_trade_percent": SAFE_DEFAULTS["risk_per_trade_percent"], "entry": candidate.candidate_entry, "stop": candidate.stop_price},
            content_hash=_hash(payload),
        )

    def approve(self, candidate_id: str, account_id: str | None = None, execution_provider: str = LOCAL_SIMULATOR_PROVIDER) -> TradeCandidate:
        candidate = self.store.get_candidate(candidate_id)
        if candidate.status != CandidateStatus.AWAITING_CONFIRMATION:
            raise ValueError("CANDIDATE_NOT_AWAITING_CONFIRMATION")
        if candidate.expiration <= utcnow():
            candidate.status = CandidateStatus.EXPIRED
            self.store.upsert_candidate(candidate)
            raise ValueError("CANDIDATE_EXPIRED")
        if not candidate.risk_decision or not candidate.risk_decision.approved:
            candidate.status = CandidateStatus.RISK_REJECTED
            self.store.upsert_candidate(candidate)
            raise ValueError("RISK_NOT_APPROVED")
        account = account_id or self._ensure_account()
        deployment = self._ensure_deployment(candidate, account)
        intent = self._intent(candidate, deployment)
        intent.metadata["execution_provider"] = execution_provider
        if execution_provider == FX5_EXECUTION_PROVIDER:
            reasons = ibkr_acceptance_service.pre_submit_guard(
                candidate_id=candidate.candidate_id,
                oms_intent_id=intent.intent_id,
                account_id=account,
                symbol=candidate.symbol,
                order_type=intent.order_type.value,
                quantity=str(candidate.risk_decision.position_size if candidate.risk_decision else 0),
            )
            if reasons:
                candidate.status = CandidateStatus.FAILED
                candidate.explanation.setdefault("execution_rejection", reasons)
                self.store.upsert_candidate(candidate)
                raise ValueError("|".join(reasons))
        market = MarketReference(price=Decimal(str(candidate.candidate_entry)), provider="forex-strategy", quality_score=Decimal(str(candidate.data_quality)), is_simulated=True, conversion_rate=Decimal("1"))
        result = self.trading.submit_intent(intent, market)
        order = result.get("order")
        risk = result.get("risk_evaluation")
        candidate.oms_intent_id = intent.intent_id
        if risk and risk.blocking_reasons:
            candidate.status = CandidateStatus.RISK_REJECTED
        elif order:
            candidate.status = CandidateStatus.APPROVED
            candidate.oms_order_id = order.order_id
            if execution_provider == FX5_EXECUTION_PROVIDER:
                broker_record = ibkr_acceptance_service.record_order(
                    candidate_id=candidate.candidate_id,
                    oms_intent_id=intent.intent_id,
                    account_id=account,
                    symbol=candidate.symbol,
                    side=candidate.direction.value,
                    quantity=str(candidate.risk_decision.position_size if candidate.risk_decision else 0),
                    broker_status="ACKNOWLEDGED",
                    internal_status="SUBMITTED",
                )
                candidate.broker_order_id = broker_record.broker_order_id or broker_record.client_order_id
        self.store.upsert_candidate(candidate)
        return candidate

    def simulate_fill(self, candidate_id: str) -> TradeCandidate:
        candidate = self.store.get_candidate(candidate_id)
        if not candidate.oms_order_id:
            raise ValueError("NO_OMS_ORDER")
        result = self.trading.simulate_order(candidate.oms_order_id, MarketReference(price=Decimal(str(candidate.candidate_entry)), provider="forex-strategy", quality_score=Decimal(str(candidate.data_quality)), is_simulated=True))
        fill = result.get("fill")
        if fill:
            candidate.status = CandidateStatus.FILLED
            candidate.fill_ids.append(fill.fill_id)
            qty = candidate.risk_decision.position_size if candidate.risk_decision else 0
            trade = ActiveTrade(
                trade_id=_id("fxtrade", [candidate.candidate_id, fill.fill_id]),
                candidate_id=candidate.candidate_id,
                strategy_id=candidate.strategy_id,
                symbol=candidate.symbol,
                direction=candidate.direction,
                quantity=qty,
                entry_price=candidate.candidate_entry,
                current_price=candidate.candidate_entry,
                stop_price=candidate.stop_price,
                target_price=candidate.target_prices[0],
                unrealized_pnl=0,
                r_multiple=0,
            )
            self.store.upsert_trade(trade)
        self.store.upsert_candidate(candidate)
        return candidate

    def reject(self, candidate_id: str, reason: str = "user rejected") -> TradeCandidate:
        candidate = self.store.get_candidate(candidate_id)
        candidate.status = CandidateStatus.REJECTED
        candidate.explanation.setdefault("user_decision", reason)
        self.store.upsert_candidate(candidate)
        return candidate

    def cancel(self, candidate_id: str, reason: str = "user cancelled") -> TradeCandidate:
        candidate = self.store.get_candidate(candidate_id)
        candidate.status = CandidateStatus.CANCELLED
        candidate.explanation.setdefault("user_decision", reason)
        self.store.upsert_candidate(candidate)
        return candidate

    def list_candidates(self) -> list[TradeCandidate]:
        return self.store.candidates()

    def list_trades(self) -> list[ActiveTrade]:
        return self.store.trades()

    def get_candidate(self, candidate_id: str) -> TradeCandidate:
        return self.store.get_candidate(candidate_id)

    def instrument_status(self) -> dict[str, str]:
        return {
            "EURUSD": "PAPER_ELIGIBLE",
            "GBPUSD": "PAPER_ELIGIBLE",
            "USDJPY": "PAPER_ELIGIBLE",
            "USDCHF": "ANALYSIS_ONLY",
            "USDCAD": "ANALYSIS_ONLY",
            "AUDUSD": "ANALYSIS_ONLY",
            "NZDUSD": "ANALYSIS_ONLY",
            "XAUUSD": "CONTRACT_UNAVAILABLE",
        }

    def _active_by_idempotency(self, key: str) -> TradeCandidate | None:
        active = {CandidateStatus.CREATED, CandidateStatus.AWAITING_CONFIRMATION, CandidateStatus.APPROVED, CandidateStatus.SUBMITTED, CandidateStatus.PARTIALLY_FILLED}
        for candidate in self.store.candidates():
            if candidate.idempotency_key == key and candidate.status in active:
                return candidate
        return None

    def _direction(self, context: dict[str, Any]) -> Direction:
        bias = str(context.get("framework_bias") or context.get("bias") or FrameworkBias.BULLISH.value)
        if "BEARISH" in bias:
            return Direction.SELL
        if "BULLISH" in bias:
            return Direction.BUY
        return Direction.NEUTRAL

    def _position_size(self, candidate: TradeCandidate, account_equity: float) -> float:
        risk_budget = account_equity * float(SAFE_DEFAULTS["risk_per_trade_percent"]) / 100
        stop_distance = abs(candidate.candidate_entry - candidate.stop_price)
        if stop_distance <= 0:
            return 0
        return round(min(risk_budget / stop_distance, 50000), 0)

    def _usd_exposure_after(self, candidate: TradeCandidate, trades: list[ActiveTrade]) -> float:
        exposure = 0.0
        for trade in trades:
            exposure += self._usd_exposure_for(trade.symbol, trade.direction, trade.quantity, trade.current_price)
        qty = candidate.risk_decision.position_size if candidate.risk_decision else 10000
        return exposure + self._usd_exposure_for(candidate.symbol, candidate.direction, qty, candidate.candidate_entry)

    def _usd_exposure_for(self, symbol: str, direction: Direction, quantity: float, price: float) -> float:
        if symbol.endswith("USD"):
            return quantity * price * (1 if direction == Direction.BUY else -1)
        if symbol.startswith("USD"):
            return quantity * (-1 if direction == Direction.BUY else 1)
        return 0

    def _ensure_account(self) -> str:
        accounts = self.trading.list_accounts()
        if accounts:
            return accounts[0].account_id
        return self.trading.create_account("FX-4 Paper Account", Decimal("100000"), "USD").account_id

    def _ensure_deployment(self, candidate: TradeCandidate, account_id: str) -> StrategyDeployment:
        for deployment in self.trading.list_deployments():
            if deployment.account_id == account_id and deployment.strategy_id == candidate.strategy_id and deployment.status == DeploymentStatus.ENABLED:
                return deployment
        instrument = FOREX_INSTRUMENTS[candidate.symbol]
        deployment = self.trading.create_deployment(
            account_id=account_id,
            candidate_id=candidate.candidate_id,
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            instrument=self._instrument_ref(candidate.symbol),
            selected_parameters={"source": "forex_strategy_candidate"},
        )
        deployment.risk_policy = RiskPolicy()
        deployment.risk_policy.position["allow_short_selling"] = True
        deployment.risk_policy.position["maximum_risk_per_trade_percent"] = Decimal(str(SAFE_DEFAULTS["maximum_risk_per_trade_percent"]))
        deployment.risk_policy.instrument["maximum_notional"] = Decimal("500000")
        deployment.risk_policy.account["maximum_pending_orders"] = 10
        audit = approve_deployment(deployment, approver="fx4_manual_policy", notes="manual confirmation paper deployment", strategy_hash=candidate.strategy_id, candidate_hash=candidate.content_hash)
        self.trading.store.upsert_deployment(deployment)
        self.trading.store.append_audit(audit)
        _ = instrument
        return deployment

    def _instrument_ref(self, symbol: str) -> InstrumentReference:
        instrument = FOREX_INSTRUMENTS[symbol]
        return InstrumentReference(
            instrument_id=f"FX:{symbol}",
            symbol=symbol,
            asset_class="FOREX" if symbol != "XAUUSD" else "COMMODITY_PROXY",
            quote_currency=instrument.quote_currency,
            minimum_tick=Decimal(str(instrument.minimum_price_increment)),
            lot_size=Decimal("1"),
            contract_multiplier=Decimal("1"),
            shortable=True,
            market_open=True,
            metadata={"broker_contract_status": "BROKER_CONTRACT_UNAVAILABLE" if symbol == "XAUUSD" else "PAPER_FX_FIXTURE"},
        )

    def _intent(self, candidate: TradeCandidate, deployment: StrategyDeployment) -> OrderIntent:
        side = OrderSide.BUY if candidate.direction == Direction.BUY else OrderSide.SHORT
        return OrderIntent(
            account_id=deployment.account_id,
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            deployment_id=deployment.deployment_id,
            proposal_id=candidate.candidate_id,
            instrument=self._instrument_ref(candidate.symbol),
            side=side,
            order_type=PaperOrderType.MARKET,
            requested_quantity=Decimal(str(candidate.risk_decision.position_size if candidate.risk_decision else 0)),
            sizing_intent={"type": "fixed_units"},
            reference_price=Decimal(str(candidate.candidate_entry)),
            invalidation_price=Decimal(str(candidate.stop_price)),
            target_price=Decimal(str(candidate.target_prices[0])),
            idempotency_key=candidate.idempotency_key,
            metadata={"forex_candidate_id": candidate.candidate_id, "execution_mode": ExecutionMode.MANUAL_CONFIRMATION.value},
        )

    def _explanation(self, strategy_id: str, context: dict[str, Any], direction: Direction, entry: float, stop: float, targets: list[float], eligibility: list[StrategyEligibility]) -> dict[str, Any]:
        return {
            "why_triggered": ["completed candle", "eligible strategy selected", "framework agreement above deterministic threshold"],
            "symbol_supported": context.get("symbol") in strategy_registry.require(strategy_id).supported_symbols,
            "regime_suitable": context.get("regime", "trend") in strategy_registry.require(strategy_id).supported_regimes,
            "entry_logic": f"{direction.value} at {entry}",
            "stop_logic": f"stop at {stop}",
            "target_logic": f"primary target at {targets[0]}",
            "risk_reward": abs(targets[0] - entry) / abs(entry - stop) if entry != stop else 0,
            "supporting_frameworks": context.get("frameworks", []),
            "conflicting_frameworks": context.get("conflicting_frameworks", []),
            "missing_evidence": [reason for row in eligibility if not row.eligible for reason in row.reasons],
            "expiration_conditions": ["two hours", "candidate expiry", "stale data", "spread exceeds threshold"],
        }

    def _rejected_candidate(self, context: dict[str, Any], eligibility: list[StrategyEligibility]) -> TradeCandidate:
        symbol = str(context.get("symbol", "EURUSD")).upper()
        timeframe = str(context.get("timeframe", "1h"))
        now = utcnow()
        payload = {"symbol": symbol, "timeframe": timeframe, "reasons": [r.model_dump() for r in eligibility]}
        price = float(context.get("price", 1.0))
        return TradeCandidate(
            candidate_id=_id("fxcand", payload),
            idempotency_key=_hash(payload),
            strategy_id="none",
            strategy_version="0",
            symbol=symbol,
            timeframe=timeframe,
            direction=Direction.NEUTRAL,
            signal_timestamp=now,
            candle_timestamp=context.get("candle_timestamp") or now,
            candidate_entry=price,
            entry_zone={"low": price, "high": price},
            stop_price=price,
            target_prices=[],
            risk_reward=0,
            confidence=0,
            framework_agreement=float(context.get("framework_agreement", 0)),
            regime=str(context.get("regime", "unknown")),
            session=str(context.get("session", "unknown")),
            spread=float(context.get("spread", 0)),
            data_quality=float(context.get("data_quality", 0)),
            invalidation="no eligible strategy",
            expiration=now,
            status=CandidateStatus.REJECTED,
            content_hash=_hash(payload),
            explanation={"missing_evidence": [reason for row in eligibility for reason in row.reasons]},
            eligibility=eligibility,
        )


forex_signal_service = ForexSignalService()
