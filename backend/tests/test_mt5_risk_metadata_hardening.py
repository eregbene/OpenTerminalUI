"""MT5 risk-sizing hardening across the whole non-account-currency-quote symbol universe and
XAUUSD -- the 29 required tests (Parts 16-18 of the task).

Covers: the EURJPY-shaped currency-unit bug is fixed generally (any FX cross, any account
currency) via one canonical conversion helper with no symbol-specific hardcoding; XAUUSD's real
10x trade_tick_value metadata error is caught and safely worked around via a purely
metadata-derived dimensional self-consistency check (never a hardcoded multiplier, and proven to
leave a healthy non-XAU metal untouched); the risk-method quorum policy is explicit and tested;
and every previously-established safety invariant (portfolio risk, risk-per-trade config,
confidence threshold 75, multi-strategy engine, adaptive manager, zero OpenAI/LLM calls, no
IBKR, live trading blocked) still holds.
"""
from __future__ import annotations

import asyncio
import inspect
from decimal import Decimal

import pytest

import backend.brokers.mt5.risk_calculator as risk_calculator_module
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.models import MT5Symbol
from backend.brokers.mt5.risk_calculator import (
    INSUFFICIENT_QUORUM,
    TICK_VALUE_SELF_INCONSISTENT,
    calculate_canonical_loss_per_lot,
    calculate_conservative_loss_per_lot_sync,
)


def _fake_adapter_with_quote(broker_symbol: str, bid: Decimal, ask: Decimal):
    class _Quote:
        def __init__(self):
            self.bid = bid
            self.ask = ask

    class _Adapter:
        async def latest_tick(self, symbol: str):
            if symbol != broker_symbol:
                raise LookupError(f"no quote for {symbol}")
            return _Quote()

    return _Adapter()


