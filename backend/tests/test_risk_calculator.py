from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from backend.brokers.mt5.models import MT5Symbol
from backend.brokers.mt5.risk_calculator import (
    CRITICAL_MISMATCH,
    INSUFFICIENT_QUORUM,
    TICK_VALUE_SELF_INCONSISTENT,
    WARNING_MISMATCH,
    calculate_canonical_loss_per_lot,
    calculate_conservative_loss_per_lot_sync,
)


class _FakeNativeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, per_point: float):
        self.per_point = per_point

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        diff = (price_close - price_open) if order_type == self.ORDER_TYPE_BUY else (price_open - price_close)
        return diff * self.per_point * volume


def _eurusd_clean() -> MT5Symbol:
    return MT5Symbol(
        symbol="EURUSD", visible=True, selected=True, digits=5, point=Decimal("0.00001"),
        trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"),
        trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _xauusd_broken() -> MT5Symbol:
    """Shape of the real live incident (both the original XAUUSD ticket and the one found again
    on this account 2026-08-10): trade_tick_value is exactly 10x too small versus
    trade_contract_size, while trade_contract_size and order_calc_profit agree exactly."""
    return MT5Symbol(
        symbol="XAUUSD", visible=True, selected=True, digits=2, point=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("0.1"), trade_tick_value_profit=Decimal("0.1"),
        trade_tick_value_loss=Decimal("0.1"), trade_contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _eurjpy_live() -> MT5Symbol:
    """Real values observed on the live $10K demo (2026-08-10): order_calc_profit and
    tick_value agree closely (~$19.47/lot); contract_size(100000) x stop_distance(0.031) =
    3100, off by ~159x -- the live USD/JPY rate -- because that formula yields a JPY figure on
    a USD account, not a currency-conversion bug in the OTHER two methods."""
    return MT5Symbol(
        symbol="EURJPY", visible=True, selected=True, digits=3, point=Decimal("0.001"),
        currency_base="EUR", currency_profit="JPY", currency_margin="EUR",
        trade_tick_size=Decimal("0.001"), trade_tick_value=Decimal("0.6279829188646069"),
        trade_tick_value_profit=Decimal("0.6279829188646069"), trade_tick_value_loss=Decimal("0.6279829188646069"),
        trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _fake_adapter_with_quote(broker_symbol: str, bid: Decimal, ask: Decimal):
    """Minimal async-quote stand-in for resolve_conversion_rate()'s adapter.latest_tick(symbol)
    -- only ever returns a quote for the ONE symbol it was built for, raising for anything else
    (proving callers never guess a symbol they can't confirm exists)."""
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


# 1. order_calc_profit calculation available.
def test_order_calc_profit_estimate_available_when_client_provided():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken(), mt5_client=_FakeNativeMT5(100.0)))
    assert result.estimates["order_calc_profit"].available is True
    assert result.estimates["order_calc_profit"].loss_per_lot == pytest.approx(Decimal("2359.0"), rel=Decimal("0.001"))


# 2. order_calc_profit selected/considered.
def test_order_calc_profit_selected_when_it_is_the_most_conservative():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken(), mt5_client=_FakeNativeMT5(100.0)))
    assert result.selected_method == "order_calc_profit"
    assert result.selected_loss_per_lot == result.estimates["order_calc_profit"].loss_per_lot


# 3. Sync path (contract_size + tick_value only, no order_calc_profit tie-breaker): once the
# self-consistency check excludes the dimensionally-broken tick_value, only ONE trustworthy
# method (contract_size) remains -- the explicit quorum policy (MIN_TRUSTED_METHODS=2) fails
# this closed rather than silently trusting the lone survivor.
def test_sync_path_fails_closed_when_self_consistency_leaves_only_one_method():
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken())
    assert result.estimates["tick_value"].available is False
    assert TICK_VALUE_SELF_INCONSISTENT in result.estimates["tick_value"].detail
    assert result.estimates["contract_size"].available is True
    assert result.blocked is True
    assert result.block_reason == INSUFFICIENT_QUORUM


# 4. A single available method (no corroboration at all) always fails closed -- the module's
# founding "never trust one field alone" principle, now structurally enforced.
def test_single_available_method_fails_closed():
    symbol = _eurusd_clean().model_copy(update={"trade_contract_size": None})
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=symbol)
    assert result.estimates["contract_size"].available is False
    assert result.estimates["tick_value"].available is True
    assert result.blocked is True
    assert result.block_reason == INSUFFICIENT_QUORUM


