from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5Symbol
from backend.brokers.mt5.orm import MT5RiskMetadataMismatchORM
from backend.shared.db import SessionLocal

# The single authoritative definition of "what does 1.0 lot of this MT5 symbol lose if price
# moves from entry to stop" in this codebase. Exists because a real incident (XAUUSD ticket
# 57873187767) proved that trusting ONE broker-reported field in isolation is unsafe: this
# account's trade_tick_value for XAUUSD (0.1) implied $10/point/lot, while the broker's own
# order_calc_profit (ground truth) and trade_contract_size both agreed on $100/point/lot -- a
# silent 10x under-estimate that oversized a position 10x against its risk budget. No module in
# this codebase should compute MT5 monetary risk any other way; see PART 3 of the hardening task
# for the full list of call sites migrated to this module.
WARNING_MISMATCH = "SYMBOL_RISK_METADATA_MISMATCH"
CRITICAL_MISMATCH = "SYMBOL_RISK_METADATA_CRITICAL_MISMATCH"
NO_VALID_ESTIMATE = "NO_VALID_RISK_ESTIMATE"

# Preference order used only to break ties when two methods land on the exact same (maximum)
# value -- selection itself is "most conservative available", never a blind hierarchy pick. See
# module docstring: hierarchy is about what we'd LIKE to trust most, not what we DO trust most.
METHOD_ORDER: tuple[str, ...] = ("order_calc_profit", "contract_size", "tick_value")


@dataclass(frozen=True)
class MethodEstimate:
    method: str
    loss_per_lot: Decimal | None
    available: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "loss_per_lot": str(self.loss_per_lot) if self.loss_per_lot is not None else None,
            "available": self.available,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CanonicalRiskResult:
    selected_loss_per_lot: Decimal | None
    selected_method: str | None
    estimates: dict[str, MethodEstimate] = field(default_factory=dict)
    max_disagreement_pct: float | None = None
    warning_codes: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_loss": str(self.selected_loss_per_lot) if self.selected_loss_per_lot is not None else None,
            "selected_method": self.selected_method,
            "estimates": {name: est.to_dict() for name, est in self.estimates.items()},
            "max_disagreement_pct": self.max_disagreement_pct,
            "warning_codes": list(self.warning_codes),
            "blocked": self.blocked,
            "block_reason": self.block_reason,
        }


async def calculate_canonical_loss_per_lot(
    *,
    direction: str,
    entry: Decimal,
    stop: Decimal,
    symbol_info: MT5Symbol,
    mt5_client: Any | None = None,
    warning_pct: float = 10.0,
    critical_pct: float = 100.0,
) -> CanonicalRiskResult:
    """Computes projected monetary loss for 1.0 lot from `entry` to `stop`, using every method
    that has the data to run, and always SELECTS THE LARGEST (most conservative) valid estimate
    -- never the smallest, never a single trusted field in isolation. `direction` ("LONG"/
    "SHORT") only matters for the order_calc_profit call (it needs the correct broker side to
    return a same-signed loss); the other two methods are direction-agnostic (they work on
    |entry - stop|).

    Methods:
      1. order_calc_profit -- broker-native, ground truth when available. Requires `mt5_client`
         (an already-`ensure_ready()`'d MT5 client/module) with `order_calc_profit` and the
         ORDER_TYPE_BUY/ORDER_TYPE_SELL constants. Skipped (not an error) when mt5_client is None
         -- callers that only have symbol_info (no live client) still get a usable result from
         the other two methods.
      2. contract_size -- stop_distance * symbol_info.trade_contract_size. For a
         profit-currency == account-currency symbol (true for USD-denominated majors/XAUUSD on a
         USD account) this is a direct, broker-metadata-grounded calculation independent of
         trade_tick_value.
      3. tick_value -- (stop_distance / tick_size) * tick_value, the traditional MT5 formula.
         For a correctly configured symbol this agrees with method 2 exactly (tick_value ==
         contract_size * tick_size by construction); it silently DISAGREED for this account's
         XAUUSD, which is exactly the scenario `max_disagreement_pct`/warning_codes exist to
         catch even when a caller doesn't have a live client for method 1.

    Never raises: an unavailable/erroring method just gets `available=False` in its estimate and
    is excluded from selection. If EVERY method is unavailable, returns
    `selected_loss_per_lot=None` and `warning_codes=["NO_VALID_RISK_ESTIMATE"]` (blocked=True) --
    callers must treat that as "cannot safely size this trade", not size to zero risk.
    """
    stop_distance = abs(Decimal(str(entry)) - Decimal(str(stop)))
    estimates: dict[str, MethodEstimate] = {
        "order_calc_profit": await _order_calc_profit_estimate(direction=direction, entry=entry, stop=stop, symbol_name=getattr(symbol_info, "symbol", "") or "", mt5_client=mt5_client),
        "contract_size": _contract_size_estimate(stop_distance=stop_distance, symbol_info=symbol_info),
        "tick_value": _tick_value_estimate(stop_distance=stop_distance, symbol_info=symbol_info),
    }
    return _select_conservative(estimates, warning_pct=warning_pct, critical_pct=critical_pct)