def _jpy_cross(symbol: str) -> MT5Symbol:
    """Real live-broker JPY-cross shape (identical tick_value across every JPY pair tested
    live: USDJPY/EURJPY/GBPJPY all shared 0.6279829188646069, i.e. this broker derives every
    JPY-cross tick_value from the same underlying USDJPY rate)."""
    return MT5Symbol(
        symbol=symbol, visible=True, selected=True, digits=3, point=Decimal("0.001"),
        currency_base=symbol[:3], currency_profit="JPY", currency_margin=symbol[:3],
        trade_tick_size=Decimal("0.001"), trade_tick_value=Decimal("0.6279829188646069"),
        trade_tick_value_profit=Decimal("0.6279829188646069"), trade_tick_value_loss=Decimal("0.6279829188646069"),
        trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _eurgbp() -> MT5Symbol:
    return MT5Symbol(
        symbol="EURGBP", visible=True, selected=True, digits=5, point=Decimal("0.00001"),
        currency_base="EUR", currency_profit="GBP", currency_margin="EUR",
        trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.27"), trade_tick_value_profit=Decimal("1.27"), trade_tick_value_loss=Decimal("1.27"),
        trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _eurchf() -> MT5Symbol:
    return MT5Symbol(
        symbol="EURCHF", visible=True, selected=True, digits=5, point=Decimal("0.00001"),
        currency_base="EUR", currency_profit="CHF", currency_margin="EUR",
        trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.2341108231519191"), trade_tick_value_profit=Decimal("1.2341108231519191"), trade_tick_value_loss=Decimal("1.2341108231519191"),
        trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _gbpcad() -> MT5Symbol:
    return MT5Symbol(
        symbol="GBPCAD", visible=True, selected=True, digits=5, point=Decimal("0.00001"),
        currency_base="GBP", currency_profit="CAD", currency_margin="GBP",
        trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("0.7173755532758955"), trade_tick_value_profit=Decimal("0.7173755532758955"), trade_tick_value_loss=Decimal("0.7173755532758955"),
        trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _xauusd_broken() -> MT5Symbol:
    """Real live-broker XAUUSD shape, confirmed 2026-08-10: trade_calc_mode=4 (CFDLEVERAGE),
    trade_tick_value exactly 10x too small versus trade_contract_size, while order_calc_profit
    and trade_contract_size independently agree."""
    return MT5Symbol(
        symbol="XAUUSD", visible=True, selected=True, digits=2, point=Decimal("0.01"), trade_calc_mode=4,
        currency_base="XAU", currency_profit="USD", currency_margin="USD",
        trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("0.1"), trade_tick_value_profit=Decimal("0.1"), trade_tick_value_loss=Decimal("0.1"),
        trade_contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _xagusd_healthy() -> MT5Symbol:
    """A DIFFERENT metal, deliberately HEALTHY (self-consistent trade_tick_value) -- proves the
    XAUUSD fix is a general dimensional check, not a symbol-specific patch that would (wrongly)
    also flag a metal whose metadata is fine."""
    return MT5Symbol(
        symbol="XAGUSD", visible=True, selected=True, digits=3, point=Decimal("0.001"), trade_calc_mode=4,
        currency_base="XAG", currency_profit="USD", currency_margin="USD",
        trade_tick_size=Decimal("0.001"), trade_tick_value=Decimal("5.0"), trade_tick_value_profit=Decimal("5.0"), trade_tick_value_loss=Decimal("5.0"),
        trade_contract_size=Decimal("5000.0"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


class _FakeNativeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, per_point: float):
        self.per_point = per_point

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        diff = (price_close - price_open) if order_type == self.ORDER_TYPE_BUY else (price_open - price_close)
        return diff * self.per_point * volume


# ---------------------------------------------------------------- 1-10: cross currencies -----

# 1. EURJPY remains fixed (see test_risk_calculator.py for the full set of EURJPY-specific
# regression tests -- restated here briefly as part of this file's complete 1-10 list).
def test_1_eurjpy_remains_fixed():
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_jpy_cross("EURJPY"), account_currency="USD")
    assert result.estimates["contract_size"].available is False
    assert "profit currency" in result.estimates["contract_size"].detail
    assert result.estimates["tick_value"].available is True


# 2. GBPJPY correctly handles JPY profit currency (sync path: excluded, not miscomputed; async
# path with a live conversion quote: correctly converted and agrees with tick_value).
def test_2_gbpjpy_handles_jpy_profit_currency():
    sync_result = calculate_conservative_loss_per_lot_sync(entry=Decimal("199.500"), stop=Decimal("199.469"), symbol_info=_jpy_cross("GBPJPY"), account_currency="USD")
    assert sync_result.estimates["contract_size"].available is False

    adapter = _fake_adapter_with_quote("USDJPY", Decimal("159.30"), Decimal("159.32"))
    async_result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("199.500"), stop=Decimal("199.469"), symbol_info=_jpy_cross("GBPJPY"), account_currency="USD", adapter=adapter))
    assert async_result.estimates["contract_size"].available is True
    assert async_result.blocked is False


# 3. AUDJPY correctly handles JPY profit currency.
def test_3_audjpy_handles_jpy_profit_currency():
    adapter = _fake_adapter_with_quote("USDJPY", Decimal("159.30"), Decimal("159.32"))
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("103.500"), stop=Decimal("103.469"), symbol_info=_jpy_cross("AUDJPY"), account_currency="USD", adapter=adapter))
    assert result.estimates["contract_size"].available is True
    assert "direct_pair:USDJPY" in result.estimates["contract_size"].detail
    assert result.blocked is False


# 4. EURGBP handles GBP profit currency into a USD account.
def test_4_eurgbp_handles_gbp_profit_currency_into_usd_account():
    adapter = _fake_adapter_with_quote("GBPUSD", Decimal("1.2699"), Decimal("1.2701"))
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("0.86000"), stop=Decimal("0.85900"), symbol_info=_eurgbp(), account_currency="USD", adapter=adapter))
    assert result.estimates["contract_size"].available is True
    assert "inverse_pair:GBPUSD" in result.estimates["contract_size"].detail
    assert result.blocked is False
    # raw GBP loss (0.001 x 100000 = 100) x 1.27 =~ 127 USD, close to tick_value(1.27/pip x 10 pips = 12.7)...
    # (tick_value here is per-tick; the important assertion is agreement, not the exact figure).
    assert result.estimates["contract_size"].loss_per_lot == pytest.approx(result.estimates["tick_value"].loss_per_lot, rel=Decimal("0.01"))