# 5. Largest conservative loss selected -- never the smallest -- among genuinely, independently
# disagreeing trustworthy methods (order_calc_profit reporting 150 vs contract_size/tick_value's
# consistent 100; this disagreement is NOT explained/resolved by the tick_value<->contract_size
# self-consistency check, since contract_size and tick_value themselves agree).
def test_always_selects_largest_not_smallest_estimate():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=_eurusd_clean(), mt5_client=_FakeNativeMT5(150000.0)))
    all_available = [est.loss_per_lot for est in result.estimates.values() if est.available and est.loss_per_lot]
    assert result.selected_loss_per_lot == max(all_available)
    assert result.selected_loss_per_lot != min(all_available)
    assert result.selected_method == "order_calc_profit"


# 6. Genuine (self-consistency-unresolvable) disagreement still produces a warning when below
# the critical threshold.
def test_unresolved_disagreement_produces_warning_code():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=_eurusd_clean(), mt5_client=_FakeNativeMT5(150000.0)))
    assert WARNING_MISMATCH in result.warning_codes
    assert result.max_disagreement_pct == pytest.approx(50.0, rel=0.01)  # order_calc_profit(150) vs contract/tick(100)
    assert result.blocked is False


# 7. Genuine (self-consistency-unresolvable) critical disagreement still blocks entry.
def test_unresolved_critical_disagreement_sets_blocked_true():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=_eurusd_clean(), mt5_client=_FakeNativeMT5(1_000_000.0), critical_pct=100.0))
    assert CRITICAL_MISMATCH in result.warning_codes
    assert result.blocked is True
    assert result.block_reason == CRITICAL_MISMATCH


# 8. XAUUSD FIX regression: the exact live-incident numbers no longer produce a false block.
# trade_tick_value is dimensionally inconsistent with trade_contract_size (confirmed live,
# exactly 10x) and is excluded; order_calc_profit and trade_contract_size independently agree
# exactly ($2359/lot), meeting the 2-method quorum -- genuinely resolved, not blindly trusted.
def test_xauusd_fix_incident_numbers_no_longer_falsely_blocked():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken(), mt5_client=_FakeNativeMT5(100.0)))
    assert result.estimates["tick_value"].available is False
    assert TICK_VALUE_SELF_INCONSISTENT in result.estimates["tick_value"].detail
    assert result.trusted_method_count == 2
    assert result.quorum_met is True
    assert result.blocked is False
    assert result.selected_loss_per_lot == pytest.approx(Decimal("2359.0"), rel=Decimal("0.001"))
    assert (result.selected_loss_per_lot * Decimal("0.10")) == pytest.approx(Decimal("235.90"), rel=Decimal("0.001"))
    # The malformed, 10x-too-small tick_value figure must never be the selected/trusted value.
    assert result.selected_loss_per_lot != pytest.approx(Decimal("235.9"), rel=Decimal("0.001"))


# 9. normal EURUSD estimates agree within tolerance (no false positive mismatch on healthy data).
def test_eurusd_clean_metadata_estimates_agree_no_warning():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=_eurusd_clean(), mt5_client=_FakeNativeMT5(100_000.0)))
    assert result.warning_codes == []
    assert result.blocked is False
    assert result.max_disagreement_pct == pytest.approx(0.0, abs=0.01)


# 10. SELL direction uses the correct broker-side sign (mirrors the BUY case).
def test_short_direction_produces_positive_loss_for_stop_above_entry():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="SHORT", entry=Decimal("1.10000"), stop=Decimal("1.10100"), symbol_info=_eurusd_clean(), mt5_client=_FakeNativeMT5(100_000.0)))
    assert result.selected_loss_per_lot > 0
    assert result.estimates["order_calc_profit"].loss_per_lot == pytest.approx(Decimal("100.0"), rel=Decimal("0.001"))


# 11. Sync path (no adapter available): cross-currency contract_size can never be converted, so
# only tick_value remains -- correctly fails closed under the explicit quorum policy rather than
# trusting the lone survivor (this is a deliberate strengthening: previously a lone tick_value
# was accepted here).
def test_sync_path_cross_currency_symbol_fails_closed_no_adapter():
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_eurjpy_live(), account_currency="USD")
    assert result.estimates["contract_size"].available is False
    assert "profit currency" in result.estimates["contract_size"].detail
    assert result.estimates["tick_value"].available is True
    assert result.blocked is True
    assert result.block_reason == INSUFFICIENT_QUORUM