def calculate_conservative_loss_per_lot_sync(
    *,
    entry: Decimal,
    stop: Decimal,
    symbol_info: MT5Symbol,
    warning_pct: float = 10.0,
    critical_pct: float = 100.0,
) -> CanonicalRiskResult:
    """Sync-callable subset of the canonical calculator: contract_size and tick_value methods
    only (no broker-native order_calc_profit, which requires an event loop). For contexts that
    cannot await -- e.g. AdaptiveManagementService._sync_position_state's legacy-position
    reconstruction path (PART 8), which runs inside a synchronous ORM-sync method. Still selects
    the most conservative (largest) of the two available methods and still flags disagreement;
    only ever OMITS the order_calc_profit corroboration, never substitutes a worse selection
    rule. Callers that can await should prefer calculate_canonical_loss_per_lot."""
    stop_distance = abs(Decimal(str(entry)) - Decimal(str(stop)))
    estimates: dict[str, MethodEstimate] = {
        "contract_size": _contract_size_estimate(stop_distance=stop_distance, symbol_info=symbol_info),
        "tick_value": _tick_value_estimate(stop_distance=stop_distance, symbol_info=symbol_info),
    }
    return _select_conservative(estimates, warning_pct=warning_pct, critical_pct=critical_pct)


def _select_conservative(estimates: dict[str, MethodEstimate], *, warning_pct: float, critical_pct: float) -> CanonicalRiskResult:
    valid = [(name, est.loss_per_lot) for name, est in estimates.items() if est.available and est.loss_per_lot is not None and est.loss_per_lot > 0]
    if not valid:
        return CanonicalRiskResult(
            selected_loss_per_lot=None,
            selected_method=None,
            estimates=estimates,
            max_disagreement_pct=None,
            warning_codes=[NO_VALID_ESTIMATE],
            blocked=True,
            block_reason=NO_VALID_ESTIMATE,
        )

    values = [v for _, v in valid]
    min_v, max_v = min(values), max(values)
    max_disagreement_pct = float((max_v - min_v) / min_v * 100) if min_v > 0 else None
    candidates_at_max = [name for name, v in valid if v == max_v]
    selected_method = next((m for m in METHOD_ORDER if m in candidates_at_max), candidates_at_max[0])

    warning_codes: list[str] = []
    blocked = False
    block_reason: str | None = None
    if max_disagreement_pct is not None and max_disagreement_pct >= critical_pct:
        warning_codes = [WARNING_MISMATCH, CRITICAL_MISMATCH]
        blocked = True
        block_reason = CRITICAL_MISMATCH
    elif max_disagreement_pct is not None and max_disagreement_pct >= warning_pct:
        warning_codes = [WARNING_MISMATCH]

    return CanonicalRiskResult(
        selected_loss_per_lot=max_v,
        selected_method=selected_method,
        estimates=estimates,
        max_disagreement_pct=max_disagreement_pct,
        warning_codes=warning_codes,
        blocked=blocked,
        block_reason=block_reason,
    )


