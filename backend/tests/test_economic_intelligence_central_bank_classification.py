"""Central-bank event classification refinement -- the 13 required tests.

Root cause under test: event_mapping.is_central_bank_event() used to be a bare keyword
substring match (e.g. "fed" in text), so "Cleveland Fed Inflation Expectations" -- a regional
research release the provider itself labels low-impact -- was wrongly promoted to the same
60-minutes-before/30-minutes-after CENTRAL_BANK_EVENT_PRE_BLOCK window as an actual FOMC
decision. The fix requires an institution + genuine policy-event-type match (or a known
policymaker surname + speech term), with an explicit regional-Fed-branch exclusion checked
first. Downstream window selection (calendar_guard._tier_for/_evaluate_single) is unchanged --
only the classification feeding it is corrected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.brokers.mt5.config import mt5_config
from backend.economic_intelligence import calendar_guard
from backend.economic_intelligence.config import economic_intelligence_config
from backend.economic_intelligence.event_mapping import is_central_bank_event, normalize_broker_symbol

CFG = economic_intelligence_config()
NOW = datetime(2026, 8, 10, 13, 16, tzinfo=timezone.utc)


def _event(*, currency="USD", impact="high", minutes_from_now, name):
    """Mirrors production: is_central_bank_event is computed from raw_name exactly once, at
    ingestion (backend/economic_intelligence/persistence.py:upsert_event), using the SAME
    function under test here -- not hand-set, so these fixtures exercise the real classifier."""
    return {
        "currency": currency,
        "impact": impact,
        "scheduled_at_utc": (NOW + timedelta(minutes=minutes_from_now)).isoformat(),
        "is_central_bank_event": is_central_bank_event(name),
        "raw_name": name,
        "normalized_name": name.lower(),
        "provider": "ff_calendar_json",
    }


# 1. FOMC rate decision triggers CENTRAL_BANK_EVENT_PRE_BLOCK.
def test_fomc_rate_decision_triggers_central_bank_pre_block():
    assert is_central_bank_event("FOMC Rate Decision") is True
    events = [_event(minutes_from_now=30, name="FOMC Rate Decision", impact="high")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "BLOCK"
    assert any("CENTRAL_BANK_EVENT_PRE_BLOCK" in reason for reason in result["reason_codes"])


# 2. FOMC minutes receive critical central-bank treatment.
def test_fomc_minutes_receive_critical_treatment():
    assert is_central_bank_event("FOMC Minutes") is True
    events = [_event(minutes_from_now=45, name="FOMC Minutes", impact="medium")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    # Central-bank tier wins even though the provider tagged it only "medium" -- FOMC Minutes
    # is never a routine medium-impact release.
    assert result["decision"] == "BLOCK"
    assert any("CENTRAL_BANK_EVENT_PRE_BLOCK" in reason for reason in result["reason_codes"])


# 3. Major Fed Chair policy event can receive critical treatment.
def test_major_fed_chair_event_receives_critical_treatment():
    assert is_central_bank_event("Fed Chair Powell Testimony") is True
    assert is_central_bank_event("Powell Speaks") is True  # surname-only calendar title
    events = [_event(minutes_from_now=20, name="Fed Chair Powell Testimony", impact="high")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "BLOCK"
    assert any("CENTRAL_BANK_EVENT_PRE_BLOCK" in reason for reason in result["reason_codes"])


# 4. Cleveland Fed Inflation Expectations does NOT receive central-bank-critical
# classification solely because it contains "Fed".
def test_cleveland_fed_inflation_expectations_is_not_central_bank_critical():
    assert is_central_bank_event("Cleveland Fed Inflation Expectations") is False
    # As actually labeled by Forex Factory (low impact): no hard block at all.
    events = [_event(minutes_from_now=30, name="Cleveland Fed Inflation Expectations", impact="low")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "ALLOW"
    assert not any("CENTRAL_BANK_EVENT" in reason for reason in result["reason_codes"])


# 5. Regional Fed research releases do not automatically become critical.
def test_regional_fed_research_releases_are_not_automatically_critical():
    for name in ("Philly Fed Manufacturing Index", "Chicago Fed National Activity Index", "Richmond Fed Manufacturing Index", "Kansas City Fed Manufacturing Index", "Empire State Manufacturing Index"):
        assert is_central_bank_event(name) is False, name


# 6. CPI/NFP retain appropriate high-impact protection.
def test_cpi_and_nfp_retain_high_impact_protection():
    for name in ("CPI m/m", "Non-Farm Payrolls"):
        assert is_central_bank_event(name) is False
        events = [_event(minutes_from_now=10, name=name, impact="high")]
        result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
        assert result["decision"] == "BLOCK"
        assert any("HIGH_IMPACT_EVENT_PRE_BLOCK" in reason for reason in result["reason_codes"])


# 7. Medium events get only the shorter configured window.
def test_medium_event_gets_shorter_configured_window():
    events = [_event(minutes_from_now=CFG.ff_medium_impact_block_before_minutes - 2, name="Cleveland Fed Inflation Expectations", impact="medium")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "REDUCE_SIZE"
    assert any("MEDIUM_IMPACT_EVENT_PRE_REDUCE" in reason for reason in result["reason_codes"])
    # Confirms it is NOT the long central-bank window: well inside the (much longer)
    # central-bank pre-block window, a medium-tier event must already have cleared.
    far_out = [_event(minutes_from_now=CFG.ff_medium_impact_block_before_minutes + 5, name="Cleveland Fed Inflation Expectations", impact="medium")]
    assert calendar_guard.evaluate(["USD"], NOW, far_out, CFG)["decision"] == "ALLOW"


# 8. Low events do not hard-block trading.
def test_low_impact_event_does_not_hard_block():
    events = [_event(minutes_from_now=5, name="Minor Regional Survey", impact="low")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "ALLOW"


# 9. Currency relevance is respected where supported.
def test_currency_relevance_is_respected():
    fomc = [_event(currency="USD", minutes_from_now=15, name="FOMC Rate Decision", impact="high")]
    # A globally systemic USD event reaches gold (quoted in USD) via the existing
    # base/quote currency-set matching -- no unrelated-instrument change needed.
    xauusd_currencies = normalize_broker_symbol("XAUUSD").currencies
    assert calendar_guard.evaluate(list(xauusd_currencies), NOW, fomc, CFG)["decision"] == "BLOCK"
    # An unrelated pair (neither currency is USD) is correctly left alone.
    assert calendar_guard.evaluate(["GBP", "JPY"], NOW, fomc, CFG)["decision"] == "ALLOW"

    ecb = [_event(currency="EUR", minutes_from_now=15, name="ECB Interest Rate Decision", impact="high")]
    assert calendar_guard.evaluate(["EUR", "USD"], NOW, ecb, CFG)["decision"] == "BLOCK"
    assert calendar_guard.evaluate(["GBP", "JPY"], NOW, ecb, CFG)["decision"] == "ALLOW"


# 10. Existing portfolio/risk controls remain unchanged.
def test_portfolio_risk_controls_unchanged():
    block = calendar_guard.empty_result("BLOCK", ["X"])
    reduce = calendar_guard.empty_result("REDUCE_SIZE", ["Y"])
    allow = calendar_guard.empty_result("ALLOW")
    assert calendar_guard.combine(allow, reduce, block)["decision"] == "BLOCK"
    assert calendar_guard.combine(allow, reduce)["decision"] == "REDUCE_SIZE"


# 11. Confidence threshold matches the current operational value -- 2026-08-26: lowered
# 75 -> 55 (user-requested trade-frequency increase, docker-compose.yml).
def test_confidence_threshold_matches_current_operational_value():
    assert mt5_config().min_trade_confidence == 55.0


# 12. OpenAI remains absent from the MT5 decision pipeline.
def test_openai_absent_from_mt5_decision_pipeline():
    import inspect

    import backend.brokers.mt5.autonomous as autonomous_module

    source = inspect.getsource(autonomous_module)
    assert "usage_ledger" not in source
    assert "ai_trading_config" not in source
    assert "provider_registry" not in source


# 13. Live trading remains blocked.
def test_live_trading_remains_blocked():
    assert mt5_config().live_trading_enabled is False