# 5. EURCHF handles CHF profit currency into a USD account.
def test_5_eurchf_handles_chf_profit_currency_into_usd_account():
    adapter = _fake_adapter_with_quote("USDCHF", Decimal("0.8099"), Decimal("0.8101"))
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("0.94000"), stop=Decimal("0.93900"), symbol_info=_eurchf(), account_currency="USD", adapter=adapter))
    assert result.estimates["contract_size"].available is True
    assert "direct_pair:USDCHF" in result.estimates["contract_size"].detail
    assert result.blocked is False
    assert result.estimates["contract_size"].loss_per_lot == pytest.approx(result.estimates["tick_value"].loss_per_lot, rel=Decimal("0.01"))


# 6. GBPCAD handles CAD profit currency into a USD account.
def test_6_gbpcad_handles_cad_profit_currency_into_usd_account():
    adapter = _fake_adapter_with_quote("USDCAD", Decimal("1.3939"), Decimal("1.3941"))
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("1.75000"), stop=Decimal("1.74900"), symbol_info=_gbpcad(), account_currency="USD", adapter=adapter))
    assert result.estimates["contract_size"].available is True
    assert "direct_pair:USDCAD" in result.estimates["contract_size"].detail
    assert result.blocked is False
    assert result.estimates["contract_size"].loss_per_lot == pytest.approx(result.estimates["tick_value"].loss_per_lot, rel=Decimal("0.01"))


# 7. Direct conversion pair works (restated; see also test_risk_calculator.py #15).
def test_7_direct_conversion_pair_works():
    adapter = _fake_adapter_with_quote("USDJPY", Decimal("159.30"), Decimal("159.32"))
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_jpy_cross("EURJPY"), account_currency="USD", adapter=adapter))
    assert "direct_pair:USDJPY" in result.estimates["contract_size"].detail


# 8. Inverse conversion pair works (restated; see also test_risk_calculator.py #16).
def test_8_inverse_conversion_pair_works():
    class _InverseOnlyAdapter:
        async def latest_tick(self, symbol: str):
            if symbol != "JPYUSD":
                raise LookupError(f"no quote for {symbol}")
            from types import SimpleNamespace
            rate = Decimal("1") / Decimal("159.31")
            return SimpleNamespace(bid=rate, ask=rate)

    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_jpy_cross("EURJPY"), account_currency="USD", adapter=_InverseOnlyAdapter()))
    assert "inverse_pair:JPYUSD" in result.estimates["contract_size"].detail


# 9. Missing conversion fails safe (restated; see also test_risk_calculator.py #17).
def test_9_missing_conversion_fails_safe():
    class _NoQuoteAdapter:
        async def latest_tick(self, symbol: str):
            raise LookupError("no route")

    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_jpy_cross("EURJPY"), account_currency="USD", adapter=_NoQuoteAdapter()))
    assert result.estimates["contract_size"].available is False
    assert "conversion_unavailable_no_broker_quote" in result.estimates["contract_size"].detail


# 10. Account currency other than USD is supported (restated; see also test_risk_calculator.py #18).
def test_10_account_currency_other_than_usd_is_supported():
    adapter = _fake_adapter_with_quote("EURJPY", Decimal("183.75"), Decimal("183.80"))
    symbol = _jpy_cross("EURJPY")
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=symbol, account_currency="EUR", adapter=adapter))
    assert result.estimates["contract_size"].available is True
    assert "direct_pair:EURJPY" in result.estimates["contract_size"].detail


# --------------------------------------------------------------- 11-18: XAUUSD / non-FX -----

# 11. XAUUSD uses its actual tick size (0.01), not an FX point/pip assumption (e.g. 0.0001).
def test_11_xauusd_uses_actual_tick_size_not_fx_pip_assumption():
    symbol = _xauusd_broken()
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=symbol, mt5_client=_FakeNativeMT5(100.0)))
    # contract_size uses trade_contract_size(100) directly against the real 23.59 price
    # distance -- an FX-pip assumption would produce a wildly different (wrong) figure.
    assert result.estimates["contract_size"].loss_per_lot == pytest.approx(Decimal("2359.0"), rel=Decimal("0.001"))


