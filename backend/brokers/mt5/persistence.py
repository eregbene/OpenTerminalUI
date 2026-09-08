from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func

from backend.brokers.mt5.models import MT5Candle
from backend.brokers.mt5.orm import (
    MT5AIDecisionORM,
    MT5CanonicalCandleORM,
    MT5OrderRecordORM,
    MT5RetentionPolicyORM,
    MT5SchedulerCandidateORM,
    MT5SchedulerCycleORM,
    MT5TradeMemorySnapshotORM,
    MT5TradeRecordORM,
)
from backend.brokers.mt5.trading_costs import compute_trade_costs
from backend.shared.db import SessionLocal

SENSITIVE_KEY_PARTS = ("password", "api_key", "apikey", "secret", "token", "authorization", "auth")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if any(part in key_str.lower() for part in SENSITIVE_KEY_PARTS):
                clean[key_str] = "***REDACTED***"
            else:
                clean[key_str] = sanitize(item)
        return clean
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def persist_candles(candles: list[MT5Candle], *, broker_symbol: str, canonical_symbol: str | None = None, provider: str = "MT5", dataset_policy: str = "MT5_ONLY") -> int:
    if not candles:
        return 0
    now = utcnow()
    canonical = canonical_symbol or broker_symbol.upper()
    with SessionLocal() as db:
        count = 0
        for candle in candles:
            candle_id = _candle_id(provider, broker_symbol, candle.timeframe, candle.time)
            row = db.get(MT5CanonicalCandleORM, candle_id) or MT5CanonicalCandleORM(candle_id=candle_id)
            row.provider = provider
            row.dataset_policy = dataset_policy
            row.canonical_symbol = canonical
            row.broker_symbol = broker_symbol.upper()
            row.timeframe = candle.timeframe.upper()
            row.timestamp = candle.time
            row.open = float(candle.open)
            row.high = float(candle.high)
            row.low = float(candle.low)
            row.close = float(candle.close)
            row.tick_volume = int(candle.tick_volume)
            row.spread = int(candle.spread)
            row.real_volume = int(candle.real_volume)
            row.quality = _candle_quality(candle, now)
            row.delayed = False
            row.proxy = False
            row.lineage = {
                "provider": provider,
                "dataset_policy": dataset_policy,
                "source": "MT5.copy_rates_from_pos",
                "symbol_mapping": {"canonical": canonical, "broker": broker_symbol.upper()},
                "delayed": False,
                "proxy": False,
                "quality_flags": list(candle.quality_flags or []),
            }
            row.broker_server = candle.server
            row.fetched_at = candle.ingestion_timestamp or now
            row.updated_at = now
            db.merge(row)
            count += 1
        db.commit()
        return count


def persist_cycle_result(result: dict[str, Any]) -> None:
    if not result.get("cycle_id"):
        return
    payload = sanitize(result)
    cycle_id = str(result["cycle_id"])
    account_id = str(result.get("account_id") or _account_id_from_cycle_id(cycle_id))
    winner = result.get("winner") or {}
    trade = result.get("trade") or {}
    decision = result.get("ai_decision") or {}
    with SessionLocal() as db:
        candidate_rows = [_candidate_row(cycle_id, candidate, account_id) for candidate in _result_candidates(result)]
        winner_candidate_id = winner.get("candidate_id")
        if not winner_candidate_id and candidate_rows:
            winner_candidate_id = candidate_rows[0].candidate_id
        if decision and not decision.get("decision_id"):
            decision["decision_id"] = _hash({"cycle_id": cycle_id, "candidate": winner_candidate_id, "decision": decision})[:32]
        cycle = db.get(MT5SchedulerCycleORM, cycle_id) or MT5SchedulerCycleORM(cycle_id=cycle_id)
        cycle.account_id = account_id
        cycle.status = str(result.get("status") or "UNKNOWN")
        cycle.candle_id = cycle_id
        cycle.candle_timestamp = _cycle_ts(cycle_id)
        cycle.provider_policy = "MT5_ONLY"
        cycle.symbols_discovered = int(result.get("symbols_discovered") or 0)
        cycle.eligible_symbols = int(result.get("eligible_symbols") or 0)
        cycle.selected_symbol = winner.get("canonical_pair")
        cycle.selected_candidate_id = winner_candidate_id
        cycle.ai_decision_id = decision.get("decision_id")
        cycle.trade_id = trade.get("trade_id")
        cycle.openai_calls = int(result.get("openai_calls") or 0)
        cycle.order_send_calls = int(result.get("order_send_calls") or 0)
        cycle.result_payload = payload
        cycle.updated_at = utcnow()
        db.merge(cycle)

        for candidate_row in candidate_rows:
            db.merge(candidate_row)

        if decision:
            db.merge(_decision_row(cycle_id, winner, decision, account_id))
        if trade:
            order = _order_row(cycle_id, winner, decision, trade, account_id)
            db.merge(order)
            db.merge(_trade_row(cycle_id, winner, decision, trade, order.order_id, account_id))
        _ensure_default_retention(db)
        db.commit()