# 12. Async path with order_calc_profit available: order_calc_profit + tick_value (both already
# account-currency, agreeing closely) meet the 2-method quorum even without a live conversion.
def test_cross_currency_symbol_estimates_agree_via_order_calc_and_tick_value():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_eurjpy_live(), mt5_client=_FakeNativeMT5(0.6279829188646069 / 0.001), account_currency="USD"))
    assert result.estimates["contract_size"].available is False
    assert result.warning_codes == []
    assert result.blocked is False
    assert result.selected_method in {"order_calc_profit", "tick_value"}


# 13. account_currency default ("USD") does not change behavior for existing USD-quoted
# fixtures whose currency_profit is unset -- no regression for every pre-existing caller.
def test_account_currency_default_does_not_affect_symbols_without_currency_profit():
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken())
    assert result.estimates["contract_size"].available is True


# 14. same-currency symbol (currency_profit == account_currency) is unaffected by the guard.
def test_contract_size_still_runs_when_profit_currency_matches_account_currency():
    symbol = _eurusd_clean().model_copy(update={"currency_profit": "USD"})
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=symbol, account_currency="USD")
    assert result.estimates["contract_size"].available is True


# 15. Direct conversion pair (e.g. USDJPY) resolves and converts contract_size correctly.
def test_conversion_direct_pair_resolves_and_converts_contract_size():
    adapter = _fake_adapter_with_quote("USDJPY", Decimal("159.30"), Decimal("159.32"))
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_eurjpy_live(), account_currency="USD", adapter=adapter))
    assert result.estimates["contract_size"].available is True
    assert "direct_pair:USDJPY" in result.estimates["contract_size"].detail
    # raw JPY loss (0.031 * 100000 = 3100) / ~159.31 =~ 19.46, consistent with tick_value/order_calc_profit.
    assert result.estimates["contract_size"].loss_per_lot == pytest.approx(Decimal("19.46"), rel=Decimal("0.01"))


# 16. Inverse conversion pair (e.g. JPYUSD, when the broker only exposes that direction) works.
def test_conversion_inverse_pair_resolves_and_converts_contract_size():
    class _InverseOnlyAdapter:
        async def latest_tick(self, symbol: str):
            if symbol != "JPYUSD":
                raise LookupError(f"no quote for {symbol}")
            from types import SimpleNamespace
            rate = Decimal("1") / Decimal("159.31")
            return SimpleNamespace(bid=rate, ask=rate)

    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_eurjpy_live(), account_currency="USD", adapter=_InverseOnlyAdapter()))
    assert result.estimates["contract_size"].available is True
    assert "inverse_pair:JPYUSD" in result.estimates["contract_size"].detail
    assert result.estimates["contract_size"].loss_per_lot == pytest.approx(Decimal("19.46"), rel=Decimal("0.01"))


# 17. Missing conversion (adapter present but no matching quote for either direction) fails safe
# -- contract_size stays unavailable with a precise reason, never a guessed rate.
def test_conversion_missing_fails_safe_never_guesses():
    class _NoQuoteAdapter:
        async def latest_tick(self, symbol: str):
            raise LookupError("no route to this symbol")

    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_eurjpy_live(), mt5_client=_FakeNativeMT5(0.6279829188646069 / 0.001), account_currency="USD", adapter=_NoQuoteAdapter()))
    assert result.estimates["contract_size"].available is False
    assert "conversion_unavailable_no_broker_quote" in result.estimates["contract_size"].detail
    # order_calc_profit + tick_value still meet quorum even though contract_size could not convert.
    assert result.blocked is False


# 18. Account currency other than USD is supported -- no hardcoded USD assumption anywhere.
# Sizing a JPY-profit-currency symbol (EURJPY) on a EUR account: converting JPY -> EUR uses the
# instrument's own quote (EURJPY IS the EUR/JPY rate), resolved via the exact same
# resolve_conversion_rate() helper used for JPY -> USD -- no USD-specific code path exists.
def test_account_currency_other_than_usd_is_supported():
    eur_account_symbol = _eurjpy_live().model_copy(update={"currency_profit": "JPY"})
    adapter = _fake_adapter_with_quote("EURJPY", Decimal("183.75"), Decimal("183.80"))
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=eur_account_symbol, account_currency="EUR", adapter=adapter))
    assert result.estimates["contract_size"].available is True
    assert "direct_pair:EURJPY" in result.estimates["contract_size"].detail
    # raw JPY loss (0.031 x 100000 = 3100) / ~183.775 (mid EURJPY) =~ 16.87 EUR.
    assert result.estimates["contract_size"].loss_per_lot == pytest.approx(Decimal("16.87"), rel=Decimal("0.01"))