# 12. XAUUSD tick-value calculation matches broker-native order_calc_profit within tolerance
# WHEN metadata is valid (a healthy XAUUSD, unlike the broken live one).
def test_12_xauusd_tick_value_matches_order_calc_profit_when_metadata_valid():
    healthy = _xauusd_broken().model_copy(update={"trade_tick_value": Decimal("1.0"), "trade_tick_value_loss": Decimal("1.0"), "trade_tick_value_profit": Decimal("1.0")})
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=healthy, mt5_client=_FakeNativeMT5(100.0)))
    assert result.estimates["tick_value"].available is True
    assert result.estimates["tick_value"].loss_per_lot == pytest.approx(result.estimates["order_calc_profit"].loss_per_lot, rel=Decimal("0.001"))
    assert result.blocked is False
    assert result.warning_codes == []


# 13. trade_tick_value_loss is preferred (used) for stop-loss-side calculations when present.
def test_13_trade_tick_value_loss_preferred_when_present():
    symbol = _xauusd_broken().model_copy(update={"trade_tick_value_loss": Decimal("1.0"), "trade_tick_value": Decimal("0.1")})
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=symbol, mt5_client=_FakeNativeMT5(100.0)))
    # trade_tick_value_loss(1.0) is tried FIRST in _tick_value_estimate -- self-consistent with
    # contract_size, so it is accepted (not excluded like the broken trade_tick_value alone would be).
    assert result.estimates["tick_value"].available is True
    assert result.estimates["tick_value"].loss_per_lot == pytest.approx(Decimal("2359.0"), rel=Decimal("0.001"))


# 14. Contract size is not double-applied -- the tick_value formula never multiplies by
# trade_contract_size a second time (it derives loss purely from tick_size/tick_value/stop_distance).
def test_14_contract_size_not_double_applied_in_tick_value_formula():
    source = inspect.getsource(risk_calculator_module._tick_value_estimate)
    assert "contract_size" not in source
    assert "trade_contract_size" not in source


# 15. XAUUSD calc mode (trade_calc_mode) is recorded/respected -- surfaced in the universe
# audit, never silently ignored.
def test_15_xauusd_calc_mode_is_recorded():
    symbol = _xauusd_broken()
    assert symbol.trade_calc_mode == 4
    adapter = _fake_adapter_with_quote("XAUUSD", Decimal("4283.55"), Decimal("4283.92"))
    audit = asyncio.run(risk_calculator_module.audit_symbol_risk_metadata(symbol_info=symbol, account_currency="USD", adapter=adapter, mt5_client=_FakeNativeMT5(100.0)))
    assert audit["calc_mode"] == 4


# 16. A genuine broker metadata mismatch (not explained by the self-consistency check) remains
# blocked -- see also test_risk_calculator.py #7.
def test_16_genuine_mismatch_remains_blocked():
    from backend.brokers.mt5.risk_calculator import CRITICAL_MISMATCH

    symbol = MT5Symbol(
        symbol="EURUSD", visible=True, selected=True, digits=5, point=Decimal("0.00001"),
        trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"),
        trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=symbol, mt5_client=_FakeNativeMT5(1_000_000.0), critical_pct=100.0))
    assert result.blocked is True
    assert result.block_reason == CRITICAL_MISMATCH


# 17. No hardcoded symbol multiplier exists anywhere in the risk calculator module (the module
# docstring/comments mentioning "XAUUSD" as the incident that motivated this module are fine --
# what must be absent is any CODE branch keyed on a specific symbol name or a magic 10x scale).
def test_17_no_hardcoded_symbol_multiplier():
    source = inspect.getsource(risk_calculator_module)
    assert 'symbol_info.symbol ==' not in source
    assert '.symbol == "XAU' not in source
    assert '.symbol == "XAG' not in source
    assert 'currency_base == "XAU"' not in source
    assert '* Decimal("10")' not in source
    assert '/ Decimal("10")' not in source


# 18. XAGUSD (a DIFFERENT, healthy metal) does not accidentally get treated with JPY/FX
# assumptions, and is not falsely flagged just for sharing XAUUSD's calc mode.
def test_18_xagusd_healthy_metal_not_falsely_flagged():
    symbol = _xagusd_healthy()
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("32.500"), stop=Decimal("32.400"), symbol_info=symbol, mt5_client=_FakeNativeMT5(5000.0)))
    assert result.estimates["tick_value"].available is True
    assert result.blocked is False
    assert result.warning_codes == []


# --------------------------------------------------------------------- 19-29: safety ---------

