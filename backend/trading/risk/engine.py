from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from backend.trading.models import (
    DeploymentStatus,
    EmergencyStatus,
    MarketReference,
    OrderIntent,
    OrderSide,
    PaperAccount,
    PaperAccountSnapshot,
    PaperOrder,
    RiskDecision,
    RiskEvaluation,
    RuleEvaluation,
    StrategyDeployment,
)
from backend.trading.risk.sizing import size_order


def _d(value: object, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return default


class RiskEngine:
    def evaluate(
        self,
        *,
        account: PaperAccount,
        deployment: StrategyDeployment,
        intent: OrderIntent,
        market: MarketReference,
        existing_positions: dict[str, Decimal],
        pending_orders: list[PaperOrder],
        emergency_status: EmergencyStatus = EmergencyStatus.CLEAR,
        approval_ttl_seconds: int = 300,
    ) -> RiskEvaluation:
        snapshot = PaperAccountSnapshot(
            account_id=account.account_id,
            cash_balance=account.cash_balance,
            reserved_cash=account.reserved_cash,
            equity=account.equity,
            buying_power=account.buying_power,
            gross_exposure=account.gross_exposure,
            net_exposure=account.net_exposure,
            realized_pnl=account.realized_pnl,
            unrealized_pnl=account.unrealized_pnl,
            fees=account.fees,
        )
        rules: list[RuleEvaluation] = []
        blocks: list[str] = []
        warnings: list[str] = []
        resize: list[str] = []

        def rule(rule_id: str, ok: bool, message: str, evidence: dict[str, object]) -> None:
            rules.append(RuleEvaluation(rule_id=rule_id, status="PASS" if ok else "FAIL", message=message, evidence=evidence))
            if not ok:
                blocks.append(message)

        rule("emergency_disable", emergency_status == EmergencyStatus.CLEAR, "Emergency disable is enabled", {"status": emergency_status.value})
        rule("account_active", account.status.value == "ACTIVE", "Paper account is not active", {"status": account.status.value})
        rule(
            "deployment_enabled",
            deployment.status == DeploymentStatus.ENABLED and not deployment.stale,
            "Deployment is not approved and enabled or is stale",
            {"status": deployment.status.value, "stale": deployment.stale, "stale_reasons": deployment.stale_reasons},
        )
        rule("instrument_supported", intent.instrument.asset_class in {"EQUITY", "ETF", "FUTURE", "FOREX", "CRYPTO"}, "Unsupported instrument type", {"asset_class": intent.instrument.asset_class})
        rule("market_state", intent.instrument.market_open and not intent.instrument.halted and not intent.instrument.expired, "Instrument is not in a tradable market state", {"market_open": intent.instrument.market_open, "halted": intent.instrument.halted, "expired": intent.instrument.expired})
        if intent.side in {OrderSide.SHORT, OrderSide.SELL}:
            allow_short = bool(deployment.risk_policy.position.get("allow_short_selling", False))
            has_long = existing_positions.get(intent.instrument.instrument_id, Decimal("0")) > 0
            rule("short_selling", allow_short or has_long, "Short selling is not allowed and no long position exists", {"allow_short_selling": allow_short, "current_quantity": str(existing_positions.get(intent.instrument.instrument_id, Decimal("0")))})

        if market.is_stale and not bool(deployment.risk_policy.data.get("allow_stale", False)):
            rule("data_stale", False, "Stale market data is blocked by policy", {"timestamp": market.timestamp.isoformat()})
        else:
            rules.append(RuleEvaluation(rule_id="data_stale", status="PASS", message="Market data staleness accepted", evidence={"is_stale": market.is_stale}))
        if market.is_fallback and not bool(deployment.risk_policy.data.get("allow_fallback", False)):
            rule("data_fallback", False, "Fallback market data is blocked by policy", {"provider": market.provider})
        if market.quality_score < _d(deployment.risk_policy.data.get("minimum_quality_score"), Decimal("0")):
            rule("data_quality", False, "Market data quality score is below policy minimum", {"quality_score": str(market.quality_score)})
        if intent.instrument.quote_currency != account.base_currency and market.conversion_rate <= 0:
            rule("fx_conversion", False, "Missing currency conversion for non-base instrument", {"quote_currency": intent.instrument.quote_currency, "base_currency": account.base_currency})

        sizing = size_order(intent.sizing_intent, intent.requested_quantity, snapshot, intent.instrument, market, intent.invalidation_price)
        for err in sizing.errors:
            blocks.append(err)
            rules.append(RuleEvaluation(rule_id="sizing", status="FAIL", message=err, evidence={"sizing_intent": intent.sizing_intent}))
        warnings.extend(sizing.warnings)

        max_position_notional = _d(deployment.risk_policy.instrument.get("maximum_notional"), Decimal("0"))
        if max_position_notional > 0 and sizing.approved_notional > max_position_notional:
            resized_qty = (max_position_notional / (market.price * market.conversion_rate * intent.instrument.contract_multiplier)).quantize(intent.instrument.lot_size)
            if resized_qty > 0:
                sizing.approved_quantity = min(sizing.approved_quantity, resized_qty)
                sizing.approved_notional = sizing.approved_quantity * market.price * market.conversion_rate * intent.instrument.contract_multiplier
                resize.append("position notional capped by instrument maximum_notional")
                rules.append(RuleEvaluation(rule_id="instrument_max_notional", status="RESIZE", message="Order resized to instrument maximum notional", evidence={"maximum_notional": str(max_position_notional)}))
            else:
                blocks.append("instrument maximum notional produces zero quantity")

        max_gross = account.equity * (_d(deployment.risk_policy.account.get("maximum_gross_exposure_percent"), Decimal("100")) / Decimal("100"))
        rule("account_gross_exposure", account.gross_exposure + sizing.approved_notional <= max_gross, "Maximum gross exposure exceeded", {"current": str(account.gross_exposure), "new": str(sizing.approved_notional), "limit": str(max_gross)})
        rule("pending_orders", len(pending_orders) < int(deployment.risk_policy.account.get("maximum_pending_orders", 20)), "Maximum pending orders exceeded", {"pending_orders": len(pending_orders)})
        if deployment.risk_policy.position.get("require_invalidation_level", True) and intent.invalidation_price is None:
            rule("required_invalidation", False, "Invalidation level is required", {})

        decision = RiskDecision.APPROVED
        if blocks:
            decision = RiskDecision.BLOCKED if any("Emergency" in reason or "not active" in reason for reason in blocks) else RiskDecision.REJECTED
        elif resize:
            decision = RiskDecision.APPROVED_WITH_RESIZE
        return RiskEvaluation(
            account_id=account.account_id,
            deployment_id=deployment.deployment_id,
            proposal_id=intent.proposal_id,
            requested_direction=intent.side.value,
            requested_quantity=sizing.requested_quantity,
            requested_notional=sizing.requested_notional,
            approved_quantity=Decimal("0") if blocks else sizing.approved_quantity,
            approved_notional=Decimal("0") if blocks else sizing.approved_notional,
            decision=decision,
            rules_evaluated=rules,
            warnings=warnings,
            blocking_reasons=blocks,
            resizing_reasons=resize,
            account_snapshot_id=snapshot.snapshot_id,
            market_data_reference=market,
            risk_policy_id=deployment.risk_policy.policy_id,
            expires_at=market.timestamp + timedelta(seconds=approval_ttl_seconds),
        )