async def _order_calc_profit_estimate(*, direction: str, entry: Decimal, stop: Decimal, symbol_name: str, mt5_client: Any | None) -> MethodEstimate:
    if mt5_client is None:
        return MethodEstimate("order_calc_profit", None, False, "no broker client available")
    if not hasattr(mt5_client, "order_calc_profit"):
        return MethodEstimate("order_calc_profit", None, False, "mt5 client has no order_calc_profit")
    order_type = getattr(mt5_client, "ORDER_TYPE_BUY", 0) if direction.upper() == "LONG" else getattr(mt5_client, "ORDER_TYPE_SELL", 1)
    try:
        raw = await asyncio.to_thread(mt5_client.order_calc_profit, order_type, symbol_name, 1.0, float(entry), float(stop))
    except Exception as exc:
        return MethodEstimate("order_calc_profit", None, False, f"order_calc_profit raised {exc.__class__.__name__}")
    if raw is None:
        return MethodEstimate("order_calc_profit", None, False, "order_calc_profit returned None")
    loss = Decimal(str(raw)).copy_abs()
    return MethodEstimate("order_calc_profit", loss, True, "broker-native order_calc_profit(1.0 lot, entry -> stop)")


def _contract_size_estimate(*, stop_distance: Decimal, symbol_info: MT5Symbol) -> MethodEstimate:
    # getattr(..., None), not direct attribute access: symbol_info is sometimes a lightweight
    # duck-typed test double (or a partial payload from a degraded broker response) that only
    # defines the fields IT cares about, not the full MT5Symbol shape -- this must degrade to
    # "unavailable" for THIS method, never raise and take out the whole calculation.
    contract_size = getattr(symbol_info, "trade_contract_size", None)
    if not contract_size or contract_size <= 0:
        return MethodEstimate("contract_size", None, False, "trade_contract_size unavailable")
    loss = (stop_distance * contract_size).copy_abs()
    return MethodEstimate("contract_size", loss, True, f"stop_distance x trade_contract_size({contract_size})")


def _tick_value_estimate(*, stop_distance: Decimal, symbol_info: MT5Symbol) -> MethodEstimate:
    tick_size = getattr(symbol_info, "trade_tick_size", None) or getattr(symbol_info, "point", None)
    tick_value = getattr(symbol_info, "trade_tick_value_loss", None) or getattr(symbol_info, "trade_tick_value", None) or getattr(symbol_info, "trade_tick_value_profit", None)
    if not tick_size or tick_size <= 0 or not tick_value or tick_value <= 0:
        return MethodEstimate("tick_value", None, False, "trade_tick_size/trade_tick_value unavailable")
    loss = ((stop_distance / tick_size) * tick_value).copy_abs()
    return MethodEstimate("tick_value", loss, True, f"(stop_distance/tick_size({tick_size})) x tick_value({tick_value})")


def record_mismatch_if_needed(result: CanonicalRiskResult, *, symbol: str, account_fingerprint: str | None, context: str) -> None:
    """Persists the audit record PART 2 requires whenever a calculation crossed the warning
    threshold -- symbol, selected method, every estimate, disagreement %, account fingerprint,
    timestamp. No-op (and no DB round trip) when there was no disagreement to report.
    `context` identifies the call site (e.g. "new_entry_sizing", "position_risk_snapshot",
    "management_current_risk") so a symbol that only ever mismatches in one code path is
    distinguishable from one that mismatches everywhere."""
    if not result.warning_codes:
        return
    with SessionLocal() as db:
        row = MT5RiskMetadataMismatchORM(
            mismatch_id="MTMM_" + hashlib.sha256(json.dumps({"symbol": symbol, "context": context, "time": datetime.now(timezone.utc).isoformat()}, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:40]
        )
        row.symbol = symbol.upper()
        row.account_fingerprint = account_fingerprint
        row.selected_method = result.selected_method
        row.selected_loss_per_lot = float(result.selected_loss_per_lot) if result.selected_loss_per_lot is not None else None
        row.estimates = {name: est.to_dict() for name, est in result.estimates.items()}
        row.disagreement_pct = result.max_disagreement_pct
        row.critical = CRITICAL_MISMATCH in result.warning_codes
        row.blocked_entry = result.blocked
        row.context = context
        db.merge(row)
        db.commit()