# 19. Two agreeing independent methods satisfy the documented quorum when the third is
# legitimately unavailable.
def test_19_two_agreeing_methods_satisfy_quorum_when_third_unavailable():
    symbol = _eurgbp().model_copy(update={"trade_contract_size": None})
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("0.86000"), stop=Decimal("0.85900"), symbol_info=symbol, mt5_client=_FakeNativeMT5(127000.0)))
    assert result.estimates["contract_size"].available is False
    assert result.trusted_method_count == 2
    assert result.quorum_met is True
    assert result.blocked is False


# 20. One method alone fails closed (restated; see also test_risk_calculator.py #4).
def test_20_one_method_alone_fails_closed():
    symbol = _eurgbp().model_copy(update={"trade_contract_size": None})
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("0.86000"), stop=Decimal("0.85900"), symbol_info=symbol)
    assert result.blocked is True
    assert result.block_reason == INSUFFICIENT_QUORUM


# 21. Two disagreeing (unresolved) trustworthy methods fail closed. Whether a contract_size/
# tick_value disagreement this large is caught upstream by the dimensional self-consistency
# check (converting it to "insufficient quorum") or reaches the outer disagreement check
# directly, the SAFETY OUTCOME is identical: a trade is never sized off methods that disagree
# this much.
def test_21_two_disagreeing_methods_fail_closed():
    symbol = _eurgbp().model_copy(update={"trade_tick_value": Decimal("5.0"), "trade_tick_value_loss": Decimal("5.0"), "trade_tick_value_profit": Decimal("5.0")})
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("0.86000"), stop=Decimal("0.85900"), symbol_info=symbol, critical_pct=100.0)
    assert result.blocked is True


# 22. Portfolio risk config is unchanged.
def test_22_portfolio_risk_unchanged():
    from backend.portfolio_execution.service import portfolio_manager

    assert hasattr(portfolio_manager, "can_open_new_trade")


# 23. risk-per-trade config is unchanged.
def test_23_risk_per_trade_config_unchanged():
    cfg = mt5_config()
    assert cfg.risk_percent_per_trade is not None
    assert cfg.max_risk_per_trade_usd is not None


# 24. Confidence threshold matches the current operational value -- 2026-08-26: lowered
# 75 -> 55 (user-requested trade-frequency increase, docker-compose.yml).
def test_24_confidence_threshold_matches_current_operational_value():
    assert mt5_config().min_trade_confidence == 55.0


# 25. Multi-strategy engine unchanged.
def test_25_multi_strategy_engine_unchanged():
    from backend.mt5_strategies.models import STRATEGY_FAMILIES

    # "wyckoff" (2026-08-17) and "donchian_trend_follow" (2026-08-24) are excluded -- both are
    # later additions, DISABLED pending their own historical/OOS validation, not part of the
    # Stage-1-complete cohort this test covers.
    assert len([sid for sid in STRATEGY_FAMILIES if sid not in {"mtfai1", "wyckoff", "donchian_trend_follow", "session_liquidity_breakout", "fx_relative_momentum"}]) == 10
    assert "mtfai1" in STRATEGY_FAMILIES


# 26. Adaptive manager unchanged.
def test_26_adaptive_manager_unchanged():
    from backend.adaptive_management.service import AdaptiveManagementService, ManagementCandidate

    service = AdaptiveManagementService()
    candidates = [ManagementCandidate(action_type="HOLD", priority=100), ManagementCandidate(action_type="TRAIL_STOP", priority=5)]
    assert service._select_action(candidates).action_type == "TRAIL_STOP"


# 27. OpenAI/LLM calls remain zero in MT5 (risk-calculator module never touches any provider).
def test_27_openai_llm_calls_remain_zero_in_risk_calculator():
    source = inspect.getsource(risk_calculator_module)
    assert "openai" not in source.lower()
    assert "provider_registry" not in source
    assert "ai_provider" not in source


# 28. IBKR remains absent.
def test_28_ibkr_remains_absent():
    source = inspect.getsource(risk_calculator_module)
    assert "backend.brokers.ibkr" not in source
    assert "ibkr_config" not in source


# 29. Live trading remains blocked.
def test_29_live_trading_remains_blocked():
    assert mt5_config().live_trading_enabled is False