def update_trade_reconciliation(reconciliation: dict[str, Any]) -> None:
    positions = reconciliation.get("positions") or []
    orders = reconciliation.get("orders") or []
    status = str(reconciliation.get("status") or "")
    account_id = str(reconciliation.get("account_id") or "demo_10k")
    with SessionLocal() as db:
        for position in positions:
            ticket = str(position.get("ticket") or position.get("identifier") or "")
            if not ticket:
                continue
            row = db.query(MT5TradeRecordORM).filter(MT5TradeRecordORM.account_id == account_id, MT5TradeRecordORM.order_ticket == ticket).first()
            if not row:
                row = db.query(MT5TradeRecordORM).filter(MT5TradeRecordORM.account_id == account_id, MT5TradeRecordORM.raw_payload["submission"]["order_ticket"].as_string() == ticket).first() if db.bind and db.bind.dialect.name == "postgresql" else None
            if not row:
                row = MT5TradeRecordORM(trade_id=_scoped_id(account_id, f"MT5POS_{ticket}"), account_id=account_id, cycle_id="MT5_RECONCILIATION_OBSERVED", symbol=str(position.get("symbol") or "").upper(), broker_symbol=str(position.get("symbol") or "").upper(), direction=_position_direction(position), lot_size=float(position.get("volume") or 0))
                db.add(row)
            row.account_id = account_id
            row.entry = _float(position.get("price_open"))
            row.stop_loss = _float(position.get("sl"))
            row.take_profit = _float(position.get("tp"))
            row.order_ticket = ticket
            row.deal_tickets = [str(position.get("identifier"))] if position.get("identifier") else []
            row.fill_price = _float(position.get("price_open"))
            row.current_pnl = _float(position.get("profit"))
            row.swap = _float(position.get("swap"))
            row.commission = _float(position.get("commission"))
            row.open_timestamp = _parse_dt(position.get("time"))
            row.reconciliation_state = status
            row.account_mode = "DEMO"
            row.raw_payload = sanitize({"observed_position": position})
            row.updated_at = utcnow()
        for order in orders:
            ticket = str(order.get("ticket") or "")
            if not ticket:
                continue
            order_id = _scoped_id(account_id, ticket)
            row = db.get(MT5OrderRecordORM, order_id) or MT5OrderRecordORM(order_id=order_id)
            row.account_id = account_id
            row.trade_id = _scoped_id(account_id, f"MT5POS_{ticket}")
            row.cycle_id = "MT5_RECONCILIATION_OBSERVED"
            row.symbol = str(order.get("symbol") or "").upper()
            row.direction = _order_direction(order)
            row.status = "OPEN"
            row.broker_order_ticket = ticket
            row.requested_volume = _float(order.get("volume_current"))
            row.raw_response = sanitize({"observed_order": order})
            row.updated_at = utcnow()
            db.merge(row)
        db.commit()


def update_trade_history(deals: list[Any], account_id: str = "demo_10k") -> dict[str, Any]:
    """Update persisted MT5 trades from broker history without placing orders. account_id scopes
    both which trade records are eligible to match (a deal from Account A's history must never
    close out Account B's trade record just because tickets/symbols happen to line up) and the
    memory-snapshot refresh below (so one account's win-rate/expectancy memory never blends into
    another's confidence scoring)."""
    normalized_deals = [_history_dict(deal) for deal in deals]
    updated = 0
    reviewed = 0
    with SessionLocal() as db:
        rows = db.query(MT5TradeRecordORM).filter(MT5TradeRecordORM.account_id == account_id).order_by(MT5TradeRecordORM.created_at.desc()).limit(500).all()
        for row in rows:
            matching = _matching_exit_deals(row, normalized_deals)
            if not matching:
                continue
            # Canonical gross/commission/swap/fee/net breakdown (backend/brokers/mt5/
            # trading_costs.py) -- previously this summed only profit+commission+swap (no fee),
            # inconsistent with the 4-term formula used elsewhere in the codebase for the same
            # underlying deals.
            cost_breakdown = compute_trade_costs([{"profit": deal.get("profit"), "commission": deal.get("commission"), "swap": deal.get("swap"), "fee": deal.get("fee"), "volume": deal.get("volume")} for deal in matching])
            realized = cost_breakdown.net_pnl
            close_timestamp = max((_parse_dt(deal.get("time")) for deal in matching if _parse_dt(deal.get("time"))), default=None)
            close_price = _float(matching[-1].get("price"))
            row.realized_pnl = realized
            row.gross_pnl = cost_breakdown.gross_pnl
            row.current_pnl = None
            row.close_timestamp = close_timestamp or row.close_timestamp
            if row.open_timestamp and row.close_timestamp:
                row.duration_seconds = int((_aware_dt(row.close_timestamp) - _aware_dt(row.open_timestamp)).total_seconds())
            row.exit_reason = row.exit_reason or _exit_reason(row, realized, close_price)
            row.commission = cost_breakdown.commission
            row.swap = cost_breakdown.swap
            row.fee = cost_breakdown.other_fees
            row.total_trading_cost = cost_breakdown.total_trading_cost
            row.commission_per_lot_effective = cost_breakdown.commission_per_lot_effective
            row.commission_source = cost_breakdown.commission_source
            existing = row.raw_payload if isinstance(row.raw_payload, dict) else {}
            review = _review_for_trade(row)
            row.raw_payload = sanitize({**existing, "history_deals": matching, "outcome_review": review})
            row.reconciliation_state = "MATCHED_CLOSED"
            row.updated_at = utcnow()
            updated += 1
            reviewed += 1
        if updated:
            db.flush()
            _refresh_memory_snapshots(db, account_id)
        db.commit()
    return {"updated_trades": updated, "reviews_created": reviewed, "deals_seen": len(normalized_deals)}


