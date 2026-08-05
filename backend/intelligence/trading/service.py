from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.brokers import broker_registry
from backend.brokers.ibkr.configuration import ibkr_config
from backend.forex_intelligence.service import ForexIntelligenceService
from backend.forex_intelligence.instruments import get_forex_instrument, normalize_forex_symbol
from backend.intelligence.providers.openai_client import OpenAITradeDecisionClient
from backend.intelligence.trading.consensus import build_weighted_consensus
from backend.intelligence.trading.config import AITradingConfig, ai_trading_config
from backend.intelligence.trading.candles import TIMEFRAME_SECONDS, canonicalize_candles, resample_candles
from backend.intelligence.trading.diagnostics import audit_candles
from backend.intelligence.trading.market_context import build_market_context
from backend.intelligence.trading.memory import list_trade_memory, list_trade_reviews, performance_metrics, persist_trade_memory, persist_trade_review
from backend.intelligence.trading.models import ShadowAnalysisResult, TradeDecision
from backend.intelligence.trading.persistence import get_decision, list_decisions, save_decision, utcnow
from backend.intelligence.trading.strategies import evaluate_strategies
from backend.intelligence.trading.usage import estimate_openai_cost_usd, usage_ledger
from backend.services.forex_service import service as forex_service


class AITradingService:
    def __init__(self, provider: OpenAITradeDecisionClient | None = None, config: AITradingConfig | None = None) -> None:
        self.config = config or ai_trading_config()
        self.provider = provider or OpenAITradeDecisionClient(self.config)
        self.last_successful_request: datetime | None = None
        self.last_provider_error: str | None = None
        self.place_order_call_count = 0

    async def status(self) -> dict[str, Any]:
        return {
            "provider_configured": self.config.provider == "openai",
            "model_configured": bool(self.config.model),
            "model": self.config.model,
            "api_key_configured": self.config.api_key_configured,
            "ai_mode": self.config.trading_mode,
            "analysis_enabled": self.config.analysis_enabled,
            "order_submission_enabled": self.config.order_submission_enabled,
            "broker_mode": ibkr_config.mode,
            "live_trading_enabled": bool(getattr(ibkr_config, "allow_live", False)),
            "last_successful_request": self.last_successful_request.isoformat() if self.last_successful_request else None,
            "last_provider_error": self.last_provider_error,
        }

    async def analyze(self, symbol: str, timeframe: str = "15m") -> ShadowAnalysisResult:
        self._assert_analysis_safety()
        normalized = normalize_forex_symbol(symbol)
        if normalized not in self.config.symbol_allowlist:
            result = self._skipped(normalized, timeframe, "REJECTED_INVALID", ["SYMBOL_NOT_ALLOWLISTED"])
            self._persist_result(result, context={}, data_source="none")
            return result
        context, precheck_status, reasons = await self._build_context(normalized, timeframe)
        if precheck_status != "READY":
            status = "SKIPPED_STALE_DATA" if "STALE_DATA" in reasons else "SKIPPED_NO_DATA"
            result = self._skipped(normalized, timeframe, status, reasons)
            self._persist_result(result, context=context, data_source=str(context.get("data_source") or "unknown"))
            return result
        context_hash = _hash(context)
        consensus = context.get("strategy_consensus")
        eligibility = context.get("consensus_eligibility")
        legacy_weak_consensus = False
        if consensus and eligibility is None:
            agreement_score = float((consensus or {}).get("agreement_score") or 0)
            legacy_weak_consensus = agreement_score < self.config.consensus_threshold or consensus.get("recommended_direction") == "NO_TRADE"
        if consensus and ((eligibility is not None and not bool(eligibility.get("eligible", False))) or legacy_weak_consensus):
            reasons = list((eligibility or {}).get("reasons") or ["CONSENSUS_BELOW_THRESHOLD"])
            result = self._skipped(normalized, timeframe, "SKIPPED_WEAK_CONSENSUS", reasons)
            result.context_hash = context_hash
            self._persist_result(result, context=context, data_source=str(context.get("data_source") or "unknown"))
            return result
        can_request, limit_reasons = usage_ledger.can_request(self.config, context_hash)
        if not can_request:
            result = self._skipped(normalized, timeframe, "SKIPPED_PROVIDER_LIMIT", limit_reasons)
            result.context_hash = context_hash
            self._persist_result(result, context=context, data_source=str(context.get("data_source") or "unknown"))
            return result
        decision, telemetry, raw_text = await self.provider.generate_trade_decision(context)
        telemetry.estimated_cost_usd = estimate_openai_cost_usd(telemetry.model, telemetry.input_tokens, telemetry.output_tokens)
        usage_ledger.record_request(
            context_hash=context_hash,
            input_tokens=telemetry.input_tokens,
            output_tokens=telemetry.output_tokens,
            estimated_cost_usd=telemetry.estimated_cost_usd,
        )
        telemetry.hourly_request_count = usage_ledger.requests_this_hour()
        telemetry.daily_request_count = usage_ledger.requests_today()
        if telemetry.error_category:
            self.last_provider_error = telemetry.error_category
        if decision is None:
            result = self._skipped(normalized, timeframe, "PROVIDER_ERROR", [telemetry.error_category or "PROVIDER_ERROR"])
            result.provider = telemetry
            self._persist_result(result, context=context, data_source=str(context.get("data_source") or "unknown"))
            return result
        status, risk_status, rejection_reasons = self._post_validate(decision, context)
        shadow_id = None
        shadow_created = False
        if status == "ACCEPTED_SHADOW":
            shadow_id = _id("shadowtrade", [decision.symbol, decision.timeframe, decision.decision_timestamp.isoformat(), _hash(context)])
            shadow_created = True
        result = ShadowAnalysisResult(
            decision_id=_id("aidecision", [decision.symbol, decision.timeframe, decision.decision_timestamp.isoformat(), _hash(context)]),
            shadow_trade_id=shadow_id,
            symbol=decision.symbol,
            timeframe=decision.timeframe,
            status=status,
            validation_status="VALID" if not rejection_reasons else "REJECTED",
            risk_status=risk_status,
            rejection_reasons=rejection_reasons,
            decision=decision,
            provider=telemetry,
            shadow_trade_created=shadow_created,
            context_hash=context_hash,
        )
        self._persist_result(result, context=context, data_source=str(context.get("data_source") or "unknown"), raw_text=raw_text)
        if result.status == "ACCEPTED_SHADOW":
            self._persist_trade_memory(result, context)
        self.last_successful_request = utcnow()
        return result

    async def _build_context(self, symbol: str, timeframe: str) -> tuple[dict[str, Any], str, list[str]]:
        if timeframe not in {"5m", "15m"}:
            return {"symbol": symbol, "timeframe": timeframe}, "BLOCKED", ["UNSUPPORTED_TIMEFRAME"]
        try:
            instrument = get_forex_instrument(symbol)
            chart = await forex_service.get_pair_chart(symbol, interval=timeframe, range_str="10d")
        except Exception as exc:
            return {"symbol": symbol, "timeframe": timeframe, "error": exc.__class__.__name__}, "BLOCKED", ["NO_DATA"]
        source = str(chart.get("source_symbol") or "forex_service")
        canonical, canonical_meta = canonicalize_candles(list(chart.get("candles") or []), symbol=symbol, timeframe=timeframe, source=source)
        candles = [candle.as_chart_row() for candle in canonical][-max(self.config.max_candles, 220) :]
        interval_seconds = TIMEFRAME_SECONDS[timeframe]
        data_quality = audit_candles(candles, interval_seconds=interval_seconds, source=source)
        reasons = self._validate_candles(candles)
        if data_quality["status"] == "INVALID":
            reasons = list(set(reasons + list(data_quality.get("reasons") or [])))
        if reasons:
            return {"symbol": symbol, "timeframe": timeframe, "data_source": chart.get("source_symbol") or "unknown", "data_quality": data_quality}, "BLOCKED", reasons
        rows = [{"timestamp": datetime.fromtimestamp(row["t"], tz=timezone.utc).isoformat(), "open": row["o"], "high": row["h"], "low": row["l"], "close": row["c"], "volume": row.get("v", 0)} for row in candles]
        snapshot = ForexIntelligenceService().analyze_rows(rows, symbol=symbol, timeframe=timeframe, source_provider=source, provider_symbol_value=str(chart.get("source_symbol") or ""))
        multi_timeframe_context = await self._multi_timeframe_context(symbol, instrument.typical_spread_pips, canonical_15m=canonical if timeframe == "15m" else None, source=source)
        higher_trend = str(((multi_timeframe_context.get("1h") or multi_timeframe_context.get("4h") or {}).get("market_context") or {}).get("trend", {}).get("higher_timeframe_trend") or "unknown")
        market_context = build_market_context(candles, symbol=symbol, timeframe=timeframe, spread=instrument.typical_spread_pips, higher_timeframe_trend=higher_trend)
        strategy_outputs = evaluate_strategies(market_context)
        consensus_weights = self._adaptive_consensus_weights()
        consensus = build_weighted_consensus(strategy_outputs, consensus_weights)
        profile = self.config.threshold_profile()
        conflict_classification = self._classify_conflicts(consensus.model_dump(), strategy_outputs, multi_timeframe_context)
        consensus_eligibility = self._consensus_eligibility(consensus.model_dump(), multi_timeframe_context, profile.model_dump(), conflict_classification)
        account = await _broker_account_context()
        latest = candles[-1]
        data_ts = datetime.fromtimestamp(int(latest["t"]), tz=timezone.utc)
        indicators = snapshot.indicators.model_dump(mode="json")
        latest_candle = {"t": latest["t"], "o": latest["o"], "h": latest["h"], "l": latest["l"], "c": latest["c"], "v": latest.get("v", 0)}
        returns = _returns_summary(candles)
        strategy_signal = {
            "strategy": "institutional_consensus",
            "signal_direction": consensus.recommended_direction,
            "signal_strength": consensus.agreement_score,
            "entry_conditions_met": consensus_eligibility["eligible"],
            "exit_conditions": snapshot.trade_explanation.get("risk_notes", [])[:3],
            "invalidation_level": None,
            "required_timeframe": timeframe,
            "eligibility_reasons": consensus_eligibility["reasons"],
        }
        return (
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "latest_price": float(chart.get("current_rate") or latest["c"]),
                "bid": None,
                "ask": None,
                "spread_pips": instrument.typical_spread_pips,
                "latest_completed_candle": latest_candle,
                "recent_returns": returns,
                "recent_candles": candles[-20:],
                "data_source": str(chart.get("source_symbol") or "forex_service"),
                "data_timestamp": data_ts.isoformat(),
                "market_session": snapshot.current_feature.session,
                "market_open": True,
                "market_regime": snapshot.regime,
                "market_context": market_context.model_dump(),
                "multi_timeframe_context": multi_timeframe_context,
                "strategy_outputs": [row.model_dump() for row in strategy_outputs],
                "strategy_consensus": consensus.model_dump(),
                "adaptive_strategy_weights": consensus_weights,
                "threshold_profile": profile.model_dump(),
                "active_threshold_profile": profile.name,
                "production_thresholds": self.config.production_profile.model_dump(),
                "validation_thresholds": self.config.validation_profile.model_dump(),
                "conflict_classification": conflict_classification,
                "consensus_eligibility": consensus_eligibility,
                "data_quality": {
                    timeframe: data_quality,
                    "multi_timeframe": {tf: row.get("data_quality") for tf, row in multi_timeframe_context.items()},
                    "canonical": canonical_meta,
                    "status": "VALID"
                    if data_quality["status"] == "VALID" and all((row.get("data_quality") or {}).get("status") in {None, "VALID"} for tf, row in multi_timeframe_context.items() if tf != "5m")
                    else "INVALID",
                },
                "risk_summary": {
                    "min_confidence": self.config.min_confidence,
                    "min_risk_reward": self.config.min_risk_reward,
                    "max_risk_percent": self.config.max_risk_percent,
                    "max_trade_loss_usd": self.config.max_trade_loss_usd,
                    "open_position_count": len(account.get("positions") or []),
                    "open_order_count": len(account.get("open_orders") or []),
                },
                "open_position": (account.get("positions") or [None])[0],
                "atr": indicators.get("atr"),
                "volatility": indicators.get("volatility_state"),
                "ema_fast": indicators.get("ema_fast"),
                "ema_slow": indicators.get("ema_slow"),
                "rsi": indicators.get("rsi"),
                "stochastic_rsi": indicators.get("stochastic_rsi"),
                "mfi": None,
                "support_resistance": _support_resistance(candles),
                "recent_swings": _recent_swings(candles),
                "liquidity_or_imbalance": {
                    "liquidity_sweeps": snapshot.current_feature.liquidity_sweeps,
                    "active_fvgs": snapshot.current_feature.active_fvgs,
                    "active_order_blocks": snapshot.current_feature.active_order_blocks,
                },
                "enabled_strategy_signals": [strategy_signal],
                "account": account,
                "daily_usage": usage_ledger.status(self.config),
                "risk_limits": {
                    "min_confidence": self.config.min_confidence,
                    "min_risk_reward": self.config.min_risk_reward,
                    "max_risk_percent": self.config.max_risk_percent,
                },
                "prompt_version": self.config.prompt_version,
                "context_version": self.config.context_version,
                "schema_version": self.config.schema_version,
            },
            "READY",
            [],
        )

    def _validate_candles(self, candles: list[dict[str, Any]]) -> list[str]:
        if len(candles) < 30:
            return ["NO_DATA"]
        previous = 0
        for row in candles:
            ts = int(row.get("t") or 0)
            prices = [float(row.get(key) or 0) for key in ("o", "h", "l", "c")]
            if ts <= previous:
                return ["CANDLES_NOT_ORDERED"]
            if min(prices) <= 0 or prices[1] < max(prices[0], prices[2], prices[3]) or prices[2] > min(prices[0], prices[1], prices[3]):
                return ["IMPOSSIBLE_CANDLE"]
            previous = ts
        latest_ts = datetime.fromtimestamp(int(candles[-1]["t"]), tz=timezone.utc)
        if utcnow() - latest_ts > timedelta(seconds=self.config.max_staleness_seconds):
            return ["STALE_DATA"]
        return []

    def _consensus_eligibility(
        self,
        consensus: dict[str, Any],
        multi_timeframe_context: dict[str, Any],
        profile: dict[str, Any],
        conflict_classification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        recommended = str(consensus.get("recommended_direction") or "NO_TRADE").upper()
        agreement = float(consensus.get("agreement_score") or 0)
        eligible_count = int(consensus.get("eligible_strategy_count") or 0)
        directional_score = max(float(consensus.get("bull_score") or 0), float(consensus.get("bear_score") or 0))
        conflicts = list(consensus.get("conflicting_strategies") or [])
        required_timeframes = ("4h", "1h", "15m")
        missing_timeframes = [tf for tf in required_timeframes if (multi_timeframe_context.get(tf) or {}).get("status") != "READY"]
        if recommended not in {"LONG", "SHORT"}:
            reasons.append("NO_CONSENSUS_DIRECTION")
        if agreement < float(profile["consensus_threshold"]):
            reasons.append("CONSENSUS_BELOW_THRESHOLD")
        if eligible_count < int(profile["min_eligible_strategies"]):
            reasons.append("INSUFFICIENT_ELIGIBLE_STRATEGIES")
        if directional_score < float(profile["min_directional_score"]):
            reasons.append("DIRECTIONAL_SCORE_TOO_LOW")
        if len(conflicts) > int(profile["max_conflicting_strategies"]):
            reasons.append("CONFLICTING_STRATEGIES_PRESENT")
        if conflict_classification and not conflict_classification.get("tolerated", False) and conflicts:
            reasons.extend([reason for reason in conflict_classification.get("reasons", []) if reason not in reasons])
        if missing_timeframes:
            reasons.append("MULTI_TIMEFRAME_CONTEXT_INCOMPLETE")
        return {
            "eligible": not reasons,
            "reasons": reasons,
            "profile": profile.get("name"),
            "thresholds": profile,
            "observed": {
                "recommended_direction": recommended,
                "agreement_score": agreement,
                "eligible_strategy_count": eligible_count,
                "directional_score": directional_score,
                "conflicting_strategy_count": len(conflicts),
                "missing_timeframes": missing_timeframes,
            },
        }

    def _adaptive_consensus_weights(self) -> dict[str, float]:
        try:
            from backend.research.performance_intelligence import strategy_performance_service

            return strategy_performance_service.adaptive_weights()
        except Exception:
            return {}

    def _classify_conflicts(self, consensus: dict[str, Any], strategy_outputs: list[Any], multi_timeframe_context: dict[str, Any]) -> dict[str, Any]:
        recommended = str(consensus.get("recommended_direction") or "NO_TRADE").upper()
        conflicts = list(consensus.get("conflicting_strategies") or [])
        output_by_name = {row.strategy: row for row in strategy_outputs}
        recommended_confidence = max((row.confidence for row in strategy_outputs if row.decision == recommended and row.valid), default=0.0)
        reasons: list[str] = []
        classifications: list[dict[str, Any]] = []
        hard_conflict_names = {"Market Structure", "Breakout", "Support Resistance Break"}
        for name in conflicts:
            row = output_by_name.get(name)
            confidence = float(getattr(row, "confidence", 0.0) or 0.0)
            hard = name in hard_conflict_names
            if hard:
                reasons.append("STRUCTURAL_OR_BREAKOUT_CONFLICT")
            if confidence >= recommended_confidence:
                reasons.append("CONFLICT_CONFIDENCE_NOT_LOWER")
            classifications.append(
                {
                    "strategy": name,
                    "decision": getattr(row, "decision", None),
                    "confidence": confidence,
                    "lower_confidence_than_recommendation": confidence < recommended_confidence,
                    "structural_or_breakout_conflict": hard,
                }
            )
        missing_timeframes = [tf for tf in ("4h", "1h", "15m") if (multi_timeframe_context.get(tf) or {}).get("status") != "READY"]
        if missing_timeframes:
            reasons.append("MULTI_TIMEFRAME_CONTEXT_INCOMPLETE")
        return {
            "conflicts": classifications,
            "recommended_direction": recommended,
            "recommended_confidence": recommended_confidence,
            "tolerated": not reasons and len(conflicts) <= self.config.threshold_profile().max_conflicting_strategies,
            "reasons": sorted(set(reasons)),
        }

    async def _multi_timeframe_context(self, symbol: str, spread: float | None, *, canonical_15m: list[Any] | None = None, source: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not canonical_15m:
            for tf in ("4h", "1h", "15m", "5m"):
                try:
                    chart = await forex_service.get_pair_chart(symbol, interval=tf, range_str="10d" if tf in {"4h", "1h"} else "5d")
                    candles = list(chart.get("candles") or [])[-max(self.config.max_candles, 60) :]
                    interval_seconds = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[tf]
                    quality = audit_candles(candles, interval_seconds=interval_seconds, source=str(chart.get("source_symbol") or "forex_service"))
                    if len(candles) < 30:
                        out[tf] = {"status": "OPTIONAL_INPUT_UNAVAILABLE" if tf == "5m" else "NO_DATA", "data_quality": quality}
                        continue
                    ctx = build_market_context(candles, symbol=symbol, timeframe=tf, spread=spread)
                    out[tf] = {"status": "READY", "market_context": ctx.model_dump(), "candle_count": len(candles), "data_quality": quality}
                except Exception as exc:
                    out[tf] = {"status": "OPTIONAL_INPUT_UNAVAILABLE" if tf == "5m" else "ERROR", "error": exc.__class__.__name__}
            return out
        if canonical_15m:
            rows_15m = [candle.as_chart_row() for candle in canonical_15m][-max(self.config.max_candles, 220) :]
            quality_15m = audit_candles(rows_15m, interval_seconds=900, source=source or "forex_service")
            if len(rows_15m) >= 30:
                ctx = build_market_context(rows_15m, symbol=symbol, timeframe="15m", spread=spread)
                out["15m"] = {"status": "READY", "market_context": ctx.model_dump(), "candle_count": len(rows_15m), "data_quality": quality_15m, "source_provenance": {"timeframe": "15m", "source": source, "method": "direct"}}
            else:
                out["15m"] = {"status": "NO_DATA", "data_quality": quality_15m}
            for tf, interval_seconds in (("1h", 3600), ("4h", 14400)):
                resampled, meta = resample_candles(canonical_15m, target_timeframe=tf)
                rows = [candle.as_chart_row() for candle in resampled][-max(self.config.max_candles, 220) :]
                quality = audit_candles(rows, interval_seconds=interval_seconds, source=source or "forex_service")
                if len(rows) < 30:
                    out[tf] = {"status": "NO_DATA", "data_quality": quality, "resampling": meta}
                    continue
                ctx = build_market_context(rows, symbol=symbol, timeframe=tf, spread=spread)
                out[tf] = {"status": "READY", "market_context": ctx.model_dump(), "candle_count": len(rows), "data_quality": quality, "resampling": meta, "source_provenance": {"timeframe": tf, "source": source, "method": meta["method"]}}
        for tf in ("5m",):
            try:
                chart = await forex_service.get_pair_chart(symbol, interval=tf, range_str="10d" if tf in {"4h", "1h"} else "5d")
                candles = list(chart.get("candles") or [])[-max(self.config.max_candles, 60) :]
                interval_seconds = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[tf]
                quality = audit_candles(candles, interval_seconds=interval_seconds, source=str(chart.get("source_symbol") or "forex_service"))
                if len(candles) < 30:
                    out[tf] = {"status": "OPTIONAL_INPUT_UNAVAILABLE", "data_quality": quality}
                    continue
                ctx = build_market_context(candles, symbol=symbol, timeframe=tf, spread=spread)
                out[tf] = {"status": "READY", "market_context": ctx.model_dump(), "candle_count": len(candles), "data_quality": quality}
            except Exception as exc:
                out[tf] = {"status": "OPTIONAL_INPUT_UNAVAILABLE", "error": exc.__class__.__name__}
        return out

    def _post_validate(self, decision: TradeDecision, context: dict[str, Any]) -> tuple[str, str, list[str]]:
        reasons: list[str] = []
        if decision.symbol != context["symbol"]:
            reasons.append("SYMBOL_MISMATCH")
        if decision.timeframe != context["timeframe"]:
            reasons.append("TIMEFRAME_MISMATCH")
        if decision.data_timestamp.isoformat() != str(context["data_timestamp"]):
            reasons.append("DATA_TIMESTAMP_CHANGED")
        if decision.symbol not in self.config.symbol_allowlist:
            reasons.append("SYMBOL_NOT_ALLOWLISTED")
        if decision.confidence < self.config.min_confidence and decision.decision not in {"FLAT", "NO_TRADE"}:
            reasons.append("CONFIDENCE_TOO_LOW")
        if decision.risk_reward_ratio < self.config.min_risk_reward and decision.decision not in {"FLAT", "NO_TRADE"}:
            reasons.append("RISK_REWARD_TOO_LOW")
        if decision.risk_percent > self.config.max_risk_percent:
            reasons.append("RISK_PERCENT_TOO_HIGH")
        if decision.decision in {"FLAT", "NO_TRADE"}:
            return "NO_TRADE", "NO_TRADE", reasons
        if reasons:
            return "REJECTED_INVALID", "REJECTED_INVALID", reasons
        return "ACCEPTED_SHADOW", "ACCEPTED_SHADOW", []

    def _persist_result(self, result: ShadowAnalysisResult, *, context: dict[str, Any], data_source: str, raw_text: str | None = None) -> None:
        decision = result.decision
        telemetry = result.provider
        context_hash = result.context_hash or _hash(context)
        save_decision(
            {
                "id": result.decision_id,
                "shadow_trade_id": result.shadow_trade_id,
                "provider": telemetry.provider if telemetry else self.config.provider,
                "model": telemetry.model if telemetry else self.config.model,
                "prompt_version": self.config.prompt_version,
                "context_version": self.config.context_version,
                "schema_version": self.config.schema_version,
                "symbol": result.symbol,
                "timeframe": result.timeframe,
                "data_source": data_source,
                "data_timestamp": decision.data_timestamp if decision else None,
                "decision_timestamp": decision.decision_timestamp if decision else utcnow(),
                "context_hash": context_hash,
                "decision": decision.decision if decision else result.status,
                "confidence": decision.confidence if decision else None,
                "strategy": _db_text(decision.strategy, 80) if decision else None,
                "market_regime": _db_text(decision.market_regime, 80) if decision else None,
                "proposed_entry": decision.proposed_entry if decision else None,
                "stop_loss": decision.stop_loss if decision else None,
                "take_profit": decision.take_profit if decision else None,
                "risk_reward_ratio": decision.risk_reward_ratio if decision else None,
                "requested_risk_percent": decision.risk_percent if decision else None,
                "risk_approved_position_size": 1000.0 if result.status == "ACCEPTED_SHADOW" else None,
                "maximum_theoretical_loss": _max_loss(decision) if decision and result.status == "ACCEPTED_SHADOW" else None,
                "validation_status": result.validation_status,
                "risk_status": result.risk_status,
                "rejection_reasons": result.rejection_reasons,
                "reasoning_summary": decision.reasoning_summary if decision else None,
                "provider_request_id": telemetry.request_id if telemetry else None,
                "provider_latency_ms": telemetry.latency_ms if telemetry else None,
                "input_tokens": telemetry.input_tokens if telemetry else 0,
                "output_tokens": telemetry.output_tokens if telemetry else 0,
                "total_tokens": telemetry.total_tokens if telemetry else 0,
                "estimated_cost_usd": telemetry.estimated_cost_usd if telemetry else 0,
                "hourly_request_count": telemetry.hourly_request_count if telemetry else usage_ledger.requests_this_hour(),
                "daily_request_count": telemetry.daily_request_count if telemetry else usage_ledger.requests_today(),
                "shadow_status": "OPEN_SHADOW" if result.status == "ACCEPTED_SHADOW" else result.status,
                "theoretical_entry_timestamp": utcnow() if result.status == "ACCEPTED_SHADOW" else None,
                "theoretical_exit_timestamp": None,
                "theoretical_exit_reason": None,
                "theoretical_pnl": None,
                "mfe": None,
                "mae": None,
                "raw_decision": {
                    "provider_decision": json.loads(raw_text) if raw_text else (decision.model_dump(mode="json") if decision else {}),
                    "market_context": context.get("market_context"),
                    "multi_timeframe_context": context.get("multi_timeframe_context"),
                    "strategy_outputs": context.get("strategy_outputs"),
                    "strategy_consensus": context.get("strategy_consensus"),
                    "threshold_profile": context.get("threshold_profile"),
                    "active_threshold_profile": context.get("active_threshold_profile"),
                    "production_thresholds": context.get("production_thresholds"),
                    "validation_thresholds": context.get("validation_thresholds"),
                    "conflict_classification": context.get("conflict_classification"),
                    "risk_summary": context.get("risk_summary"),
                },
                "updated_at": utcnow(),
            }
        )

    def _persist_trade_memory(self, result: ShadowAnalysisResult, context: dict[str, Any]) -> None:
        decision = result.decision
        if decision is None:
            return
        consensus = context.get("strategy_consensus") or {}
        market = (context.get("market_context") or {}).get("market") or {}
        record = persist_trade_memory(
            {
                "id": result.shadow_trade_id or result.decision_id,
                "symbol": result.symbol,
                "timeframe": result.timeframe,
                "market_context": context.get("market_context"),
                "strategy_outputs": context.get("strategy_outputs"),
                "strategy_consensus": consensus,
                "threshold_profile": context.get("threshold_profile"),
                "used_validation_thresholds": context.get("active_threshold_profile") == "PAPER_ACCEPTANCE_VALIDATION",
                "production_thresholds": context.get("production_thresholds"),
                "validation_thresholds": context.get("validation_thresholds"),
                "conflict_classification": context.get("conflict_classification"),
                "would_pass_production_thresholds": _would_pass_production(context),
                "ai_reasoning": decision.reasoning_summary,
                "confidence": decision.confidence,
                "entry": decision.proposed_entry,
                "exit": None,
                "risk": abs(decision.proposed_entry - decision.stop_loss) if decision.proposed_entry and decision.stop_loss else None,
                "reward": abs(decision.take_profit - decision.proposed_entry) if decision.proposed_entry and decision.take_profit else None,
                "pnl": None,
                "duration": None,
                "screenshots": [],
                "broker_ids": [],
                "strategy": decision.strategy or consensus.get("recommended_direction"),
                "session": market.get("session"),
                "weekday": decision.decision_timestamp.strftime("%A"),
                "volatility": ((context.get("market_context") or {}).get("volatility") or {}).get("atr_percentile"),
                "market_regime": decision.market_regime,
                "entry_timestamp": decision.decision_timestamp.isoformat(),
                "exit_timestamp": None,
            }
        )
        if record.get("pnl") is not None:
            persist_trade_review(record)

    def _skipped(self, symbol: str, timeframe: str, status: str, reasons: list[str]) -> ShadowAnalysisResult:
        decision_id = _id("aidecision", [symbol, timeframe, status, utcnow().isoformat()])
        return ShadowAnalysisResult(decision_id=decision_id, symbol=symbol, timeframe=timeframe, status=status, validation_status=status, risk_status=status, rejection_reasons=reasons, context_hash=None)

    def _assert_shadow_safety(self) -> None:
        if self.config.trading_mode != "SHADOW":
            raise RuntimeError("AI_TRADING_MODE must be SHADOW")
        if self.config.order_submission_enabled:
            raise RuntimeError("AI_ORDER_SUBMISSION_ENABLED must remain 0")
        if bool(getattr(ibkr_config, "allow_live", False)):
            raise RuntimeError("live trading is disabled for AI trading")

    def _assert_analysis_safety(self) -> None:
        if self.config.trading_mode not in {"SHADOW", "AUTO_PAPER"}:
            raise RuntimeError("AI_TRADING_MODE must be SHADOW or AUTO_PAPER")
        if bool(getattr(ibkr_config, "allow_live", False)) or bool(getattr(ibkr_config, "live_trading_enabled", False)):
            raise RuntimeError("live trading is disabled for AI trading")

    def context_hash(self, context: dict[str, Any]) -> str:
        return _hash(context)

    def decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        return [_row(row) for row in list_decisions(limit)]

    def decision(self, decision_id: str) -> dict[str, Any] | None:
        row = get_decision(decision_id)
        return _row(row) if row else None

    def performance(self) -> dict[str, Any]:
        rows = self.decisions(500)
        shadow = [row for row in rows if row["shadow_status"] in {"OPEN_SHADOW", "CLOSED_SHADOW"}]
        return {
            "shadow_trade_count": len(shadow),
            "closed_shadow_trade_count": len([row for row in shadow if row.get("theoretical_exit_timestamp")]),
            "theoretical_pnl": sum(float(row.get("theoretical_pnl") or 0) for row in shadow),
            "mfe": None,
            "mae": None,
            "institutional": performance_metrics(),
        }

    def institutional_status(self) -> dict[str, Any]:
        memory = list_trade_memory(25)
        return {"trade_memory": memory, "recent_reviews": list_trade_reviews(25), "performance": performance_metrics(memory)}


async def _broker_account_context() -> dict[str, Any]:
    try:
        adapter = broker_registry.get("ibkr")
        accounts = await adapter.accounts()
        account_id = accounts[0].account_id if accounts else ""
        snapshot = await adapter.account_snapshot(account_id) if account_id else None
        positions = await adapter.positions(account_id) if account_id else []
        orders = await adapter.open_orders(account_id) if account_id else []
        return {
            "equity": str(snapshot.net_liquidation) if snapshot else None,
            "available_funds": str(snapshot.available_funds) if snapshot else None,
            "positions": [row.model_dump(mode="json", exclude={"account_id"}) for row in positions],
            "open_orders": [row.model_dump(mode="json", exclude={"account_id"}) for row in orders],
            "daily_realized_pnl": str(snapshot.realized_pnl) if snapshot else None,
            "daily_unrealized_pnl": str(snapshot.unrealized_pnl) if snapshot else None,
        }
    except Exception:
        return {"equity": None, "available_funds": None, "positions": [], "open_orders": [], "daily_realized_pnl": None, "daily_unrealized_pnl": None}


def _support_resistance(candles: list[dict[str, Any]]) -> dict[str, float]:
    window = candles[-40:]
    return {"support": min(float(row["l"]) for row in window), "resistance": max(float(row["h"]) for row in window)}


def _recent_swings(candles: list[dict[str, Any]]) -> dict[str, list[float]]:
    window = candles[-30:]
    return {"highs": sorted({float(row["h"]) for row in window}, reverse=True)[:5], "lows": sorted({float(row["l"]) for row in window})[:5]}


def _returns_summary(candles: list[dict[str, Any]]) -> dict[str, float]:
    closes = [float(row["c"]) for row in candles if float(row.get("c") or 0) > 0]
    if len(closes) < 2:
        return {"last_1": 0.0, "last_4": 0.0, "last_16": 0.0, "range_20": 0.0}

    def ret(periods: int) -> float:
        if len(closes) <= periods:
            return 0.0
        base = closes[-1 - periods]
        return ((closes[-1] - base) / base) * 10000 if base else 0.0

    window = closes[-20:]
    return {
        "last_1_bps": ret(1),
        "last_4_bps": ret(4),
        "last_16_bps": ret(16),
        "range_20_bps": ((max(window) - min(window)) / window[-1]) * 10000 if window and window[-1] else 0.0,
    }


def _max_loss(decision: TradeDecision | None) -> float | None:
    if not decision or decision.proposed_entry is None or decision.stop_loss is None:
        return None
    return abs(decision.proposed_entry - decision.stop_loss) * 1000.0


def _db_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit]


def _would_pass_production(context: dict[str, Any]) -> bool:
    consensus = context.get("strategy_consensus") or {}
    production = context.get("production_thresholds") or {}
    conflicts = list(consensus.get("conflicting_strategies") or [])
    directional_score = max(float(consensus.get("bull_score") or 0), float(consensus.get("bear_score") or 0))
    return (
        str(consensus.get("recommended_direction") or "").upper() in {"LONG", "SHORT"}
        and float(consensus.get("agreement_score") or 0) >= float(production.get("consensus_threshold") or 0)
        and int(consensus.get("eligible_strategy_count") or 0) >= int(production.get("min_eligible_strategies") or 0)
        and directional_score >= float(production.get("min_directional_score") or 0)
        and len(conflicts) <= int(production.get("max_conflicting_strategies") or 0)
    )


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _id(prefix: str, parts: list[Any]) -> str:
    return f"{prefix}_{_hash(parts)[:16]}"


def _row(row: Any) -> dict[str, Any]:
    payload = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    payload["decision_id"] = payload.get("id")
    payload["data_provider"] = payload.get("data_source")
    return payload


ai_trading_service = AITradingService()