def query_trade_reviews(limit: int = 100, outcome: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        query = db.query(MT5TradeRecordORM).filter(MT5TradeRecordORM.raw_payload["outcome_review"].isnot(None)) if db.bind and db.bind.dialect.name == "postgresql" else db.query(MT5TradeRecordORM)
        rows = query.order_by(MT5TradeRecordORM.updated_at.desc()).limit(limit * 3).all()
        reviews: list[dict[str, Any]] = []
        for row in rows:
            review = (row.raw_payload or {}).get("outcome_review") if isinstance(row.raw_payload, dict) else None
            if not review:
                review = _review_for_trade(row) if row.realized_pnl is not None else None
            if not review:
                continue
            if outcome and review.get("outcome") != outcome.upper():
                continue
            reviews.append(review)
            if len(reviews) >= limit:
                break
        return sanitize(reviews)


def trade_performance_summary(limit: int = 500) -> dict[str, Any]:
    with SessionLocal() as db:
        rows = db.query(MT5TradeRecordORM).order_by(MT5TradeRecordORM.created_at.desc()).limit(limit).all()
    closed = [row for row in rows if row.realized_pnl is not None]
    open_rows = [row for row in rows if row.realized_pnl is None]
    pnls = [float(row.realized_pnl or 0) for row in closed]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    reviews = [_review_for_trade(row) for row in closed]
    return {
        "trades_recorded": len(rows),
        "closed_trades": len(closed),
        "open_trades": len(open_rows),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "expectancy": sum(pnls) / len(pnls) if pnls else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "total_realized_pnl": sum(pnls),
        "open_floating_pnl": sum(float(row.current_pnl or 0) for row in open_rows),
        "mistake_count": sum(len(row.get("mistakes") or []) for row in reviews),
        "recent_reviews": sanitize(reviews[:25]),
        "memory": query_trade_memory(limit=25),
    }


def refresh_trade_memory() -> dict[str, Any]:
    with SessionLocal() as db:
        snapshots = _refresh_memory_snapshots(db)
        db.commit()
        return {"snapshots": snapshots, "updated_at": utcnow().isoformat()}


def query_trade_memory(limit: int = 100, symbol: str | None = None, recommendation: str | None = None, account_id: str = "demo_10k") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        query = db.query(MT5TradeMemorySnapshotORM).filter(MT5TradeMemorySnapshotORM.account_id == account_id)
        if symbol:
            query = query.filter(MT5TradeMemorySnapshotORM.symbol == symbol.upper())
        if recommendation:
            query = query.filter(MT5TradeMemorySnapshotORM.recommendation == recommendation.upper())
        rows = query.order_by(MT5TradeMemorySnapshotORM.closed_trade_count.desc(), MT5TradeMemorySnapshotORM.updated_at.desc()).limit(limit).all()
        return [_orm_dict(row) for row in rows]


def confidence_memory_for_symbol(symbol: str, account_id: str = "demo_10k", *, strategy_id: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(symbol_memory, strategy_memory) for the deterministic confidence engine
    (backend/brokers/mt5/confidence.py -- symbol_performance/strategy_performance components).

    Was previously backed by MT5TradeMemorySnapshotORM, which nothing has ever written to (that
    write pipeline was never implemented), so both components were always a constant neutral
    score. Delegates instead to mt5_strategies.performance_monitor, the authoritative,
    already-real, position-level, sample-size-gated performance source (see that module's
    docstring) -- per the fix instruction not to build a second competing source of truth.
    strategy_id is optional (a candidate that predates strategy tagging still gets symbol memory).

    2026-08-25 Confidence Architecture & Calibration Audit (Part 4): when strategy_id is known,
    symbol_memory now prefers the strategy+symbol tier (how has THIS strategy done on THIS
    symbol) over the original cross-strategy symbol-only read, falling back to the original
    behavior only when the strategy has no trades/shadow history on this symbol yet -- see
    performance_memory_for_confidence's own docstring for the full hierarchy."""
    from backend.mt5_strategies.performance_monitor import performance_memory_for_confidence

    symbol_memory = performance_memory_for_confidence(symbol=symbol.upper(), strategy_id=strategy_id)
    strategy_memory = performance_memory_for_confidence(strategy_id=strategy_id) if strategy_id else None
    return symbol_memory, strategy_memory


def learning_context_for_candidate(candidate: dict[str, Any], account_id: str = "demo_10k") -> dict[str, Any]:
    symbol = str(candidate.get("canonical_pair") or candidate.get("symbol") or "").upper()
    session = str(candidate.get("session") or _session()).upper()
    regime = str(candidate.get("market_regime") or _regime(candidate.get("direction"))).upper()
    memories = query_trade_memory(limit=50, symbol=symbol, account_id=account_id)
    exact = _first_memory(memories, session=session, regime=regime)
    symbol_scope = _first_memory(memories, scope="SYMBOL")
    global_scope = _first_memory(query_trade_memory(limit=10, account_id=account_id), scope="GLOBAL")
    rows = [row for row in (exact, symbol_scope, global_scope) if row]
    blockers = [row for row in rows if row.get("recommendation") == "AVOID"]
    cautions = [row for row in rows if row.get("recommendation") == "REDUCE_RISK"]
    return sanitize(
        {
            "symbol": symbol,
            "session": session,
            "market_regime": regime,
            "memory_available": bool(rows),
            "exact_match": exact,
            "symbol_memory": symbol_scope,
            "global_memory": global_scope,
            "guidance": _memory_guidance(blockers, cautions, rows),
        }
    )


def history_availability(*, provider: str = "MT5", dataset_policy: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        query = db.query(
            MT5CanonicalCandleORM.canonical_symbol,
            MT5CanonicalCandleORM.broker_symbol,
            MT5CanonicalCandleORM.timeframe,
            func.min(MT5CanonicalCandleORM.timestamp),
            func.max(MT5CanonicalCandleORM.timestamp),
            func.count(MT5CanonicalCandleORM.candle_id),
        ).filter(MT5CanonicalCandleORM.provider == provider)
        query = query.filter(MT5CanonicalCandleORM.timestamp <= utcnow() + timedelta(minutes=5))
        query = query.filter(MT5CanonicalCandleORM.quality.notin_(["INVALID_FUTURE_TIMESTAMP"]))
        if dataset_policy:
            query = query.filter(MT5CanonicalCandleORM.dataset_policy == dataset_policy)
        query = query.group_by(MT5CanonicalCandleORM.canonical_symbol, MT5CanonicalCandleORM.broker_symbol, MT5CanonicalCandleORM.timeframe)
        return [
            {
                "provider": provider,
                "dataset_policy": dataset_policy or "ALL",
                "symbol": symbol,
                "broker_symbol": broker_symbol,
                "timeframe": timeframe,
                "earliest": _iso_utc(earliest),
                "latest": _iso_utc(latest),
                "count": count,
            }
            for symbol, broker_symbol, timeframe, earliest, latest, count in query.all()
        ]


def query_cycles(limit: int = 100, symbol: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        query = db.query(MT5SchedulerCycleORM)
        if symbol:
            query = query.filter(MT5SchedulerCycleORM.selected_symbol == symbol.upper())
        if status:
            query = query.filter(MT5SchedulerCycleORM.status == status.upper())
        rows = query.order_by(MT5SchedulerCycleORM.created_at.desc()).limit(limit).all()
        return [_orm_dict(row) for row in rows]


def query_candidates(limit: int = 100, symbol: str | None = None, rejected: bool | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        query = db.query(MT5SchedulerCandidateORM)
        if symbol:
            query = query.filter(MT5SchedulerCandidateORM.symbol == symbol.upper())
        if rejected is not None:
            query = query.filter(MT5SchedulerCandidateORM.rejected == rejected)
        return [_orm_dict(row) for row in query.order_by(MT5SchedulerCandidateORM.created_at.desc()).limit(limit).all()]


def query_decisions(limit: int = 100, symbol: str | None = None, decision: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        query = db.query(MT5AIDecisionORM)
        if symbol:
            query = query.filter(MT5AIDecisionORM.symbol == symbol.upper())
        if decision:
            query = query.filter(MT5AIDecisionORM.decision == decision.upper())
        return [_orm_dict(row) for row in query.order_by(MT5AIDecisionORM.created_at.desc()).limit(limit).all()]


def query_trades(limit: int = 100, symbol: str | None = None, session: str | None = None, regime: str | None = None, exit_reason: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        query = db.query(MT5TradeRecordORM)
        if symbol:
            query = query.filter(MT5TradeRecordORM.symbol == symbol.upper())
        if session:
            query = query.filter(MT5TradeRecordORM.session == session.upper())
        if regime:
            query = query.filter(MT5TradeRecordORM.market_regime == regime.upper())
        if exit_reason:
            query = query.filter(MT5TradeRecordORM.exit_reason == exit_reason.upper())
        return [_orm_dict(row) for row in query.order_by(MT5TradeRecordORM.created_at.desc()).limit(limit).all()]


def retention_policies() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        _ensure_default_retention(db)
        db.commit()
        return [_orm_dict(row) for row in db.query(MT5RetentionPolicyORM).order_by(MT5RetentionPolicyORM.entity).all()]


def apply_retention() -> dict[str, int]:
    deleted: dict[str, int] = {}
    with SessionLocal() as db:
        _ensure_default_retention(db)
        policies = db.query(MT5RetentionPolicyORM).filter(MT5RetentionPolicyORM.enabled.is_(True)).all()
        for policy in policies:
            cutoff = utcnow() - timedelta(days=policy.retention_days)
            model = {
                "cycles": MT5SchedulerCycleORM,
                "candidates": MT5SchedulerCandidateORM,
                "decisions": MT5AIDecisionORM,
                "orders": MT5OrderRecordORM,
                "trades": MT5TradeRecordORM,
                "memory": MT5TradeMemorySnapshotORM,
                "candles": MT5CanonicalCandleORM,
            }.get(policy.entity)
            if model is None:
                continue
            count = db.query(model).filter(model.created_at < cutoff).delete(synchronize_session=False)
            deleted[policy.entity] = count
        db.commit()
        return deleted


def _result_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = list(result.get("candidates") or [])
    winner = result.get("winner")
    if winner and not _contains_winner_candidate(candidates, winner):
        candidates.insert(0, winner)
    return candidates


def _contains_winner_candidate(candidates: list[dict[str, Any]], winner: dict[str, Any]) -> bool:
    winner_id = winner.get("candidate_id")
    if winner_id:
        return any(row.get("candidate_id") == winner_id for row in candidates)
    winner_symbol = winner.get("broker_symbol") or winner.get("canonical_pair")
    winner_hash = winner.get("context_hash")
    return any((row.get("broker_symbol") or row.get("canonical_pair")) == winner_symbol and row.get("context_hash") == winner_hash for row in candidates)


def _candidate_row(cycle_id: str, candidate: dict[str, Any], account_id: str) -> MT5SchedulerCandidateORM:
    candidate_id = _candidate_id(cycle_id, candidate)
    candidate["candidate_id"] = candidate_id
    context = candidate.get("context") or {}
    row = MT5SchedulerCandidateORM(candidate_id=candidate_id)
    row.cycle_id = cycle_id
    row.account_id = account_id
    row.symbol = str(candidate.get("canonical_pair") or candidate.get("symbol") or "").upper()
    row.broker_symbol = str(candidate.get("broker_symbol") or row.symbol).upper()
    row.asset_class = candidate.get("asset_class")
    row.direction = str(candidate.get("direction") or "NO_TRADE").upper()
    row.ranking_score = float(candidate.get("ranking_score") or 0)
    trade_confidence = candidate.get("trade_confidence") or {}
    row.trade_confidence_score = _float(trade_confidence.get("overall_score"))
    row.rank = candidate.get("rank")
    row.rejection_reasons = list(candidate.get("rejection_reasons") or [])
    row.selected = bool(row.rank == 1 and not row.rejection_reasons)
    row.rejected = bool(row.rejection_reasons or row.direction == "NO_TRADE")
    row.entry = _float(candidate.get("entry") or context.get("entry"))
    row.stop_loss = _float(candidate.get("stop_loss") or context.get("stop_loss"))
    row.take_profit = _float(candidate.get("take_profit") or context.get("take_profit"))
    row.risk_reward = _float(candidate.get("risk_reward") or context.get("risk_reward"))
    row.strategy_outputs = sanitize(candidate.get("strategy_outputs") or context.get("strategy_outputs") or {})
    row.consensus = sanitize(candidate.get("consensus") or {"score": candidate.get("ranking_score"), "direction": candidate.get("direction")})
    row.market_regime = str(candidate.get("market_regime") or context.get("market_regime") or _regime(row.direction)).upper()
    row.session = str(candidate.get("session") or context.get("session") or _session()).upper()
    row.timeframe_context = sanitize(candidate.get("timeframe_context") or context.get("timeframe_context") or {"policy": "MT5_ONLY", "timeframes": ["M5", "M15", "H1", "H4"]})
    row.context_hash = candidate.get("context_hash")
    row.raw_payload = sanitize(candidate)
    row.updated_at = utcnow()
    return row


def _decision_row(cycle_id: str, winner: dict[str, Any], decision: dict[str, Any], account_id: str) -> MT5AIDecisionORM:
    decision_id = decision.get("decision_id") or _hash({"cycle_id": cycle_id, "candidate": winner.get("candidate_id"), "decision": decision})[:32]
    decision["decision_id"] = decision_id
    row = MT5AIDecisionORM(decision_id=decision_id)
    row.cycle_id = cycle_id
    row.account_id = account_id
    row.candidate_id = winner.get("candidate_id")
    row.symbol = winner.get("canonical_pair")
    row.decision = str(decision.get("decision") or "NO_TRADE").upper()
    row.confidence = float(decision.get("confidence") or 0)
    row.model = decision.get("model")
    row.input_tokens = int(decision.get("input_tokens") or 0)
    row.output_tokens = int(decision.get("output_tokens") or 0)
    row.estimated_cost_usd = float(decision.get("estimated_cost") or 0)
    row.raw_response = sanitize({"raw": decision.get("raw")})
    row.raw_payload = sanitize(decision)
    row.updated_at = utcnow()
    return row


def _order_row(cycle_id: str, winner: dict[str, Any], decision: dict[str, Any], trade: dict[str, Any], account_id: str) -> MT5OrderRecordORM:
    submission = trade.get("submission") or {}
    intent = trade.get("intent") or {}
    raw_order_id = str(submission.get("order_ticket") or intent.get("intent_id") or trade.get("trade_id") or _hash(trade)[:32])
    order_id = _scoped_id(account_id, raw_order_id)
    row = MT5OrderRecordORM(order_id=order_id)
    row.account_id = account_id
    row.trade_id = trade.get("trade_id")
    row.cycle_id = cycle_id
    row.candidate_id = winner.get("candidate_id")
    row.decision_id = decision.get("decision_id")
    row.intent_id = intent.get("intent_id")
    row.symbol = intent.get("canonical_pair") or winner.get("canonical_pair")
    row.direction = intent.get("direction") or winner.get("direction")
    row.status = str(submission.get("status") or trade.get("status") or "UNKNOWN")
    row.retcode = _int(submission.get("retcode"))
    row.comment = submission.get("comment")
    row.broker_order_ticket = str(submission.get("order_ticket")) if submission.get("order_ticket") is not None else None
    row.deal_ticket = str(submission.get("deal_ticket")) if submission.get("deal_ticket") is not None else None
    row.requested_volume = _float(submission.get("requested_volume") or intent.get("volume"))
    row.filled_volume = _float(submission.get("filled_volume"))
    row.fill_price = _float(submission.get("fill_price"))
    raw_request = sanitize(submission.get("request") or {})
    if isinstance(raw_request, dict) and trade.get("v3_execution_identity"):
        raw_request = {**raw_request, "bsi_v3": sanitize(trade.get("v3_execution_identity") or {})}
    row.raw_request = raw_request
    row.raw_response = sanitize(submission.get("raw") or submission)
    row.updated_at = utcnow()
    return row


def _trade_row(cycle_id: str, winner: dict[str, Any], decision: dict[str, Any], trade: dict[str, Any], order_id: str, account_id: str) -> MT5TradeRecordORM:
    intent = trade.get("intent") or {}
    risk = trade.get("risk") or {}
    submission = trade.get("submission") or {}
    trade_id = _scoped_id(account_id, str(trade.get("trade_id") or intent.get("intent_id") or order_id))
    trade["trade_id"] = trade_id
    row = MT5TradeRecordORM(trade_id=trade_id)
    row.account_id = account_id
    row.cycle_id = cycle_id
    row.candidate_id = winner.get("candidate_id")
    row.ai_decision_id = decision.get("decision_id")
    row.symbol = str(intent.get("canonical_pair") or winner.get("canonical_pair") or "").upper()
    row.broker_symbol = str(intent.get("broker_symbol") or winner.get("broker_symbol") or row.symbol).upper()
    row.direction = str(intent.get("direction") or winner.get("direction") or "").upper()
    row.lot_size = float(intent.get("volume") or risk.get("volume") or 0)
    row.entry = _float(intent.get("entry_price") or winner.get("entry"))
    row.stop_loss = _float(intent.get("stop_loss") or winner.get("stop_loss"))
    row.take_profit = _float(intent.get("take_profit") or winner.get("take_profit"))
    row.projected_margin = _float(trade.get("projected_margin") or risk.get("projected_margin"))
    row.projected_risk = _float(risk.get("projected_loss_usd") or risk.get("effective_risk_usd"))
    row.risk_reward = _float(risk.get("risk_reward") or winner.get("risk_reward"))
    row.order_ticket = str(submission.get("order_ticket") or order_id) if (submission.get("order_ticket") or order_id) else None
    row.deal_tickets = [str(submission["deal_ticket"])] if submission.get("deal_ticket") else []
    row.fill_price = _float(submission.get("fill_price"))
    row.current_pnl = _float(trade.get("current_pnl"))
    row.realized_pnl = _float(trade.get("realized_pnl"))
    row.commission = _float(trade.get("commission"))
    row.swap = _float(trade.get("swap"))
    row.open_timestamp = _parse_dt(trade.get("open_timestamp")) or (utcnow() if submission.get("status") == "ACCEPTED" else None)
    row.close_timestamp = _parse_dt(trade.get("close_timestamp"))
    row.duration_seconds = _int(trade.get("duration_seconds"))
    row.exit_reason = trade.get("exit_reason")
    row.strategy_outputs = sanitize(winner.get("strategy_outputs") or {})
    row.consensus = sanitize(winner.get("consensus") or {"score": winner.get("ranking_score"), "direction": winner.get("direction")})
    row.market_regime = str(winner.get("market_regime") or _regime(row.direction)).upper()
    row.session = str(winner.get("session") or _session()).upper()
    row.timeframe_context = sanitize(winner.get("timeframe_context") or {"policy": "MT5_ONLY", "timeframes": ["M5", "M15", "H1", "H4"]})
    row.broker_server = (winner.get("context") or {}).get("broker_server")
    row.account_mode = str((winner.get("context") or {}).get("account_mode") or "DEMO")
    row.reconciliation_state = trade.get("reconciliation_state")
    row.raw_payload = sanitize(trade)
    row.updated_at = utcnow()
    return row


def _ensure_default_retention(db: Any) -> None:
    defaults = {
        "cycles": 365,
        "candidates": 365,
        "decisions": 365,
        "orders": 2555,
        "trades": 2555,
        "memory": 2555,
        "candles": 3650,
    }
    for entity, days in defaults.items():
        policy_id = f"mt5_{entity}_default"
        if not db.get(MT5RetentionPolicyORM, policy_id):
            db.add(MT5RetentionPolicyORM(policy_id=policy_id, entity=entity, retention_days=days, notes="Default MT5 autonomous persistence retention."))


def _orm_dict(row: Any) -> dict[str, Any]:
    return {column.name: sanitize(getattr(row, column.name)) for column in row.__table__.columns}


def _history_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        data = item.model_dump(mode="json")
    elif hasattr(item, "_asdict"):
        data = item._asdict()
    else:
        data = dict(item)
    return sanitize(data)


def _aware_dt(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _matching_exit_deals(row: MT5TradeRecordORM, deals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order_ticket = str(row.order_ticket or "")
    known_deals = {str(ticket) for ticket in (row.deal_tickets or [])}
    raw = row.raw_payload if isinstance(row.raw_payload, dict) else {}
    comment = str(((raw.get("submission") or {}).get("request") or {}).get("comment") or "")
    matches: list[dict[str, Any]] = []
    for deal in deals:
        if str(deal.get("symbol") or "").upper() != row.broker_symbol.upper():
            continue
        deal_ticket = str(deal.get("ticket") or "")
        deal_order = str(deal.get("order") or "")
        position_id = str(deal.get("position_id") or deal.get("position") or "")
        deal_comment = str(deal.get("comment") or "")
        linked = bool(
            (deal_ticket and deal_ticket in known_deals)
            or (order_ticket and deal_order == order_ticket)
            or (order_ticket and position_id == order_ticket)
            or (known_deals and position_id in known_deals)
            or (comment and comment in deal_comment)
        )
        if not linked:
            continue
        entry_type = str(deal.get("entry") if deal.get("entry") is not None else "").upper()
        profit = _float(deal.get("profit")) or 0.0
        if entry_type in {"1", "OUT", "DEAL_ENTRY_OUT", "OUT_BY"} or profit != 0.0:
            matches.append(deal)
    return sorted(matches, key=lambda item: str(item.get("time") or ""))


def _exit_reason(row: MT5TradeRecordORM, realized: float, close_price: float | None) -> str:
    if close_price is not None and row.take_profit is not None and abs(close_price - row.take_profit) <= _price_tolerance(row):
        return "TAKE_PROFIT"
    if close_price is not None and row.stop_loss is not None and abs(close_price - row.stop_loss) <= _price_tolerance(row):
        return "STOP_LOSS"
    if realized > 0:
        return "PROFIT_EXIT"
    if realized < 0:
        return "LOSS_EXIT"
    return "FLAT_EXIT"


def _price_tolerance(row: MT5TradeRecordORM) -> float:
    reference = abs(float(row.entry or row.fill_price or 1.0))
    return max(reference * 0.00005, 0.00001)


def _review_for_trade(row: MT5TradeRecordORM) -> dict[str, Any]:
    pnl = float(row.realized_pnl or 0.0)
    outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT")
    mistakes: list[str] = []
    lessons: list[str] = []
    if row.stop_loss is None or row.take_profit is None:
        mistakes.append("MISSING_PROTECTION_LEVEL")
        lessons.append("Do not treat an unprotected MT5 position as strategy-valid.")
    if row.risk_reward is not None and row.risk_reward < 1.5:
        mistakes.append("LOW_RISK_REWARD")
        lessons.append("Review whether risk/reward was too compressed for the regime.")
    score = _consensus_score(row.consensus)
    if score is not None and score < 75:
        mistakes.append("LOW_CONSENSUS_SCORE")
        lessons.append("Require stronger deterministic consensus before AI review.")
    if outcome == "LOSS":
        mistakes.append("LOSS_RECORDED")
        if row.exit_reason == "STOP_LOSS":
            mistakes.append("STOP_LOSS_HIT")
        lessons.append("Compare entry candle context, session, spread, and higher-timeframe alignment before repeating this setup.")
    elif outcome == "WIN":
        lessons.append("Preserve this setup for performance grouping by symbol/session/regime.")
    else:
        lessons.append("Flat result; keep for expectancy and execution-quality review.")
    return {
        "id": f"mt5_review_{row.trade_id}",
        "trade_id": row.trade_id,
        "cycle_id": row.cycle_id,
        "symbol": row.symbol,
        "direction": row.direction,
        "outcome": outcome,
        "realized_pnl": pnl,
        "exit_reason": row.exit_reason,
        "mistakes": sorted(set(mistakes)),
        "lessons": lessons,
        "entry": row.entry,
        "stop_loss": row.stop_loss,
        "take_profit": row.take_profit,
        "risk_reward": row.risk_reward,
        "consensus_score": score,
        "market_regime": row.market_regime,
        "session": row.session,
        "open_timestamp": _iso_utc(row.open_timestamp),
        "close_timestamp": _iso_utc(row.close_timestamp),
        "updated_at": utcnow().isoformat(),
    }


def _refresh_memory_snapshots(db: Any, account_id: str = "demo_10k") -> int:
    rows = db.query(MT5TradeRecordORM).filter(MT5TradeRecordORM.realized_pnl.isnot(None), MT5TradeRecordORM.account_id == account_id).order_by(MT5TradeRecordORM.close_timestamp.desc()).limit(2000).all()
    groups: dict[tuple[str, str | None, str | None, str | None], list[MT5TradeRecordORM]] = {("GLOBAL", None, None, None): rows}
    for row in rows:
        groups.setdefault(("SYMBOL", row.symbol, None, None), []).append(row)
        groups.setdefault(("SYMBOL_SESSION", row.symbol, row.session, None), []).append(row)
        groups.setdefault(("SYMBOL_REGIME", row.symbol, None, row.market_regime), []).append(row)
        groups.setdefault(("SYMBOL_SESSION_REGIME", row.symbol, row.session, row.market_regime), []).append(row)
    now = utcnow()
    updated = 0
    for (scope, symbol, session, regime), trades in groups.items():
        memory = _memory_payload(scope, symbol, session, regime, trades)
        memory_id = _memory_id(scope, symbol, session, regime, account_id)
        row = db.get(MT5TradeMemorySnapshotORM, memory_id) or MT5TradeMemorySnapshotORM(memory_id=memory_id)
        row.account_id = account_id
        row.scope = scope
        row.symbol = symbol
        row.session = session
        row.market_regime = regime
        row.trade_count = memory["trade_count"]
        row.closed_trade_count = memory["closed_trade_count"]
        row.win_rate = memory["win_rate"]
        row.expectancy = memory["expectancy"]
        row.profit_factor = memory["profit_factor"]
        row.total_realized_pnl = memory["total_realized_pnl"]
        row.average_win = memory["average_win"]
        row.average_loss = memory["average_loss"]
        row.max_loss = memory["max_loss"]
        row.mistake_counts = memory["mistake_counts"]
        row.lessons = memory["lessons"]
        row.recommendation = memory["recommendation"]
        row.confidence = memory["confidence"]
        row.raw_payload = sanitize(memory)
        row.updated_at = now
        db.merge(row)
        updated += 1
    _ensure_default_retention(db)
    return updated


def _memory_payload(scope: str, symbol: str | None, session: str | None, regime: str | None, rows: list[MT5TradeRecordORM]) -> dict[str, Any]:
    pnls = [float(row.realized_pnl or 0.0) for row in rows]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    reviews = [_review_for_trade(row) for row in rows]
    mistake_counts: dict[str, int] = {}
    lesson_counts: dict[str, int] = {}
    for review in reviews:
        for mistake in review.get("mistakes") or []:
            mistake_counts[mistake] = mistake_counts.get(mistake, 0) + 1
        for lesson in review.get("lessons") or []:
            lesson_counts[lesson] = lesson_counts.get(lesson, 0) + 1
    recommendation = _memory_recommendation(len(rows), pnls, mistake_counts)
    confidence = min(1.0, len(rows) / 20.0)
    return {
        "scope": scope,
        "symbol": symbol,
        "session": session,
        "market_regime": regime,
        "trade_count": len(rows),
        "closed_trade_count": len(rows),
        "win_rate": len(wins) / len(rows) if rows else 0.0,
        "expectancy": sum(pnls) / len(rows) if rows else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "total_realized_pnl": sum(pnls),
        "average_win": sum(wins) / len(wins) if wins else 0.0,
        "average_loss": sum(losses) / len(losses) if losses else 0.0,
        "max_loss": min(losses) if losses else 0.0,
        "mistake_counts": dict(sorted(mistake_counts.items(), key=lambda item: (-item[1], item[0]))),
        "lessons": [item[0] for item in sorted(lesson_counts.items(), key=lambda item: (-item[1], item[0]))[:8]],
        "recommendation": recommendation,
        "confidence": confidence,
        "sample_trade_ids": [row.trade_id for row in rows[:25]],
    }


def _memory_recommendation(count: int, pnls: list[float], mistake_counts: dict[str, int]) -> str:
    if count < 3:
        return "INSUFFICIENT_DATA"
    expectancy = sum(pnls) / count if count else 0.0
    loss_rate = len([pnl for pnl in pnls if pnl < 0]) / count
    repeated_mistakes = any(total >= 2 for total in mistake_counts.values())
    if expectancy < 0 and loss_rate >= 0.65:
        return "AVOID"
    if expectancy < 0 or repeated_mistakes:
        return "REDUCE_RISK"
    return "PREFER"


def _memory_id(scope: str, symbol: str | None, session: str | None, regime: str | None, account_id: str = "demo_10k") -> str:
    # account_id folded into the hash (not just appended) for every account except demo_10k, so
    # existing demo_10k memory_ids are byte-identical to before this column existed -- without
    # this, two accounts would compute the SAME memory_id for the same scope/symbol/session/
    # regime combination and silently overwrite each other's win-rate/expectancy snapshot.
    key = {"scope": scope, "symbol": symbol, "session": session, "regime": regime}
    if account_id != "demo_10k":
        key["account_id"] = account_id
    return "MT5MEM_" + _hash(key)[:32]


def _first_memory(rows: list[dict[str, Any]], *, scope: str | None = None, session: str | None = None, regime: str | None = None) -> dict[str, Any] | None:
    for row in rows:
        if scope and row.get("scope") != scope:
            continue
        if session and row.get("session") != session:
            continue
        if regime and row.get("market_regime") != regime:
            continue
        return row
    return None


def _memory_guidance(blockers: list[dict[str, Any]], cautions: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    if blockers:
        return {"action": "AVOID_OR_REQUIRE_STRONG_OVERRIDE", "reason": "historical_memory_negative", "matched_scopes": [row.get("scope") for row in blockers]}
    if cautions:
        return {"action": "REDUCE_RISK_OR_REQUIRE_HIGHER_CONFIDENCE", "reason": "repeated_mistakes_or_negative_expectancy", "matched_scopes": [row.get("scope") for row in cautions]}
    if any(row.get("recommendation") == "PREFER" for row in rows):
        return {"action": "PREFER_WITH_NORMAL_RISK", "reason": "historical_memory_positive"}
    return {"action": "NO_MEMORY_EDGE", "reason": "insufficient_closed_trade_history"}


def _consensus_score(consensus: Any) -> float | None:
    if not isinstance(consensus, dict):
        return None
    for key in ("score", "agreement_score", "confidence"):
        value = _float(consensus.get(key))
        if value is not None:
            return value
    return None


def _candle_id(provider: str, symbol: str, timeframe: str, ts: datetime) -> str:
    return f"{provider.upper()}:{symbol.upper()}:{timeframe.upper()}:{ts.isoformat()}"


def _candidate_id(cycle_id: str, candidate: dict[str, Any]) -> str:
    return candidate.get("candidate_id") or f"{cycle_id}:{candidate.get('broker_symbol') or candidate.get('canonical_pair')}:{_hash(candidate)[:16]}"


def _account_id_from_cycle_id(cycle_id: str) -> str:
    return cycle_id.split(":", 1)[0] if ":" in cycle_id else "demo_10k"


def _scoped_id(account_id: str, value: str) -> str:
    if account_id == "demo_10k" or value.startswith(f"{account_id}:"):
        return value
    return f"{account_id}:{value}"


def _candle_quality(candle: MT5Candle, now: datetime) -> str:
    if candle.time > now + timedelta(minutes=5):
        return "INVALID_FUTURE_TIMESTAMP"
    return "VALID" if candle.complete else "PARTIAL"


def _cycle_ts(cycle_id: str) -> datetime | None:
    try:
        stamp = cycle_id.rsplit("_", 1)[-1]
        return datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(sanitize(payload), sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _position_direction(position: dict[str, Any]) -> str:
    return "LONG" if int(position.get("type") or 0) == 0 else "SHORT"


def _order_direction(order: dict[str, Any]) -> str:
    return "LONG" if int(order.get("type") or 0) in {0, 2, 4, 6} else "SHORT"


def _iso_utc(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _regime(direction: str | None) -> str:
    if direction in {"LONG", "SHORT"}:
        return "TREND"
    return "NEUTRAL"


def _session() -> str:
    hour = utcnow().hour
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 21:
        return "NEW_YORK"
    return "ASIA"
